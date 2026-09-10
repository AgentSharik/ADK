"""Окно «Пинг» — собственная реализация ADK 3.2.

Не таблица строк консоли, а монитор доступности: крупный индикатор состояния, график отклика
(последние 60 замеров), статистика (отправлено / получено / потери / мин / сред / макс / джиттер),
единая лента журнала (каждый ответ ping структурированно: №, время, результат, отклик, TTL; события
выделены) с фильтром «Все / Сбои и события / События» и управление: пауза, сброс, копировать отчёт / вывод. Источник данных прежний — :class:`adk.workers.PingWorker` (системный ``ping``).
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget, QPushButton,
    QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import db
from .widgets import FramelessDialog, app_palette, make_badge
from .workers import PingWorker

log = logging.getLogger(__name__)

HISTORY = 60


class LatencyGraph(QWidget):
    """Спарклайн отклика: столбики по замерам, таймауты — красные штрихи, пунктир — среднее."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.samples: list[float | None] = []   # мс; None — таймаут
        self.waiting = False                    # ждём ответ дольше секунды: ping.exe держит таймаут до 4 с — график «замирает» не просто так
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def push(self, ms: float | None):
        self.samples.append(ms)
        if len(self.samples) > HISTORY:
            self.samples = self.samples[-HISTORY:]
        self.update()

    def clear(self):
        self.samples.clear()
        self.waiting = False
        self.update()

    def set_waiting(self, on: bool):
        if on != self.waiting:
            self.waiting = on
            self.update()

    def paintEvent(self, _e):  # noqa: N802
        pal = app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        p.fillRect(r, QColor(pal.input))
        p.setPen(QPen(QColor(pal.border), 1))
        p.drawRect(r)
        area = QRectF(r.left() + 44, r.top() + 10, r.width() - 54, r.height() - 28)
        ok = [s for s in self.samples if s is not None]
        top = max(10.0, (max(ok) if ok else 10.0) * 1.25)
        # сетка + подписи
        p.setPen(QPen(QColor(pal.border), 1, Qt.PenStyle.DotLine))
        font = QFont(self.font())
        font.setPointSizeF(9.0)
        p.setFont(font)
        for k in (0.0, 0.5, 1.0):
            y = area.bottom() - area.height() * k
            p.setPen(QPen(QColor(pal.border), 1, Qt.PenStyle.DotLine))
            p.drawLine(int(area.left()), int(y), int(area.right()), int(y))
            p.setPen(QColor(pal.subtext))
            p.drawText(QRectF(r.left() + 2, y - 8, 38, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{top * k:.0f}")
        p.setPen(QColor(pal.subtext))
        p.drawText(QRectF(area.left(), area.bottom() + 4, area.width(), 14), Qt.AlignmentFlag.AlignLeft, f"последние {HISTORY} замеров, мс")
        if not self.samples:
            p.setPen(QColor(pal.subtext))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "ожидание первого ответа…")
            p.end()
            return
        n = HISTORY
        step = area.width() / n
        bw = max(2.0, step - 2)
        start = n - len(self.samples)
        good, bad = QColor(pal.solid("success")), QColor(pal.solid("danger"))
        path = QPainterPath()
        first = True
        for i, s in enumerate(self.samples):
            x = area.left() + (start + i) * step
            if s is None:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bad)
                p.drawRoundedRect(QRectF(x, area.top(), bw, area.height()), 2, 2)
                first = True
                continue
            h = area.height() * min(1.0, s / top)
            p.setPen(Qt.PenStyle.NoPen)
            c = QColor(good)
            c.setAlpha(210)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x, area.bottom() - h, bw, h), 2, 2)
            pt = (x + bw / 2, area.bottom() - h)
            if first:
                path.moveTo(*pt)
                first = False
            else:
                path.lineTo(*pt)
        p.setPen(QPen(QColor(pal.title_accent), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        if self.waiting and len(self.samples) < n:
            x = area.left() + (start + len(self.samples)) * step
            wc = QColor(pal.solid("warning"))
            wc.setAlpha(110)
            p.setPen(QPen(wc, 1, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(x, area.top(), bw, area.height()), 2, 2)
            # подпись — слева сверху на плашке, чтобы не ложиться на столбики (они всегда прижаты к правому краю)
            label = "⏳ ждём ответ (таймаут до 4 с)…"
            tw = p.fontMetrics().horizontalAdvance(label) + 12
            plate = QRectF(area.left() + 2, area.top() - 2, tw, 16)
            bgc = QColor(pal.input)
            bgc.setAlpha(230)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bgc)
            p.drawRoundedRect(plate, 4, 4)
            p.setPen(QColor(pal.solid("warning")))
            p.drawText(plate, Qt.AlignmentFlag.AlignCenter, label)
        if ok:
            avg = statistics.fmean(ok)
            y = area.bottom() - area.height() * min(1.0, avg / top)
            p.setPen(QPen(QColor(pal.solid("warning")), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(area.left()), int(y), int(area.right()), int(y))
        p.end()


class PingDialog(FramelessDialog):
    """Монитор доступности узла. Совместим по сигнатуре со старым окном: (computer_name, ip, app, parent)."""

    def __init__(self, computer_name: str, ip: str, app=None, parent=None):
        target = ip if ip and ip != "Не найден" else computer_name
        super().__init__(f"📡 Пинг: {computer_name}", parent, (900, 800), large_font=True)
        self.computer_name, self.target, self.app = computer_name, target, app
        self.sent = self.recv = 0
        self.times: list[float] = []
        self._last_state: bool | None = None
        self._paused = False
        self._started = datetime.now()
        pal = app_palette()

        # --- шапка: состояние + цель
        head = QFrame()
        head.setObjectName("dashCard")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 12, 16, 12)
        self.dot = QLabel("●")
        self.dot.setStyleSheet(f"font-size: 30pt; color: {pal.subtext};")
        hl.addWidget(self.dot)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        self.lbl_state = QLabel("Запуск…")
        self.lbl_state.setStyleSheet("font-size: 15pt; font-weight: bold;")
        self.lbl_target = QLabel(f"{computer_name} · {target}" if target != computer_name else computer_name)
        self.lbl_target.setStyleSheet(f"color: {pal.subtext};")
        self.lbl_target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        tl.addWidget(self.lbl_state)
        tl.addWidget(self.lbl_target)
        hl.addLayout(tl, 1)
        self.lbl_now = QLabel("—")
        self.lbl_now.setStyleSheet(f"font-size: 24pt; font-weight: bold; color: {pal.title_accent};")
        self.lbl_now.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self.lbl_now)
        unit = QLabel("мс\nсейчас")
        unit.setStyleSheet(f"color: {pal.subtext}; font-size: 10pt;")
        hl.addWidget(unit)
        self.body.addWidget(head)

        # --- график
        self.graph = LatencyGraph()
        self.graph.setMaximumHeight(150)
        self.body.addWidget(self.graph)

        # --- статистика
        stats = QFrame()
        stats.setObjectName("dashCard")
        sg = QGridLayout(stats)
        sg.setContentsMargins(14, 10, 14, 10)
        sg.setHorizontalSpacing(12)
        sg.setVerticalSpacing(4)
        self.stat: dict[str, QLabel] = {}
        for i, (key, title, unit) in enumerate((("sent", "Отправлено", ""), ("recv", "Получено", ""), ("loss", "Потери", ""),
                                                ("min", "Мин", "мс"), ("avg", "Сред", "мс"), ("max", "Макс", "мс"), ("jit", "Джиттер", "мс"))):
            t = QLabel(title.upper() + (f" · {unit}" if unit else ""))
            t.setStyleSheet(f"color: {pal.subtext}; font-size: 9.5pt; font-weight: bold; letter-spacing: 0.5px;")
            t.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v = QLabel("—")
            v.setStyleSheet("font-size: 13pt; font-weight: bold;")
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setMinimumWidth(84)
            sg.addWidget(t, 0, i)
            sg.addWidget(v, 1, i)
            sg.setColumnStretch(i, 1)
            self.stat[key] = v
        self.body.addWidget(stats)

        # --- журнал: одна лента «как в консоли», но структурированная: №, время, результат, отклик, TTL.
        # Слева у каждой строки цветная метка (зелёная — ответ, красная — таймаут, серая — служебная, синяя — событие).
        # Фильтр: Все · Сбои и события · События. Списки self.events / self.raw хранят те же данные текстом для отчёта.
        journal = QFrame()
        journal.setObjectName("dashCard")
        jl = QVBoxLayout(journal)
        jl.setContentsMargins(12, 8, 12, 10)
        jl.setSpacing(6)
        jh = QHBoxLayout()
        jh.addWidget(QLabel("<b>Журнал</b>"))
        self.lbl_journal = QLabel("")
        self.lbl_journal.setStyleSheet(f"color: {pal.subtext}; font-size: 10pt;")
        jh.addWidget(self.lbl_journal)
        jh.addStretch()
        self.filter_btns: dict[str, QPushButton] = {}
        for key, text in (("all", "Все"), ("bad", "Сбои и события"), ("events", "События")):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setObjectName("chipBtn")
            b.clicked.connect(lambda _c, k=key: self._set_filter(k))
            self.filter_btns[key] = b
            jh.addWidget(b)
        # окно всегда открывается на «События»: это режим для наблюдения, консольный текст включают по месту
        self._filter = "events"
        self.filter_btns[self._filter].setChecked(True)
        self.badge_box = QHBoxLayout()
        jh.addSpacing(8)
        jh.addLayout(self.badge_box)
        jl.addLayout(jh)
        self.journal = QTableWidget(0, 6)
        self.journal.setHorizontalHeaderLabels(["", "№", "Время", "Результат", "Отклик", "TTL"])
        self.journal.verticalHeader().setVisible(False)
        self.journal.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.journal.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.journal.setShowGrid(False)
        self.journal.setAlternatingRowColors(True)
        hh = self.journal.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for c, wdt in ((0, 28), (1, 56), (2, 96), (4, 92), (5, 64)):
            self.journal.setColumnWidth(c, wdt)
        self.journal.verticalHeader().setDefaultSectionSize(26)
        self.journal.setMinimumHeight(230)
        jl.addWidget(self.journal, 1)
        self.events = QListWidget()   # текстовые копии для отчёта/тестов — не показываются
        self.raw = QListWidget()
        self.events.hide()
        self.raw.hide()
        self.body.addWidget(journal, 2)

        # --- кнопки
        btns = QHBoxLayout()
        self.btn_pause = QPushButton("⏸ Пауза")
        self.btn_pause.clicked.connect(self.toggle_pause)
        self.btn_reset = QPushButton("↺ Сброс")
        self.btn_reset.clicked.connect(self.reset)
        self.btn_copy = QPushButton("📋 Копировать отчёт")
        self.btn_copy.setObjectName("btnSuccess")
        self.btn_copy.clicked.connect(self.copy_report)
        self.btn_copy_raw = QPushButton("🖥 Копировать вывод ping")
        self.btn_copy_raw.setToolTip("Весь вывод как в консоли — для вставки в заявку")
        self.btn_copy_raw.clicked.connect(self.copy_raw)
        btns.addWidget(self.btn_pause)
        btns.addWidget(self.btn_reset)
        btns.addWidget(self.btn_copy)
        btns.addWidget(self.btn_copy_raw)
        btns.addStretch()
        close = QPushButton("Закрыть")
        close.setObjectName("btnPrimary")
        close.clicked.connect(self.close)
        btns.addWidget(close)
        self.body.addLayout(btns)

        self.worker = PingWorker(target, parent=self)
        self.worker.ping_event.connect(self.on_event)
        self.worker.error.connect(self.on_error)
        self.worker.start()
        # «замирание» графика — это ping.exe ждёт ответ (до 4 с на таймаут); показываем это явно, а не молча
        self._last_event = datetime.now()
        self._wait_timer = QTimer(self)
        self._wait_timer.setInterval(250)
        self._wait_timer.timeout.connect(self._check_waiting)
        self._wait_timer.start()

    def _check_waiting(self):
        idle = (datetime.now() - self._last_event).total_seconds()
        self.graph.set_waiting(not self._paused and self.sent > 0 and idle > 1.0)

    # ------------------------------------------------------------------ данные
    @staticmethod
    def _ms(text: str) -> float | None:
        digits = "".join(ch for ch in str(text) if ch.isdigit() or ch == ".")
        if not digits:
            return None
        try:
            return float(digits)
        except ValueError:
            return None

    def _add_row(self, kind: str, result: str, rtt: str = "", ttl: str = "", num: str = ""):
        """Строка журнала. kind: ok · fail · info · event."""
        pal = app_palette()
        color = {"ok": pal.solid("success"), "fail": pal.solid("danger"), "event": pal.title_accent}.get(kind, pal.subtext)
        fg = {"fail": pal.danger[0], "event": pal.title_accent, "info": pal.subtext}.get(kind, pal.text)
        r = self.journal.rowCount()
        self.journal.insertRow(r)
        mark = QTableWidgetItem({"ok": "●", "fail": "✖", "event": "▶"}.get(kind, "·"))
        mark.setForeground(QColor(color))
        mark.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        cells = [mark, QTableWidgetItem(num), QTableWidgetItem(f"{datetime.now():%H:%M:%S}"),
                 QTableWidgetItem(result), QTableWidgetItem(rtt), QTableWidgetItem(ttl)]
        base_font = QFont(self.journal.font())          # один шрифт на все строки — таймауты не выбиваются
        for c, it in enumerate(cells):
            it.setFont(base_font)
            if c:
                it.setForeground(QColor(fg))
            elif kind == "event":
                it.setBackground(QColor(pal.title_accent))
            if c in (1, 4, 5):
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            it.setToolTip(result)
            it.setData(Qt.ItemDataRole.UserRole, kind)
            self.journal.setItem(r, c, it)
        if kind == "event":
            f = QFont(self.journal.font())
            f.setBold(True)
            cells[3].setFont(f)
        self.journal.setRowHidden(r, not self._row_visible(kind))
        while self.journal.rowCount() > 600:
            self.journal.removeRow(0)
        self.journal.scrollToBottom()
        self._update_journal_label()

    def _row_visible(self, kind: str) -> bool:
        return self._filter == "all" or (self._filter == "bad" and kind in ("fail", "event")) or (self._filter == "events" and kind == "event")

    def _set_filter(self, key: str):
        self._filter = key
        for k, b in self.filter_btns.items():
            b.setChecked(k == key)
        for r in range(self.journal.rowCount()):
            it = self.journal.item(r, 0)
            self.journal.setRowHidden(r, not self._row_visible(it.data(Qt.ItemDataRole.UserRole) if it else "ok"))
        self.journal.scrollToBottom()
        self._update_journal_label()

    def _update_journal_label(self):
        n = self.journal.rowCount()
        shown = sum(1 for r in range(n) if not self.journal.isRowHidden(r))
        self.lbl_journal.setText(f"строк: {n}" if shown == n else f"показано {shown} из {n}")

    def _raw_line(self, d: dict, ok: bool | None):
        """Строка как в консоли; текст дублируется в self.raw для «Копировать вывод»."""
        if d.get("is_info"):
            text = d.get("status") or ""
            self._add_row("info", text)
        elif ok:
            text = f"Ответ от {d.get('ip', self.target)}: число байт={d.get('bytes', '32')} время={d.get('time', '—')} TTL={d.get('ttl', '—')}"
            self._add_row("ok", f"Ответ от {d.get('ip', self.target)}: число байт={d.get('bytes', '32')}",
                          str(d.get("time", "—")), str(d.get("ttl", "—")), str(self.sent + 1))
        else:
            text = d.get("status") or "Превышен интервал ожидания для запроса."
            self._add_row("fail", text, "—", "—", str(self.sent + 1))
        self.raw.addItem(f"{datetime.now():%H:%M:%S}  {text}")
        while self.raw.count() > 600:
            self.raw.takeItem(0)

    def on_event(self, d: dict):
        self._last_event = datetime.now()
        self.graph.set_waiting(False)
        if self._paused:
            return
        self._raw_line(d, None if d.get("is_info") else bool(d.get("success")))
        if d.get("is_info"):
            return
        ok = bool(d.get("success"))
        self.sent += 1
        ms = self._ms(d.get("time", "")) if ok else None
        if ok:
            self.recv += 1
            if ms is not None:
                if ms == 0:
                    ms = 0.5  # «<1мс» у Windows
                self.times.append(ms)
                if len(self.times) > 1000:
                    self.times = self.times[-1000:]
        self.graph.push(ms if ok else None)
        self.lbl_now.setText(f"{ms:.0f}" if ok and ms is not None else "—")
        self._refresh_stats()
        if not ok:
            self.events.insertItem(0, f"{datetime.now():%H:%M:%S}  ⛔ {d.get('status') or 'нет ответа'}")
        if ok != self._last_state:
            self._set_state(ok)
            if self._last_state is not None or not ok:
                self._log("🟢 узел снова отвечает" if ok else "🔴 узел перестал отвечать", "success" if ok else "danger")
            self._last_state = ok
            try:
                db.set_pc_online(self.computer_name, ok)
            except Exception as exc:  # noqa: BLE001
                log.debug("set_pc_online: %s", exc)
            if self.app and hasattr(self.app, "update_pc_status_in_ui"):
                self.app.update_pc_status_in_ui(self.computer_name, ok)

    def on_error(self, msg: str):
        self.lbl_state.setText("Ошибка")
        self._log(f"⚠️ {msg}", "danger")
        self._set_state(False)

    def _set_state(self, ok: bool):
        pal = app_palette()
        self.dot.setStyleSheet(f"font-size: 30pt; color: {pal.solid('success' if ok else 'danger')};")
        self.lbl_state.setText("Узел отвечает" if ok else "Узел не отвечает")
        while self.badge_box.count():
            w = self.badge_box.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        badge = make_badge("● В сети" if ok else "● Не в сети", "online" if ok else "offline", pal)
        self.badge_box.addWidget(badge)
        self.badge_box.activate()   # иначе новый QLabel до первого layout-прохода висит 640×480 поверх окна
        badge.show()

    def _refresh_stats(self):
        pal = app_palette()
        loss = (self.sent - self.recv) / self.sent * 100 if self.sent else 0.0
        self.stat["sent"].setText(str(self.sent))
        self.stat["recv"].setText(str(self.recv))
        self.stat["loss"].setText(f"{loss:.0f}%")
        self.stat["loss"].setStyleSheet(f"font-size: 12pt; font-weight: bold; color: {pal.danger[0] if loss >= 10 else pal.warning[0] if loss > 0 else pal.text};")
        if self.times:
            self.stat["min"].setText(f"{min(self.times):.0f}")
            self.stat["avg"].setText(f"{statistics.fmean(self.times):.1f}")
            self.stat["max"].setText(f"{max(self.times):.0f}")
            self.stat["jit"].setText(f"{statistics.pstdev(self.times):.1f}" if len(self.times) > 1 else "0")
        else:
            for k in ("min", "avg", "max", "jit"):
                self.stat[k].setText("—")

    def _log(self, text: str, kind: str = ""):
        self.events.insertItem(0, f"{datetime.now():%H:%M:%S}  {text}")
        while self.events.count() > 200:
            self.events.takeItem(self.events.count() - 1)
        self._add_row("event", text)

    # ------------------------------------------------------------------ управление
    def toggle_pause(self):
        self._paused = not self._paused
        self.btn_pause.setText("▶ Продолжить" if self._paused else "⏸ Пауза")
        self._log("⏸ пауза" if self._paused else "▶ продолжаем")

    def reset(self):
        self.sent = self.recv = 0
        self.times.clear()
        self.graph.clear()
        self.events.clear()
        self.raw.clear()
        self.journal.setRowCount(0)
        self._update_journal_label()
        self.lbl_now.setText("—")
        self._started = datetime.now()
        self._refresh_stats()

    def report_text(self) -> str:
        loss = (self.sent - self.recv) / self.sent * 100 if self.sent else 0.0
        lines = [f"📡 Пинг {self.computer_name} ({self.target}) · с {self._started:%H:%M:%S} по {datetime.now():%H:%M:%S}",
                 f"Отправлено {self.sent}, получено {self.recv}, потери {loss:.0f}%"]
        if self.times:
            lines.append(f"Отклик: мин {min(self.times):.0f} · сред {statistics.fmean(self.times):.1f} · макс {max(self.times):.0f} мс"
                         + (f" · джиттер {statistics.pstdev(self.times):.1f}" if len(self.times) > 1 else ""))
        ev = [self.events.item(i).text() for i in range(min(10, self.events.count()))]
        if ev:
            lines.append("События: " + "; ".join(reversed(ev)))
        n = self.raw.count()
        if n:
            lines.append("Последние ответы:")
            lines += ["  " + self.raw.item(i).text() for i in range(max(0, n - 8), n)]
        return "\n".join(lines)

    def copy_report(self):
        QApplication.clipboard().setText(self.report_text())
        self._log("📋 отчёт скопирован")

    def raw_text(self) -> str:
        head = f"Обмен пакетами с {self.computer_name} [{self.target}] с 32 байтами данных:"
        return "\n".join([head] + [self.raw.item(i).text() for i in range(self.raw.count())])

    def copy_raw(self):
        QApplication.clipboard().setText(self.raw_text())
        self._log("🖥 вывод ping скопирован")

    def on_dialog_done(self):
        self._wait_timer.stop()
        self.worker.stop()
