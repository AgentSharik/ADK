"""Свой выбор цвета в стиле приложения (вместо стандартного QColorDialog).

Состав окна: квадрат «насыщенность × яркость» с маркером, вертикальная полоса оттенка, палитра готовых цветов,
превью «было → стало», поле HEX и ползунки R/G/B. Всё рисуется вручную (QPainter), поэтому выглядит одинаково
в любой теме. Использование: ``ColorPickerDialog.get_color("#38bdf8", parent, "Акцентный цвет")`` → hex или None.
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QConicalGradient, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from .widgets import FramelessDialog, app_palette

PRESETS = [
    "#007AFF", "#34C759", "#5856D6", "#FF9500", "#FF2D55", "#AF52DE", "#FF3B30", "#5AC8FA", "#FFCC00", "#8E8E93",
    "#0A84FF", "#30D158", "#1C1C1E", "#2C2C2E", "#1A1E1B", "#1E1B22", "#201E1C", "#211C1E", "#3A3A3C", "#48484A",
    "#F2F2F7", "#F7F2EC", "#EFF5F1", "#FFFFFF",
]


def _hsv(color: QColor) -> tuple[float, float, float]:
    h, s, v, _a = color.getHsvF()
    return (0.0 if h < 0 else h), s, v


class SVSquare(QWidget):
    """Квадрат: по горизонтали насыщенность, по вертикали яркость; оттенок задаётся снаружи."""

    changed = pyqtSignal(float, float)   # s, v

    def __init__(self, parent=None):
        super().__init__(parent)
        self.h, self.s, self.v = 0.55, 0.8, 0.9
        self.setMinimumSize(220, 180)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_hsv(self, h: float, s: float, v: float):
        self.h, self.s, self.v = h, s, v
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        base = QColor.fromHsvF(self.h, 1.0, 1.0)
        g1 = QLinearGradient(r.topLeft(), r.topRight())
        g1.setColorAt(0, QColor("#ffffff"))
        g1.setColorAt(1, base)
        g2 = QLinearGradient(r.topLeft(), r.bottomLeft())
        g2.setColorAt(0, QColor(0, 0, 0, 0))
        g2.setColorAt(1, QColor(0, 0, 0, 255))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g1)
        p.drawRoundedRect(r, 8, 8)
        p.setBrush(g2)
        p.drawRoundedRect(r, 8, 8)
        p.setPen(QPen(QColor(app_palette().border), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 8, 8)
        # маркер
        x = r.left() + self.s * r.width()
        y = r.top() + (1 - self.v) * r.height()
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.drawEllipse(QPointF(x, y), 7, 7)
        p.setPen(QPen(QColor("#0f172a"), 1.2))
        p.drawEllipse(QPointF(x, y), 8.5, 8.5)
        p.end()

    def _pick(self, pos):
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        s = min(1.0, max(0.0, (pos.x() - r.left()) / max(1.0, r.width())))
        v = 1 - min(1.0, max(0.0, (pos.y() - r.top()) / max(1.0, r.height())))
        self.s, self.v = s, v
        self.update()
        self.changed.emit(s, v)

    def mousePressEvent(self, e):  # noqa: N802
        self._pick(e.position())

    def mouseMoveEvent(self, e):  # noqa: N802
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())


class HueBar(QWidget):
    """Вертикальная полоса оттенка 0…360°."""

    changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.h = 0.55
        self.setFixedWidth(26)
        self.setMinimumHeight(180)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_hue(self, h: float):
        self.h = h
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(4, 1, -4, -1)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0 if i < 6 else 0.999, 1, 1))
        p.setPen(QPen(QColor(app_palette().border), 1.5))
        p.setBrush(g)
        p.drawRoundedRect(r, 6, 6)
        y = r.top() + self.h * r.height()
        p.setPen(QPen(QColor("#ffffff"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(1, y - 4, self.width() - 2, 8), 3, 3)
        p.setPen(QPen(QColor("#0f172a"), 1))
        p.drawRoundedRect(QRectF(0, y - 5, self.width(), 10), 4, 4)
        p.end()

    def _pick(self, pos):
        r = QRectF(self.rect()).adjusted(4, 1, -4, -1)
        self.h = min(0.999, max(0.0, (pos.y() - r.top()) / max(1.0, r.height())))
        self.update()
        self.changed.emit(self.h)

    def mousePressEvent(self, e):  # noqa: N802
        self._pick(e.position())

    def mouseMoveEvent(self, e):  # noqa: N802
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())


class ColorWheelIcon(QWidget):
    """Маленькое «колесо» рядом с заголовком — просто украшение, но живое: показывает текущий оттенок."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.h = 0.55
        self.setFixedSize(22, 22)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QConicalGradient(QPointF(11, 11), 0)
        for i in range(7):
            g.setColorAt(i / 6, QColor.fromHsvF((i / 6) % 1.0 if i < 6 else 0.999, 1, 1))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(11, 11), 10, 10)
        p.setBrush(QColor.fromHsvF(self.h, 1, 1))
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawEllipse(QPointF(11, 11), 4.5, 4.5)
        p.end()


class ColorPickerDialog(FramelessDialog):
    """Окно выбора цвета. ``color()`` → hex после ``accept()``."""

    def __init__(self, initial: str = "#38bdf8", parent=None, title: str = "Выбор цвета"):
        super().__init__(f"🎨 {title}", parent, (640, 430))
        self.initial = QColor(initial) if QColor(initial).isValid() else QColor("#38bdf8")
        self._color = QColor(self.initial)
        self._updating = False
        pal = app_palette()

        top = QHBoxLayout()
        top.setSpacing(14)
        # левая часть: квадрат + полоса оттенка
        self.sv = SVSquare()
        self.hue = HueBar()
        self.sv.changed.connect(self._from_sv)
        self.hue.changed.connect(self._from_hue)
        top.addWidget(self.sv, 1)
        top.addWidget(self.hue)

        # правая часть: превью, hex, RGB, палитра
        right = QVBoxLayout()
        right.setSpacing(8)
        prev = QHBoxLayout()
        prev.setSpacing(0)
        self.lbl_old = QLabel("было")
        self.lbl_new = QLabel("стало")
        for lb in (self.lbl_old, self.lbl_new):
            lb.setFixedSize(104, 50)
            lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        prev.addWidget(self.lbl_old)
        prev.addWidget(self.lbl_new)
        prev.addStretch()
        self.wheel = ColorWheelIcon()
        prev.addWidget(self.wheel)
        right.addLayout(prev)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.addWidget(QLabel("HEX"), 0, 0)
        self.hex = QLineEdit()
        self.hex.setMaxLength(7)
        self.hex.setPlaceholderText("#rrggbb")
        self.hex.textEdited.connect(self._from_hex)
        form.addWidget(self.hex, 0, 1, 1, 2)
        self.sliders: dict[str, QSlider] = {}
        self.slider_vals: dict[str, QLabel] = {}
        for i, (ch, name) in enumerate((("r", "R"), ("g", "G"), ("b", "B")), start=1):
            form.addWidget(QLabel(name), i, 0)
            sl = QSlider(Qt.Orientation.Horizontal)
            sl.setRange(0, 255)
            sl.valueChanged.connect(self._from_sliders)
            val = QLabel("0")
            val.setFixedWidth(30)
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.addWidget(sl, i, 1)
            form.addWidget(val, i, 2)
            self.sliders[ch] = sl
            self.slider_vals[ch] = val
        right.addLayout(form)

        cap = QLabel("Готовые цвета")
        cap.setObjectName("subtle")
        right.addWidget(cap)
        grid = QGridLayout()
        grid.setSpacing(6)
        self.preset_btns: list[QPushButton] = []
        for i, c in enumerate(PRESETS):
            b = QPushButton()
            b.setFixedSize(24, 24)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(c)
            b.setProperty("hex", c)
            b.setStyleSheet(f"QPushButton {{ background-color: {c}; border: 1.5px solid {pal.border}; border-radius: 6px; }}"
                            f"QPushButton:hover {{ border: 2px solid {pal.text}; }}")
            b.clicked.connect(lambda _c, hx=c: self.set_color(hx))
            grid.addWidget(b, i // 8, i % 8)
            self.preset_btns.append(b)
        right.addLayout(grid)
        right.addStretch()
        top.addLayout(right)
        self.body.addLayout(top, 1)

        btns = QHBoxLayout()
        self.lbl_info = QLabel("")
        self.lbl_info.setObjectName("subtle")
        btns.addWidget(self.lbl_info)
        btns.addStretch()
        self.btn_reset = QPushButton("↺ Как было")
        self.btn_reset.clicked.connect(lambda: self.set_color(self.initial.name()))
        self.btn_cancel = QPushButton("Отмена")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("✔ Выбрать")
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self.accept)
        btns.addWidget(self.btn_reset)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_ok)
        self.body.addLayout(btns)
        self.set_color(self.initial.name())

    # ------------------------------------------------------------------ состояние
    def color(self) -> str:
        return self._color.name()

    def set_color(self, hx: str):
        c = QColor(hx)
        if not c.isValid():
            return
        self._color = c
        h, s, v = _hsv(c)
        self.sv.set_hsv(h, s, v)
        self.hue.set_hue(h)
        self._sync(from_hex=True)

    def _sync(self, from_hex: bool = False, from_sliders: bool = False):
        """Обновить все представления цвета, не зацикливаясь на сигналах."""
        if self._updating:
            return
        self._updating = True
        try:
            c = self._color
            if not from_hex or self.hex.text().lower() != c.name():
                self.hex.setText(c.name())
            if not from_sliders:
                for ch, val in (("r", c.red()), ("g", c.green()), ("b", c.blue())):
                    self.sliders[ch].setValue(val)
            for ch, val in (("r", c.red()), ("g", c.green()), ("b", c.blue())):
                self.slider_vals[ch].setText(str(val))
            self.wheel.h = self.sv.h
            self.wheel.update()
            ink_new = "#0f172a" if c.lightnessF() > 0.55 else "#f8fafc"
            ink_old = "#0f172a" if self.initial.lightnessF() > 0.55 else "#f8fafc"
            pal = app_palette()
            # обе половинки превью одного размера и одного кегля: слева «было · #HEX», справа «стало · #HEX»
            self.lbl_old.setStyleSheet(f"background-color: {self.initial.name()}; color: {ink_old}; border: 1.5px solid {pal.border};"
                                       f"border-right: none; border-top-left-radius: 8px; border-bottom-left-radius: 8px; font-weight: bold;")
            self.lbl_new.setStyleSheet(f"background-color: {c.name()}; color: {ink_new}; border: 1.5px solid {pal.border};"
                                       f"border-top-right-radius: 8px; border-bottom-right-radius: 8px; font-weight: bold;")
            self.lbl_old.setText(f"было\n{self.initial.name().upper()}")
            self.lbl_new.setText(f"стало\n{c.name().upper()}")
            h, s, v = _hsv(c)
            self.lbl_info.setText(f"H {round(h * 360)}°  S {round(s * 100)}%  V {round(v * 100)}%  ·  "
                                  f"{'тёмный' if c.lightnessF() < 0.5 else 'светлый'}")
            for b in self.preset_btns:
                on = b.property("hex").lower() == c.name().lower()
                b.setStyleSheet(f"QPushButton {{ background-color: {b.property('hex')}; border-radius: 6px; "
                                f"border: {'3px solid ' + pal.title_accent if on else '1.5px solid ' + pal.border}; }}"
                                f"QPushButton:hover {{ border: 2px solid {pal.text}; }}")
        finally:
            self._updating = False

    # ------------------------------------------------------------------ источники изменений
    def _from_sv(self, s: float, v: float):
        self._color = QColor.fromHsvF(self.sv.h, s, v)
        self._sync()

    def _from_hue(self, h: float):
        self.sv.set_hsv(h, self.sv.s, self.sv.v)
        self._color = QColor.fromHsvF(h, self.sv.s, self.sv.v)
        self._sync()

    def _from_hex(self, text: str):
        t = text.strip()
        if not t.startswith("#"):
            t = "#" + t
        c = QColor(t)
        if len(t) == 7 and c.isValid():
            self._color = c
            h, s, v = _hsv(c)
            self.sv.set_hsv(h, s, v)
            self.hue.set_hue(h)
            self._sync(from_hex=True)

    def _from_sliders(self, *_):
        if self._updating:
            return
        c = QColor(self.sliders["r"].value(), self.sliders["g"].value(), self.sliders["b"].value())
        self._color = c
        h, s, v = _hsv(c)
        self.sv.set_hsv(h, s, v)
        self.hue.set_hue(h)
        self._sync(from_sliders=True)

    # ------------------------------------------------------------------ удобный вызов
    @classmethod
    def get_color(cls, initial: str, parent=None, title: str = "Выбор цвета") -> str | None:
        dlg = cls(initial, parent, title)
        return dlg.color() if dlg.exec() else None
