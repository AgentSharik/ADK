"""Темы оформления: палитра в одном месте, генерация QSS."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor

PRESET_THEMES = {
    "light": {"name": "Светлая", "type": "solid", "bg": "#f8fafc", "is_dark": False, "accent": "#2563eb"},
    "sand": {"name": "Песочная", "type": "solid", "bg": "#fbf7ee", "is_dark": False, "accent": "#d97706"},
    "azure": {"name": "Лазурь", "type": "grad", "c1": "#f0f9ff", "c2": "#dbeafe", "is_dark": False, "accent": "#0284c7"},
    "dark": {"name": "Тёмная", "type": "solid", "bg": "#0b132b", "is_dark": True, "accent": "#38bdf8"},
    "graphite": {"name": "Графит", "type": "solid", "bg": "#242424", "is_dark": True, "accent": "#4ade80"},
    "indigo": {"name": "Индиго", "type": "solid", "bg": "#131138", "is_dark": True, "accent": "#818cf8"},
    "sunset": {"name": "Закат", "type": "grad", "c1": "#181033", "c2": "#4d0d2a", "is_dark": True, "accent": "#fb7185"},
    "ocean": {"name": "Океан", "type": "grad", "c1": "#0b132b", "c2": "#1c2541", "is_dark": True, "accent": "#38bdf8"},
    "neon": {"name": "Неон", "type": "grad", "c1": "#12002b", "c2": "#360018", "is_dark": True, "accent": "#e879f9"},
    "forest": {"name": "Лес", "type": "grad", "c1": "#022019", "c2": "#064030", "is_dark": True, "accent": "#34d399"},
}


def luminance(hex_str: str) -> float:
    c = QColor(hex_str)
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() if c.isValid() else 255


def is_color_dark(hex_str: str) -> bool:
    return luminance(hex_str) < 140


def contrast_text(bg_hex: str) -> str:
    return "#ffffff" if is_color_dark(bg_hex) else "#0f172a"


def readable_accent(accent: str, is_dark: bool) -> str:
    c = QColor(accent)
    if not c.isValid():
        return "#38bdf8" if is_dark else "#2563eb"
    lum = luminance(accent)
    if not is_dark and lum > 125:
        return c.darker(165).name()
    if is_dark and lum < 90:
        return c.lighter(180).name()
    return accent


@dataclass(frozen=True)
class Palette:
    is_dark: bool
    accent: str

    @property
    def text(self): return "#ffffff" if self.is_dark else "#0f172a"
    @property
    def subtext(self): return "#94a3b8" if self.is_dark else "#475569"
    @property
    def card(self): return "#151e36" if self.is_dark else "#ffffff"
    @property
    def border(self): return "#233055" if self.is_dark else "#cbd5e1"
    @property
    def input(self): return "#0d1527" if self.is_dark else "#ffffff"
    @property
    def header(self): return "#121a30" if self.is_dark else "#f1f5f9"
    @property
    def hover(self): return "#1f2c4e" if self.is_dark else "#e2e8f0"
    @property
    def button(self): return "#19233e" if self.is_dark else "#ffffff"
    @property
    def title_accent(self): return readable_accent(self.accent, self.is_dark)
    @property
    def on_accent(self): return contrast_text(self.accent)

    # семантические цвета (текст / фон / рамка)
    @property
    def success(self): return ("#86efac", "rgba(34,197,94,0.28)", "#22c55e") if self.is_dark else ("#15803d", "#dcfce7", "#86efac")
    @property
    def danger(self): return ("#fca5a5", "rgba(239,68,68,0.30)", "#ef4444") if self.is_dark else ("#991b1b", "#fee2e2", "#fca5a5")
    @property
    def warning(self): return ("#fcd34d", "rgba(245,158,11,0.28)", "#f59e0b") if self.is_dark else ("#92400e", "#fef3c7", "#fde68a")
    @property
    def info(self): return ("#7dd3fc", "rgba(56,189,248,0.28)", "#0ea5e9") if self.is_dark else ("#1e40af", "#dbeafe", "#bfdbfe")
    @property
    def neutral(self): return ("#e2e8f0", "rgba(148,163,184,0.2)", "#64748b") if self.is_dark else ("#334155", "#f1f5f9", "#cbd5e1")

    def badge(self, kind: str) -> tuple[str, str, str]:
        return {"online": self.success, "offline": self.danger, "active": self.neutral,
                "disabled": self.danger, "warning": self.warning, "info": self.info}.get(kind, self.neutral)


def build_stylesheet(bg_style: str, is_dark: bool, font_family: str, font_size: int, accent: str) -> str:
    p = Palette(is_dark, accent)

    def btn(name: str, colors: tuple[str, str, str]) -> str:
        fg, bg, bd = colors
        return (f"QPushButton#{name} {{ background-color: {bg}; color: {fg}; border: 1.5px solid {bd}; font-weight: bold; }}"
                f"QPushButton#{name}:hover {{ border: 2px solid {p.title_accent}; }}")

    return f"""
    QMainWindow, QWidget#bgWidget {{ {bg_style} }}
    QDialog {{ background-color: {p.card}; border: 1.5px solid {p.title_accent}; }}
    QWidget {{ font-family: '{font_family}', 'Segoe UI', sans-serif; font-size: {font_size}pt; color: {p.text}; }}
    QLabel {{ color: {p.text}; }}
    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {{ background-color: {p.input}; border: 1.5px solid {p.border}; padding: 6px 10px;
        color: {p.text}; selection-background-color: {accent}; selection-color: {p.on_accent}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 2px solid {p.title_accent}; }}
    QGroupBox {{ font-weight: bold; border: 1.5px solid {p.border}; margin-top: 12px; padding-top: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; padding: 0 6px; color: {p.title_accent}; }}
    QWidget#titleBar {{ background-color: {p.header}; border-bottom: 1px solid {p.border}; }}
    QLabel#titleLabel {{ font-weight: bold; font-size: {font_size + 1}pt; color: {p.title_accent}; background: transparent; }}
    QLabel#brandLabel {{ font-weight: 800; font-size: 22pt; color: {p.title_accent}; background: transparent;
        letter-spacing: 1px; padding-left: 2px; }}
    QLabel#brandLogo {{ background: transparent; }}
    QCheckBox, QRadioButton {{ color: {p.text}; spacing: 6px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 16px; height: 16px; border: 1.5px solid {p.subtext};
        background-color: {p.input}; border-radius: 3px; }}
    QRadioButton::indicator {{ border-radius: 8px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {p.title_accent}; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {accent}; border-color: {accent}; }}
    QCheckBox::indicator:disabled {{ border-color: {p.border}; background-color: {p.header}; }}
    QPlainTextEdit, QTextEdit, QTextBrowser {{ background-color: {p.input}; border: 1.5px solid {p.border}; color: {p.text};
        selection-background-color: {accent}; selection-color: {p.on_accent}; padding: 4px; }}
    QComboBox QAbstractItemView {{ background-color: {p.card}; color: {p.text}; border: 1.5px solid {p.border};
        selection-background-color: {p.hover}; selection-color: {p.text}; outline: none; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QLabel#subtle {{ color: {p.subtext}; }}
    QLabel#statusLabel {{ color: {p.subtext}; }}
    QLabel#readonlyBadge {{ color: {p.warning[0]}; background-color: {p.warning[1]}; border: 1px solid {p.warning[2]};
        border-radius: 4px; padding: 3px 8px; font-weight: bold; }}
    QLabel#updateLabel {{ color: {p.info[0]}; font-weight: bold; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {p.hover}; color: {p.text}; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {p.hover}; }}
    QSplitter::handle {{ background-color: {p.border}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QWidget#detailsPane, QWidget#detailsViewport {{ background-color: {p.card}; }}
    QProgressBar {{ background-color: {p.input}; border: 1.5px solid {p.border}; text-align: center; color: {p.text}; }}
    QProgressBar::chunk {{ background-color: {accent}; }}
    QPushButton#winBtn, QPushButton#btnClose {{ background: transparent; border: none; border-radius: 4px; padding: 0; }}
    QPushButton#winBtn:hover {{ background-color: {p.hover}; }}
    QPushButton#btnClose:hover {{ background-color: #ef4444; color: #ffffff; }}
    QPushButton {{ background-color: {p.button}; border: 1.5px solid {p.border}; padding: 7px 15px; font-weight: 600; color: {p.text}; outline: none; }}
    QPushButton:hover {{ background-color: {p.hover}; border: 2px solid {p.title_accent}; }}
    QPushButton:pressed {{ background-color: {accent}; color: {p.on_accent}; }}
    QPushButton:disabled {{ color: {p.subtext}; }}
    QPushButton#btnPrimary {{ background-color: {accent}; color: {p.on_accent}; border: 1.5px solid {accent}; font-weight: bold; }}
    QPushButton#btnPrimary:hover {{ border: 2px solid {p.title_accent}; }}
    {btn("btnSuccess", p.success)} {btn("btnDanger", p.danger)} {btn("btnWarning", p.warning)} {btn("btnInfo", p.info)}
    QPushButton#historyBtn {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {p.card}, stop:1 {p.header});
        border: 1.5px solid {p.border}; border-bottom: 3px solid {p.subtext}; }}
    QPushButton#historyBtn:hover {{ border-color: {p.title_accent}; border-bottom: 3px solid {p.title_accent}; }}
    QPushButton#chipBtn {{ padding: 5px 16px; min-height: 16px; border-radius: 13px; border: 1.5px solid {p.border};
        background: {p.card}; color: {p.subtext}; font-weight: 600; }}
    QPushButton#chipBtn:checked {{ background: {accent}; color: {p.on_accent}; border: 2px solid {p.title_accent}; padding: 4px 15px; }}
    QPushButton#chipBtn:hover {{ border-color: {p.title_accent}; color: {p.text}; }}
    QTableWidget, QTableView {{ background-color: {p.card}; alternate-background-color: {p.header}; border: 1.5px solid {p.border};
        gridline-color: {p.border}; selection-background-color: {p.hover}; selection-color: {p.text}; outline: none; }}
    QTableWidget::item, QTableView::item {{ padding: 4px; border-bottom: 1px solid {p.border}; border-right: 1px solid {p.border}; }}
    QTableWidget::item:selected, QTableView::item:selected {{ background-color: {p.hover}; color: {p.text};
        border-bottom: 1px solid {p.subtext}; border-right: 1px solid {p.border}; }}
    QTableWidget::item:selected:first, QTableView::item:selected:first {{ border-left: 4px solid {p.title_accent}; padding-left: 2px; }}
    QAbstractScrollArea::viewport {{ background-color: {p.card}; }}
    QHeaderView::section {{ background-color: {p.header}; color: {p.text}; padding: 8px 12px; border: none;
        border-right: 1.5px solid {p.border}; border-bottom: 1.5px solid {p.border}; font-weight: bold; }}
    QTableCornerButton::section {{ background-color: {p.header}; border: none; }}
    QScrollBar:vertical {{ background: {p.header}; width: 14px; margin: 0; border-left: 1px solid {p.border}; }}
    QScrollBar::handle:vertical {{ background: {p.title_accent}; min-height: 32px; border-radius: 4px; margin: 3px 3px; }}
    QScrollBar::handle:vertical:hover {{ background: {accent}; margin: 2px 2px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: {p.header}; height: 14px; margin: 0; border-top: 1px solid {p.border}; }}
    QScrollBar::handle:horizontal {{ background: {p.title_accent}; min-width: 32px; border-radius: 4px; margin: 3px 3px; }}
    QScrollBar::handle:horizontal:hover {{ background: {accent}; margin: 2px 2px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    #sideCard, #dashCard {{ background-color: {p.card}; border: 1.5px solid {p.border}; padding: 14px; }}
    #sideCard QLabel, #dashCard QLabel {{ background: transparent; border: none; }}
    #specBox {{ background-color: {p.input}; border: 1.5px solid {p.border}; padding: 8px; font-family: Consolas, monospace; font-size: 9.5pt; }}
    QTabWidget::pane {{ border: 1.5px solid {p.border}; background: {p.card}; }}
    QTabBar::tab {{ background: transparent; color: {p.subtext}; padding: 8px 16px; border-bottom: 3px solid transparent; }}
    QTabBar::tab:selected {{ color: {p.title_accent}; border-bottom: 3px solid {p.title_accent}; font-weight: bold; }}
    QListWidget, QTreeWidget {{ background-color: {p.card}; border: 1.5px solid {p.border}; outline: none; }}
    QPushButton#drivePicker {{ background-color: {p.card}; color: {p.title_accent}; border: 1.5px solid {p.title_accent};
        border-radius: 14px; padding: 4px 6px; font-weight: bold; font-size: 10.5pt; text-align: center; }}
    QPushButton#drivePicker:hover {{ background-color: {p.hover}; }}
    QPushButton#drivePicker:pressed {{ background-color: {accent}; color: {p.on_accent}; }}
    QPushButton#drivePicker::menu-indicator {{ image: none; width: 0; }}
    QMenu {{ background-color: {p.card}; border: 1.5px solid {p.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 20px; }}
    QMenu::item:selected {{ background-color: {p.hover}; }}
    QMenu#diskMenu {{ border: 1.5px solid {p.title_accent}; border-radius: 10px; padding: 6px; }}
    QMenu#diskMenu::item {{ padding: 8px 22px; border-radius: 6px; font-weight: 600; }}
    QMenu#diskMenu::item:disabled {{ color: {p.subtext}; font-weight: normal; }}
    QMenu#diskMenu::item:selected {{ background-color: {accent}; color: {p.on_accent}; }}
    QLabel#diagTitle {{ font-size: 18pt; font-weight: 700; color: {p.title_accent}; padding-top: 8px; }}
    QLabel#diagText {{ font-size: 11pt; color: {p.subtext}; padding: 4px 40px 12px 40px; }}
    QToolTip {{ background-color: {p.card}; color: {p.text}; border: 1px solid {p.border}; }}
    """


# --------------------------------------------------------------------------- системная тема
def system_prefers_dark() -> bool | None:
    """Windows: HKCU\\...\\Themes\\Personalize\\AppsUseLightTheme (0 → тёмная). Иначе — по палитре Qt. None — неизвестно."""
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return int(val) == 0
    except Exception:  # noqa: BLE001 — не Windows или нет ключа
        pass
    try:
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtCore import Qt
        app = QGuiApplication.instance()
        if app is not None:
            hints = app.styleHints()
            if hasattr(hints, "colorScheme"):
                cs = hints.colorScheme()
                if cs == Qt.ColorScheme.Dark:
                    return True
                if cs == Qt.ColorScheme.Light:
                    return False
    except Exception:  # noqa: BLE001
        pass
    return None


def design_for_system(design: dict) -> dict:
    """Если follow_system — подменяет фон/акцент на пресет light/dark по системной теме. Остальное (шрифт) сохраняет."""
    if not design.get("follow_system"):
        return design
    dark = system_prefers_dark()
    if dark is None:
        return design
    t = PRESET_THEMES["dark" if dark else "light"]
    return {**design, "bg_style": f"background-color: {t['bg']};", "is_dark": t["is_dark"], "accent_color": t["accent"]}
