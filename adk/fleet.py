"""Инструменты парка ПК (3.1): массовый пинг + Wake-on-LAN, установленное ПО, входы за сутки, сравнение двух ПК.

Каждый диалог — ``FramelessDialog``; сетевые операции — в фоне (``run_in_background``/``ThreadPoolExecutor``);
все действия, меняющие что-либо (WoL, опрос ПО), пишутся в ``audit_log``. Чистая логика (:func:`compare_specs`,
:func:`flatten_specs`) вынесена в функции без Qt — она покрыта тестами.
"""
from __future__ import annotations

import logging
import threading

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import access, db, logons, nettools, netutils, software
from .i18n import tr
from .widgets import FramelessDialog, InputDialog, MessageBox, app_palette, fit_columns, run_in_background

log = logging.getLogger(__name__)


# ============================================================================ массовый пинг + WoL
class _PingBus(QObject):
    """Сигналы из пула потоков в GUI-поток."""
    one = pyqtSignal(str, str, bool)
    done = pyqtSignal()


class MassPingDialog(FramelessDialog):
    """Пинг списка ПК параллельно; выключенные можно разбудить (WoL по MAC из БД)."""

    def __init__(self, hosts: list[str], app, parent=None):
        super().__init__(f"📡 Массовый пинг: {len(hosts)} ПК", parent, (720, 520))
        self.app = app
        self.hosts = [db.clean_computer_name(h) for h in dict.fromkeys(hosts) if db.clean_computer_name(h)]
        self._cancel = False
        self.status = QLabel("")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["ПК", "IP", "Сеть", "MAC (для WoL)"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.body.addWidget(self.table, 1)
        self.body.addWidget(self.status)
        btns = QHBoxLayout()
        self.btn_again = QPushButton("🔄 Повторить")
        self.btn_again.clicked.connect(self.start)
        self.btn_wol = QPushButton("⚡ Разбудить выключенные (WoL)")
        self.btn_wol.setObjectName("btnPrimary")
        self.btn_wol.clicked.connect(self.wake_offline)
        self.btn_wol.setEnabled(False)
        btns.addWidget(self.btn_again)
        btns.addWidget(self.btn_wol)
        btns.addStretch()
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        self.body.addLayout(btns)
        self.rows: dict[str, int] = {}
        self.result: dict[str, tuple[str, bool]] = {}
        self.bus = _PingBus()
        self.bus.one.connect(self._one)
        self.bus.done.connect(self._done)
        self.start()

    def start(self):
        self._cancel = False
        self.result.clear()
        self.table.setRowCount(0)
        self.rows.clear()
        for h in self.hosts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.rows[h] = r
            for c, v in enumerate((h, "…", "⏳", db.get_mac(h) or "—")):
                self.table.setItem(r, c, QTableWidgetItem(v))
        fit_columns(self.table, wrap=False)
        self.status.setText(f"Опрос {len(self.hosts)} ПК…")
        self.btn_wol.setEnabled(False)
        self.btn_again.setEnabled(False)

        def work():
            nettools.mass_ping(self.hosts, lambda c, ip, on: self.bus.one.emit(c, ip, on), cancelled=lambda: self._cancel)
            self.bus.done.emit()
        threading.Thread(target=work, name="adk-massping", daemon=True).start()

    def _one(self, comp: str, ip: str, online: bool):
        pal = app_palette()
        r = self.rows.get(comp)
        if r is None:
            return
        if not ip:
            ip = "Не найден"
        # если DNS имя не знает, nettools.probe_host уже подставил адрес из инвентаря с пометкой источника
        # и пропинговал именно его — поэтому «в сети» здесь честное, а не «нет» из-за устаревшей записи DNS
        self.result[comp] = (ip, online)
        self.table.item(r, 1).setText(ip)
        it = self.table.item(r, 2)
        it.setText("🟢 в сети" if online else "🔴 нет")
        it.setForeground(QColor(pal.success[0] if online else pal.danger[0]))
        try:
            db.set_pc_online(comp, online)
        except Exception as exc:  # noqa: BLE001
            log.debug("set_pc_online: %s", exc)
        bare_ip = ip.split(" ", 1)[0]
        if online and bare_ip and bare_ip != "Не найден" and not db.get_mac(comp):
            run_in_background(self, lambda: nettools.learn_mac(comp, bare_ip),
                              lambda m, row=r: self.table.item(row, 3).setText(m or "—"), lambda m: None)

    def _done(self):
        on = sum(1 for _, o in self.result.values() if o)
        off = [c for c, (_, o) in self.result.items() if not o]
        wakeable = [c for c in off if db.get_mac(c)]
        fit_columns(self.table, wrap=False)
        self.status.setText(f"В сети: {on} · не в сети: {len(off)} · можно разбудить: {len(wakeable)}")
        self.btn_wol.setEnabled(bool(wakeable) and access.can("wol"))
        self.btn_again.setEnabled(True)
        if hasattr(self.app, "refresh_dashboard"):
            self.app.refresh_dashboard()

    def wake_offline(self):
        targets = [(c, db.get_mac(c)) for c, (_, o) in self.result.items() if not o and db.get_mac(c)]
        if not targets:
            return
        if not MessageBox.question(self, "Wake-on-LAN", f"Отправить magic-пакет на {len(targets)} ПК?"):
            return
        sent = 0
        for comp, mac in targets:
            if nettools.wake(mac):
                sent += 1
                db.log_action(self.app.admin_name, "wol", comp, mac)
        self.status.setText(f"⚡ WoL отправлен: {sent} из {len(targets)}. Повторите пинг через 30–60 секунд.")

    def on_dialog_done(self):
        self._cancel = True


def wake_single(comp: str, app, parent=None) -> None:
    """WoL одного ПК из меню/инспектора: MAC берём из БД, при отсутствии — спрашиваем."""
    if not access.can("wol"):
        MessageBox.warning(parent, tr("Недостаточно прав"), tr(access.deny_text("wol")))
        return
    mac = db.get_mac(comp)
    if not mac:
        text, ok = InputDialog.get_text(parent, "Wake-on-LAN", f"MAC-адрес {comp} неизвестен (он запоминается, когда ПК в сети).\nВведите вручную:")
        if not ok:
            return
        mac = nettools.parse_mac(text)
        if not mac:
            MessageBox.warning(parent, "Wake-on-LAN", "Некорректный MAC-адрес.")
            return
        db.save_mac(comp, mac)
    if nettools.wake(mac):
        db.log_action(app.admin_name, "wol", comp, mac)
        MessageBox.information(parent, "Wake-on-LAN", f"Magic-пакет отправлен на {comp} ({mac}).\nПК обычно поднимается за 30–60 секунд.")
    else:
        MessageBox.warning(parent, "Wake-on-LAN", "Не удалось отправить пакет (см. журнал).")


# ============================================================================ установленное ПО
class SoftwareDialog(FramelessDialog):
    """Вкладки: ПО на этом ПК (кэш + опрос), обновления Windows, поиск «у кого стоит…» по всему парку."""

    def __init__(self, comp: str, app, parent=None):
        super().__init__(f"📦 Установленное ПО: {comp}" if comp else "📦 ПО парка", parent, (860, 600))
        self.comp, self.app = comp, app
        self.tabs = QTabWidget()
        self.body.addWidget(self.tabs, 1)
        if comp:
            self.tabs.addTab(self._build_pc_tab(), "💻 На этом ПК")
            self.tabs.addTab(self._build_hotfix_tab(), "🩹 Обновления Windows")
        self.tabs.addTab(self._build_fleet_tab(), "🔎 У кого установлено")
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        self.body.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        if comp:
            self._show_cached()

    # --- вкладка ПК
    def _build_pc_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Фильтр по названию/издателю…")
        self.filter.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter, 1)
        self.btn_poll = QPushButton("🔄 Опросить ПК")
        self.btn_poll.setObjectName("btnPrimary")
        self.btn_poll.clicked.connect(self.poll)
        top.addWidget(self.btn_poll)
        lay.addLayout(top)
        self.table = _table(["Программа", "Версия", "Издатель", "Установлена"])
        lay.addWidget(self.table, 1)
        self.lbl = QLabel("")
        lay.addWidget(self.lbl)
        return w

    def _build_hotfix_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.hot = _table(["KB", "Описание", "Установлено"])
        lay.addWidget(self.hot, 1)
        lay.addWidget(QLabel("Последние 15 обновлений (Win32_QuickFixEngineering). Заполняется при опросе ПК."))
        return w

    def _build_fleet_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText("Название программы (часть), например: 1С, Chrome, KES…")
        self.q.returnPressed.connect(self.search_fleet)
        top.addWidget(self.q, 1)
        b = QPushButton("Найти")
        b.setObjectName("btnPrimary")
        b.clicked.connect(self.search_fleet)
        top.addWidget(b)
        lay.addLayout(top)
        self.fleet = _table(["ПК", "Программа", "Версия", "Опрошен"])
        self.fleet.itemDoubleClicked.connect(lambda it: self.app.search_text(self.fleet.item(it.row(), 0).text()) if hasattr(self.app, "search_text") else None)
        lay.addWidget(self.fleet, 1)
        self.fleet_lbl = QLabel("Поиск идёт по сохранённым данным опрошенных ПК. Двойной клик — найти ПК в главном окне.")
        lay.addWidget(self.fleet_lbl)
        self._fill_summary()
        return w

    def _fill_summary(self):
        top = software.software_summary()
        if not top:
            return
        self.fleet_lbl.setText("Топ программ по числу ПК (по сохранённым данным). Введите название для точного поиска.")
        _fill(self.fleet, [(f"{n} ПК", name, "", "") for name, n in top[:100]])

    # --- данные
    def _show_cached(self):
        items, ts = software.cached_software(self.comp)
        self._items = items
        self._apply_filter()
        self.lbl.setText(f"Сохранённые данные от {ts}" if ts else "ПО ещё не опрашивалось — нажмите «Опросить ПК»")

    def _apply_filter(self):
        f = self.filter.text().strip().casefold()
        rows = [(s["name"], s["version"], s["publisher"], s["installed"]) for s in getattr(self, "_items", [])
                if not f or f in s["name"].casefold() or f in s["publisher"].casefold()]
        _fill(self.table, rows)

    def poll(self):
        self.btn_poll.setEnabled(False)
        self.lbl.setText("⏳ Опрос через PowerShell Remoting (10–60 с)…")

        def done(d: dict):
            self.btn_poll.setEnabled(True)
            if "error" in d:
                self.lbl.setText(f"⚠️ {d['error']}")
                return
            self._items = d["software"]
            self._apply_filter()
            _fill(self.hot, [(h["id"], h["desc"], h["installed"]) for h in d["hotfixes"]])
            self.lbl.setText(f"Опрошено: программ — {len(d['software'])}, обновлений — {len(d['hotfixes'])}")
            db.log_action(self.app.admin_name, "software", self.comp, f"{len(d['software'])} программ")

        run_in_background(self, lambda: software.get_software(self.comp), done,
                          lambda m: (self.btn_poll.setEnabled(True), self.lbl.setText(f"⚠️ {m}")))

    def search_fleet(self):
        rows = software.find_software(self.q.text())
        _fill(self.fleet, [(r["comp"], r["name"], r["version"], r["ts"][:16]) for r in rows])
        comps = len({r["comp"] for r in rows})
        self.fleet_lbl.setText(f"Найдено: {len(rows)} записей на {comps} ПК" if rows else "Ничего не найдено в сохранённых данных (опросите ПК или уточните запрос)")


# ============================================================================ входы за сутки
class LogonsDialog(FramelessDialog):
    """События 4624/4625 за N часов — кто входил на ПК и кто ломился с неверным паролем."""

    def __init__(self, comp: str, app, parent=None):
        super().__init__(f"🔐 Входы на ПК за 24 ч: {comp}", parent, (860, 560))
        self.comp, self.app = comp, app
        top = QHBoxLayout()
        top.addWidget(QLabel("Период:"))
        self.hours = QComboBox()
        for h in (1, 8, 24, 72, 168):
            self.hours.addItem(f"{h} ч" if h < 48 else f"{h // 24} дн.", h)
        self.hours.setCurrentIndex(2)
        top.addWidget(self.hours)
        self.only_fail = QCheckBox("Только отказы")
        self.only_fail.toggled.connect(self._render)
        top.addWidget(self.only_fail)
        top.addStretch()
        b = QPushButton("🔄 Обновить")
        b.setObjectName("btnPrimary")
        b.clicked.connect(self.load)
        top.addWidget(b)
        self.body.addLayout(top)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)
        self.table = _table(["Время", "Пользователь", "Тип", "Откуда (IP)", "Результат"])
        self.body.addWidget(self.table, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        self.body.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._data: dict = {"events": [], "by_user": [], "ok": 0, "fails": 0}
        self.load()

    def load(self):
        self.summary.setText("⏳ Читаю журнал Security (Get-WinEvent)…")
        hours = int(self.hours.currentData())
        run_in_background(self, lambda: logons.get_logons(self.comp, hours), self._done, lambda m: self.summary.setText(f"⚠️ {m}"))

    def _done(self, d: dict):
        self._data = d
        self.summary.setText(logons.format_summary(d))
        self._render()

    def _render(self):
        pal = app_palette()
        evs = self._data.get("events", [])
        if self.only_fail.isChecked():
            evs = [e for e in evs if e["kind"] == "fail"]
        self.table.setRowCount(0)
        for e in evs:
            r = self.table.rowCount()
            self.table.insertRow(r)
            user = f"{e['domain']}\\{e['user']}" if e["domain"] else e["user"]
            res = "✅ вход" if e["kind"] == "ok" else f"⛔ {e['reason'] or 'отказ'}"
            for c, v in enumerate((e["ts"], user, e["type"], e["ip"], res)):
                it = QTableWidgetItem(v)
                if e["kind"] == "fail":
                    it.setForeground(QColor(pal.danger[0]))
                self.table.setItem(r, c, it)
        fit_columns(self.table, max_width=360)


# ============================================================================ сравнение двух ПК
def flatten_specs(d: dict) -> dict[str, str]:
    """Дерево характеристик из CSV → плоский словарь «Раздел / параметр» → значение."""
    out: dict[str, str] = {}
    if not d or "error" in d:
        return out
    titles = {"os": "ОС", "board": "Плата", "bios": "BIOS", "cpu": "CPU", "rams": "ОЗУ", "disks": "Диск", "logdisks": "Том",
              "gpu": "Видео", "adapters": "Сеть", "printers": "Принтер"}
    for sect, val in d.items():
        t = titles.get(sect, sect)
        if not isinstance(val, dict):
            continue
        for k, v in val.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    out[f"{t} #{k} / {kk}"] = str(vv)
            else:
                out[f"{t} / {k}"] = str(v)
    return out


def compare_specs(a: dict, b: dict, only_diff: bool = False) -> list[tuple[str, str, str, bool]]:
    """→ [(параметр, значение A, значение B, различается)]. Чистая функция — тестируется без CSV/Qt."""
    fa, fb = flatten_specs(a), flatten_specs(b)
    rows = []
    for key in sorted(set(fa) | set(fb), key=str.casefold):
        va, vb = fa.get(key, "—"), fb.get(key, "—")
        diff = va.strip().casefold() != vb.strip().casefold()
        if only_diff and not diff:
            continue
        rows.append((key, va, vb, diff))
    return rows


def compare_lists(a: list[str], b: list[str]) -> list[tuple[str, str, str, bool]]:
    """Сравнение множеств (ПО, принтеры): ✔/— по каждому имени."""
    sa, sb = {x.casefold(): x for x in a}, {x.casefold(): x for x in b}
    rows = []
    for k in sorted(set(sa) | set(sb)):
        name = sa.get(k) or sb.get(k)
        rows.append((name, "✔" if k in sa else "—", "✔" if k in sb else "—", (k in sa) != (k in sb)))
    return rows


class ComparePCDialog(FramelessDialog):
    """Две колонки характеристик/ПО/принтеров, различия подсвечены."""

    def __init__(self, comp_a: str, comp_b: str, app, parent=None):
        super().__init__(f"⚖️ Сравнение ПК: {comp_a} ↔ {comp_b}", parent, (960, 620))
        self.a, self.b, self.app = comp_a, comp_b, app
        top = QHBoxLayout()
        self.only_diff = QCheckBox("Только различия")
        self.only_diff.setChecked(True)
        self.only_diff.toggled.connect(self.render)
        top.addWidget(self.only_diff)
        top.addStretch()
        self.lbl = QLabel("⏳ Загрузка…")
        self.lbl.setWordWrap(True)          # две ошибки «CSV … не найден» не влезали в строку и уходили за край окна
        self.lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.lbl, 1)
        self.body.addLayout(top)
        self.tabs = QTabWidget()
        self.t_specs = _table(["Параметр", comp_a, comp_b])
        self.t_soft = _table(["Программа", comp_a, comp_b])
        self.t_prn = _table(["Принтер", comp_a, comp_b])
        self.tabs.addTab(self.t_specs, "💻 Характеристики")
        soft_page = QWidget()
        sl = QVBoxLayout(soft_page)
        sl.setContentsMargins(0, 6, 0, 0)
        self.lbl_soft = QLabel("⏳ Опрашиваю оба ПК…")
        self.lbl_soft.setObjectName("subtle")
        self.lbl_soft.setWordWrap(True)
        sl.addWidget(self.lbl_soft)
        sl.addWidget(self.t_soft, 1)
        self.tabs.addTab(soft_page, "📦 Установленное ПО")
        self.tabs.setTabToolTip(1, "Программы снимаются с ПК прямо сейчас; если ПК недоступен — берётся последний "
                                   "сохранённый список, и в шапке написано, от какой он даты")
        self.tabs.addTab(self.t_prn, "🖨️ Принтеры")
        self.body.addWidget(self.tabs, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        self.body.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._data = None
        run_in_background(self, self._load, self._loaded, lambda m: self.lbl.setText(f"⚠️ {m}"))

    @staticmethod
    def _software_live_or_cached(host: str) -> tuple[list[str], str]:
        """Сначала живой опрос ПК; если ПК недоступен — последний сохранённый список с пометкой, когда он снят."""
        live = software.get_software(host, timeout=40)
        if "error" not in live:
            return [s["name"] for s in live["software"]], "сейчас"
        items, ts = software.cached_software(host)
        if items:
            return [s["name"] for s in items], f"недоступен, данные от {str(ts)[:16]}"
        return [], "недоступен, ранее не опрашивался"

    def _load(self):
        sa, ta = self._software_live_or_cached(self.a)
        sb, tb = self._software_live_or_cached(self.b)
        return {
            "specs": (netutils.get_computer_specs_dict(self.a), netutils.get_computer_specs_dict(self.b)),
            "soft": (sa, sb),
            "soft_src": (ta, tb),
            "prn": ([p["name"] for p in netutils.get_computer_printers_detailed(self.a)],
                    [p["name"] for p in netutils.get_computer_printers_detailed(self.b)]),
        }

    def _loaded(self, d):
        self._data = d
        self.render()

    def render(self):
        if not self._data:
            return
        only = self.only_diff.isChecked()
        pal = app_palette()
        sa, sb = self._data["specs"]
        specs = compare_specs(sa, sb, only)
        soft = [r for r in compare_lists(*self._data["soft"]) if not only or r[3]]
        prn = [r for r in compare_lists(*self._data["prn"]) if not only or r[3]]
        for table, rows in ((self.t_specs, specs), (self.t_soft, soft), (self.t_prn, prn)):
            table.setSortingEnabled(False)   # иначе строки пересортировываются во время заполнения и ячейки «теряются»
            table.setRowCount(0)
            for key, va, vb, diff in rows:
                r = table.rowCount()
                table.insertRow(r)
                for c, v in enumerate((key, va, vb)):
                    it = QTableWidgetItem(v)
                    it.setToolTip(v)
                    if diff:
                        it.setForeground(QColor(pal.warning[0]))
                    table.setItem(r, c, it)
            fit_columns(table, max_width=380)
            table.setColumnWidth(0, max(200, table.columnWidth(0)))
            for c in (1, 2):
                table.setColumnWidth(c, max(220, table.columnWidth(c)))
            table.setSortingEnabled(True)
        errs = [x.get("error") for x in (sa, sb) if "error" in x]
        if len(errs) == 2 and all("не найден" in e for e in errs):
            errs = [f"CSV с характеристиками не найден ни для {self.a}, ни для {self.b}"]
        n = sum(1 for r in compare_specs(sa, sb) if r[3])
        src_a, src_b = self._data.get("soft_src", ("", ""))
        self.lbl_soft.setText("Список программ снят с ПК прямо сейчас; если ПК недоступен — последний сохранённый.   "
                              + "   ·   ".join(f"<b>{c}</b>: {t}" for c, t in ((self.a, src_a), (self.b, src_b)) if t))
        self.lbl.setText("; ".join(errs) if errs else f"Различий в характеристиках: {n}")


# --------------------------------------------------------------------------- мелочи
def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True)
    return t


def _fill(table: QTableWidget, rows) -> None:
    table.setSortingEnabled(False)
    table.setRowCount(0)
    for row in rows:
        r = table.rowCount()
        table.insertRow(r)
        for c, v in enumerate(row):
            table.setItem(r, c, QTableWidgetItem(str(v)))
    fit_columns(table, max_width=400)
    table.setSortingEnabled(True)
