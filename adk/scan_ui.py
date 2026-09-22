"""Стартовое окно сканирования и окно прогресса полного опроса парка (3.8.0).

До 3.8.0 ADK сам запускал сканирование парка сразу после входа — без спроса. Теперь при старте
появляется короткий вопрос: «всё (ПК + принтеры + ПО)» или «только ПК», и сканирование начинается
только после выбора.

* **Только ПК** — прежний сканер (F5): адреса, кто за каким ПК, последние входы.
* **Всё** — три шага: ПК из домена → принтеры с ПК в сети → программы с ПК в сети. Окно прогресса
  показывает **круговую диаграмму** с процентом и подписанные пункты шагов; процент — доля реально
  выполненных проверок (одна проверка = один ПК), а не «время до конца».

Принцип честного процента: диаграмма считает выполненные *проверки* (одна ячейка = один опрошенный ПК).
Объём шагов известен сразу: ПК — из AD, «в сети» для принтеров и программ — оценка по последнему скану
(«✓ i/n» шага уточняется, когда шаг начался и список онлайн-ПК стал точным).
"""
from __future__ import annotations

import logging
from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import config, db
from .i18n import tr  # noqa: E402
from .widgets import FramelessDialog, app_palette
from .workers import BaseWorker, PCScannerWorker, _Emitter

log = logging.getLogger(__name__)


# ============================================================================ круговая диаграмма
class DonutWidget(QWidget):
    """Круговой индикатор прогресса: тонкое серое кольцо-фон, акцентная дуга, в центре — процент.

    ``set_progress(done, total)`` — выполнено ``done`` проверок из ``total``; ``total=0`` — «не начато»
    (пустое кольцо без цифры).
    ``set_segments(segments)`` (3.11.0) — диаграмма-«пончик» по шагам сканирования: каждый шаг —
    сектор, чья доля кольца пропорциональна его объёму, заполненная часть — реальному прогрессу.
    ``segments`` — список ``(total, done, цвет)``; суммарный процент — в центре.
    """

    def __init__(self, diameter: int = 150, parent=None):
        super().__init__(parent)
        self._done, self._total = 0, 0
        self._segments: list[tuple[int, int, str]] = []
        self.setFixedSize(diameter, diameter)

    def set_progress(self, done: int, total: int):
        self._done, self._total = max(0, done), max(0, total)
        self._segments = []
        self.update()

    def set_segments(self, segments: list[tuple[int, int, str]]):
        """``[(total, done, hex-цвет), …]`` — доли шагов; общий процент считается из реальных чисел."""
        self._segments = [(max(0, t), max(0, d), c) for t, d, c in segments]
        self._total = sum(t for t, _d, _c in self._segments)
        self._done = sum(min(d, t) for t, d, _c in self._segments)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        pal = app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = max(10.0, self.width() * 0.085)          # толщина кольца ~8,5% диаметра
        r = QRectF(w / 2 + 2, w / 2 + 2, self.width() - w - 4, self.height() - w - 4)
        pen = QPen(QColor(pal.input), w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        if not self._segments:
            p.drawArc(r, 0, 360 * 16)                # кольцо-фон (одношаговый режим)
        else:
            # сектора шагов: доля кольца ∝ объём шага, заполнение ∝ прогрессу шага; между секторами зазор
            gap = 3 * 16
            spans: list[int] = []
            for t, _d, _c in self._segments:
                spans.append(int(360 * 16 * t / self._total) if self._total else 0)
            for i, (t, d, color) in enumerate(self._segments):
                span = spans[i] - (gap if len(self._segments) > 1 else 0)
                if span <= 0:
                    continue
                start = 90 * 16 - sum(spans[:i + 1])          # от 12 часов, по часовой
                pen.setColor(QColor(pal.input))
                p.setPen(pen)
                p.drawArc(r, start, -span)                    # фон сектора
                if t > 0 and d > 0:
                    pen.setColor(QColor(color))
                    p.setPen(pen)
                    p.drawArc(r, start, -int(span * min(1.0, d / t)))
        if self._total > 0:
            pct = min(1.0, self._done / self._total)
            if not self._segments:
                pen.setColor(QColor(pal.title_accent))
                p.setPen(pen)
                p.drawArc(r, 90 * 16, int(-360 * 16 * pct))   # от 12 часов, по часовой
            big = QFont(self.font())
            big.setPointSizeF(max(14.0, self.width() * 0.16))
            big.setBold(True)
            p.setPen(QColor(pal.text))
            p.setFont(big)
            p.drawText(self.rect().adjusted(0, -self.height() // 8, 0, -self.height() // 8),
                       Qt.AlignmentFlag.AlignCenter, f"{round(pct * 100)}%")
            small = QFont(self.font())
            small.setPointSizeF(max(7.5, self.width() * 0.075))
            p.setFont(small)
            p.setPen(QColor(pal.subtext))
            p.drawText(self.rect().adjusted(0, self.height() // 5, 0, self.height() // 5),
                       Qt.AlignmentFlag.AlignCenter, f"{self._done} из {self._total}")
        p.end()


# ============================================================================ вопрос при старте
class StartupScanDialog(FramelessDialog):
    """«Что сделать при запуске?» — всё / только ПК / не сейчас. Ответ — ``choice``: full · pcs · skip."""

    def __init__(self, parent=None):
        super().__init__("ADK — сканирование парка", parent, (560, 300))
        self.choice = ""
        pal = app_palette()
        q = QLabel(tr("Собрать данные о парке сейчас?"))
        q.setStyleSheet(f"font-size: 13pt; font-weight: bold; color: {pal.text};")
        q.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(q)
        sub = QLabel(tr("База обновляется сканированием — без него поиск видит прошлый снимок."))
        sub.setStyleSheet(f"color: {pal.subtext};")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(sub)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.body.addLayout(row, 1)

        def tile(title: str, desc: str, key: str, primary: bool = False) -> QPushButton:
            b = QPushButton()
            b.setObjectName("btnPrimary" if primary else "")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumHeight(96)
            b.clicked.connect(lambda: (setattr(self, "choice", key), self.accept()))
            lay = QVBoxLayout(b)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(2)
            t = QLabel(title)
            # 3.9.0: на залитой акцентом кнопке текст — контрастным к акценту цветом (on_accent),
            # а не title_accent: яркий акцент по акценту сливался (тёмные темы со светлым акцентом)
            t.setStyleSheet(f"font-size: 11.5pt; font-weight: bold; color: {pal.on_accent if primary else pal.text};"
                            " background: transparent;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            d = QLabel(desc)
            d.setWordWrap(True)
            d.setStyleSheet(f"color: {pal.on_accent if primary else pal.subtext}; font-size: 8.5pt; background: transparent;")
            d.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(t)
            lay.addWidget(d)
            return b

        row.addWidget(tile("🌐 Всё:\nПК + принтеры + ПО", "три шага, дольше всего\nи самая полная база", "full", True))
        row.addWidget(tile("💻 Только ПК", "адреса, кто за каким ПК,\nпоследние входы — быстро", "pcs"))
        row.addWidget(tile("⏭ Не сейчас", "F5 — запустить\nв любой момент", "skip"))
        hint = QLabel(tr("«Всё» опрашивает и выключенные ПК пропускает — окно прогресса можно свернуть, работа продолжится."))
        hint.setStyleSheet(f"color: {pal.subtext}; font-size: 8.5pt;")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.addWidget(hint)


# ============================================================================ рабочий поток полного опроса
def _phase_colors() -> dict[str, str]:
    """Цвет сектора каждого шага на диаграмме (3.11.0) — из семантики темы, различимы на всех 10 темах."""
    pal = app_palette()
    return {"pcs": pal.info[0], "printers": pal.success[0],
            "software": pal.warning[0], "specs": pal.title_accent}


def _phase_labels() -> dict[str, tuple[str, str]]:
    """Ключ шага → (заголовок, подпись) для окна прогресса."""
    return {
        "pcs": ("💻 ПК из домена", "адреса, кто за каким ПК, последние входы"),
        "printers": ("🖨️ Принтеры", "что подключено на каждом ПК в сети"),
        "software": ("📦 Программы", "что установлено на каждом ПК в сети"),
        "specs": ("🖥 Характеристики", "процессор, память, диски, ОС — для карточки и Excel-описи"),
    }


def _online_estimate(total_pcs: int) -> int:
    """Оценка «сколько ПК в сети» по прошлому скану — для честного общего процента с самого начала."""
    try:
        rows = db.db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory WHERE is_online", fetch="all")
        n = int(rows[0][0]) if rows else 0
    except Exception:  # noqa: BLE001
        n = 0
    return n if 0 < n < total_pcs else total_pcs      # пустая/кривая база → считаем «все», переоценив не рискуем


class FullScanPlan:
    """Юниты шагов для процента: одна проверка = один ПК. Оценки шагов 2–3 уточняются при их старте."""

    def __init__(self):
        self.totals: dict[str, int] = {}
        self.done: dict[str, int] = {}

    def announce(self, phase: str, total: int):
        self.totals[phase] = max(0, total)
        self.done.setdefault(phase, 0)

    def advance(self, phase: str, done: int):
        self.done[phase] = max(0, min(done, self.totals.get(phase, done)))

    @property
    def total(self) -> int:
        return sum(self.totals.values())

    @property
    def done_total(self) -> int:
        return sum(min(self.done.get(k, 0), self.totals.get(k, 0)) for k in self.totals)


class FullScanWorker(BaseWorker):
    """Полный опрос парка: ПК (порциями по 50 — пул сканера остаётся широким) → принтеры → характеристики.

    3.9.2: шаг «программы» убран из полного опроса — опрос ПО всех онлайн-ПК был самым тяжёлым;
    ПО по-прежнему собирается точечно: опрос одного ПК из карточки, «Область» (одна организация),
    «Опросить парк» из окна ПО.

    Сигналы: ``plan(phase, total)`` — объём шага стал известен; ``unit(phase, done, host)`` — выполнено
    ``done`` проверок шага, сейчас ``host``; ``step_text(text)`` — короткий текст текущего действия;
    ``finished_full(summary)``.
    """

    plan = pyqtSignal(str, int)
    unit = pyqtSignal(str, int, str)
    step_text = pyqtSignal(str)
    finished_full = pyqtSignal(dict)

    CHUNK = 50   # размер порции шага «ПК»: равен ширине пула PowerShell-сканера — скорость та же, прогресс виден

    def __init__(self, conn_factory, mode: str = "full", parent=None):
        super().__init__(parent)
        self.conn_factory = conn_factory
        self.mode = mode                       # "full" — три шага; "pcs" — только первый

    def run(self) -> None:
        from . import fleetpoll
        s = {"pcs": 0, "online": 0, "printers_pcs": 0, "printers": 0,
             "specs_pcs": 0, "error": "", "stopped": False, "mode": self.mode}
        try:
            # ---- шаг 1: ПК из домена (LDAP, при пустом результате — ADSI, как в старой программе)
            from .workers import host_list_from_ad
            hosts, via = host_list_from_ad(self.conn_factory, lambda m: self.step_text.emit(m))
            if not hosts:
                what = (f"маска парка «{config.settings.host_mask}» ([Scanner] host_mask)"
                        if config.settings.host_mask.strip()
                        else f"host_pattern «{config.settings.host_pattern}»")
                s["error"] = (f"ни один ПК домена не подошёл под {what}; список ПК брался через {via.upper()}")
                self.step_text.emit(f"⚠️ {s['error']}")
                self.finished_full.emit(s)
                return
            est = _online_estimate(len(hosts))
            self.plan.emit("pcs", len(hosts))
            if self.mode == "full":
                self.plan.emit("printers", est)
            scanner = PCScannerWorker.__new__(PCScannerWorker)
            scanner.conn_factory, scanner._cancelled = self.conn_factory, False
            scanner.deep = True   # полный опрос: WMI «кто за ПК» включён (фоновый скан — без него)
            scanner.progress = _Emitter(self.step_text.emit)
            results = []
            for k in range(0, len(hosts), self.CHUNK):
                if self.cancelled:
                    break
                results += scanner.probe_hosts(hosts[k:k + self.CHUNK])
                done = min(k + self.CHUNK, len(hosts))
                self.unit.emit("pcs", done, f"опрошено {done} из {len(hosts)}")
            from .workers import enrich_with_dc_logons
            results = enrich_with_dc_logons(results, lambda m: self.step_text.emit(m))
            if self.cancelled:
                s["stopped"] = True
            if results:
                self.step_text.emit("Сохранение инвентаря…")
                db.batch_update_inventory(results, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                s["pcs"] = len(hosts)
                scanner._index_printers(hosts)          # индекс принтеров из CSV — как у обычного сканера
            if self.mode != "full" or (self.cancelled and not results):
                self.finished_full.emit(s)
                return
            # ---- шаги 2–3: живой опрос ПК, которые в сети после шага 1
            hosts_online = fleetpoll.fleet_hosts(self.conn_factory, online_only=True)
            s["online"] = len(hosts_online)
            self.plan.emit("printers", len(hosts_online))
            if self.cancelled:
                s["stopped"] = True
                self.finished_full.emit(s)
                return
            if hosts_online:
                self.step_text.emit(tr("Принтеры: опрашиваю {0} ПК в сети…").format(len(hosts_online)))
                res = fleetpoll.poll_fleet(
                    hosts_online, fleetpoll.printers_live,
                    progress=lambda i, n, h: self.unit.emit("printers", i, h),
                    cancelled=lambda: self.cancelled)
                for comp, r in res.items():
                    if "printers" in r:
                        db.replace_printers(comp, r["printers"])
                        s["printers_pcs"] += 1
                        s["printers"] += len(r["printers"])
                if self.cancelled:
                    s["stopped"] = True
                # ---- шаг 3 (3.9.0): характеристики — процессор/память/диски/ОС — в базу.
                # Только для ПК без свежих данных: повторные полные опросы не пересобирают всё заново.
                if not self.cancelled:
                    try:
                        fresh = set(db.hosts_with_fresh_specs())
                    except Exception:  # noqa: BLE001
                        fresh = set()
                    todo = [h for h in hosts_online if h not in fresh]
                    self.plan.emit("specs", len(todo))
                    if todo:
                        self.step_text.emit(tr("Характеристики: опрашиваю {0} ПК (остальные свежие)…").format(len(todo)))
                        res = fleetpoll.poll_fleet(
                            todo, fleetpoll.specs_live,
                            progress=lambda i, n, h: self.unit.emit("specs", i, h),
                            cancelled=lambda: self.cancelled)
                        for comp, r in res.items():
                            if "specs" in r:
                                db.save_specs(comp, r["specs"])
                                s["specs_pcs"] += 1
                    else:
                        self.step_text.emit("Характеристики: у всех ПК в сети данные уже свежие")
                        self.unit.emit("specs", 0, "")
        except Exception as exc:  # noqa: BLE001
            log.exception("FullScanWorker")
            s["error"] = str(exc)
        self.finished_full.emit(s)


def full_summary_text(s: dict) -> str:
    """Итог полного опроса для строки состояния."""
    if s.get("error"):
        return f"⚠️ Сканирование прервано: {s['error']}"
    parts = [f"ПК: {s.get('pcs', 0)}"]
    if s.get("mode") == "full":
        parts.append(f"принтеры: {s.get('printers', 0)} на {s.get('printers_pcs', 0)} ПК")
        if s.get("specs_pcs"):
            parts.append(f"характеристики: {s.get('specs_pcs')} ПК")
    if s.get("stopped"):
        parts.append("остановлено — начатое доработало")
    return "✅ " + " · ".join(parts) + "."


# ============================================================================ окно прогресса
class FullScanDialog(FramelessDialog):
    """Прогресс полного опроса: круговая диаграмма с процентом, подписанные шаги, текущий ПК, «Свернуть»/«Остановить».

    ``on_finished(summary)`` вызывается по завершении потока (даже если окно свёрнуто или закрыто).
    """

    def __init__(self, conn_factory, mode: str = "full", on_finished=None, parent=None):
        super().__init__("ADK — сканирование парка", parent, (620, 460))
        self.on_finished = on_finished
        pal = app_palette()
        self.worker = FullScanWorker(conn_factory, mode, parent=self)
        top = QHBoxLayout()
        top.setSpacing(18)
        self.body.addLayout(top)
        self.donut = DonutWidget(150)
        top.addWidget(self.donut, 0, Qt.AlignmentFlag.AlignVCenter)
        rows = QVBoxLayout()
        rows.setSpacing(8)
        top.addLayout(rows, 1)
        self._rows: dict[str, tuple[QLabel, QLabel]] = {}
        for key, (title, desc) in _phase_labels().items():
            if mode != "full" and key != "pcs":
                continue
            head = QLabel(f"{title} — {desc}")
            head.setStyleSheet(f"color: {pal.subtext}; font-size: 9.5pt; background: transparent;")
            val = QLabel(tr("ожидает своей очереди"))
            val.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt; background: transparent;")
            self._rows[key] = (head, val)
            rows.addWidget(head)
            rows.addWidget(val)
            rows.addSpacing(2)
        rows.addStretch()
        self.lbl_step = QLabel(tr("Подготовка…"))
        self.lbl_step.setWordWrap(True)
        self.lbl_step.setStyleSheet(f"color: {pal.text}; font-size: 10.5pt; font-weight: bold;")
        self.body.addWidget(self.lbl_step)
        self.lbl_host = QLabel("")
        self.lbl_host.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        self.body.addWidget(self.lbl_host)

        btns = QHBoxLayout()
        btns.addStretch()
        self.btn_hide = QPushButton(tr("⏬ Свернуть — работа продолжится"))
        self.btn_hide.clicked.connect(self.hide)
        btns.addWidget(self.btn_hide)
        self.btn_stop = QPushButton(tr("⏹ Остановить"))
        self.btn_stop.clicked.connect(self.stop)
        btns.addWidget(self.btn_stop)
        self.btn_close = QPushButton(tr("Закрыть"))
        self.btn_close.setObjectName("btnSuccess")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_close)
        self.body.addLayout(btns)

        self._planned: dict[str, int] = {}
        self._done: dict[str, int] = {}
        self._finished_state: dict = {}
        self.worker.plan.connect(self._on_plan)
        self.worker.unit.connect(self._on_unit)
        self.worker.step_text.connect(self.lbl_step.setText)
        self.worker.finished_full.connect(self._on_done)
        self.worker.error.connect(lambda m: self.lbl_step.setText(tr("⚠️ {0}").format(m)))
        self.worker.start()

    # ---- прогресс
    def _refresh_donut(self):
        colors = _phase_colors()
        self.donut.set_segments([(self._planned.get(k, 0), self._done.get(k, 0), colors.get(k, "#0A84FF"))
                                 for k in self._rows])

    def _on_plan(self, phase: str, total: int):
        self._planned[phase] = total
        self._done.setdefault(phase, 0)
        head, _val = self._rows.get(phase, (None, None))
        pal = app_palette()
        if head is not None:
            head.setStyleSheet(f"color: {pal.text}; font-size: 9.5pt; font-weight: bold; background: transparent;")
        self._refresh_donut()

    def _on_unit(self, phase: str, done: int, host: str):
        self._done[phase] = min(done, self._planned.get(phase, done))
        head, val = self._rows.get(phase, (None, None))
        if val is not None:
            total = self._planned.get(phase, 0)
            pct = f"{round(100 * done / total)}%" if total else "0%"
            val.setText(f"✓ {done}/{total} ({pct})" + (f" · {host}" if host else ""))
            if done >= total > 0:
                pal = app_palette()
                val.setText(tr("✅ готово: {0} из {1} (100%)").format(done, total))
                val.setStyleSheet(f"color: {pal.title_accent}; font-size: 9pt; background: transparent;")
        self.lbl_host.setText(host if host and "/" not in host else "")
        self._refresh_donut()

    # ---- завершение
    def stop(self):
        if self.worker.isRunning():
            self.worker.cancel()
            self.lbl_step.setText(tr("Останавливаю — начатые опросы дорабатывают…"))
            self.btn_stop.setEnabled(False)

    def _on_done(self, summary: dict):
        self._finished_state = summary
        self.btn_stop.setEnabled(False)
        self.btn_hide.setEnabled(False)
        self.btn_close.setEnabled(True)
        for key in self._planned:
            head, val = self._rows.get(key, (None, None))
            if val is not None and not val.text().startswith("✅"):
                val.setText(val.text() + tr(" — остановлено") if summary.get("stopped") else val.text())
        text = full_summary_text(summary)
        self.lbl_step.setText(text)
        self.lbl_host.setText("")
        if self.on_finished:
            self.on_finished(summary)

    def closeEvent(self, event):  # noqa: N802
        # «Закрыть» после завершения — просто закрыть; крестик во время работы — свернуть (работа продолжится)
        if self.worker.isRunning():
            event.ignore()
            self.hide()
            return
        super().closeEvent(event)

    def on_dialog_done(self):
        if self.worker.isRunning():
            self.worker.cancel()


def ask_startup_scan(window) -> str:
    """Показать вопрос при старте. Возвращает choice: full · pcs · skip (skip — также при закрытии окна)."""
    d = StartupScanDialog(window)
    d.exec()
    return d.choice or "skip"
