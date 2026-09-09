"""Окно «Здоровье ПК»: обзор, S.M.A.R.T. физических дисков и карта диска (treemap).

Данные приходят из :mod:`adk.health`; окно ничего не считает само. Быстрая часть (обзор + S.M.A.R.T.)
запускается при открытии, тяжёлая карта диска — только по кнопке, потому что обход ``C$`` по сети
занимает минуты и грузит ПК сотрудника.
"""
from __future__ import annotations

import logging
import os


from PyQt6.QtCore import QDateTime, QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import db, health
from .widgets import DrivePicker, FramelessDialog, app_palette, fit_columns, make_badge, run_in_background

log = logging.getLogger(__name__)

VERDICT_KIND = {"good": "online", "caution": "warning", "bad": "offline", "unknown": "neutral"}
VERDICT_ICON = {"good": "🟢", "caution": "🟡", "bad": "🔴", "unknown": "⚪"}


def _bar(value: int, warn: int = 80, danger: int = 90) -> QProgressBar:
    pal = app_palette()
    b = QProgressBar()
    b.setRange(0, 100)
    b.setValue(max(0, min(100, int(value))))
    b.setFormat(f"{int(value)}%")
    b.setFixedHeight(18)
    color = pal.danger[2] if value >= danger else pal.warning[2] if value >= warn else pal.success[2]
    b.setStyleSheet(f"QProgressBar {{ background-color: {pal.input}; border: 1px solid {pal.border}; text-align: center; "
                    f"color: {pal.text}; font-weight: bold; font-size: 9pt; }} QProgressBar::chunk {{ background-color: {color}; }}")
    return b


def _tile(title: str, value: str, sub: str = "", kind: str = "", compact: bool = False) -> QFrame:
    pal = app_palette()
    f = QFrame()
    f.setObjectName("dashCard")
    if compact:
        f.setStyleSheet("#dashCard { padding: 0px; }")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 4 if compact else 10, 12, 4 if compact else 10)
    lay.setSpacing(0 if compact else 2)
    t = QLabel(title)
    t.setStyleSheet(f"color: {pal.subtext}; font-size: {8 if compact else 9}pt; font-weight: bold;")
    v = QLabel(value)
    v.setStyleSheet(f"font-size: {13 if compact else 15}pt; font-weight: bold; color: {pal.danger[0] if kind == 'danger' else pal.title_accent};")
    v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lay.addWidget(t)
    lay.addWidget(v)
    if sub:
        s = QLabel(sub)
        s.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        s.setWordWrap(True)
        lay.addWidget(s)
    return f


EVENT_ICON = {1: "🟥", 2: "🔴", 3: "🟡", 4: "🔵", 5: "⚪"}


def safe_txt(t) -> str:
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clear(layout):
    while layout.count():
        it = layout.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()            # иначе до deleteLater виджет остаётся нарисованным поверх шапки
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            _clear(it.layout())


# --------------------------------------------------------------------------- treemap
class SizeItem(QTableWidgetItem):
    """Ячейка размера: показывает «12.3 ГБ», сортируется по байтам."""

    def __lt__(self, other):
        return (self.data(Qt.ItemDataRole.UserRole) or 0) < (other.data(Qt.ItemDataRole.UserRole) or 0)


class TreemapWidget(QWidget):
    """Карта папок: площадь плитки ∝ размеру.

    Правила читаемости: мелкие папки (< ``MIN_PCT`` % или слишком маленькая плитка) сворачиваются в одну плитку
    «Прочее», подписи обрезаются многоточием и никогда не вылезают за плитку, у совсем маленьких подписи нет —
    их имя/размер видны в легенде под картой и во всплывающей подсказке. Клик по плитке — callback ``on_click``.
    """

    PALETTE = ["#0284c7", "#6366f1", "#db2777", "#ea580c", "#059669", "#d97706", "#7c3aed", "#0d9488",
               "#dc2626", "#16a34a", "#2563eb", "#c026d3"]
    OTHER_COLOR = "#475569"
    MIN_PCT = 1.2          # меньше — в «Прочее»
    MAX_TILES = 14
    LEGEND_ROW = 22

    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: list[dict] = []
        self.tiles: list[dict] = []
        self.rects: list[tuple[QRectF, dict]] = []
        self.total = 0
        self.on_click = None
        self._hover = -1
        self._selected = -1          # плитка, по которой кликнули: рамка акцентом + подсветка в легенде
        self._legend_rows = 0
        self.setMouseTracking(True)
        self.setMinimumHeight(self.MAP_MIN_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    MAP_MIN_H = 200      # минимум под сами плитки; легенда прибавляется сверху этого

    def _legend_rows_for(self, width: int) -> int:
        """Сколько строк займёт легенда при заданной ширине (перенос «чипов»)."""
        if not self.tiles:
            return 0
        fm = self.fontMetrics()
        x, rows, w = 0, 1, max(1, width - 12)
        for t in self.tiles:
            cw = fm.horizontalAdvance(self._legend_text(t)) + 30
            if x + cw > w and x > 0:
                rows += 1
                x = 0
            x += cw
        return min(rows, 3)

    def _sync_min_height(self):
        rows = self._legend_rows_for(self.width())
        self.setMinimumHeight(self.MAP_MIN_H + (rows * self.LEGEND_ROW + 8 if rows else 0))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._sync_min_height()

    def set_items(self, items: list[dict], total: int):
        self.items = [x for x in items if x.get("size", 0) > 0]
        self.total = total or sum(x["size"] for x in self.items) or 1
        big = [x for x in self.items if 100.0 * x["size"] / self.total >= self.MIN_PCT][:self.MAX_TILES]
        rest = [x for x in self.items if x not in big]
        self.tiles = list(big)
        if rest:
            size = sum(x["size"] for x in rest)
            self.tiles.append({"name": f"Прочее · {len(rest)} папок", "path": "; ".join(x["path"] for x in rest[:12]),
                               "size": size, "pct": round(100.0 * size / self.total, 1), "other": True, "children": rest})
        self._hover = -1
        self._selected = -1
        self._sync_min_height()
        self.update()

    def selected_item(self) -> dict | None:
        return self.rects[self._selected][1] if 0 <= self._selected < len(self.rects) else None

    # --- геометрия
    def _map_rect(self) -> QRectF:
        return QRectF(1, 1, max(1, self.width() - 2), max(1, self.height() - 2 - self._legend_rows * self.LEGEND_ROW - (8 if self._legend_rows else 0)))

    def _layout(self):
        self._legend_rows = self._legend_rows_for(self.width())
        m = self._map_rect()
        vals = [float(t["size"]) for t in self.tiles]
        self.rects = [(QRectF(rx, ry, rw, rh), t) for (rx, ry, rw, rh), t in
                      zip(health.squarify(vals, m.x(), m.y(), m.width(), m.height()), self.tiles)]

    @staticmethod
    def _legend_text(t: dict) -> str:
        return f"{t['name']}  {health.fmt_size(t['size'])} · {t.get('pct', 0)}%"

    def _color(self, i: int, t: dict) -> QColor:
        c = QColor(self.OTHER_COLOR if t.get("other") else self.PALETTE[i % len(self.PALETTE)])
        return c.lighter(125) if i == self._hover else c

    @staticmethod
    def _ink(c: QColor) -> QColor:
        return QColor("#0f172a") if c.lightnessF() > 0.55 else QColor("#f8fafc")

    # --- отрисовка
    def paintEvent(self, _e):  # noqa: N802
        pal = app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(pal.input))
        if not self.tiles:
            p.setPen(QColor(pal.subtext))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нажмите «Построить карту» — обход диска займёт несколько минут")
            p.end()
            return
        self._layout()
        bold = QFont(self.font())
        bold.setBold(True)
        small = QFont(self.font())
        small.setPointSizeF(max(7.0, self.font().pointSizeF() - 1))
        accent = QColor(pal.title_accent)
        for i, (r, t) in enumerate(self.rects):
            if r.width() < 2 or r.height() < 2:
                continue
            c = self._color(i, t)
            tile = r.adjusted(1.5, 1.5, -1.5, -1.5)
            # объём: тень снизу-справа, вертикальный градиент «светлее сверху», блик по верхней кромке
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(tile.translated(2, 3), 5, 5)
            grad = QLinearGradient(tile.topLeft(), tile.bottomLeft())
            grad.setColorAt(0.0, c.lighter(118))
            grad.setColorAt(0.55, c)
            grad.setColorAt(1.0, c.darker(128))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(c.darker(150), 1))
            p.drawRoundedRect(tile, 5, 5)
            if tile.height() > 12:
                p.setPen(QPen(QColor(255, 255, 255, 90), 1))
                p.drawLine(QPointF(tile.left() + 4, tile.top() + 1.5), QPointF(tile.right() - 4, tile.top() + 1.5))
            if i == self._selected:
                p.setPen(QPen(accent, 3))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(tile.adjusted(1, 1, -1, -1), 5, 5)
            elif i == self._hover:
                p.setPen(QPen(QColor(255, 255, 255, 160), 2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawRoundedRect(tile.adjusted(1, 1, -1, -1), 5, 5)
            inner = r.adjusted(9, 7, -9, -7)
            if inner.width() < 34 or inner.height() < 16:
                continue                                           # слишком мелко — только легенда и подсказка
            p.setPen(QColor("#ffffff"))
            p.setFont(bold)
            fm = p.fontMetrics()
            lines = [fm.elidedText(t["name"], Qt.TextElideMode.ElideRight, int(inner.width()))]
            if inner.height() >= fm.height() * 2 + 2:
                p.setFont(small)
                sfm = p.fontMetrics()
                second = f"{health.fmt_size(t['size'])} · {t.get('pct', 0)}%"
                if sfm.horizontalAdvance(second) > inner.width():
                    second = health.fmt_size(t["size"])
                lines.append(sfm.elidedText(second, Qt.TextElideMode.ElideRight, int(inner.width())))
            y = inner.top()
            for n, line in enumerate(lines):
                p.setFont(bold if n == 0 else small)
                h = p.fontMetrics().height()
                p.drawText(QRectF(inner.left(), y, inner.width(), h), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                y += h
        # --- легенда
        if self._legend_rows:
            p.setFont(self.font())
            fm = p.fontMetrics()
            x, row, w = 6, 0, max(1, self.width() - 12)
            top = self._map_rect().bottom() + 8
            for i, t in enumerate(self.tiles):
                text = self._legend_text(t)
                cw = fm.horizontalAdvance(text) + 30
                if x + cw > w and x > 6:
                    row += 1
                    x = 6
                if row >= self._legend_rows:
                    p.setPen(QColor(pal.subtext))
                    p.drawText(QRectF(x, top + row * self.LEGEND_ROW - self.LEGEND_ROW, w - x, self.LEGEND_ROW),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "…")
                    break
                y = top + row * self.LEGEND_ROW
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(self._color(i, t))
                p.drawRoundedRect(QRectF(x + 6, y + 5, 12, 12), 3, 3)
                if i == self._selected:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(pal.hover))
                    p.drawRoundedRect(QRectF(x, y + 1, cw - 4, self.LEGEND_ROW - 2), 6, 6)
                    p.setBrush(self._color(i, t))
                    p.drawRoundedRect(QRectF(x + 6, y + 5, 12, 12), 3, 3)
                p.setPen(QColor(pal.title_accent if i in (self._hover, self._selected) else pal.text))
                p.drawText(QRectF(x + 24, y, cw - 24, self.LEGEND_ROW), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                x += cw
        p.end()

    # --- мышь
    def _index_at(self, pos) -> int:
        for i, (r, _) in enumerate(self.rects):
            if r.contains(pos):
                return i
        return -1

    def mouseMoveEvent(self, e):  # noqa: N802
        i = self._index_at(e.position())
        if i != self._hover:
            self._hover = i
            if i >= 0:
                t = self.rects[i][1]
                if t.get("other"):
                    tip = "\n".join(f"{x['name']} — {health.fmt_size(x['size'])}" for x in t["children"][:15])
                    tip = f"Мелкие папки ({len(t['children'])}):\n{tip}" + ("\n…" if len(t["children"]) > 15 else "")
                else:
                    tip = f"{t['path']}\n{health.fmt_size(t['size'])} · {t.get('pct', 0)}%"
                self.setToolTip(tip)
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        i = self._index_at(e.position())
        self._selected = i
        self.update()
        if i >= 0 and self.on_click and not self.rects[i][1].get("other"):
            self.on_click(self.rects[i][1])


def open_in_explorer(path: str) -> None:
    """Открыть папку на удалённом ПК в Проводнике (путь вида \\\\ПК\\C$\\Users). На не-Windows — вежливая ошибка."""
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — путь строится самим приложением из \\ПК\\<буква>$ и имён папок
        return
    raise OSError("Открытие Проводника доступно только в Windows")


# --------------------------------------------------------------------------- диалог
def _diagnostics_placeholder(pal, size: int = 200):
    """Рисуем заглушку сами (без внешних файлов): монитор с «спящим» смайликом и стетоскоп."""
    from PyQt6.QtGui import QPainterPath, QPixmap
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    accent, fg = QColor(pal.title_accent), QColor(pal.text)
    dim = QColor(fg)
    dim.setAlpha(90)
    # монитор
    body = QRectF(size * 0.12, size * 0.16, size * 0.76, size * 0.5)
    p.setPen(QPen(accent, 4))
    p.setBrush(QColor(0, 0, 0, 40))
    p.drawRoundedRect(body, 12, 12)
    p.setPen(QPen(accent, 4))
    p.drawLine(QPointF(size * 0.5, body.bottom()), QPointF(size * 0.5, size * 0.78))
    p.drawLine(QPointF(size * 0.32, size * 0.8), QPointF(size * 0.68, size * 0.8))
    # «спящее» лицо на экране
    p.setPen(QPen(fg, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.drawLine(QPointF(size * 0.34, size * 0.36), QPointF(size * 0.42, size * 0.36))
    p.drawLine(QPointF(size * 0.58, size * 0.36), QPointF(size * 0.66, size * 0.36))
    mouth = QPainterPath(QPointF(size * 0.42, size * 0.52))
    mouth.quadTo(QPointF(size * 0.5, size * 0.56), QPointF(size * 0.58, size * 0.52))
    p.drawPath(mouth)
    # zzz
    f = QFont()
    f.setBold(True)
    for i, (x, y, pt) in enumerate(((0.7, 0.3, 10), (0.76, 0.22, 13), (0.83, 0.12, 16))):
        f.setPointSize(pt)
        p.setFont(f)
        c = QColor(accent)
        c.setAlpha(120 + i * 60)
        p.setPen(c)
        p.drawText(QPointF(size * x, size * y), "z")
    # стетоскоп, лежащий рядом
    p.setPen(QPen(dim, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    tube = QPainterPath(QPointF(size * 0.08, size * 0.9))
    tube.cubicTo(QPointF(size * 0.25, size * 0.98), QPointF(size * 0.45, size * 0.8), QPointF(size * 0.62, size * 0.93))
    p.drawPath(tube)
    p.setBrush(dim)
    p.drawEllipse(QPointF(size * 0.65, size * 0.93), 6, 6)
    p.end()
    return pm


class HealthDialog(FramelessDialog):
    """Вкладки: Обзор · Диски (S.M.A.R.T.) · Карта диска · Ошибки (журнал Windows)."""

    def __init__(self, comp: str, app, parent=None):
        super().__init__(f"🩺 Здоровье ПК: {comp}", parent, (1240, 720))
        self.comp, self.app = comp, app
        self.health_data: dict | None = None
        self.usage_data: dict | None = None
        pal = app_palette()

        head = QFrame()
        head.setObjectName("dashCard")
        hl = QHBoxLayout(head)
        self._head_layout = hl
        hl.setContentsMargins(14, 10, 14, 10)
        self.lbl_title = QLabel(f"💻 {comp}")
        self.lbl_title.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self.lbl_os = QLabel("⏳ Опрашиваю через PowerShell/CIM…")
        self.lbl_os.setStyleSheet(f"color: {pal.subtext};")
        tl = QVBoxLayout()
        tl.setSpacing(0)
        tl.addWidget(self.lbl_title)
        tl.addWidget(self.lbl_os)
        hl.addLayout(tl, 1)
        self.verdict_box = QHBoxLayout()
        hl.addLayout(self.verdict_box)
        self.btn_refresh = QPushButton("🔄 Обновить")
        self.btn_refresh.clicked.connect(self.load)
        self.btn_copy = QPushButton("📋 Копировать отчёт")
        self.btn_copy.setObjectName("btnSuccess")
        self.btn_copy.clicked.connect(self.copy_report)
        hl.addWidget(self.btn_refresh)
        hl.addWidget(self.btn_copy)
        self.body.addWidget(head)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_overview(), "📊 Обзор")
        self.tabs.addTab(self._tab_disks(), "💽 Диски (S.M.A.R.T.)")
        self.tabs.addTab(self._tab_usage(), "🗺️ Карта диска")
        self.tabs.addTab(self._tab_events(), "🚨 Ошибки")
        self.tabs.addTab(self._tab_diagnostics(), "🩻 Диагностика")
        self.body.addWidget(self.tabs, 1)

        foot = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("subtle")
        foot.addWidget(self.lbl_status, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        foot.addWidget(close)
        self.body.addLayout(foot)
        self.load()

    # ------------------------------------------------------------------ вкладки
    def _tab_overview(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.tiles = QGridLayout()
        self.tiles.setSpacing(10)
        lay.addLayout(self.tiles)
        lay.addWidget(QLabel("<b>💾 Разделы</b>"))
        self.parts = QGridLayout()
        self.parts.setColumnStretch(2, 1)
        self.parts.setHorizontalSpacing(12)
        lay.addLayout(self.parts)
        lay.addWidget(QLabel("<b>⚠️ Замечания</b>"))
        self.lbl_warn = QLabel("—")
        self.lbl_warn.setWordWrap(True)
        self.lbl_warn.setObjectName("specBox")
        self.lbl_warn.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.lbl_warn)
        lay.addStretch()
        return w

    def _tab_disks(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        left.addWidget(QLabel("<b>Физические диски</b>"))
        self.disk_list_box = QWidget()
        self.disk_list = QVBoxLayout(self.disk_list_box)
        self.disk_list.setContentsMargins(0, 0, 0, 0)
        self.disk_list.setSpacing(8)
        self.disk_list.addStretch()
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(self.disk_list_box)
        sc.setMinimumWidth(370)
        sc.viewport().setAutoFillBackground(False)
        self.disk_list_box.setAutoFillBackground(False)
        sc.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        left.addWidget(sc, 1)
        lay.addLayout(left, 0)
        right = QVBoxLayout()
        self.lbl_disk_head = QLabel("Выберите диск слева")
        self.lbl_disk_head.setStyleSheet("font-weight: bold; font-size: 11pt;")
        right.addWidget(self.lbl_disk_head)
        self.lbl_disk_reasons = QLabel("")
        self.lbl_disk_reasons.setWordWrap(True)
        right.addWidget(self.lbl_disk_reasons)
        self.attr_table = QTableWidget(0, 5)
        self.attr_table.setHorizontalHeaderLabels(["ID", "Атрибут", "Знач.", "Худш.", "RAW"])
        self.attr_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.attr_table.verticalHeader().setVisible(False)
        self.attr_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.attr_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        for c, wd in ((0, 48), (2, 70), (3, 70), (4, 120)):
            self.attr_table.setColumnWidth(c, wd)
        right.addWidget(self.attr_table, 1)
        hint = QLabel("Оценка как в CrystalDiskInfo: «Плохо» — предсказан отказ / неисправимые секторы / ресурс SSD; "
                      "«Осторожно» — переназначенные или ожидающие секторы, износ ≥ 80 %, температура ≥ 55 °C.")
        hint.setWordWrap(True)
        hint.setObjectName("subtle")
        right.addWidget(hint)
        lay.addLayout(right, 1)
        return w

    def _tab_usage(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("<b>Диск:</b>"))
        self.cb_drive = DrivePicker(tooltip="Том для карты: любой диск ПК (C:, D:, …); список появляется после опроса")
        self.cb_drive.addItem("C:")
        top.addWidget(self.cb_drive)
        top.addWidget(QLabel("Файлов в топе:"))
        self.sp_top = QSpinBox()
        self.sp_top.setRange(10, 200)
        self.sp_top.setValue(40)
        top.addWidget(self.sp_top)
        self.btn_usage = QPushButton("🗺️ Построить карту")
        self.btn_usage.setObjectName("btnInfo")
        self.btn_usage.clicked.connect(self.load_usage)
        top.addWidget(self.btn_usage)
        top.addStretch()
        self.lbl_usage = QLabel("Обход по \\\\ПК\\C$ — запускается только вручную, ПК при этом не тормозит, но ждать придётся.")
        self.lbl_usage.setObjectName("subtle")
        self.lbl_usage.setWordWrap(True)
        top.addWidget(self.lbl_usage, 1)
        lay.addLayout(top)
        self.treemap = TreemapWidget()
        self.treemap.on_click = self._tile_clicked      # клик по плитке → строка в таблице «Папки» + путь в буфер
        # карта слева, таблицы справа: внизу под картой места не хватало, а в ширину его много
        self.usage_split = QSplitter(Qt.Orientation.Horizontal)
        self.usage_split.setChildrenCollapsible(False)
        self.usage_split.setHandleWidth(8)
        self.usage_split.addWidget(self.treemap)
        self.usage_tabs = QTabWidget()
        self.usage_tabs.setMinimumWidth(440)
        self.tbl_dirs = self._usage_table(["Папка", "Размер", "%", "Файлов"])
        self.tbl_dirs.setToolTip("Двойной клик — открыть папку на ПК в Проводнике (через \\\\ПК\\C$)")
        self.tbl_dirs.itemDoubleClicked.connect(self._open_dir_row)
        self.tbl_files = self._usage_table(["Файл", "Размер"])
        self.tbl_hogs = self._usage_table(["Что", "Размер", "Файлов"])   # путь — в подсказке и в буфер по клику
        self.tbl_hogs.setToolTip("Клик по строке — путь в буфер обмена; полный путь во всплывающей подсказке")
        self.tbl_hogs.cellClicked.connect(self._copy_hog_path)
        # «Что можно почистить» — из того же обхода, что и карта (3.2.9): один том, одни цифры, никаких выдуманных
        # позиций — только пути, которые реально нашлись на этом томе (на D: без Windows корзины/Temp может и не быть).
        hogs_page = QWidget()
        hl = QVBoxLayout(hogs_page)
        hl.setContentsMargins(0, 6, 0, 0)
        self.lbl_hogs = QLabel("Появится после построения карты: это те же данные, что на карте, только отобраны известные "
                               "«пожиратели» места (корзина, Temp, кэши обновлений, дампы, подкачка). Ничего не удаляется.")
        self.lbl_hogs.setObjectName("subtle")
        self.lbl_hogs.setWordWrap(True)
        hl.addWidget(self.lbl_hogs)
        hl.addWidget(self.tbl_hogs, 1)
        self.tbl_users = self._usage_table(["Профиль", "Размер", "Файлов"])
        self.usage_tabs.addTab(self.tbl_dirs, "📁 Папки")
        self.usage_tabs.addTab(self.tbl_files, "📄 Файлы")
        self.usage_tabs.addTab(hogs_page, "🧹 Почистить")
        for i, tip in enumerate(("Папки верхнего уровня по размеру", "Самые крупные файлы тома",
                                 "Что можно почистить на этом томе — из того же обхода, что и карта; ничего не удаляется")):
            self.usage_tabs.setTabToolTip(i, tip)
        self.usage_tabs.setUsesScrollButtons(False)
        self.usage_split.addWidget(self.usage_tabs)
        self.usage_split.setStretchFactor(0, 6)
        self.usage_split.setStretchFactor(1, 5)
        self.treemap.setMinimumWidth(420)
        lay.addWidget(self.usage_split, 1)
        return w

    @staticmethod
    def _usage_table(headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(0 if len(headers) != 3 else 1, QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setSortingEnabled(True)
        return t

    def _tab_diagnostics(self) -> QWidget:
        """3.3.0: вкладка-заготовка под будущие проверки (пока — картинка и честное «здесь ничего нет»)."""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic = QLabel()
        pic.setObjectName("diagPlaceholderPic")
        pic.setPixmap(_diagnostics_placeholder(app_palette()))
        pic.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(pic)
        title = QLabel("Здесь пока пусто. Совсем.")
        title.setObjectName("diagTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(title)
        self.lbl_diag = QLabel(
            "Диагност уже выехал, но застрял в очереди на обновление Windows.<br>"
            "В следующих версиях тут появятся проверки «почему тормозит», «кто съел память» и «что это за процесс с иероглифами».<br>"
            "А пока — чайник, кнопка «Обновить» и вера в лучшее.")
        self.lbl_diag.setObjectName("diagText")
        self.lbl_diag.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.lbl_diag.setWordWrap(True)
        self.lbl_diag.setMinimumWidth(760)
        lay.addWidget(self.lbl_diag)
        return page

    def _tab_events(self) -> QWidget:
        """Журнал Windows (System/Application): фильтр по уровням и периоду; счётчики — по показанным событиям.
        Чтение через Get-WinEvent, ничего не пишется."""
        pal = app_palette()
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        # --- фильтр
        flt = QFrame()
        flt.setObjectName("dashCard")
        flt.setStyleSheet("#dashCard { padding: 2px; }")
        fl = QGridLayout(flt)
        fl.setContentsMargins(12, 6, 12, 6)
        fl.setVerticalSpacing(6)
        fl.addWidget(QLabel("<b>Уровни:</b>"), 0, 0)
        self.ev_levels: dict[int, QCheckBox] = {}
        lv_row = QHBoxLayout()
        for lvl, name in health.LEVELS.items():
            cb = QCheckBox(f"{EVENT_ICON[lvl]} {name}")
            cb.setChecked(lvl <= 3)
            self.ev_levels[lvl] = cb
            lv_row.addWidget(cb)
        lv_row.addStretch()
        fl.addLayout(lv_row, 0, 1, 1, 5)
        fl.addWidget(QLabel("<b>Журнал:</b>"), 1, 0)
        self.ev_log = QComboBox()
        self.ev_log.addItems(["System + Application", "System", "Application"])
        fl.addWidget(self.ev_log, 1, 1)
        fl.addWidget(QLabel("с"), 1, 2)
        now = QDateTime.currentDateTime()
        self.ev_from = QDateTimeEdit(now.addDays(-1))
        self.ev_to = QDateTimeEdit(now)
        for e in (self.ev_from, self.ev_to):
            e.setDisplayFormat("dd.MM.yyyy HH:mm")
            e.setCalendarPopup(True)
        fl.addWidget(self.ev_from, 1, 3)
        fl.addWidget(QLabel("по"), 1, 4)
        fl.addWidget(self.ev_to, 1, 5)
        quick = QHBoxLayout()
        for text, hours in (("1 ч", 1), ("24 ч", 24), ("7 дней", 168), ("30 дней", 720)):
            b = QPushButton(text)
            b.setMinimumWidth(84)
            b.clicked.connect(lambda _c, h=hours: self._events_quick(h))
            quick.addWidget(b)
        quick.addStretch()
        self.btn_events = QPushButton("📥 Загрузить")
        self.btn_events.setObjectName("btnPrimary")
        self.btn_events.clicked.connect(self.load_events)
        quick.addWidget(self.btn_events)
        fl.addLayout(quick, 2, 0, 1, 6)
        lay.addWidget(flt)
        # --- счётчики по тому, что в таблице (3.2.9: отдельной «сводки за сутки» больше нет — она путала:
        # её цифры считались за другой период и по другим уровням, чем показанные события)
        sl = QHBoxLayout()
        sl.setContentsMargins(0, 0, 0, 0)
        self.ev_tiles = QHBoxLayout()
        self.ev_tiles.setSpacing(8)
        sl.addLayout(self.ev_tiles)
        self.lbl_ev_top = QLabel("Счётчики появятся после загрузки — считаются ровно по событиям из таблицы.")
        self.lbl_ev_top.setObjectName("subtle")
        self.lbl_ev_top.setWordWrap(True)
        sl.addWidget(self.lbl_ev_top, 1)
        lay.addLayout(sl)
        # --- таблица
        self.tbl_events = QTableWidget(0, 6)
        self.tbl_events.setHorizontalHeaderLabels(["Время", "Уровень", "Журнал", "Источник", "ID", "Сообщение"])
        hh = self.tbl_events.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tbl_events.verticalHeader().setVisible(False)
        self.tbl_events.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tbl_events.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tbl_events.setWordWrap(False)
        lay.addWidget(self.tbl_events, 1)
        self.lbl_events = QLabel("Нажмите «Загрузить» — журнал читается с ПК по запросу, только чтение.")
        self.lbl_events.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        lay.addWidget(self.lbl_events)
        self.events_data: dict | None = None
        return w

    def _events_quick(self, hours: int):
        now = QDateTime.currentDateTime()
        self.ev_from.setDateTime(now.addSecs(-hours * 3600))
        self.ev_to.setDateTime(now)

    def event_filter(self) -> dict:
        """Текущий фильтр вкладки «Ошибки» (для теста и для запроса)."""
        levels = tuple(l for l, cb in self.ev_levels.items() if cb.isChecked())
        logs = {0: health.DEFAULT_LOGS, 1: ("System",), 2: ("Application",)}[self.ev_log.currentIndex()]
        return {"levels": levels, "logs": logs, "start": self.ev_from.dateTime().toPyDateTime(), "end": self.ev_to.dateTime().toPyDateTime()}

    def load_events(self):
        f = self.event_filter()
        if not f["levels"]:
            self.lbl_events.setText("⚠️ Выберите хотя бы один уровень.")
            return
        if f["start"] >= f["end"]:
            self.lbl_events.setText("⚠️ Дата «с» должна быть раньше даты «по».")
            return
        # запрос ровно по фильтру пользователя: лимит в 500 событий не тратится на то, чего он не просил
        self.btn_events.setEnabled(False)
        self.lbl_events.setText("⏳ Читаю журнал через Get-WinEvent…")
        run_in_background(self, lambda: health.get_events(self.comp, f["start"], f["end"], f["levels"], f["logs"]),
                          lambda d: self.show_events(d, f), lambda m: self.show_events({"error": m}, f))

    def show_events(self, d: dict, flt: dict | None = None):
        """Отрисовать события. ``flt`` — фильтр пользователя; таблица и счётчики считаются по одному и тому же набору."""
        self.btn_events.setEnabled(True)
        self.events_data = d
        pal = app_palette()
        _clear(self.ev_tiles)
        self.tbl_events.setRowCount(0)
        if "error" in d:
            self.lbl_events.setText(f"⚠️ {d['error']}")
            self.lbl_ev_top.setText("Счётчики недоступны — журнал не прочитан.")
            return
        flt = flt or self.event_filter()
        want = set(flt["levels"])
        lo, hi = flt["start"].strftime("%Y-%m-%d %H:%M"), flt["end"].strftime("%Y-%m-%d %H:%M")
        shown = [e for e in d["events"] if e["level"] in want and lo <= e["time"][:16] <= hi]
        self.tbl_events.setRowCount(len(shown))
        for r, e in enumerate(shown):
            cells = [e["time"], f"{EVENT_ICON[e['level']]} {e['level_text']}", e["log"], e["source"], str(e["id"]),
                     e["msg"].split("\n")[0][:300]]
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                if e["level"] <= 2:
                    it.setForeground(QColor(pal.danger[0]))
                elif e["level"] == 3:
                    it.setForeground(QColor(pal.warning[0]))
                if c == 5:
                    it.setToolTip(e["msg"][:2000])
                self.tbl_events.setItem(r, c, it)
        fit_columns(self.tbl_events, max_width=260, stretch_last=True)
        # --- счётчики по показанным событиям: те же строки, что в таблице, разложенные по уровням
        sm = health.summarize_events(shown)
        self.ev_tiles.addWidget(_tile("КРИТИЧЕСКИХ", str(sm["critical"]), kind="danger" if sm["critical"] else "", compact=True))
        self.ev_tiles.addWidget(_tile("ОШИБОК", str(sm["error"]), kind="danger" if sm["error"] else "", compact=True))
        self.ev_tiles.addWidget(_tile("ПРЕДУПРЕЖДЕНИЙ", str(sm["warning"]), compact=True))
        period = f"{flt['start']:%d.%m %H:%M} — {flt['end']:%d.%m %H:%M}"
        if shown:
            top_src = ", ".join(f"{safe_txt(src)} ({n})" for src, n in sm["by_source"][:3])
            self.lbl_ev_top.setText(f"По {len(shown)} событиям в таблице за {period}." +
                                    (f" Чаще всего в критических и ошибках: {top_src}." if top_src else ""))
        else:
            self.lbl_ev_top.setText(f"✅ За {period} событий выбранных уровней нет.")
        note = f" · {len(d['errors'])} журнал(ов) не прочитано" if d.get("errors") else ""
        self.lbl_events.setText(f"Показано {len(shown)} из {d['summary']['total']} загруженных событий (лимит 500){note}")

    # ------------------------------------------------------------------ загрузка
    def load(self):
        self.lbl_os.setText("⏳ Опрашиваю через PowerShell/CIM…")
        self.btn_refresh.setEnabled(False)
        run_in_background(self, lambda: health.get_health(self.comp), self.show_health,
                          lambda m: self.show_health({"error": m}))

    def _set_verdict(self, badge):
        """Бейдж вердикта в шапке. Раскладку применяем сразу, чтобы новый QLabel не мелькал
        в размере по умолчанию (640×480) серым прямоугольником поверх карточки."""
        self.verdict_box.addWidget(badge)
        self._head_layout.activate()
        badge.show()

    def show_health(self, h: dict):
        """Отрисовать результат (вызывается и из фонового потока, и напрямую — для тестов/демо)."""
        self.btn_refresh.setEnabled(True)
        self.health_data = h
        _clear(self.verdict_box)
        pal = app_palette()
        if "error" in h:
            self.lbl_os.setText(f"⚠️ {h['error']}")
            self._set_verdict(make_badge("⚪ Нет данных", "neutral", pal))
            self.lbl_warn.setText(h["error"])
            return
        self.lbl_os.setText(h.get("os") or "Windows")
        score = h.get("score", "good")
        self._set_verdict(make_badge(f"{VERDICT_ICON[score]} {health.VERDICT_LABEL[score]}", VERDICT_KIND[score], pal))
        _clear(self.tiles)
        cpu = h.get("cpu")
        tiles = [("АПТАЙМ", h["uptime"], f"загружен {h['boot']}"),
                 ("ОЗУ", f"{h['ram_used_pct']}%", f"из {h['ram_total_gb']} ГБ"),
                 ("CPU", f"{cpu}%" if cpu is not None else "—", "средняя загрузка ядер"),
                 ("ДИСКИ", f"{len(h.get('phys') or [])} физ. / {len(h['disks'])} разд.",
                  ", ".join(f"{p['media']} {p['size_gb']} ГБ" for p in (h.get("phys") or [])[:3]) or "нет данных S.M.A.R.T.")]
        for i, (t, v, s) in enumerate(tiles):
            self.tiles.addWidget(_tile(t, v, s), 0, i)
        _clear(self.parts)
        for r, d in enumerate(h["disks"]):
            name = QLabel(f"<b>{d['id']}</b> {d.get('label') or ''}")
            self.parts.addWidget(name, r, 0)
            self.parts.addWidget(QLabel(f"{d['free_gb']} ГБ свободно из {d['total_gb']} ГБ"), r, 1)
            self.parts.addWidget(_bar(100 - d["free_pct"], warn=85, danger=90), r, 2)
            self.parts.addWidget(make_badge("мало места", "offline", pal) if d["low"] else make_badge("ок", "online", pal), r, 3)
        self.cb_drive.set_drives(h["disks"] or [{"id": "C:"}])   # все тома ПК с подсказкой «сколько свободно» — карта по любому
        self.lbl_warn.setText("\n".join(f"• {x}" for x in h["warnings"]) if h["warnings"] else "✅ Замечаний нет")
        self._fill_disks(h.get("phys") or [])
        self.lbl_status.setText(f"Опрошено: {self.comp} · {len(h['warnings'])} замечаний")
        try:
            db.log_action(self.app.admin_name, "health", self.comp, "; ".join(h.get("warnings", [])) or "ок")
        except Exception as exc:  # noqa: BLE001
            log.debug("audit: %s", exc)

    def _fill_disks(self, phys: list[dict]):
        _clear(self.disk_list)
        self.disk_cards: list[QPushButton] = []
        pal = app_palette()
        if not phys:
            lbl = QLabel("Нет данных о физических дисках (WinRM/CIM недоступен или нет прав на root/wmi).")
            lbl.setWordWrap(True)
            self.disk_list.addWidget(lbl)
            self.disk_list.addStretch()
            return
        for p in phys:
            _fg, _bg, bd = pal.badge(VERDICT_KIND[p["verdict"]])
            b = QPushButton()
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ text-align: left; padding: 0; border: 1.5px solid {pal.border}; background: {pal.card}; }}"
                            f"QPushButton:hover {{ border: 1.5px solid {pal.title_accent}; }}"
                            f"QPushButton:checked {{ border: 2px solid {bd}; background: {pal.hover}; }}")
            inner = QVBoxLayout(b)
            inner.setContentsMargins(10, 8, 10, 8)
            inner.setSpacing(4)
            row = QHBoxLayout()
            title = QLabel(f"<b>{p['model']}</b>")
            title.setWordWrap(True)
            title.setStyleSheet("background: transparent; border: none;")
            row.addWidget(title, 1)
            row.setAlignment(Qt.AlignmentFlag.AlignTop)
            row.addWidget(make_badge(f"{VERDICT_ICON[p['verdict']]} {health.VERDICT_LABEL[p['verdict']]}", VERDICT_KIND[p["verdict"]], pal))
            inner.addLayout(row)
            meta = " · ".join(x for x in (p["media"], p["bus"], f"{p['size_gb']} ГБ", p["fw"]) if x and x != "?")
            m = QLabel(meta)
            m.setStyleSheet(f"color: {pal.subtext}; background: transparent; border: none; font-size: 9pt;")
            inner.addWidget(m)
            chips = QGridLayout()
            chips.setContentsMargins(0, 2, 0, 0)
            chips.setHorizontalSpacing(6)
            chips.setVerticalSpacing(4)
            for i, (text, kind) in enumerate(self._disk_chips(p)):
                chips.addWidget(make_badge(text, kind, pal), i // 2, i % 2, Qt.AlignmentFlag.AlignLeft)
            chips.setColumnStretch(2, 1)
            inner.addLayout(chips)
            b.setFixedHeight(inner.sizeHint().height() + 6)
            b.clicked.connect(lambda _, pp=p, bb=b: self._select_disk(pp, bb))
            self.disk_list.addWidget(b)
            self.disk_cards.append(b)
        self.disk_list.addStretch()
        self._select_disk(phys[0], self.disk_cards[0])

    @staticmethod
    def _disk_chips(p: dict) -> list[tuple[str, str]]:
        chips = []
        if p.get("temp") is not None:
            chips.append((f"🌡 {p['temp']} °C", "offline" if p["temp"] >= health.HOT_DISK_C + 10 else "warning" if p["temp"] >= health.HOT_DISK_C else "online"))
        if p.get("hours") is not None:
            chips.append((f"⏳ {health.fmt_hours(p['hours'])}", "warning" if p["hours"] >= health.HOURS_WARN else "neutral"))
        if p.get("wear") is not None:
            chips.append((f"📉 износ {p['wear']}%", "offline" if p["wear"] >= 95 else "warning" if p["wear"] >= health.SSD_WEAR_WARN else "online"))
        if p.get("serial"):
            chips.append((f"S/N {p['serial'][-8:]}", "neutral"))
        return chips

    def _select_disk(self, p: dict, btn: QPushButton):
        for b in self.disk_cards:
            b.setChecked(b is btn)
        pal = app_palette()
        self.lbl_disk_head.setText(f"{VERDICT_ICON[p['verdict']]} {p['model']} — {health.VERDICT_LABEL[p['verdict']]}")
        reasons = p.get("reasons") or []
        self.lbl_disk_reasons.setText("; ".join(reasons) if reasons else
                                      ("Критичных атрибутов нет." if p["attrs"] or p.get("health") is not None else "Атрибуты S.M.A.R.T. не отданы контроллером (NVMe/RAID/USB)."))
        self.lbl_disk_reasons.setStyleSheet(f"color: {pal.danger[0] if p['verdict'] == 'bad' else pal.warning[0] if reasons else pal.subtext};")
        rows = p["attrs"]  # порядок — по ID, как в CrystalDiskInfo
        self.attr_table.setRowCount(len(rows))
        for r, a in enumerate(rows):
            bad = a["critical"] and a["raw"] > 0
            cells = [QTableWidgetItem(f"{a['id']:02X}"), QTableWidgetItem(("⚠ " if bad else "") + a["name"]),
                     QTableWidgetItem(str(a["current"])), QTableWidgetItem(str(a["worst"])), QTableWidgetItem(str(a["raw"]))]
            for c, it in enumerate(cells):
                if bad:
                    it.setForeground(QColor(pal.danger[0]))
                if c >= 2:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.attr_table.setItem(r, c, it)
        fit_columns(self.attr_table, wrap=False, stretch_last=False)
        self.attr_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    # ------------------------------------------------------------------ карта диска
    def load_usage(self):
        drive = self.cb_drive.currentText() or "C:"
        self.btn_usage.setEnabled(False)
        self.lbl_usage.setText(f"⏳ Обхожу \\\\{self.comp}\\{drive.rstrip(':')}$ — это может занять несколько минут…")
        run_in_background(self, lambda: health.get_disk_usage(self.comp, drive, self.sp_top.value()), self.show_usage,
                          lambda m: self.show_usage({"error": m}))

    def show_usage(self, u: dict):
        self.btn_usage.setEnabled(True)
        self.usage_data = u
        if "error" in u:
            self.lbl_usage.setText(f"⚠️ {u['error']}")
            return
        self.treemap.set_items(u["dirs"], u["total"])
        errs = f" · недоступно папок: {u['errors']}" if u.get("errors") else ""
        self.lbl_usage.setText(f"✅ {u['root']}: {health.fmt_size(u['total'])} в {u['total_files']} файлах{errs}. Клик по прямоугольнику — путь в буфер.")
        self._fill(self.tbl_dirs, [(d["name"], d["size"], f"{d['pct']}%", d["files"]) for d in u["dirs"]], sizes=(1,), paths=[d["path"] for d in u["dirs"]])
        self._fill(self.tbl_files, [(f["path"], f["size"]) for f in u["files"]], sizes=(1,))
        self.show_hogs(u)
        self._fill(self.tbl_users, [(x["name"], x["size"], "") for x in u["users"]], sizes=(1,), paths=[x["path"] for x in u["users"]])
        try:
            db.log_action(self.app.admin_name, "disk_usage", self.comp, f"{u['root']}: {health.fmt_size(u['total'])}")
        except Exception as exc:  # noqa: BLE001
            log.debug("audit: %s", exc)

    def show_hogs(self, u: dict):
        """«Почистить» по данным карты того же тома: только реально найденные пути, суммы сходятся с картой."""
        hogs = u.get("hogs") or []
        self._fill(self.tbl_hogs, [(h["label"], h["size"], h.get("files") or "") for h in hogs], sizes=(1,), paths=[h["path"] for h in hogs])
        total = u.get("hogs_total", sum(h["size"] for h in hogs))
        drive = u.get("drive") or u.get("root") or ""
        host = getattr(self, "computer", "") or "ПК"
        if not hogs:
            self.lbl_hogs.setText(f"✅ На {host} (том {drive}): известных временных файлов (корзина, Temp, кэши) на этом томе нет.")
            return
        share = f" ({total / u['total'] * 100:.1f}% от занятого на томе)" if u.get("total") else ""
        self.lbl_hogs.setText(f"🧹 На {host} (том {drive}): обнаружено временных файлов и данных корзины до {health.fmt_size(total)}{share}. "
                              "Клик по строке копирует путь в буфер обмена.")

    @staticmethod
    def _fill(table: QTableWidget, rows, sizes=(), paths=None):
        table.setSortingEnabled(False)
        table.horizontalHeader().setSortIndicator(sizes[0] if sizes else 0, Qt.SortOrder.DescendingOrder)
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            for c, v in enumerate(row):
                it = SizeItem() if c in sizes else QTableWidgetItem()
                if c in sizes:
                    it.setData(Qt.ItemDataRole.DisplayRole, health.fmt_size(v))
                    it.setData(Qt.ItemDataRole.UserRole, int(v))
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    it.setText(str(v))
                if paths and c == 0:
                    it.setToolTip(paths[r])
                table.setItem(r, c, it)
        stretch_col = 0 if table.columnCount() != 3 else 1
        fit_columns(table, max_width=360, wrap=False, stretch_last=False)
        table.horizontalHeader().setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
        table.setSortingEnabled(True)

    def _copy_hog_path(self, row: int, _col: int):
        it = self.tbl_hogs.item(row, 0)
        if it is not None and it.toolTip():
            self._copy_path({"path": it.toolTip()})

    def _copy_path(self, item: dict):
        QApplication.clipboard().setText(item["path"])
        self.lbl_status.setText(f"📋 Скопировано: {item['path']}")

    def _tile_clicked(self, item: dict):
        """Плитка на карте → та же папка выделяется в таблице «Папки» (и путь — в буфер)."""
        self._copy_path(item)
        self.usage_tabs.setCurrentIndex(0)
        for r in range(self.tbl_dirs.rowCount()):
            it = self.tbl_dirs.item(r, 0)
            if it is not None and it.toolTip() == item["path"]:
                self.tbl_dirs.selectRow(r)
                self.tbl_dirs.scrollToItem(it)
                break

    def _open_dir_row(self, it: QTableWidgetItem):
        path = self.tbl_dirs.item(it.row(), 0).toolTip() if it is not None else ""
        if not path:
            return
        try:
            open_in_explorer(path)
            self.lbl_status.setText(f"📂 Открыто в Проводнике: {path}")
        except OSError as exc:
            self.lbl_status.setText(f"⚠️ Не удалось открыть {path}: {exc}")

    # ------------------------------------------------------------------ отчёт
    def copy_report(self):
        if not self.health_data:
            return
        text = f"🩺 {self.comp}\n" + health.format_health(self.health_data)
        u = self.usage_data
        if u and "error" not in u:
            text += f"\n\n🗺️ {u['root']}: {health.fmt_size(u['total'])}\n" + "\n".join(
                f"  {d['name']:<28} {health.fmt_size(d['size']):>10}  {d['pct']}%" for d in u["dirs"][:15])
            if u["hogs"]:
                text += "\n🧹 " + "; ".join(f"{h['label']} {health.fmt_size(h['size'])}" for h in u["hogs"][:6])
        QApplication.clipboard().setText(text)
        self.lbl_status.setText("📋 Отчёт скопирован в буфер обмена")
