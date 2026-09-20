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
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
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
        self.btn_again = QPushButton(tr("🔄 Повторить"))
        self.btn_again.clicked.connect(self.start)
        self.btn_wol = QPushButton(tr("⚡ Разбудить выключенные (WoL)"))
        self.btn_wol.setObjectName("btnPrimary")
        self.btn_wol.clicked.connect(self.wake_offline)
        self.btn_wol.setEnabled(False)
        btns.addWidget(self.btn_again)
        btns.addWidget(self.btn_wol)
        btns.addStretch()
        close = QPushButton(tr("Закрыть"))
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
        self.status.setText(tr("Опрос {0} ПК…").format(len(self.hosts)))
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
        it.setText(tr("🟢 в сети") if online else "🔴 нет")
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
        self.status.setText(tr("В сети: {0} · не в сети: {1} · можно разбудить: {2}").format(on, len(off), len(wakeable)))
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
        self.status.setText(tr("⚡ WoL отправлен: {0} из {1}. Повторите пинг через 30–60 секунд.").format(sent, len(targets)))

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
            self.tabs.addTab(self._build_pc_tab(), "💻 ПО")
            self.tabs.addTab(self._build_sec_tab(), "🛡️ Обновления безопасности")
            self.tabs.addTab(self._build_hotfix_tab(), "🩹 Обновления Windows")
        self.tabs.addTab(self._build_fleet_tab(), "🔎 У кого установлено")
        close = QPushButton(tr("Закрыть"))
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
        self.filter.setPlaceholderText(tr("Фильтр по названию/издателю…"))
        self.filter.textChanged.connect(self._apply_filter)
        top.addWidget(self.filter, 1)
        self.btn_poll = QPushButton(tr("🔄 Опросить ПК"))
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
        lay.addWidget(QLabel(tr("Все обновления за последнее время (Win32_QuickFixEngineering). Заполняется при опросе ПК.")))
        return w

    def _build_sec_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.sec = _table(["KB", "Описание", "Установлено"])
        lay.addWidget(self.sec, 1)
        lay.addWidget(QLabel(tr("Только обновления безопасности (по описанию KB). Заполняется при опросе ПК.")))
        return w

    def _build_fleet_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        self.q = QLineEdit()
        self.q.setPlaceholderText(tr("Название программы (часть), например: 1С, Chrome, KES…"))
        self.q.returnPressed.connect(self.search_fleet)
        top.addWidget(self.q, 1)
        b = QPushButton(tr("Найти"))
        b.setObjectName("btnPrimary")
        b.clicked.connect(self.search_fleet)
        top.addWidget(b)
        # 3.5.10: опрос всего парка отсюда — без него вкладка у организации без сохранённых данных была пустой
        self.btn_fleet_poll = QPushButton(tr("📡 Опросить парк"))
        self.btn_fleet_poll.setToolTip(tr("Опросить все ПК в сети (WinRM → WMI → удалённый реестр) и сохранить их ПО в базу"))
        self.btn_fleet_poll.clicked.connect(self.poll_fleet)
        top.addWidget(self.btn_fleet_poll)
        # 3.8.0: «Область» — опрос только ПК выбранной организации, с колонкой «Пользователь»
        self.btn_fleet_org = QPushButton(tr("🏢 Область"))
        self.btn_fleet_org.setToolTip(tr("Опросить только ПК выбранной организации (как в Excel-описи)\\n"
                                      "и показать, какой пользователь какое ПО использует"))
        self.btn_fleet_org.clicked.connect(self.poll_org)
        top.addWidget(self.btn_fleet_org)
        self._org_users: dict[str, str] | None = None
        self._org_rows: list[tuple[str, str, str, str]] | None = None
        self._org_name = ""
        self.btn_fleet_stop = QPushButton(tr("⏹ Стоп"))
        self.btn_fleet_stop.setEnabled(False)
        self.btn_fleet_stop.clicked.connect(lambda: (self.fleet_worker.cancel() if self.fleet_worker else None,
                                                     self.btn_fleet_stop.setEnabled(False)))
        top.addWidget(self.btn_fleet_stop)
        self.fleet_worker = None
        lay.addLayout(top)
        self.fleet = _table(["ПК", "Программа", "Версия", "Опрошен"])
        self.fleet.itemDoubleClicked.connect(lambda it: self.app.search_text(self.fleet.item(it.row(), 0).text()) if hasattr(self.app, "search_text") else None)
        lay.addWidget(self.fleet, 1)
        self.fleet_lbl = QLabel(tr("Поиск идёт по сохранённым данным опрошенных ПК. Двойной клик — найти ПК в главном окне."))
        lay.addWidget(self.fleet_lbl)
        self._fill_summary()
        return w

    def _fill_summary(self):
        top = software.software_summary()
        if not top:
            self.fleet_lbl.setText(tr("Сохранённых данных о ПО пока нет — нажмите «Опросить парк» или опросите ПК из его карточки."))
            return
        self.fleet_lbl.setText(tr("Топ программ по числу ПК (по сохранённым данным). Введите название для точного поиска."))
        _fill(self.fleet, [(f"{n} ПК", name) for name, n in top[:100]])

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
        self.lbl.setText(tr("⏳ Опрашиваю ПК (WinRM → WMI → удалённый реестр), обычно 10–60 с…"))

        def done(d: dict):
            self.btn_poll.setEnabled(True)
            if "error" in d:
                self.lbl.setText(f"⚠️ {d['error']}")
                return
            self._items = d["software"]
            self._apply_filter()
            hot = d.get("hotfixes") or []
            _fill(self.hot, [(h["id"], h["desc"], h["installed"]) for h in hot])
            _fill(self.sec, [(h["id"], h["desc"], h["installed"]) for h in hot if h.get("kind") == "security"])
            how = {"WinRM": "по WinRM", "WMI": "по WMI/DCOM", "RemoteRegistry": "через удалённый реестр"}.get(d.get("how", ""), "")
            self.lbl.setText(f"Опрошено {how}: программ — {len(d['software'])}, обновлений — {len(hot)}".replace("  ", " "))
            db.log_action(self.app.admin_name, "software", self.comp, f"{len(d['software'])} программ")

        run_in_background(self, lambda: software.get_software(self.comp), done,
                          lambda m: (self.btn_poll.setEnabled(True), self.lbl.setText(tr("⚠️ {0}").format(m))))

    def poll_fleet(self):
        from . import fleetpoll
        self._org_users, self._org_rows, self._org_name = None, None, ""
        self.fleet.setHorizontalHeaderLabels(["ПК", "Программа", "Версия", "Опрошен"])
        try:
            hosts = fleetpoll.fleet_hosts(self.app.get_conn)
        except Exception as exc:  # noqa: BLE001
            self.fleet_lbl.setText(tr("⚠️ Список ПК не получен: {0}").format(exc))
            return
        if not hosts:
            self.fleet_lbl.setText(tr("⚠️ ПК для опроса не найдены: инвентарь пуст и AD не вернул рабочих станций"))
            return
        self._start_fleet_poll(hosts)

    def poll_org(self):
        """«Область»: выбрать организацию и опросить только её ПК — в таблице появится, кто какое ПО использует."""
        from . import fleetpoll
        from .widgets import OrgPickerDialog
        d = OrgPickerDialog(self.app.get_conn, self)
        if d.exec() != QDialog.DialogCode.Accepted or not d.choice:
            return
        try:
            hosts, users = fleetpoll.org_computers(self.app.get_conn, d.choice)
        except Exception as exc:  # noqa: BLE001
            self.fleet_lbl.setText(tr("⚠️ Список ПК организации не получен: {0}").format(exc))
            return
        if not hosts:
            self.fleet_lbl.setText(tr("⚠️ В организации «{0}» не найдено ПК (нет привязок, инвентаря и userWorkstations)").format(d.choice))
            return
        self._org_users, self._org_rows, self._org_name = users, None, d.choice
        self.fleet.setHorizontalHeaderLabels(["Пользователь", "ПК", "Программа", "Версия"])
        self.fleet_lbl.setText(tr("🏢 «{0}»: опрашиваю {1} ПК организации…").format(d.choice, len(hosts)))
        self._start_fleet_poll(hosts)

    def _start_fleet_poll(self, hosts: list[str]):
        from . import fleetpoll
        self.btn_fleet_poll.setEnabled(False)
        self.btn_fleet_org.setEnabled(False)
        self.btn_fleet_stop.setEnabled(True)
        if not self._org_users:
            self.fleet_lbl.setText(tr("⏳ Опрашиваю {0} ПК…").format(len(hosts)))
        self.fleet_worker = fleetpoll.FleetPollWorker(hosts, fleetpoll.software_live, parent=self)
        self.fleet_worker.progress.connect(lambda i, n, h: self.fleet_lbl.setText(
            (f"🏢 «{self._org_name}»: " if self._org_users else "") + f"⏳ Опрошено {i} из {n} ПК · {h}"))
        self.fleet_worker.finished_poll.connect(self._fleet_done)
        self.fleet_worker.error.connect(lambda m: (self._fleet_done({}), self.fleet_lbl.setText(tr("⚠️ {0}").format(m))))
        self.fleet_worker.start()

    def _fleet_done(self, results: dict):
        from . import fleetpoll
        self.btn_fleet_poll.setEnabled(True)
        self.btn_fleet_org.setEnabled(True)
        self.btn_fleet_stop.setEnabled(False)
        sm = fleetpoll.summarize(results)
        if self._org_users:
            # «Область»: кто какое ПО использует — строки (пользователь, ПК, программа, версия) из живого опроса
            rows = []
            for comp, r in sorted(results.items()):
                for s in r.get("software") or []:
                    rows.append((self._org_users.get(comp, "—"), comp, s["name"], s.get("version") or ""))
            self._org_rows = rows
            _fill(self.fleet, rows)
            self.fleet_lbl.setText(f"🏢 «{self._org_name}»: ответили {sm['ok']} ПК, не в сети {sm['skipped']}, "
                                   f"не удалось {sm['failed']}. ПО — {len(rows)} записей; введите название для фильтра.")
            db.log_action(getattr(self.app, "admin_name", ""), "software_fleet", self._org_name, f"{sm['ok']} ПК (область)")
            return
        self._fill_summary()
        if results:
            self.fleet_lbl.setText(f"Опрос парка: ответили {sm['ok']} ПК, не в сети {sm['skipped']}, не удалось {sm['failed']}. "
                                   "ПО сохранено — введите название для поиска.")
            db.log_action(getattr(self.app, "admin_name", ""), "software_fleet", "fleet", f"{sm['ok']} ПК")

    def on_dialog_done(self):
        if self.fleet_worker:
            self.fleet_worker.cancel()

    def search_fleet(self):
        if self._org_rows is not None:
            # «Область»: фильтр по живому опросу организации, без базы
            q = self.q.text().strip().casefold()
            rows = [r for r in self._org_rows if not q or q in r[2].casefold() or q in r[0].casefold()]
            _fill(self.fleet, rows)
            self.fleet_lbl.setText(f"🏢 «{self._org_name}»: показано {len(rows)} из {len(self._org_rows)} записей ПО"
                                   + (f" по фильтру «{self.q.text().strip()}»" if q else " (фильтр пуст — всё)"))
            return
        rows = software.find_software(self.q.text())
        _fill(self.fleet, [(r["comp"], r["name"]) for r in rows])
        comps = len({r["comp"] for r in rows})
        self.fleet_lbl.setText(f"Найдено: {len(rows)} записей · {comps} ПК" if rows else "Ничего не найдено в сохранённых данных (опросите ПК или уточните запрос)")


# ============================================================================ входы за сутки
class LogonsDialog(FramelessDialog):
    """События 4624/4625 за N часов — кто входил на ПК и кто ломился с неверным паролем."""

    def __init__(self, comp: str, app, parent=None):
        super().__init__(f"🔐 Входы на ПК за 24 ч: {comp}", parent, (860, 560))
        self.comp, self.app = comp, app
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Период:")))
        self.hours = QComboBox()
        for h in (1, 8, 24, 72, 168):
            self.hours.addItem(f"{h} ч" if h < 48 else f"{h // 24} дн.", h)
        self.hours.setCurrentIndex(2)
        top.addWidget(self.hours)
        self.only_fail = QCheckBox(tr("Только отказы"))
        self.only_fail.toggled.connect(self._render)
        top.addWidget(self.only_fail)
        self.show_net = QCheckBox(tr("Сетевые входы (тип 3)"))
        self.show_net.setToolTip(tr("Тип 3 — это не вход за этим ПК, а обращение к нему по сети с другого компьютера "
                                 "(общие папки, службы). По умолчанию скрыты и не читаются с ПК — включите, чтобы увидеть."))
        self.show_net.toggled.connect(self._net_toggled)
        top.addWidget(self.show_net)
        top.addStretch()
        b = QPushButton(tr("🔄 Обновить"))
        b.setObjectName("btnPrimary")
        b.clicked.connect(self.load)
        top.addWidget(b)
        self.body.addLayout(top)
        # 3.9.0: расшифровка типов входов — вопрос «что значит вход по кэшу/пустые» больше не возникает
        legend = QLabel(tr("<b>Типы входов:</b> Консоль — вошёл за этим ПК · Разблокировка — снял блокировку · "
                        "RDP — удалённый рабочий стол · Вход по кэшу — пустило без сети, по сохранённым данным · "
                        "«—» в IP — журнал не записал, откуда"))
        legend.setWordWrap(True)
        legend.setStyleSheet("color: %s; font-size: 8.5pt;" % app_palette().subtext)
        self.body.addWidget(legend)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        self.body.addWidget(self.summary)
        self.table = _table(["Время", "Пользователь", "Тип", "Откуда (IP)", "Результат"])
        fit_columns(self.table, max_width=360)
        self.body.addWidget(self.table, 1)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.accept)
        self.body.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._data: dict = {"events": [], "by_user": [], "ok": 0, "fails": 0}
        self.load()

    def load(self):
        include = self.show_net.isChecked()
        self._loaded_net = include
        hint = "" if include else " (сетевые входы отсеиваются сразу — читается быстрее)"
        self.summary.setText(tr("⏳ Читаю журнал Security на ПК (Get-WinEvent, до 1–2 минут на большом журнале)…") + hint)
        hours = int(self.hours.currentData())
        run_in_background(self, lambda: logons.get_logons(self.comp, hours, include_net=include),
                          self._done, lambda m: self.summary.setText(tr("⚠️ {0}").format(m)))

    def _done(self, d: dict):
        self._data = d
        self.summary.setText(logons.format_summary(d))
        self._render()

    def _net_toggled(self, on: bool):
        """Включили «Сетевые входы» — их надо дочитать с ПК (при загрузке они отсеивались для скорости)."""
        if on and not getattr(self, "_loaded_net", True):
            self.load()
        else:
            self._render()

    def _render(self):
        pal = app_palette()
        evs = self._data.get("events", [])
        if self.only_fail.isChecked():
            evs = [e for e in evs if e["kind"] == "fail"]
        if not self.show_net.isChecked():
            evs = [e for e in evs if e.get("type") != "Сеть"]   # 3.6.3: сетевые обращения с чужих ПК — не «входы за ПК»
        self.table.setRowCount(0)
        for e in evs:
            r = self.table.rowCount()
            self.table.insertRow(r)
            user = f"{e['domain']}\\{e['user']}" if e["domain"] else e["user"]
            res = "✅ вход" if e["kind"] == "ok" else f"⛔ {e['reason'] or 'отказ'}"
            # 3.9.0: пустых ячеек не оставляем — «—» читается как «нет данных», а не как сломанная строка
            vals = (e["ts"] or "—", user or "—", e["type"] or "—", e["ip"] or "—", res)
            for c, v in enumerate(vals):
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
    titles = {"os": "ОС", "system": "Система", "board": "Плата", "bios": "BIOS", "cpu": "CPU", "rams": "ОЗУ", "disks": "Диск",
              "logdisks": "Том", "gpu": "Видео", "adapters": "Сеть", "printers": "Принтер"}
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
        self.only_diff = QCheckBox(tr("Только различия"))
        self.only_diff.setChecked(True)
        self.only_diff.toggled.connect(self.render)
        top.addWidget(self.only_diff)
        top.addStretch()
        self.lbl = QLabel(tr("⏳ Загрузка…"))
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
        self.lbl_soft = QLabel(tr("⏳ Опрашиваю оба ПК…"))
        self.lbl_soft.setObjectName("subtle")
        self.lbl_soft.setWordWrap(True)
        sl.addWidget(self.lbl_soft)
        sl.addWidget(self.t_soft, 1)
        self.tabs.addTab(soft_page, "📦 Установленное ПО")
        self.tabs.setTabToolTip(1, "Программы снимаются с ПК прямо сейчас; если ПК недоступен — берётся последний "
                                   "сохранённый список, и в шапке написано, от какой он даты")
        self.tabs.addTab(self.t_prn, "🖨️ Принтеры")
        self.body.addWidget(self.tabs, 1)
        close = QPushButton(tr("Закрыть"))
        close.clicked.connect(self.accept)
        self.body.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        self._data = None
        run_in_background(self, self._load, self._loaded, lambda m: self.lbl.setText(tr("⚠️ {0}").format(m)))

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
        self.lbl_soft.setText(tr("Список программ снят с ПК прямо сейчас; если ПК недоступен — последний сохранённый.   ")
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
