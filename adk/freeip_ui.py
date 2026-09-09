"""Окно «Свободный IP» (переработано в 3.2.2).

Слева — параметры и **карта подсети /24**: 254 ячейки, каждая закрашивается по мере проверки
(инвентарь · DHCP-аренда · резерв · отвечает на ping · есть PTR · свободен). Справа — результат крупно,
вердикт DHCP отдельной строкой и таблица найденных адресов за сеанс. Логика прежняя — :class:`adk.workers.FreeIPWorker`.
"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .config import settings
from .pingui import PingDialog
from .widgets import FramelessDialog, app_palette, make_badge
from .workers import FreeIPWorker

# статус ячейки → (подпись, ключ цвета палитры)
CELL = {
    # подпись честно говорит, ОТКУДА взят факт: сканер ADK (данные последнего опроса парка), DHCP-сервер,
    # ответ на ping прямо сейчас, запись в DNS. «Свободен» = ни один источник адрес не знает.
    "inventory": ("ПК из последнего скана парка", "info"),
    "lease": ("аренда DHCP (активная)", "warning"),
    "reserved": ("резервирование DHCP", "warning"),
    "alive": ("отвечает на ping сейчас", "danger"),
    "ptr": ("есть имя в DNS (PTR)", "ptr"),
    "free": ("свободен — нигде не числится", "success"),
    "found": ("выбранный результат", "accent"),
}
DHCP_ICON = {"free": "✅", "excluded": "✅", "outside": "ℹ️", "n/a": "⚠️", "unavailable": "⚠️"}


class SubnetMap(QWidget):
    """Широкая сетка адресов .1–.254: 32 в ряд × 8 рядов (3.2.9 — раньше была квадратная 16×16 и тянула окно
    в высоту; в ширину места больше, ячейки крупнее, цифры читаются). Клик по ячейке — сигнал ``picked(host)``."""
    picked = pyqtSignal(int)
    COLS, ROWS = 32, 8
    MAX_CELL = 32   # шаг сетки, px — трёхзначные номера помещаются с запасом
    GAP = 5         # просвет между ячейками, px — блоки не сливаются

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status: dict[int, str] = {}
        self.found: int | None = None
        self.start_host = 1
        # фиксированный размер ровно под сетку: ни лишней высоты, ни наезда легенды на нижний ряд
        self.setFixedSize(self.COLS * self.MAX_CELL + 8, self.ROWS * self.MAX_CELL + 8)
        self.setMouseTracking(True)
        self.setToolTip("")

    def reset(self):
        self.status.clear()
        self.found = None
        self.update()

    def mark(self, host: int, st: str):
        self.status[host] = st
        self.update()

    # ---- геометрия
    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True


    def _cell(self) -> tuple[float, float, float]:
        """(смещение x, смещение y, сторона ячейки): сетка COLS×ROWS вписана в виджет и отцентрована."""
        c = min((self.width() - 8) / self.COLS, (self.height() - 8) / self.ROWS, self.MAX_CELL)
        return 4 + (self.width() - 8 - c * self.COLS) / 2, 4 + (self.height() - 8 - c * self.ROWS) / 2, c

    def _host_at(self, x, y) -> int | None:
        ox, oy, c = self._cell()
        col, row = int((x - ox) // c), int((y - oy) // c)
        if 0 <= col < self.COLS and 0 <= row < self.ROWS:
            h = row * self.COLS + col
            return h if 1 <= h <= 254 else None
        return None

    def mouseMoveEvent(self, e):  # noqa: N802
        h = self._host_at(e.position().x(), e.position().y())
        if h is None:
            self.setToolTip("")
        else:
            st = self.status.get(h)
            self.setToolTip(f".{h} — {CELL[st][0] if st else 'не проверялся'}")

    def mousePressEvent(self, e):  # noqa: N802
        h = self._host_at(e.position().x(), e.position().y())
        if h is not None:
            self.picked.emit(h)

    # сплошные системные цвета (одинаковы во всех темах): статус ячейки читается сразу, цифры — белым
    SOLID = {"info": "#0A84FF", "warning": "#FF9F0A", "danger": "#FF453A", "success": "#30D158", "ptr": "#BF5AF2"}

    def _color(self, pal, key: str) -> QColor:
        if key == "accent":
            return QColor(pal.title_accent)
        return QColor(self.SOLID[key])

    def paintEvent(self, _e):  # noqa: N802
        pal = app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ox, oy, c = self._cell()
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, min(11.0, c * 0.29)))   # «254» помещается с запасом
        p.setFont(font)
        base = QColor(pal.input)
        for h in range(256):
            row, col = divmod(h, self.COLS)
            g = self.GAP / 2
            r = QRectF(ox + col * c + g, oy + row * c + g, c - 2 * g, c - 2 * g)
            if h in (0, 255):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pal.border))
                p.drawRoundedRect(r, 4, 4)
                continue
            st = self.status.get(h)
            if st and st not in CELL:            # неизвестный статус не должен ронять отрисовку
                st = None
            fill = self._color(pal, CELL[st][1]) if st else QColor(base)
            p.setPen(QPen(QColor(pal.border), 1) if h >= self.start_host and not st else Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, 4, 4)
            if h == self.found:
                # найденный адрес остаётся ЗЕЛЁНЫМ (он свободен — цвет = смысл), а «это он» показываем
                # толстой акцентной рамкой: цвет ячейки не должен менять значение при выборе
                p.setPen(QPen(QColor(pal.text), max(2.0, c * 0.12)))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)
            if c >= 15:
                p.setPen(QColor("#ffffff" if st else pal.subtext))
                p.drawText(r, Qt.AlignmentFlag.AlignCenter, str(h))
        p.end()


class FreeIPDialog(FramelessDialog):
    """Свободный IP: карта подсети, крупный результат, вердикт DHCP, таблица найденных за сеанс."""

    def __init__(self, parent=None):
        super().__init__("🔍 Свободный IP-адрес", parent, (1280, 780))
        self.worker: FreeIPWorker | None = None
        self.found = ""
        self.history: list[str] = []
        self._dhcp: dict | None = None
        pal = app_palette()
        self.pal = pal
        self.body.setSpacing(10)
        self.body.setContentsMargins(16, 8, 16, 12)
        root = QVBoxLayout()
        root.setSpacing(10)
        self.body.addLayout(root, 1)

        # ============ верх во всю ширину: параметры + широкая карта (32 адреса в ряд)
        left = root
        params = QFrame()
        params.setObjectName("dashCard")
        params.setStyleSheet("#dashCard { padding: 4px; }")
        pg = QGridLayout(params)
        pg.setContentsMargins(12, 6, 12, 6)
        pg.setHorizontalSpacing(8)
        self.prefix = QLineEdit("10.0.2")
        self.prefix.setPlaceholderText("10.0.2")
        self.prefix.setToolTip("Первые три октета подсети /24")
        self.prefix.returnPressed.connect(self.search)
        self.prefix.textChanged.connect(lambda _t: self.map.reset())
        self.start = QSpinBox()
        self.start.setRange(1, 254)
        self.start.setValue(50)
        self.start.setToolTip("С какого хоста начинать проверку")
        self.start.valueChanged.connect(self._start_changed)
        self.btn_start = QPushButton("🔍 Найти")
        self.btn_start.setObjectName("btnPrimary")
        self.btn_start.clicked.connect(self.search)
        self.btn_stop = QPushButton("⏹ Стоп")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop)
        pg.addWidget(QLabel("<b>Подсеть</b>"), 0, 0)
        pg.addWidget(self.prefix, 0, 1)
        pg.addWidget(QLabel(".x"), 0, 2)
        pg.addWidget(QLabel("<b>начиная с</b>"), 0, 3)
        pg.addWidget(self.start, 0, 4)
        pg.addWidget(self.btn_start, 0, 5)
        pg.addWidget(self.btn_stop, 0, 6)
        pg.setColumnStretch(1, 1)
        left.addWidget(params)

        map_card = QFrame()
        map_card.setObjectName("dashCard")
        map_card.setStyleSheet("#dashCard { padding: 4px; }")
        ml = QVBoxLayout(map_card)
        ml.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        self.lbl_map = QLabel("<b>Карта подсети</b> — клик по ячейке задаёт стартовый хост")
        head.addWidget(self.lbl_map, 1)
        self.map = SubnetMap()
        self.map.start_host = self.start.value()
        self.map.picked.connect(self.start.setValue)
        ml.addLayout(head)
        # карта слева, легенда — столбиком справа от неё (под картой ей тесно: наезжала на нижний ряд)
        row_map = QHBoxLayout()
        row_map.setSpacing(16)
        row_map.addWidget(self.map, 0, Qt.AlignmentFlag.AlignTop)
        legend = QGridLayout()
        legend.setHorizontalSpacing(6)
        legend.setVerticalSpacing(4)
        legend.setVerticalSpacing(7)
        for i, key in enumerate(("free", "found", "lease", "reserved", "alive", "ptr")):
            sw = QLabel()
            sw.setFixedSize(16, 16)
            if key == "found":
                sw.setStyleSheet(f"background: {pal.input}; border: 2.5px solid {pal.text}; border-radius: 4px;")
            else:
                sw.setStyleSheet(f"background: {self.map._color(pal, CELL[key][1]).name()}; border: none; border-radius: 4px;")
            t = QLabel(CELL[key][0])
            t.setStyleSheet(f"color: {pal.text}; font-size: 9.5pt;")
            legend.addWidget(sw, i, 0)
            legend.addWidget(t, i, 1)
        legend.setRowStretch(6, 1)
        self.legend_box = QWidget()
        self.legend_box.setLayout(legend)
        row_map.addWidget(self.legend_box, 1, Qt.AlignmentFlag.AlignTop)
        ml.addLayout(row_map)
        left.addWidget(map_card, 0)

        # ============ низ: результат слева, таблица найденных справа
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        root.addLayout(bottom, 1)
        right = QVBoxLayout()
        right.setSpacing(8)
        res = QFrame()
        res.setObjectName("dashCard")
        rl = QVBoxLayout(res)
        rl.setContentsMargins(18, 14, 18, 14)
        rl.setSpacing(8)
        rl.addStretch(1)
        cap = QLabel("СВОБОДНЫЙ АДРЕС")
        cap.setStyleSheet(f"color: {pal.subtext}; font-size: 8.5pt; font-weight: bold; letter-spacing: 1px;")
        rl.addWidget(cap)
        self.lbl_ip = QLabel("—")
        self.lbl_ip.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lbl_ip.setStyleSheet(f"font-size: 30pt; font-weight: 800; color: {pal.title_accent};")
        rl.addWidget(self.lbl_ip)
        self.checks = QHBoxLayout()
        self.checks.setSpacing(6)
        rl.addLayout(self.checks)
        self.lbl_dhcp = QLabel("")
        self.lbl_dhcp.setWordWrap(True)
        self.lbl_dhcp.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        rl.addWidget(self.lbl_dhcp)
        rl.addSpacing(4)
        row = QHBoxLayout()
        self.btn_copy = QPushButton("📋 Копировать")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy)
        self.btn_ping = QPushButton("📡 Пинг")
        self.btn_ping.setEnabled(False)
        self.btn_ping.clicked.connect(lambda: PingDialog(self.found, self.found, None, self).exec())
        self.btn_next = QPushButton("➡️ Следующий")
        self.btn_next.setObjectName("btnSuccess")
        self.btn_next.setEnabled(False)
        self.btn_next.clicked.connect(self.next_)
        for b in (self.btn_copy, self.btn_ping, self.btn_next):
            row.addWidget(b)
        rl.addLayout(row)
        rl.addStretch(1)
        bottom.addWidget(res, 4)

        trow = QHBoxLayout()
        lbl_found = QLabel("<b>Найдено за сеанс</b>")
        lbl_found.setToolTip("Двойной клик по строке — скопировать адрес")
        trow.addWidget(lbl_found, 1)
        self.btn_copy_all = QPushButton("📋 Скопировать все")
        self.btn_copy_all.setEnabled(False)
        self.btn_copy_all.clicked.connect(lambda: QApplication.clipboard().setText("\n".join(self.history)))
        self.btn_copy_all.setToolTip("Скопировать все найденные адреса, по одному в строке")
        trow.addWidget(self.btn_copy_all)
        right.addLayout(trow)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Адрес", "Время", "DHCP"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemDoubleClicked.connect(lambda it: QApplication.clipboard().setText(self.table.item(it.row(), 0).text()))
        right.addWidget(self.table, 1)
        bottom.addLayout(right, 5)

        # ============ низ: статус + подсказка
        self.status = QLabel("Укажите подсеть и нажмите «Найти».")
        self.status.setObjectName("subtle")
        dhcp_note = (f"Сверяется с DHCP: {', '.join(settings.dhcp_servers)} (аренды, резервирования, исключения)."
                     if settings.dhcp_servers else "Сверка с DHCP выключена — укажите серверы в [Scanner] dhcp_servers.")
        self.status.setToolTip("Свободным считается адрес, которого нет среди ПК последнего скана парка, который сейчас "
                               "не отвечает на ping и не имеет имени в DNS (PTR). " + dhcp_note)
        self.lbl_map.setToolTip(self.status.toolTip())
        self.body.addWidget(self.status)

    # ------------------------------------------------------------------ вспомогательное
    def _start_changed(self, v: int):
        self.map.start_host = v
        self.map.update()

    def _set_checks(self, items: list[tuple[str, str]]):
        while self.checks.count():
            it = self.checks.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.hide()
                wdg.deleteLater()
        for text, kind in items:
            b = make_badge(text, kind, self.pal)
            self.checks.addWidget(b)
            b.show()
        self.checks.addStretch()
        self.checks.activate()

    def _copy(self):
        if self.found:
            QApplication.clipboard().setText(self.found)
            self.status.setText(f"Скопировано: {self.found}")

    # ------------------------------------------------------------------ поиск
    def search(self):
        if self.worker and self.worker.isRunning():
            return
        for b in (self.btn_start, self.btn_next, self.btn_copy, self.btn_ping):
            b.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.lbl_ip.setText("…")
        self.lbl_dhcp.setText("")
        self._set_checks([])
        self._dhcp = None
        self.map.found = None
        self.worker = FreeIPWorker(self.prefix.text(), self.start.value(), parent=self)
        self.worker.progress.connect(self.status.setText)
        self.worker.error.connect(self.on_error)
        self.worker.host_checked.connect(self.map.mark)
        self.worker.dhcp_info.connect(self.on_dhcp)
        self.worker.finished_search.connect(self.on_done)
        self.worker.start()

    def stop(self):
        if self.worker:
            self.worker.cancel()
        self.btn_stop.setEnabled(False)
        self.btn_start.setEnabled(True)
        self.lbl_ip.setText("—")
        self.status.setText("Остановлено.")

    def on_dhcp(self, info: dict):
        self._dhcp = info

    def on_error(self, msg: str):
        self.lbl_ip.setText("—")
        self.status.setText(f"⚠️ {msg}")
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)

    def next_(self):
        if self.found:
            self.start.setValue(min(254, int(self.found.rsplit(".", 1)[1]) + 1))
            self.search()

    def on_done(self, ip: str):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.found = ip
        for b in (self.btn_next, self.btn_copy, self.btn_ping):
            b.setEnabled(bool(ip))
        if not ip:
            self.lbl_ip.setText("—")
            self._set_checks([])
            self.lbl_dhcp.setText("")
            self.status.setText("Свободные адреса не найдены — попробуйте другую подсеть или меньший стартовый хост.")
            return
        self.lbl_ip.setText(ip)
        host = int(ip.rsplit(".", 1)[1])
        self.map.found = host
        self.map.mark(host, "free")
        info = self._dhcp or {}
        st = info.get("status", "n/a")
        icon = DHCP_ICON.get(st, "❌")
        dhcp_kind = "online" if st in ("free", "excluded") else "info" if st == "outside" else "warning"
        self._set_checks([("✓ нет среди ПК парка", "online"), ("✓ не отвечает на ping", "online"), ("✓ нет имени в DNS", "online"),
                          (f"{icon} DHCP", dhcp_kind)])
        txt = info.get("text", "не сверялось")
        txt = "адрес не выдан" if txt == "не выдан DHCP" else txt
        dhcp_txt = f"DHCP: {txt}" + (f" ({info['detail']})" if info.get("detail") else "")
        self.lbl_dhcp.setText(f"{icon} {dhcp_txt}")
        self.status.setText(f"Готово: {ip}. Следующий поиск начнётся с .{min(254, host + 1)}.")
        if ip not in self.history:
            self.history.append(ip)
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(ip))
            self.table.setItem(r, 1, QTableWidgetItem(f"{datetime.now():%H:%M:%S}"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{icon} {info.get('text', 'не сверялось')}"))
            self.btn_copy_all.setEnabled(True)

    def on_dialog_done(self):
        if self.worker:
            self.worker.cancel()
            self.worker.wait(3000)
