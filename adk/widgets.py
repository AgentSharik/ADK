"""Базовые UI-компоненты: безрамочные окна с перетаскиванием и ресайзом, заголовок, диалоги."""
from __future__ import annotations

import html
import os
import sys
from typing import Callable

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, QtMsgType, pyqtSignal, qInstallMessageHandler
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QMenu, QLayout, QLineEdit, QMainWindow, QPushButton, QSizeGrip,
    QStyle, QVBoxLayout, QWidget,
)

from .theme import Palette

_EDGE = 8


def _silence_qt_warnings() -> None:
    """Подавляет платформенные предупреждения Qt в offscreen/headless-режиме (propagateSizeHints, raise, grabKeyboard)."""
    try:
        def _handler(msg_type, context, message):
            if "This plugin does not support" in message or "propagateSizeHints" in message:
                return
            if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                sys.stderr.write(f"{message}\n")

        qInstallMessageHandler(_handler)
    except Exception:
        pass


_silence_qt_warnings()


class EdgeResizeFilter(QObject):
    """Изменение размера безрамочного окна за края (Qt ≥ 5.15: startSystemResize)."""

    def __init__(self, window: QWidget):
        super().__init__(window)
        self.win = window
        window.setMouseTracking(True)
        window.installEventFilter(self)

    def _edges(self, pos: QPoint) -> Qt.Edge:
        r: QRect = self.win.rect()
        e = Qt.Edge(0)
        if pos.x() <= _EDGE:
            e |= Qt.Edge.LeftEdge
        if pos.x() >= r.width() - _EDGE:
            e |= Qt.Edge.RightEdge
        if pos.y() <= _EDGE:
            e |= Qt.Edge.TopEdge
        if pos.y() >= r.height() - _EDGE:
            e |= Qt.Edge.BottomEdge
        return e

    def eventFilter(self, obj, event):  # noqa: N802
        try:
            if obj is self.win and not self.win.isMaximized():
                if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                    edges = self._edges(event.position().toPoint())
                    handle = self.win.windowHandle()
                    if edges and handle is not None:
                        handle.startSystemResize(edges)
                        return True
                elif event.type() == QEvent.Type.MouseMove:
                    edges = self._edges(event.position().toPoint())
                    self.win.setCursor(_cursor_for(edges))
        except Exception:  # noqa: BLE001 — исключение в eventFilter роняет процесс
            pass
        return super().eventFilter(obj, event)


def _cursor_for(edges: Qt.Edge) -> Qt.CursorShape:
    lr = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
    tb = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
    if lr and tb:
        tl = bool(edges & Qt.Edge.LeftEdge) == bool(edges & Qt.Edge.TopEdge)
        return Qt.CursorShape.SizeFDiagCursor if tl else Qt.CursorShape.SizeBDiagCursor
    if lr:
        return Qt.CursorShape.SizeHorCursor
    if tb:
        return Qt.CursorShape.SizeVerCursor
    return Qt.CursorShape.ArrowCursor


class TitleBar(QWidget):
    def __init__(self, window: QWidget, title: str, is_main: bool = False):
        super().__init__(window)
        self.win = window
        self.is_main = is_main
        self._drag: QPoint | None = None
        self.setObjectName("titleBar")
        self.setFixedHeight(60 if is_main else 34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 6, 0)
        lay.setSpacing(6)
        # В главном окне заголовок слева не дублируем — бренд один, справа (размеры сняты с концепта: эмблема 34 px, шрифт 18 pt).
        self.label = QLabel("" if is_main else title)
        self.label.setObjectName("titleLabel")
        lay.addWidget(self.label)
        lay.addStretch()
        style = self.style()
        if is_main:
            from .tray import asset_path
            logo_path = asset_path("logo.png")
            if os.path.exists(logo_path):
                logo = QLabel()
                logo.setObjectName("brandLogo")
                logo.setPixmap(QPixmap(logo_path).scaled(44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                                                         Qt.TransformationMode.SmoothTransformation))
                lay.addWidget(logo)
            brand = QLabel("ADK")
            brand.setObjectName("brandLabel")
            brand.setToolTip("ADK — Active Directory Kit")
            lay.addWidget(brand)
            lay.addSpacing(16)
            # системные иконки стиля вместо символов 🗕🗖 — они есть в любом шрифте/ОС, квадратиков не будет
            self.btn_min = QPushButton()
            self.btn_min.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton))
            self.btn_min.setToolTip("Свернуть")
            self.btn_min.clicked.connect(window.showMinimized)
            self.btn_max = QPushButton()
            self.btn_max.setToolTip("Развернуть / восстановить (двойной клик по шапке, F11 — во весь экран)")
            self.btn_max.clicked.connect(self.toggle_max)
            for b in (self.btn_min, self.btn_max):
                b.setObjectName("winBtn")
                b.setFixedSize(34, 28)
                lay.addWidget(b)
            self._sync_max_icon()
        close = QPushButton()
        close.setIcon(style.standardIcon(QStyle.StandardPixmap.SP_TitleBarCloseButton))
        close.setObjectName("btnClose")
        close.setProperty("class", "winBtn")
        close.setFixedSize(34, 28)
        close.setToolTip("Закрыть")
        close.clicked.connect(window.close)
        lay.addWidget(close)

    # ------------------------------------------------------------------ окно: развернуть / восстановить / во весь экран
    def _sync_max_icon(self):
        if not self.is_main:
            return
        maximized = self.win.isMaximized() or self.win.isFullScreen()
        self.btn_max.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_TitleBarNormalButton if maximized else QStyle.StandardPixmap.SP_TitleBarMaxButton))

    def toggle_max(self):
        if self.win.isFullScreen() or self.win.isMaximized():
            self.win.showNormal()
        else:
            self.win.showMaximized()
        self._sync_max_icon()

    def toggle_fullscreen(self):
        self.win.showNormal() if self.win.isFullScreen() else self.win.showFullScreen()
        self._sync_max_icon()

    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            handle = self.win.windowHandle()
            if handle is not None and not self.win.isMaximized() and handle.startSystemMove():
                return
            self._drag = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag is not None and e.buttons() == Qt.MouseButton.LeftButton:
            if self.win.isMaximized():
                self.win.showNormal()
            self.win.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag = None

    def mouseDoubleClickEvent(self, e):  # noqa: N802
        if self.is_main:
            self.toggle_max()


class FramelessDialog(QDialog):
    """Безрамочный диалог. ``on_dialog_done`` вызывается при любом способе закрытия."""

    def __init__(self, title: str, parent=None, size: tuple[int, int] = (480, 240)):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.resize(*size)
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(10, 6, 10, 10)
        self._root.setSpacing(8)
        self.title_bar = TitleBar(self, title)
        self._root.addWidget(self.title_bar)
        self.body = QVBoxLayout()
        self._root.addLayout(self.body, 1)
        grip_row = QHBoxLayout()
        grip_row.addStretch()
        grip_row.addWidget(QSizeGrip(self))
        self._root.addLayout(grip_row)
        self._resizer = EdgeResizeFilter(self)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if getattr(self, "_placed", False):
            return
        self._placed = True
        self._place_below_parent_header()

    HEADER_H = 64   # высота шапки главного окна (эмблема ADK, кнопки окна) — диалог её не перекрывает, если помещается

    def _place_below_parent_header(self) -> None:
        """Qt центрирует диалог по родителю, и окно ложится прямо на шапку с эмблемой и кнопками «— ▢ ✕».
        Если высота позволяет, сдвигаем его вниз, под шапку; по горизонтали — по центру родителя."""
        parent = self.parentWidget().window() if self.parentWidget() is not None else None
        if parent is None or not parent.isVisible():
            return
        pg = parent.frameGeometry()
        screen = self.screen() or parent.screen()
        avail = screen.availableGeometry() if screen is not None else pg
        x = pg.x() + (pg.width() - self.width()) // 2
        y = pg.y() + (pg.height() - self.height()) // 2
        if self.height() <= pg.height() - self.HEADER_H:
            y = max(y, pg.y() + self.HEADER_H)
        x = max(avail.x(), min(x, avail.right() - self.width()))
        y = max(avail.y(), min(y, avail.bottom() - self.height()))
        self.move(x, y)

    def done(self, r: int) -> None:  # noqa: N802
        try:
            self.on_dialog_done()
            detach_background_workers(self)
        finally:
            super().done(r)

    def closeEvent(self, event):  # noqa: N802
        # close() у ещё не показанного диалога не проходит через done() — потоки всё равно нужно отцепить
        self.on_dialog_done()
        detach_background_workers(self)
        super().closeEvent(event)

    def on_dialog_done(self) -> None:
        """Переопределить: остановить потоки и т.п."""


class FramelessMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self._resizer = EdgeResizeFilter(self)
        self.title_bar: TitleBar | None = None

    def changeEvent(self, e):  # noqa: N802
        # Развернули/восстановили снаружи (Win+↑, Aero Snap, таскбар) — иконка кнопки в шапке следует за состоянием окна
        super().changeEvent(e)
        if e.type() == QEvent.Type.WindowStateChange and self.title_bar is not None:
            self.title_bar._sync_max_icon()

    def toggle_fullscreen(self):
        """F11: во весь экран без шапки ОС и обратно."""
        if self.title_bar is not None:
            self.title_bar.toggle_fullscreen()
        else:
            self.showNormal() if self.isFullScreen() else self.showFullScreen()


# --------------------------------------------------------------------------- диалоги
class MessageBox(FramelessDialog):
    OK, YES, NO = 1, 2, 3
    _ICONS = {"info": "ℹ️", "warning": "⚠️", "critical": "❌", "question": "❓"}

    def __init__(self, parent, title: str, text: str, kind: str = "info", yes_no: bool = False):
        super().__init__(f"{self._ICONS.get(kind, 'ℹ️')} {title}", parent, (460, 170))
        self.answer = self.NO
        row = QHBoxLayout()
        row.setContentsMargins(20, 10, 20, 10)
        from . import icons
        _name, _role = icons.EMOJI_ICON.get(self._ICONS.get(kind, "ℹ️").rstrip("\ufe0f"), ("info.circle", "info"))
        icon = QLabel()
        icon.setPixmap(icons.pixmap(_name, 36, role=_role))    # крупная контурная иконка, а не эмодзи-шрифт
        icon.setFixedSize(44, 44)
        icon.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        icon.setStyleSheet("border: none;")
        row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        lbl = QLabel(html.escape(text).replace("\n", "<br>"))
        lbl.setTextFormat(Qt.TextFormat.RichText)
        lbl.setWordWrap(True)
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl.setStyleSheet("font-size: 10.5pt; border: none;")
        row.addWidget(lbl, 1)
        self.body.addLayout(row)
        # высота под текст: длинное сообщение не должно обрезаться нижним рядом кнопок
        self._msg_lbl = lbl
        lbl.setMinimumHeight(lbl.heightForWidth(340))
        self.adjustSize()
        self.resize(max(460, self.width()), max(170, self.sizeHint().height()))
        btns = QHBoxLayout()
        btns.setContentsMargins(20, 0, 20, 0)
        btns.addStretch()
        if yes_no:
            for text_, name, val in (("Да", "btnSuccess", self.YES), ("Нет", "btnDanger", self.NO)):
                b = QPushButton(text_)
                b.setObjectName(name)
                b.setMinimumWidth(80)
                b.clicked.connect(lambda _, v=val: self._finish(v))
                btns.addWidget(b)
        else:
            b = QPushButton("ОК")
            b.setObjectName("btnPrimary")
            b.setMinimumWidth(100)
            b.clicked.connect(lambda: self._finish(self.OK))
            btns.addWidget(b)
        self.body.addLayout(btns)

    def _finish(self, val: int):
        self.answer = val
        self.accept() if val != self.NO else self.reject()

    @classmethod
    def _show(cls, parent, title, text, kind, yes_no=False) -> int:
        dlg = cls(parent, title, text, kind, yes_no)
        dlg.exec()
        ans = dlg.answer
        dlg.deleteLater()
        return ans

    @classmethod
    def information(cls, parent, title, text): return cls._show(parent, title, text, "info")
    @classmethod
    def warning(cls, parent, title, text): return cls._show(parent, title, text, "warning")
    @classmethod
    def critical(cls, parent, title, text): return cls._show(parent, title, text, "critical")
    @classmethod
    def question(cls, parent, title, text) -> bool:
        return cls._show(parent, title, text, "question", yes_no=True) == cls.YES


class InputDialog(FramelessDialog):
    def __init__(self, parent, title: str, label: str, password: bool = False):
        super().__init__(f"📝 {title}", parent, (420, 160))
        self.body.addWidget(QLabel(label))
        self.edit = QLineEdit()
        if password:
            self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.body.addWidget(self.edit)
        btns = QHBoxLayout()
        btns.addStretch()
        ok = QPushButton("ОК")
        ok.setObjectName("btnPrimary")
        ok.clicked.connect(self.accept)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        self.body.addLayout(btns)
        self.edit.returnPressed.connect(self.accept)

    @classmethod
    def get_text(cls, parent, title, label, password=False) -> tuple[str, bool]:
        dlg = cls(parent, title, label, password)
        ok = dlg.exec() == QDialog.DialogCode.Accepted
        text = dlg.edit.text()
        dlg.deleteLater()
        return text, ok


# --------------------------------------------------------------------------- мелочи
def make_badge(text: str, kind: str, palette: Palette) -> QLabel:
    fg, bg, bd = palette.badge(kind)
    lbl = QLabel(text)
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setStyleSheet(f"background-color: {bg}; color: {palette.text}; border: 1px solid {bd}; "
                      "font-weight: bold; font-size: 9pt; border-radius: 4px; padding: 4px 10px;")
    return lbl


class BadgeButton(QPushButton):
    """Кликабельный бейдж (принтер, группа…). ``kind`` — цвет из палитры."""

    def __init__(self, text: str, kind: str, palette: Palette, tooltip: str = "", parent=None):
        super().__init__(text, parent)
        fg, bg, bd = palette.badge(kind)
        # текст бейджа — основной цвет текста темы (семантический fg на полупрозрачном фоне читался плохо)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setStyleSheet(
            f"QPushButton {{ background-color: {bg}; color: {palette.text}; border: 1.5px solid {bd}; "
            "font-weight: 600; font-size: 9pt; border-radius: 10px; padding: 4px 12px; text-align: left; }"
            f"QPushButton:hover {{ border: 1.5px solid {palette.title_accent}; color: {palette.title_accent}; }}")


class FlowLayout(QLayout):
    """Раскладка «в строку с переносом» — для бейджей произвольной длины."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._sp = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        return self._do_layout(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        h = self._do_layout(rect, False)
        pw = self.parentWidget()
        if pw is not None and pw.minimumHeight() != h:  # контейнер растёт под число строк
            pw.setMinimumHeight(h)
            pw.updateGeometry()

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _do_layout(self, rect, test_only: bool) -> int:
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            w, h = it.sizeHint().width(), it.sizeHint().height()
            if x + w > rect.right() and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + self._sp, 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), it.sizeHint()))
            x += w + self._sp
            line_h = max(line_h, h)
        return y + line_h - rect.y()

    def clear(self):
        """Убирает все элементы сразу (deleteLater отложен — иначе старые бейджи «просвечивают» до следующего цикла)."""
        while self._items:
            it = self._items.pop()
            wdg = it.widget()
            if wdg is not None:
                wdg.hide()
                wdg.setParent(None)
                wdg.deleteLater()
        self.invalidate()


def bold_label(text: str) -> QLabel:
    lbl = QLabel(f"<b>{html.escape(text)}</b>")
    lbl.setTextFormat(Qt.TextFormat.RichText)
    return lbl


def safe_rich(label: str, value: str) -> str:
    """Rich-text «<b>label</b> value» с экранированием значения из AD."""
    return f"<b>{html.escape(label)}</b> {html.escape(str(value))}"


# Все фоновые потоки живут здесь до доставки finished в главный поток. Это единственный надёжный
# момент, когда QThread можно удалять: isRunning() становится False чуть РАНЬШЕ, чем поток
# окончательно завершится, и удаление объекта в этом окне роняет процесс (segfault без трейсбека).
_LIVE_WORKERS: set = set()


def run_in_background(parent, fn: Callable, on_done: Callable, on_error: Callable | None = None):
    """Запускает fn в FunctionWorker. Результат приходит в on_done в главном потоке.

    Поток создаётся БЕЗ Qt-родителя и удерживается глобальным реестром, а не окном: если окно
    закроют/соберёт GC раньше, чем поток закончит, поток спокойно доработает, его сигналы будут
    отцеплены (``detach_background_workers``), и только потом он удалится через deleteLater.
    """
    from .workers import FunctionWorker

    w = FunctionWorker(fn)
    w.done.connect(on_done)
    w.error.connect(on_error or (lambda m: MessageBox.critical(parent, "Ошибка", m)))
    workers = getattr(parent, "_bg_workers", None)
    if workers is None:
        workers = parent._bg_workers = []
    workers.append(w)
    _LIVE_WORKERS.add(w)

    def _finished():
        if w in workers:
            workers.remove(w)
        _LIVE_WORKERS.discard(w)
        w.deleteLater()

    w.finished.connect(_finished)
    w.start()
    return w


def detach_background_workers(owner) -> None:
    """Окно закрывается: отцепляем его обработчики от потоков, которые ещё не доставили результат."""
    workers = getattr(owner, "_bg_workers", None)
    if not workers:
        return
    for w in list(workers):
        for sig in (w.done, w.error):
            try:
                sig.disconnect()
            except (TypeError, RuntimeError):
                pass
    workers.clear()  # ссылку держит _LIVE_WORKERS до finished


def wait_background_workers(owner, timeout_ms: int = 3000) -> None:
    """Перед выходом из приложения: дождаться потоков окна (не бросать их на полпути)."""
    for w in list(getattr(owner, "_bg_workers", [])):
        try:
            if w.isRunning():
                w.cancel()
                w.wait(timeout_ms)
        except RuntimeError:
            pass


def app_palette() -> Palette:
    from .config import settings
    d = settings.design
    return Palette(bool(d["is_dark"]), d["accent_color"], d.get("panel_color") or "", d.get("text_color") or "",
                   d.get("border_color") or "")


def apply_theme(design: dict) -> None:
    from PyQt6.QtGui import QFont

    from .theme import build_stylesheet, design_for_system
    from .config import settings
    design = design_for_system(dict(design))
    settings.design = dict(design)  # палитра виджетов всегда читает актуальную тему отсюда
    app = QApplication.instance()
    if app is None:
        return
    app.setStyleSheet(build_stylesheet(design["bg_style"], design["is_dark"], design["font_family"],
                                       design["font_size"], design["accent_color"], design.get("panel_color") or "",
                                       design.get("text_color") or "", design.get("border_color") or ""))
    app.setFont(QFont(design["font_family"], design["font_size"]))
    from . import icons
    icons.install()                      # 3.4.0: ведущие эмодзи → контурные иконки в цвете темы
    new_pal = app_palette()
    old_map = _LAST_PALETTE.get("map")
    for w in app.topLevelWidgets():
        icons.refresh(w)
        if old_map:
            retheme(w, old_map, new_pal.color_map())   # 3.4.1: inline-цвета открытых окон переходят на новую тему
    _LAST_PALETTE["map"] = new_pal.color_map()


_LAST_PALETTE: dict = {}


def retheme(root: QWidget, old: dict[str, str], new: dict[str, str]) -> None:
    """Заменить в inline-стилях всех потомков цвета прежней палитры на цвета новой (по одинаковым именам).

    Окна, открытые до смены темы, задают цвета через ``setStyleSheet(f"color: {pal.subtext}")`` — строкой,
    которая иначе осталась бы от старой темы. Сопоставляем по имени роли, поэтому «подпись» остаётся «подписью».
    """
    import re
    table: dict[str, str] = {}
    for k in old:                       # порядок ролей важен: при совпадении цветов побеждает первая (основная) роль
        o = old[k].lower()
        if k in new and o.startswith("#") and o not in table:
            table[o] = new[k]
    table = {o: n for o, n in table.items() if o != n.lower()}
    if not table:
        return
    pat = re.compile("|".join(re.escape(o) for o in table), re.IGNORECASE)
    for w in [root] + root.findChildren(QWidget):
        ss = w.styleSheet()
        if ss and "#" in ss:
            new_ss = pat.sub(lambda m: table[m.group(0).lower()], ss)
            if new_ss != ss:
                w.setStyleSheet(new_ss)


# --------------------------------------------------------------------------- выбор тома (C:, D:, …)
class DrivePicker(QPushButton):
    """Стилизованный выпадающий выбор тома: кнопка «💽 C: ▾», по клику — меню в цветах темы со всеми томами ПК
    и их заполненностью. API совместим с QComboBox в нужном объёме (addItem/addItems/clear/count/currentText/
    setCurrentText/currentTextChanged), поэтому код, который раньше работал с QComboBox, менять не нужно."""

    currentTextChanged = pyqtSignal(str)   # noqa: N815

    def __init__(self, parent=None, tooltip: str = "Выбрать том"):
        super().__init__(parent)
        self.setObjectName("drivePicker")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._items: list[tuple[str, str]] = []   # (том, подпись: «Data · 402 из 931 ГБ»)
        self._current = ""
        self.menu = QMenu(self)
        self.setMenu(self.menu)
        self._sync()

    # --- API как у QComboBox
    def addItem(self, text: str, hint: str = "") -> None:  # noqa: N802
        if text not in dict(self._items):
            self._items.append((text, hint))
        if not self._current:
            self._current = text
        self._sync()

    def addItems(self, texts) -> None:  # noqa: N802
        for t in texts:
            self.addItem(t)

    def set_drives(self, drives: list[dict]) -> None:
        """[{'id': 'C:', 'label': 'System', 'free_gb': 31, 'total_gb': 476}, …] — с подсказками о месте."""
        cur = self._current
        self._items = []
        for d in drives:
            hint = " · ".join(x for x in (d.get("label") or "", f"{d.get('free_gb')} из {d.get('total_gb')} ГБ свободно"
                                          if d.get("total_gb") else "") if x)
            self._items.append((d["id"], hint))
        ids = [i for i, _ in self._items]
        self._current = cur if cur in ids else (ids[0] if ids else "")
        self._sync()

    def clear(self) -> None:
        self._items, self._current = [], ""
        self._sync()

    def count(self) -> int:
        return len(self._items)

    def itemText(self, i: int) -> str:  # noqa: N802
        return self._items[i][0]

    def currentText(self) -> str:  # noqa: N802
        return self._current

    def setCurrentText(self, text: str) -> None:  # noqa: N802
        if text in dict(self._items) and text != self._current:
            self._current = text
            self._sync()
            self.currentTextChanged.emit(text)
        elif text in dict(self._items):
            self._sync()

    # --- отрисовка
    def _sync(self) -> None:
        self.setText(f"{self._current or '—'}  ▾")
        fm = self.fontMetrics()
        self.setFixedWidth(fm.horizontalAdvance(self.text()) + 30)   # ровно под текст — без пустого поля справа
        self.menu.clear()
        for drive, hint in self._items:
            act = self.menu.addAction((f"●  {drive}" if drive == self._current else f"○  {drive}") + (f"   {hint}" if hint else ""))
            act.triggered.connect(lambda _c=False, d=drive: self.setCurrentText(d))
        if not self._items:
            self.menu.addAction("тома ещё не опрошены").setEnabled(False)


# --------------------------------------------------------------------------- таблицы: ширина по содержимому
def fit_columns(table, max_width: int = 420, min_width: int = 48, stretch_last: bool | None = None, wrap: bool = True) -> None:
    """Подогнать ширину столбцов под текст.

    Каждый столбец получает ширину по самому длинному значению (и заголовку) с потолком ``max_width``;
    если текст всё равно не влезает — включается перенос (``wrap``) и строка становится выше (до 2–3 строк).
    Последний столбец растягивается на остаток, если ``stretch_last`` не выключен явно. Вызывать после заполнения.
    """
    from PyQt6.QtWidgets import QHeaderView

    hh = table.horizontalHeader()
    if stretch_last is None:   # по умолчанию тянем последний столбец, если таблица и раньше «тянулась»
        stretch_last = hh.stretchLastSection() or any(
            hh.sectionResizeMode(c) == QHeaderView.ResizeMode.Stretch for c in range(table.columnCount()))
    hh.setStretchLastSection(False)
    table.setWordWrap(wrap)
    table.resizeColumnsToContents()
    fm = table.fontMetrics()
    for c in range(table.columnCount()):
        if table.isColumnHidden(c):
            continue
        hdr = table.horizontalHeaderItem(c)
        want = table.columnWidth(c) + 6
        if hdr is not None:
            want = max(want, fm.horizontalAdvance(hdr.text()) + 28)
        table.setColumnWidth(c, max(min_width, min(max_width, want)))
        if hh.sectionResizeMode(c) in (QHeaderView.ResizeMode.Stretch, QHeaderView.ResizeMode.ResizeToContents):
            hh.setSectionResizeMode(c, QHeaderView.ResizeMode.Interactive)
    hh.setStretchLastSection(bool(stretch_last))
    if wrap:
        table.resizeRowsToContents()
        vh = table.verticalHeader()
        base = vh.defaultSectionSize()
        for r in range(table.rowCount()):
            if not table.isRowHidden(r):
                table.setRowHeight(r, max(base, min(table.rowHeight(r), base * 3)))
