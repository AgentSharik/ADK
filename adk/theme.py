"""Темы оформления: палитра в одном месте, генерация QSS."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor

PRESET_THEMES = {
    # Десять тем в духе macOS/iOS: нейтральный фон с лёгким подтоном + системный акцент Apple.
    # Ключи "dark"/"light" — те, что берутся в режиме «как в системе». Градиентов нет — только спокойные заливки.
    "dark": {"name": "Графит", "type": "solid", "bg": "#1C1C1E", "panel": "#2C2C2E", "text": "#F5F5F7",
             "border": "#48484A", "is_dark": True, "accent": "#0A84FF"},
    "emerald": {"name": "Тёмная мята", "type": "solid", "bg": "#1A1E1B", "panel": "#28302B", "text": "#F2F7F3",
                "border": "#465049", "is_dark": True, "accent": "#30D158"},
    "plum": {"name": "Тёмная слива", "type": "solid", "bg": "#1E1B22", "panel": "#2C2831", "text": "#F6F3F9",
             "border": "#4A4552", "is_dark": True, "accent": "#BF5AF2"},
    "zinc": {"name": "Тёплый графит", "type": "solid", "bg": "#201E1C", "panel": "#2E2B28", "text": "#F7F4F1",
             "border": "#4C4844", "is_dark": True, "accent": "#FF9F0A"},
    "amethyst": {"name": "Тёмный кварц", "type": "solid", "bg": "#211C1E", "panel": "#30282B", "text": "#F8F3F5",
                 "border": "#4E4448", "is_dark": True, "accent": "#FF375F"},
    "light": {"name": "Светлая", "type": "solid", "bg": "#F2F2F7", "panel": "#FFFFFF", "text": "#1D1D1F",
              "border": "#D1D1D6", "is_dark": False, "accent": "#007AFF"},
    "sand": {"name": "Персик", "type": "solid", "bg": "#F7F2EC", "panel": "#FFFFFF", "text": "#1D1D1F",
             "border": "#DCD2C6", "is_dark": False, "accent": "#FF9500"},
    "sage": {"name": "Мята", "type": "solid", "bg": "#EFF5F1", "panel": "#FFFFFF", "text": "#1D1D1F",
             "border": "#C9D8CE", "is_dark": False, "accent": "#34C759"},
    "frost": {"name": "Лаванда", "type": "solid", "bg": "#F3F1F8", "panel": "#FFFFFF", "text": "#1D1D1F",
              "border": "#D5D0E0", "is_dark": False, "accent": "#AF52DE"},
    "quartz": {"name": "Индиго", "type": "solid", "bg": "#F1F1F6", "panel": "#FFFFFF", "text": "#1D1D1F",
               "border": "#CFCFDA", "is_dark": False, "accent": "#5856D6"},
}
DEFAULT_THEME = "dark"


def theme_bg_style(t: dict) -> str:
    """QSS-фон для пресета: сплошной цвет или диагональный градиент."""
    if t["type"] == "solid":
        return f"background-color: {t['bg']};"
    return f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t['c1']}, stop:1 {t['c2']});"


def theme_design(t: dict) -> dict:
    """Поля design-словаря, которые задаёт пресет (шрифт не трогаем)."""
    return {"bg_style": theme_bg_style(t), "is_dark": t["is_dark"], "accent_color": t["accent"],
            "panel_color": t["panel"], "text_color": t["text"], "border_color": t.get("border", "")}


def derive_panel(bg_hex: str, is_dark: bool) -> str:
    """Цвет панели для произвольного фона («+ Свой…»): чуть светлее тёмного фона, белый — для светлого."""
    c = QColor(bg_hex)
    if not c.isValid():
        return "#2C2C2E" if is_dark else "#FFFFFF"
    return c.lighter(140).name() if is_dark else "#FFFFFF"


def _mix(a: str, b: str, k: float) -> str:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() * (1 - k) + cb.red() * k), round(ca.green() * (1 - k) + cb.green() * k),
                  round(ca.blue() * (1 - k) + cb.blue() * k)).name()


def luminance(hex_str: str) -> float:
    c = QColor(hex_str)
    return 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue() if c.isValid() else 255


def is_color_dark(hex_str: str) -> bool:
    return luminance(hex_str) < 140


def contrast_text(bg_hex: str) -> str:
    """Текст на заливке акцентом: белый на насыщенных цветах (как у Apple), тёмный — только на очень светлых."""
    return "#ffffff" if luminance(bg_hex) < 185 else "#1D1D1F"


def readable_accent(accent: str, is_dark: bool) -> str:
    c = QColor(accent)
    if not c.isValid():
        return "#0A84FF" if is_dark else "#007AFF"
    lum = luminance(accent)
    if not is_dark and lum > 150:          # жёлтый/оранжевый/зелёный текст на белом не читается — затемняем
        return c.darker(145).name()
    if is_dark and lum < 90:
        return c.lighter(170).name()
    return accent


@dataclass(frozen=True)
class Palette:
    """Цвета интерфейса. С 3.4.0 панели/рамки/поля выводятся из цвета панели темы, а не зашиты."""
    is_dark: bool
    accent: str
    panel: str = ""
    text_color: str = ""
    border_color: str = ""

    @property
    def _panel(self) -> QColor:
        c = QColor(self.panel) if self.panel else QColor()
        return c if c.isValid() else QColor("#2C2C2E" if self.is_dark else "#FFFFFF")

    @property
    def text(self):
        return self.text_color if QColor(self.text_color).isValid() else ("#F5F5F7" if self.is_dark else "#1D1D1F")
    @property
    def subtext(self): return _mix(self.text, self._panel.name(), 0.30)      # secondaryLabel: читается на любой панели
    @property
    def card(self): return self._panel.name()
    @property
    def border(self):
        if QColor(self.border_color).isValid():
            return self.border_color
        return self._panel.lighter(165).name() if self.is_dark else self._panel.darker(118).name()
    @property
    def input(self): return self._panel.darker(122).name() if self.is_dark else "#FFFFFF"
    @property
    def header(self): return self._panel.darker(110).name() if self.is_dark else self._panel.darker(104).name()
    @property
    def hover(self): return self._panel.lighter(135).name() if self.is_dark else self._panel.darker(107).name()
    @property
    def button(self): return self._panel.lighter(128).name() if self.is_dark else "#FFFFFF"
    @property
    def title_accent(self): return readable_accent(self.accent, self.is_dark)
    @property
    def on_accent(self): return contrast_text(self.accent)
    @property
    def selection(self):
        """Фон выбранной строки/элемента: акцент, разбавленный панелью — заметно, но текст остаётся читаемым."""
        return _mix(self._panel.name(), self.accent, 0.30 if self.is_dark else 0.16)

    # семантические цвета (текст / фон / рамка) в духе системных цветов Apple: tinted-стиль —
    # заливка = цвет, разбавленный панелью; текст = сам цвет (тёмная тема) или его «доступный» вариант (светлая)
    def _sem(self, dark_fg: str, base: str, light_fg: str, light_bg: str, light_bd: str) -> tuple[str, str, str]:
        if self.is_dark:
            return (dark_fg, _mix(self._panel.name(), base, 0.20), _mix(self._panel.name(), base, 0.55))
        return (light_fg, light_bg, light_bd)

    @property
    def success(self): return self._sem("#30D158", "#30D158", "#1F8A3A", "#E1F5E6", "#9EDCAF")
    @property
    def danger(self): return self._sem("#FF6961", "#FF453A", "#C4160C", "#FDE3E1", "#F4A9A3")
    @property
    def warning(self): return self._sem("#FFB340", "#FF9F0A", "#B25000", "#FFEED8", "#F8CB92")
    @property
    def info(self): return self._sem("#5AA9FF", "#0A84FF", "#0A5BD3", "#E1EEFF", "#A9CBFF")
    @property
    def neutral(self): return self._sem("#C7C7CC", "#8E8E93", "#3A3A3C", "#EBEBF0", "#C7C7CC")

    def color_map(self) -> dict[str, str]:
        """Все цвета палитры по именам — для перекраски inline-стилей при смене темы (см. widgets.retheme)."""
        m = {k: getattr(self, k) for k in ("text", "subtext", "card", "border", "input", "header", "hover", "button",
                                              "selection", "title_accent", "on_accent", "accent")}
        for k in ("success", "danger", "warning", "info", "neutral"):
            for i, v in enumerate(getattr(self, k)):
                m[f"{k}{i}"] = v
        return m

    def badge(self, kind: str) -> tuple[str, str, str]:
        return {"online": self.success, "offline": self.danger, "active": self.neutral,
                "disabled": self.danger, "warning": self.warning, "info": self.info}.get(kind, self.neutral)


def build_stylesheet(bg_style: str, is_dark: bool, font_family: str, font_size: int, accent: str,
                     panel: str = "", text_color: str = "", border_color: str = "") -> str:
    p = Palette(is_dark, accent, panel, text_color, border_color)
    R = 8            # радиус скругления кнопок/полей — как у элементов управления macOS
    acc_hover = QColor(accent).lighter(112).name() if is_dark else QColor(accent).darker(108).name()
    acc_press = QColor(accent).darker(115).name()

    # заливка семантических кнопок — системные цвета Apple (тёмная / светлая тема)
    sem_fill = {"btnSuccess": ("#30D158", "#34C759"), "btnDanger": ("#FF453A", "#FF3B30"),
                "btnWarning": ("#FF9F0A", "#FF9500"), "btnInfo": ("#0A84FF", "#007AFF")}

    def btn(name: str, _colors: tuple[str, str, str]) -> str:
        """Семантическая кнопка: сплошная заливка системным цветом + контрастный текст (prominent-стиль Apple).
        Раньше была тонированная — на тёмных панелях сливалась с фоном."""
        base = QColor(sem_fill[name][0 if is_dark else 1])
        fill, hov, prs = base.name(), (base.lighter(110) if is_dark else base.darker(108)).name(), base.darker(118).name()
        return (f"QPushButton#{name} {{ background-color: {fill}; color: {contrast_text(fill)}; border: 1px solid {fill}; font-weight: 600; }}"
                f"QPushButton#{name}:hover {{ background-color: {hov}; border-color: {hov}; }}"
                f"QPushButton#{name}:pressed {{ background-color: {prs}; border-color: {prs}; }}"
                f"QPushButton#{name}:disabled {{ background-color: {p.header}; color: {p.subtext}; border-color: {p.border}; }}")

    return f"""
    QMainWindow, QWidget#bgWidget {{ {bg_style} }}
    QDialog {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 12px; }}
    QWidget {{ font-family: '{font_family}', 'SF Pro Text', 'Segoe UI', sans-serif; font-size: {font_size}pt; color: {p.text}; }}
    QLabel {{ color: {p.text}; }}
    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: {R}px;
        padding: 7px 11px; color: {p.text}; selection-background-color: {accent}; selection-color: {p.on_accent}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 2px solid {accent}; padding: 6px 10px; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p.subtext}; background-color: {p.header}; }}
    QGroupBox {{ font-weight: 600; border: 1px solid {p.border}; border-radius: 10px; margin-top: 12px; padding-top: 10px; }}
    QGroupBox::title {{ subcontrol-origin: margin; padding: 0 6px; color: {p.title_accent}; }}
    QWidget#titleBar {{ background-color: {p.header}; border-bottom: 1px solid {p.border}; }}
    QLabel#titleLabel {{ font-weight: 600; font-size: {font_size + 1}pt; color: {p.text}; background: transparent; }}
    QLabel#brandLabel {{ font-weight: 800; font-size: 22pt; color: {p.text}; background: transparent;
        letter-spacing: 1px; padding-left: 2px; }}
    QLabel#brandLogo {{ background: transparent; }}
    QCheckBox, QRadioButton {{ color: {p.text}; spacing: 8px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 1.5px solid {p.subtext};
        background-color: {p.input}; border-radius: 5px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background-color: {accent}; border-color: {accent};
        image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSIjZmZmZmZmIiBzdHJva2Utd2lkdGg9IjMuMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJtNiAxMi41IDQgNCA4LTkiLz48L3N2Zz4=); }}
    QRadioButton::indicator:checked {{ image: none; border: 5px solid {accent}; background-color: #ffffff; }}
    QCheckBox::indicator:disabled {{ border-color: {p.border}; background-color: {p.header}; }}
    QPlainTextEdit, QTextEdit, QTextBrowser {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: {R}px; color: {p.text};
        selection-background-color: {accent}; selection-color: {p.on_accent}; padding: 6px; }}
    QComboBox QAbstractItemView {{ background-color: {p.card}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px;
        selection-background-color: {p.selection}; selection-color: {p.text}; outline: none; padding: 4px; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QLabel#subtle {{ color: {p.subtext}; }}
    QLabel#statusLabel {{ color: {p.subtext}; }}
    QLabel#readonlyBadge {{ color: {p.warning[0]}; background-color: {p.warning[1]}; border: 1px solid {p.warning[2]};
        border-radius: 10px; padding: 4px 10px; font-weight: 600; }}
    QLabel#updateLabel {{ color: {p.info[0]}; font-weight: 600; }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 6px; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {p.selection}; color: {p.text}; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {p.hover}; }}
    QSplitter::handle {{ background-color: {p.border}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QWidget#detailsPane, QWidget#detailsViewport {{ background-color: {p.card}; }}
    QProgressBar {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: 7px; text-align: center; color: {p.text}; }}
    QProgressBar::chunk {{ background-color: {accent}; border-radius: 6px; }}
    QPushButton#winBtn, QPushButton#btnClose {{ background: transparent; border: none; border-radius: 6px; padding: 0; }}
    QPushButton#winBtn:hover {{ background-color: {p.hover}; }}
    QPushButton#btnClose:hover {{ background-color: #FF453A; color: #ffffff; }}
    QPushButton {{ background-color: {p.button}; border: 1px solid {p.border}; border-radius: {R}px; padding: 7px 16px;
        font-weight: 600; color: {p.text}; outline: none; }}
    QPushButton:hover {{ background-color: {p.hover}; border-color: {p.subtext}; }}
    QPushButton:pressed {{ background-color: {p.selection}; border-color: {accent}; }}
    QPushButton:checked {{ background-color: {p.selection}; border-color: {accent}; color: {p.text}; }}
    QPushButton:disabled {{ color: {p.subtext}; background-color: {p.header}; border-color: {p.border}; }}
    QPushButton#btnPrimary {{ background-color: {accent}; color: {p.on_accent}; border: 1px solid {accent}; font-weight: 600; }}
    QPushButton#btnPrimary:hover {{ background-color: {acc_hover}; border-color: {acc_hover}; }}
    QPushButton#btnPrimary:pressed {{ background-color: {acc_press}; border-color: {acc_press}; }}
    QPushButton#btnPrimary:disabled {{ background-color: {p.header}; color: {p.subtext}; border-color: {p.border}; }}
    {btn("btnSuccess", p.success)} {btn("btnDanger", p.danger)} {btn("btnWarning", p.warning)} {btn("btnInfo", p.info)}
    QPushButton#historyBtn {{ background-color: {p.button}; border: 1px solid {p.border}; border-radius: 14px; padding: 6px 14px; }}
    QPushButton#historyBtn:hover {{ border-color: {accent}; color: {p.title_accent}; }}
    QPushButton#chipBtn {{ padding: 5px 16px; min-height: 16px; border-radius: 14px; border: 1px solid {p.border};
        background: {p.button}; color: {p.text}; font-weight: 600; }}
    QPushButton#chipBtn:checked {{ background: {accent}; color: {p.on_accent}; border: 1px solid {accent}; }}
    QPushButton#chipBtn:hover {{ border-color: {accent}; }}
    QTableWidget, QTableView {{ background-color: {p.card}; alternate-background-color: {p.header}; border: 1px solid {p.border}; border-radius: 8px;
        gridline-color: {p.border}; selection-background-color: {p.selection}; selection-color: {p.text}; outline: none; }}
    QTableWidget::item, QTableView::item {{ padding: 4px; border-bottom: 1px solid {p.border}; border-right: 1px solid {p.border}; }}
    QTableWidget::item:selected, QTableView::item:selected {{ background-color: {p.selection}; color: {p.text};
        border-bottom: 1px solid {p.border}; border-right: 1px solid {p.border}; }}
    QTableWidget::item:selected:first, QTableView::item:selected:first {{ border-left: 4px solid {accent}; padding-left: 2px; }}
    QAbstractScrollArea::viewport {{ background-color: {p.card}; }}
    QHeaderView::section {{ background-color: {p.header}; color: {p.subtext}; padding: 9px 12px; border: none;
        border-right: 1px solid {p.border}; border-bottom: 1px solid {p.border}; font-weight: 600; }}
    QTableCornerButton::section {{ background-color: {p.header}; border: none; }}
    QHeaderView {{ background-color: {p.header}; }}
    QHeaderView::section:vertical {{ background-color: {p.header}; color: {p.subtext}; padding: 0 6px; border-right: 1px solid {p.border}; }}
    QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {p.title_accent}; min-height: 32px; border-radius: 4px; margin: 0 2px; }}
    QScrollBar::handle:vertical:hover {{ background: {acc_hover}; margin: 0 1px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {p.title_accent}; min-width: 32px; border-radius: 4px; margin: 2px 0; }}
    QScrollBar::handle:horizontal:hover {{ background: {acc_hover}; margin: 1px 0; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
    #sideCard, #dashCard {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 12px; padding: 14px; }}
    #sideCard QLabel, #dashCard QLabel {{ background: transparent; border: none; }}
    #specBox {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: 8px; padding: 8px; font-family: 'SF Mono', Consolas, monospace; font-size: 9.5pt; }}
    QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: 10px; background: {p.card}; top: -1px; }}
    QTabBar {{ qproperty-iconSize: 20px 20px; }}
    QTabBar::tab {{ background: transparent; color: {p.subtext}; padding: 9px 18px; border-bottom: 2px solid transparent; font-weight: 600; }}
    QTabBar::tab:hover {{ color: {p.text}; }}
    QTabBar::tab:selected {{ color: {p.title_accent}; border-bottom: 2px solid {accent}; }}
    QListWidget, QTreeWidget {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 8px; outline: none; qproperty-iconSize: 20px 20px; }}
    QTableWidget, QTableView {{ qproperty-iconSize: 20px 20px; }}
    QMenu {{ icon-size: 20px; }}
    QPushButton#drivePicker {{ background-color: {p.button}; color: {p.title_accent}; border: 1px solid {p.border};
        border-radius: 14px; padding: 4px 8px; font-weight: 600; font-size: 10.5pt; text-align: center; }}
    QPushButton#drivePicker:hover {{ border-color: {accent}; }}
    QPushButton#drivePicker:pressed {{ background-color: {accent}; color: {p.on_accent}; }}
    QPushButton#drivePicker::menu-indicator {{ image: none; width: 0; }}
    QMenu {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background-color: {accent}; color: {p.on_accent}; }}
    QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}
    QMenu#diskMenu {{ border: 1px solid {p.border}; border-radius: 10px; padding: 6px; }}
    QMenu#diskMenu::item {{ padding: 8px 22px; border-radius: 6px; font-weight: 600; }}
    QMenu#diskMenu::item:disabled {{ color: {p.subtext}; font-weight: normal; }}
    QMenu#diskMenu::item:selected {{ background-color: {accent}; color: {p.on_accent}; }}
    QLabel#diagTitle {{ font-size: 18pt; font-weight: 700; color: {p.text}; padding-top: 8px; }}
    QLabel#diagText {{ font-size: 11pt; color: {p.subtext}; padding: 4px 40px 12px 40px; }}
    QToolTip {{ background-color: {p.card}; color: {p.text}; border: 1px solid {p.border}; border-radius: 6px; padding: 4px 6px; }}
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
    return {**design, **theme_design(PRESET_THEMES["dark" if dark else "light"])}
