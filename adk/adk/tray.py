"""Значок в трее и глобальная горячая клавиша.

* Трей: «Показать», «Поиск», «Сканировать парк», «Выход». Закрытие окна крестиком — сворачивание в трей
  (``[UI] minimize_to_tray``), выход — из меню трея или ``Ctrl+Q``.
* Глобальный хоткей (по умолчанию ``Ctrl+Shift+A``): на Windows — ``RegisterHotKey`` через нативный фильтр
  событий (WM_HOTKEY), без сторонних библиотек. На других ОС — обычный QShortcut внутри окна.
"""
from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QKeySequence, QPixmap, QPainter, QColor, QPolygon, QShortcut
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
WM_HOTKEY = 0x0312
HOTKEY_ID = 0xADC


def asset_path(name: str) -> str:
    """assets/<name>: рядом с пакетом при запуске из исходников, в _internal/assets — в сборке PyInstaller."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def app_icon() -> QIcon:
    """Иконка приложения из assets/icon.png; если файла нет — рисуется программно."""
    import os
    path = asset_path("icon.png")
    if os.path.exists(path):
        ic = QIcon(path)
        if not ic.isNull():
            return ic
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#2563eb"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QColor("#ffffff"))
    k = 2
    p.drawPolygon(QPolygon([QPoint(int(x * k), int(y * k)) for x, y in ((18, 4), (9, 17), (15, 17), (13, 28), (23, 13), (17, 13))]))
    p.end()
    return QIcon(pm)


def parse_hotkey(text: str) -> tuple[int, int] | None:
    """'Ctrl+Shift+A' → (modifiers, virtual-key) для RegisterHotKey. None — пусто/не разобрано."""
    if not text or not text.strip():
        return None
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    mods, key = 0, None
    for p in parts:
        if p in ("ctrl", "control"):
            mods |= MOD_CONTROL
        elif p == "shift":
            mods |= MOD_SHIFT
        elif p == "alt":
            mods |= MOD_ALT
        elif p in ("win", "meta", "super"):
            mods |= MOD_WIN
        else:
            key = p
    if key is None:
        return None
    is_fkey = key.startswith("f") and key[1:].isdigit()
    if not mods and not is_fkey:
        return None  # одиночная буква без модификатора перехватила бы ввод во всей системе
    if len(key) == 1 and key.isalnum():
        vk = ord(key.upper())
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x70 + int(key[1:]) - 1
    elif key == "space":
        vk = 0x20
    else:
        return None
    return mods, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            try:
                import ctypes
                from ctypes import wintypes
                msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    self.cb()
                    return True, 0
            except Exception:  # noqa: BLE001
                pass
        return False, 0


class GlobalHotkey(QObject):
    """Регистрирует системный хоткей (Windows) или QShortcut-фолбэк. ``activated`` — сигнал."""
    activated = pyqtSignal()

    def __init__(self, window, text: str):
        super().__init__(window)
        self.window = window
        self.text = text
        self.native = False
        self._filter = None
        self._shortcut = None
        parsed = parse_hotkey(text)
        if parsed is None:
            return
        if sys.platform.startswith("win"):
            try:
                import ctypes
                mods, vk = parsed
                if ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, mods | 0x4000, vk):  # MOD_NOREPEAT
                    self._filter = _HotkeyFilter(self.activated.emit)
                    QApplication.instance().installNativeEventFilter(self._filter)
                    self.native = True
                    return
                log.warning("RegisterHotKey(%s) не удался — занята другим приложением", text)
            except Exception as exc:  # noqa: BLE001
                log.warning("RegisterHotKey: %s", exc)
        self._shortcut = QShortcut(QKeySequence(text), window)
        self._shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._shortcut.activated.connect(self.activated.emit)

    def unregister(self) -> None:
        if self.native:
            try:
                import ctypes
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
                QApplication.instance().removeNativeEventFilter(self._filter)
            except Exception:  # noqa: BLE001
                pass
            self.native = False


class Tray(QObject):
    """Обёртка над QSystemTrayIcon с меню. Если трей недоступен — ``available == False`` и всё молча выключено."""

    def __init__(self, window, on_show, on_search, on_scan, on_quit):
        super().__init__(window)
        self.window = window
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.icon = QSystemTrayIcon(app_icon(), window)
        self.menu = QMenu()
        from .i18n import tr
        self.menu.addAction(tr("Показать ADK"), on_show)
        self.menu.addAction("🔍 " + tr("Поиск"), on_search)
        self.menu.addAction("🔄 " + tr("Сканировать парк"), on_scan)
        self.menu.addSeparator()
        self.menu.addAction("🚪 " + tr("Выход"), on_quit)
        self.icon.setContextMenu(self.menu)
        self.icon.setToolTip("ADK — Active Directory Kit")
        self.icon.activated.connect(lambda r: on_show() if r in (QSystemTrayIcon.ActivationReason.Trigger,
                                                                 QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        if self.available:
            self.icon.show()

    def notify(self, title: str, text: str) -> None:
        if self.available:
            self.icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def hide(self) -> None:
        self.icon.hide()
