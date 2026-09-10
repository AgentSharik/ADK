"""Темы оформления: палитра в одном месте, генерация QSS."""
from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QColor

PRESET_THEMES = {
    # Десять тем, каждая со своим характером: у тёмных — разный подтон фона и панелей (нейтральный графит,
    # тёплый уголь, хвойный, сливовый), у светлых — заметно окрашенный фон (бумага, песок, сад, лаванда).
    # Две темы градиентные («Сумерки» тёмная и «Рассвет» светлая): фон окна — диагональный переход двух цветов,
    # панели и карточки при этом сплошные. Ключи "dark"/"light" — те, что берутся в режиме «как в системе».
    "dark": {"name": "Графит", "type": "solid", "bg": "#1C1C1E", "panel": "#2C2C2E", "text": "#F5F5F7",
             "border": "#48484A", "is_dark": True, "accent": "#0A84FF"},
    "ember": {"name": "Уголь и янтарь", "type": "solid", "bg": "#17140F", "panel": "#282219", "text": "#F8F1E4",
              "border": "#4E4433", "is_dark": True, "accent": "#FFB020"},
    "pine": {"name": "Хвоя", "type": "solid", "bg": "#0E1813", "panel": "#1A2A21", "text": "#EAF6EE",
             "border": "#355243", "is_dark": True, "accent": "#3DD68C"},
    "plum": {"name": "Слива", "type": "solid", "bg": "#1A1222", "panel": "#2B1F36", "text": "#F6EEFB",
             "border": "#54406A", "is_dark": True, "accent": "#C084FC"},
    "dusk": {"name": "Сумерки", "type": "grad", "c1": "#1F1526", "c2": "#4A2236", "panel": "#33252F", "text": "#FBEFF4",
             "border": "#63505C", "is_dark": True, "accent": "#F472B6"},
    "light": {"name": "Светлая", "type": "solid", "bg": "#F2F2F7", "panel": "#FFFFFF", "text": "#1D1D1F",
              "border": "#D1D1D6", "is_dark": False, "accent": "#007AFF"},
    "sand": {"name": "Песок", "type": "solid", "bg": "#F3E9DA", "panel": "#FFFBF4", "text": "#2A211A",
             "border": "#DCCBB2", "is_dark": False, "accent": "#C2410C"},
    "garden": {"name": "Сад", "type": "solid", "bg": "#E4F0E6", "panel": "#F8FCF8", "text": "#16241A",
               "border": "#BFD6C4", "is_dark": False, "accent": "#15803D"},
    "lavender": {"name": "Лаванда", "type": "solid", "bg": "#ECE7F7", "panel": "#FBFAFF", "text": "#1F1A2E",
                 "border": "#CFC5E6", "is_dark": False, "accent": "#7C3AED"},
    "dawn": {"name": "Рассвет", "type": "grad", "c1": "#FFE3D2", "c2": "#DCD7FA", "panel": "#FFFFFF", "text": "#241C1F",
             "border": "#E0D3D9", "is_dark": False, "accent": "#DB2777"},
}
DEFAULT_THEME = "dark"


def theme_bg_style(t: dict) -> str:
    """QSS-фон для пресета: сплошной цвет или диагональный градиент."""
    if t["type"] == "solid":
        return f"background-color: {t['bg']};"
    return f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {t['c1']}, stop:1 {t['c2']});"


def theme_colors(t: dict) -> tuple[str, str]:
    """(первый, второй) цвет фона пресета; у сплошных тем оба одинаковые — удобно для плиток и тестов."""
    if t["type"] == "solid":
        return t["bg"], t["bg"]
    return t["c1"], t["c2"]


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
    """Текст на заливке акцентом: белый на насыщенных цветах, тёмный — только на очень светлых."""
    return "#ffffff" if luminance(bg_hex) < 185 else "#1D1D1F"


WARNING_FILL = "#F5B324"     # янтарь: заливка кнопок-предупреждений (смена пароля, снятие блокировки)
WARNING_TEXT = "#1F1A0E"     # текст/иконка на янтаре — тёмные: белый на жёлтом не читался


def button_text_color(object_name: str, fill: str) -> str:
    """Цвет текста заливной кнопки: на янтарной («btnWarning») всегда тёмный, на остальных — по яркости заливки."""
    if object_name == "btnWarning":
        return WARNING_TEXT
    return contrast_text(fill)


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
    def subtext(self): return _mix(self.text, self._panel.name(), 0.22)      # вторичная подпись: приглушена, но не блёклая
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

    # семантические цвета (текст / фон / рамка), тонированный стиль —
    # заливка = цвет, разбавленный панелью; текст = сам цвет (тёмная тема) или его «доступный» вариант (светлая)
    def _sem(self, dark_fg: str, base: str, light_fg: str, light_bg: str, light_bd: str) -> tuple[str, str, str]:
        if self.is_dark:
            return (dark_fg, _mix(self._panel.name(), base, 0.34), _mix(self._panel.name(), base, 0.80))
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


_QSS_IMG_CACHE: dict[str, str] = {}


def _svg_uri(svg: str) -> str:
    """Путь к SVG-файлу для ``image: url(...)`` в QSS. Qt не понимает data-URI в таблицах стилей,
    поэтому картинка один раз пишется во временную папку (имя — по хэшу содержимого) и дальше берётся из кэша."""
    import hashlib
    import os
    import tempfile
    key = hashlib.sha1(svg.encode()).hexdigest()[:16]
    path = _QSS_IMG_CACHE.get(key)
    if path and os.path.exists(path):
        return path.replace("\\", "/")
    folder = os.path.join(tempfile.gettempdir(), "adk-qss")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{key}.svg")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
    _QSS_IMG_CACHE[key] = path
    return path.replace("\\", "/")


def relief(color: str, top: int = 108, bottom: int = 96) -> str:
    """Вертикальный градиент «сверху светлее, снизу темнее» — объём кнопки/панели без картинок.
    top/bottom — множители QColor.lighter() для верхнего и нижнего края."""
    c = QColor(color)
    return (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {c.lighter(top).name()}, "
            f"stop:0.5 {c.name()}, stop:1 {c.lighter(bottom).name()})")


def build_stylesheet(bg_style: str, is_dark: bool, font_family: str, font_size: int, accent: str,
                     panel: str = "", text_color: str = "", border_color: str = "") -> str:
    p = Palette(is_dark, accent, panel, text_color, border_color)
    R = 8            # радиус скругления кнопок/полей
    acc = QColor(accent)
    acc_hover = acc.lighter(112).name() if is_dark else acc.darker(106).name()
    acc_press = acc.darker(115).name()
    acc_edge = acc.darker(125).name()            # нижняя кромка акцентной кнопки — читается как тень
    btn_edge = QColor(p.border).darker(125).name() if is_dark else QColor(p.border).darker(112).name()
    btn_light = QColor(p.button).lighter(118).name() if is_dark else "#FFFFFF"   # блик по верхнему краю
    card_top = QColor(p.border).lighter(125).name() if is_dark else "#FFFFFF"    # светлая кромка панели сверху
    card_bottom = QColor(p.border).darker(112).name() if is_dark else QColor(p.border).darker(108).name()
    # разделители внутри выделения: цвет самой подсветки, но заметно темнее/светлее — иначе при выделении
    # нескольких строк границы строк и столбцов сливаются в одно пятно
    sel = QColor(p.selection) if not str(p.selection).startswith("rgba") else QColor(accent)
    sel_line = sel.darker(135).name() if is_dark else sel.darker(118).name()
    input_top = QColor(p.border).darker(105).name() if is_dark else QColor(p.border).darker(104).name()

    # заливка семантических кнопок (тёмная / светлая тема)
    sem_fill = {"btnSuccess": ("#30D158", "#34C759"), "btnDanger": ("#FF453A", "#FF3B30"),
                "btnWarning": (WARNING_FILL, WARNING_FILL), "btnInfo": ("#0A84FF", "#007AFF")}

    def solid_btn(sel: str, base_hex: str, fg: str) -> str:
        """Заливная кнопка (акцентная и семантические): градиент с бликом сверху, тёмная кромка снизу,
        три состояния. Все цвета непрозрачные — не сливается ни с одной панелью."""
        base = QColor(base_hex)
        hov = (base.lighter(110) if is_dark else base.darker(106)).name()
        prs = base.darker(115).name()
        edge = base.darker(125).name()
        return (f"{sel} {{ background: {relief(base.name(), 112, 94)}; color: {fg}; border: 1px solid {edge};"
                f"  border-top-color: {base.lighter(104).name()}; font-weight: 600; }}"
                f"{sel}:hover {{ background: {relief(hov, 112, 94)}; }}"
                f"{sel}:pressed {{ background: {prs}; border-color: {edge}; padding-top: 8px; padding-bottom: 6px; }}"
                f"{sel}:disabled {{ background: {p.header}; color: {p.subtext}; border-color: {p.border}; }}")

    chev_down = _svg_uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{p.text}" '
                         f'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>')
    chev_up = _svg_uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{p.text}" '
                       f'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>')
    chev_dim = _svg_uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{p.subtext}" '
                        f'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>')
    check_img = _svg_uri(f'<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="{p.on_accent}" '
                         'stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5 9.5-10"/></svg>')
    sel_edge = _mix(p.selection, accent, 0.55)    # обводка выделенной строки/элемента — заметная, но не кричащая

    def btn(name: str, _colors: tuple[str, str, str]) -> str:
        fill = sem_fill[name][0 if is_dark else 1]
        return solid_btn(f"QPushButton#{name}", fill, button_text_color(name, fill))

    return f"""
    QMainWindow, QWidget#bgWidget {{ {bg_style} }}
    QDialog {{ background-color: {p.card}; border: 1px solid {p.border}; border-top-color: {card_top}; border-bottom-color: {card_bottom}; border-radius: 12px; }}
    QWidget {{ font-family: '{font_family}', 'Inter', 'Segoe UI', sans-serif; font-size: {font_size}pt; color: {p.text}; }}
    QLabel {{ color: {p.text}; }}
    QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {{ background-color: {p.input}; border: 1px solid {p.border}; border-top-color: {input_top};
        border-radius: {R}px; padding: 7px 11px; color: {p.text}; selection-background-color: {accent}; selection-color: {p.on_accent}; }}
    QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDateTimeEdit:hover {{ border-color: {p.subtext}; }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {{ border: 2px solid {accent}; padding: 6px 10px; background-color: {p.card}; }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {p.subtext}; background-color: {p.header}; }}
    QGroupBox {{ font-weight: 600; border: 1px solid {p.border}; border-top-color: {card_top}; border-bottom-color: {card_bottom};
        border-radius: 10px; margin-top: 12px; padding-top: 10px; background-color: {p.card}; }}
    QGroupBox::title {{ subcontrol-origin: margin; padding: 0 6px; color: {p.title_accent}; }}
    QWidget#titleBar {{ background: {relief(p.header, 106, 98)}; border-bottom: 1px solid {btn_edge}; }}
    QLabel#titleLabel {{ font-weight: 600; font-size: {font_size + 1}pt; color: {p.text}; background: transparent; }}
    QLabel#brandLabel {{ font-weight: 800; font-size: 22pt; color: {p.text}; background: transparent;
        letter-spacing: 1px; padding-left: 2px; }}
    QLabel#brandLogo {{ background: transparent; }}
    QCheckBox, QRadioButton {{ color: {p.text}; spacing: 8px; background: transparent; }}
    QCheckBox::indicator, QRadioButton::indicator {{ width: 18px; height: 18px; border: 1.5px solid {p.subtext};
        background: {relief(p.input, 104, 96)}; border-radius: 5px; }}
    QRadioButton::indicator {{ border-radius: 9px; }}
    QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {accent}; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {relief(accent, 112, 94)}; border-color: {acc_edge};
        image: url({check_img}); }}
    QRadioButton::indicator:checked {{ image: none; border: 5px solid {accent}; background-color: #ffffff; }}
    QCheckBox::indicator:disabled {{ border-color: {p.border}; background-color: {p.header}; }}
    QPlainTextEdit, QTextEdit, QTextBrowser {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: {R}px; color: {p.text};
        selection-background-color: {accent}; selection-color: {p.on_accent}; padding: 6px; }}
    QComboBox QAbstractItemView {{ background-color: {p.card}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px;
        selection-background-color: {p.selection}; selection-color: {p.text}; outline: none; padding: 4px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none;
        border-left: 1px solid {p.border}; margin: 4px 0; }}
    QComboBox::down-arrow {{ image: url({chev_down}); width: 14px; height: 14px; }}
    QComboBox::down-arrow:disabled {{ image: url({chev_dim}); }}
    QDateTimeEdit::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; width: 26px; border: none;
        border-left: 1px solid {p.border}; margin: 4px 0; }}
    QDateTimeEdit::down-arrow {{ image: url({chev_down}); width: 14px; height: 14px; }}
    QSpinBox {{ padding-right: 28px; }}
    QSpinBox::up-button, QSpinBox::down-button {{ subcontrol-origin: padding; width: 24px; border: none;
        border-left: 1px solid {p.border}; background: {relief(p.button, 110, 95)}; }}
    QSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: {R}px; border-bottom: 1px solid {p.border}; }}
    QSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: {R}px; }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {relief(p.hover, 110, 95)}; }}
    QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{ background: {p.selection}; }}
    QSpinBox::up-arrow {{ image: url({chev_up}); width: 11px; height: 11px; }}
    QSpinBox::down-arrow {{ image: url({chev_down}); width: 11px; height: 11px; }}
    QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url({chev_dim}); }}
    QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url({chev_dim}); }}
    QLabel#subtle {{ color: {p.subtext}; }}
    QLabel#statusLabel {{ color: {p.subtext}; }}
    QLabel#roleStatusLabel {{ color: {p.text}; background-color: {p.card}; border: 1px solid {p.border};
        border-radius: 8px; padding: 4px 10px; font-weight: 600; font-size: 12px; }}
    QLabel#roleStatusLabel:hover {{ border-color: {accent}; color: {accent}; background-color: {p.hover}; }}
    QLabel#readonlyBadge {{ color: {p.warning[0]}; background-color: {p.warning[1]}; border: 1px solid {p.warning[2]};
        border-radius: 10px; padding: 4px 10px; font-weight: 600; }}
    QLabel#updateLabel {{ color: {p.info[0]}; font-weight: 600; }}
    QListWidget::item {{ padding: 5px 8px; border-radius: 6px; border: 1px solid transparent; }}
    QListWidget::item:selected, QTreeWidget::item:selected {{ background-color: {p.selection}; color: {p.text};
        border: 1px solid {sel_edge}; }}
    QListWidget::item:hover, QTreeWidget::item:hover {{ background-color: {p.hover}; }}
    QSplitter::handle {{ background-color: {p.border}; }}
    QScrollArea {{ background: transparent; border: none; }}
    QWidget#detailsPane, QWidget#detailsViewport {{ background-color: {p.card}; }}
    QProgressBar {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: 7px; text-align: center; color: {p.text}; }}
    QProgressBar::chunk {{ background: {relief(accent, 114, 92)}; border-radius: 6px; }}
    QPushButton#winBtn, QPushButton#btnClose {{ background: transparent; border: none; border-radius: 6px; padding: 0; }}
    QPushButton#winBtn:hover {{ background-color: {p.hover}; }}
    QPushButton#btnClose:hover {{ background-color: #FF453A; color: #ffffff; }}
    QPushButton {{ background: {relief(p.button, 110, 95)}; border: 1px solid {btn_edge}; border-top-color: {btn_light};
        border-radius: {R}px; padding: 7px 16px; font-weight: 600; color: {p.text}; outline: none; }}
    QPushButton:hover {{ background: {relief(p.hover, 110, 95)}; border-color: {p.subtext}; border-top-color: {btn_light}; }}
    QPushButton:pressed {{ background: {p.selection}; border-color: {accent}; padding-top: 8px; padding-bottom: 6px; }}
    QPushButton:checked {{ background: {p.selection}; border-color: {accent}; color: {p.text}; }}
    QPushButton:disabled {{ color: {p.subtext}; background: {p.header}; border-color: {p.border}; }}
    {solid_btn("QPushButton#btnPrimary", accent, p.on_accent)}
    QPushButton#btnPrimary:hover {{ background: {relief(acc_hover, 112, 94)}; }}
    QPushButton#btnPrimary:pressed {{ background: {acc_press}; }}
    {btn("btnSuccess", p.success)} {btn("btnDanger", p.danger)} {btn("btnWarning", p.warning)} {btn("btnInfo", p.info)}
    QPushButton#historyBtn {{ background: {relief(p.button, 110, 95)}; border: 1px solid {btn_edge}; border-top-color: {btn_light};
        border-radius: 14px; padding: 6px 14px; }}
    QPushButton#historyBtn:hover {{ border-color: {accent}; color: {p.title_accent}; }}
    QPushButton#chipBtn {{ padding: 5px 16px; min-height: 16px; border-radius: 14px; border: 1px solid {btn_edge};
        border-top-color: {btn_light}; background: {relief(p.button, 110, 95)}; color: {p.text}; font-weight: 600; }}
    QPushButton#chipBtn:checked {{ background: {relief(accent, 112, 94)}; color: {p.on_accent}; border: 1px solid {acc_edge}; }}
    QPushButton#chipBtn:hover {{ border-color: {accent}; }}
    QTableWidget, QTableView {{ background-color: {p.card}; alternate-background-color: {p.header}; border: 1px solid {p.border}; border-radius: 8px;
        gridline-color: {p.border}; selection-background-color: {p.selection}; selection-color: {p.text}; outline: none; }}
    QTableWidget::item, QTableView::item {{ padding: 4px; border-bottom: 1px solid {p.border}; border-right: 1px solid {p.border}; }}
    QTableWidget::item:selected, QTableView::item:selected {{ background-color: {p.selection}; color: {p.text};
        border-top: 1px solid {sel_edge}; border-bottom: 1px solid {sel_edge}; border-right: 1px solid {sel_line}; padding-top: 3px; }}
    QTableWidget::item:selected:first, QTableView::item:selected:first {{ border-left: 4px solid {accent}; padding-left: 2px; }}
    QTableWidget::item:selected:last, QTableView::item:selected:last {{ border-right: 1px solid {sel_edge}; }}
    QAbstractScrollArea::viewport {{ background-color: {p.card}; }}
    QHeaderView::section {{ background: {relief(p.header, 105, 98)}; color: {p.subtext}; padding: 9px 12px; border: none;
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
    #sideCard, #dashCard {{ background: {relief(p.card, 103, 99)}; border: 1px solid {p.border}; border-top-color: {card_top};
        border-bottom-color: {card_bottom}; border-radius: 12px; padding: 14px; }}
    #sideCard QLabel, #dashCard QLabel {{ background: transparent; border: none; }}
    #specBox {{ background-color: {p.input}; border: 1px solid {p.border}; border-radius: 8px; padding: 8px; font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 9.5pt; }}
    QTabWidget::pane {{ border: 1px solid {p.border}; border-top-color: {card_top}; border-radius: 10px; background: {p.card}; top: 6px; }}
    QTabBar {{ qproperty-iconSize: 22px 22px; qproperty-drawBase: 0; background: {p.input}; border: 1px solid {input_top}; border-radius: 10px; }}
    QTabBar::tab {{ background: transparent; color: {p.subtext}; padding: 7px 16px; margin: 3px 2px; border-radius: 8px;
        border: 1px solid transparent; font-weight: 600; }}
    QTabBar::tab:first {{ margin-left: 3px; }}
    QTabBar::tab:last {{ margin-right: 3px; }}
    QTabBar::tab:hover {{ color: {p.text}; background: {p.hover}; }}
    QTabBar::tab:selected {{ color: {p.text}; background: {relief(p.button, 112, 96)}; border: 1px solid {btn_edge}; border-top-color: {btn_light}; }}
    QListWidget, QTreeWidget {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 8px; outline: none; qproperty-iconSize: 22px 22px; }}
    QTableWidget, QTableView {{ qproperty-iconSize: 22px 22px; }}
    QMenu {{ icon-size: 22px; }}
    QPushButton#drivePicker {{ background: {relief(p.button, 110, 95)}; color: {p.text}; border: 1.5px solid {p.title_accent};
        border-radius: 14px; padding: 4px 10px; font-weight: 700; font-size: 10.5pt; text-align: center; }}
    QPushButton#drivePicker:hover {{ background: {p.selection}; }}
    QPushButton#drivePicker:pressed {{ background-color: {accent}; color: {p.on_accent}; }}
    QPushButton#drivePicker::menu-indicator {{ image: none; width: 0; }}
    QMenu {{ background-color: {p.card}; border: 1px solid {p.border}; border-radius: 10px; padding: 6px; }}
    QMenu::item {{ padding: 7px 22px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {relief(accent, 110, 94)}; color: {p.on_accent}; }}
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
