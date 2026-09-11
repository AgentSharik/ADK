"""Внешний вид: заголовок окна, бейджи-«пилюли», набор и меню столбцов, подгонка ширины столбцов, стили таблиц
(сетка, полоска выделения, скроллбары, чипы), собственный выбор цвета, артефакты документации.
"""
import os
import pytest
from types import SimpleNamespace

from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from adk import theme


# ------------------------------------------------------------------ 1. заголовок окна и бейджи
def test_title_bar_window_controls(qapp):
    """Шапка: развернуть/восстановить и F11 меняют состояние окна, иконка кнопки следует за состоянием."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from adk.widgets import FramelessMainWindow, TitleBar
    w = FramelessMainWindow()
    c = QWidget(); w.setCentralWidget(c)
    w.title_bar = TitleBar(w, "t", is_main=True)
    QVBoxLayout(c).addWidget(w.title_bar)
    w.show(); qapp.processEvents()
    assert w.title_bar.label.text() == ""           # в главном окне заголовок не дублируется
    w.title_bar.toggle_max(); qapp.processEvents()
    assert w.isMaximized()
    w.title_bar.toggle_max(); qapp.processEvents()
    assert not w.isMaximized()
    w.toggle_fullscreen(); qapp.processEvents()
    assert w.isFullScreen()
    w.toggle_fullscreen(); qapp.processEvents()
    assert not w.isFullScreen()
    assert not w.title_bar.btn_max.icon().isNull() and not w.title_bar.btn_min.icon().isNull()
    w.close()


def test_badge_delegate_draws_pill(qapp):
    """Колонки «Учётка» и «Сеть» рисуются «пилюлей» с рамкой: в центре ячейки — цвет заливки, а не фон таблицы."""
    from PyQt6.QtGui import QImage
    from adk.main_window import BADGE_ROLE, BadgeDelegate, StatusItem, parse_color
    from adk.widgets import app_palette
    from PyQt6.QtWidgets import QTableWidget
    assert parse_color("rgba(34,197,94,0.5)").alpha() == 128 and parse_color("#ff0000").red() == 255
    t = QTableWidget(1, 1)
    dlg = BadgeDelegate(t)
    t.setItemDelegateForColumn(0, dlg)
    it = StatusItem("● В сети", "online")
    assert it.data(BADGE_ROLE) == "online"
    t.setItem(0, 0, it)
    t.setColumnWidth(0, 110); t.resize(140, 80); t.show(); qapp.processEvents()
    img: QImage = t.viewport().grab().toImage()
    r = t.visualItemRect(it)
    pill = dlg.last_pill
    assert r.contains(pill.toRect()) and pill.width() < r.width()   # пилюля уже ячейки и лежит внутри неё
    inside = img.pixelColor(int(pill.left()) + 3, int(pill.center().y()))  # слева от текста, внутри заливки
    corner = img.pixelColor(r.left() + 1, r.top() + 1)
    fg, _bg, _bd = app_palette().badge("online")
    assert inside != corner                              # внутри пилюли — своя заливка, угол ячейки — фон таблицы
    assert inside.name() != parse_color(fg).name()       # и это не цвет текста
    t.close()


# ------------------------------------------------------------------ 2. столбцы таблицы
def test_default_visible_columns():
    from adk.main_window import COLUMNS, DEFAULT_HIDDEN, DEFAULT_VISIBLE

    visible = [c for i, c in enumerate(COLUMNS) if i not in DEFAULT_HIDDEN]
    assert visible == ["Логин", "ФИО", "Учётка", "Имя ПК", "Сеть", "Телефон", "IP-тел"]
    assert set(DEFAULT_VISIBLE) <= set(COLUMNS)
    assert {"Отдел", "Кабинет", "Адрес", "Организация", "Должность", "Последний вход"} <= {COLUMNS[i] for i in DEFAULT_HIDDEN}


@pytest.mark.parametrize("name", ["printer_search", "health_overview"])
def test_docs_exist(name):
    assert os.path.exists(os.path.join(os.path.dirname(__file__), "..", "docs", f"{name}.png"))


def test_column_menu_checklist(qapp, monkeypatch):
    """ПКМ по заголовку — простой список столбцов с галочками; по умолчанию видны только 7 стандартных."""
    from adk import main_window
    monkeypatch.setattr(main_window.ADApp, "start_scan", lambda self: None)
    w = main_window.ADApp("CORP\\admin", "x")
    w.reset_columns()
    visible = [main_window.COLUMNS[c] for c in range(w.table.columnCount()) if not w.table.isColumnHidden(c)]
    assert visible == list(main_window.DEFAULT_VISIBLE)
    assert "Последний вход" not in visible and "Кабинет" not in visible
    menu = w.build_column_menu(0)
    checkable = [a for a in menu.actions() if a.isCheckable()]
    assert [a.text() for a in checkable] == main_window.COLUMNS
    assert not any("Добавить столбец" in a.text() or "Скрыть" in a.text() for a in menu.actions())
    dept = next(a for a in checkable if a.text() == "Отдел")
    assert not dept.isChecked()
    dept.setChecked(True)            # галочка — столбец появился
    assert not w.table.isColumnHidden(main_window.COLUMNS.index("Отдел"))
    w.close()


def test_dialogs_reexport():
    """dialogs остаётся точкой входа для старого кода и тестов."""
    from adk import dialogs
    from adk.freeip_ui import FreeIPDialog
    from adk.inventory_ui import InventoryDialog
    assert dialogs.FreeIPDialog is FreeIPDialog and dialogs.InventoryDialog is InventoryDialog
    assert not os.path.exists(os.path.join(os.path.dirname(dialogs.__file__), "inventory_dialog_old.py"))


def test_compare_pc_dialog_keeps_all_cells(qapp, monkeypatch, tmp_path):
    """Сравнение ПК: таблица заполняется при включённой сортировке — раньше часть ячеек «терялась»."""
    from adk.fleet import ComparePCDialog
    from adk import netutils, software
    monkeypatch.setattr(netutils, "get_computer_specs_dict",
                        lambda n: {"os": {"Название": "Windows 11" if n == "A" else "Windows 10"},
                                   "cpu": {"Название": "i5"}, "ram": [], "disks": [], "printers": []})
    monkeypatch.setattr(software, "cached_software", lambda n: ([{"name": "Chrome"}] if n == "A" else [{"name": "Chrome"}, {"name": "1C"}], None))
    monkeypatch.setattr(netutils, "get_computer_printers_detailed", lambda n: [])
    d = ComparePCDialog("A", "B", SimpleNamespace(get_conn=lambda: None), None)
    d.only_diff.setChecked(False)
    d._loaded(d._load())
    t = d.t_specs
    assert t.rowCount() >= 2
    for r in range(t.rowCount()):
        assert all(t.item(r, c) is not None and t.item(r, c).text() for c in range(3)), f"пустая ячейка в строке {r}"
    keys = {t.item(r, 0).text() for r in range(t.rowCount())}
    assert any("ОС" in k or "os" in k.lower() for k in keys)
    assert d.t_soft.rowCount() == 2 and (d.t_soft.item(1, 2).text() == "—" or not d.t_soft.item(1, 2).icon().isNull())
    d.close()


# ------------------------------------------------------------------ 3. подгонка ширины столбцов
def test_fit_columns_widens_to_header_and_content(qapp):
    from adk.widgets import fit_columns
    t = QTableWidget(2, 3)
    t.setHorizontalHeaderLabels(["Принтер", "В сети", "Очень длинный заголовок столбца"])
    t.setColumnWidth(1, 20)          # «В сети» намеренно зажат — как было в «Принтерах парка»
    t.setItem(0, 0, QTableWidgetItem("HP LaserJet M404dn"))
    t.setItem(0, 1, QTableWidgetItem("3"))
    t.setItem(0, 2, QTableWidgetItem("очень длинное описание " * 12))
    t.setItem(1, 0, QTableWidgetItem("Kyocera"))
    t.resize(900, 300)
    t.show()
    fit_columns(t, max_width=300)
    fm = t.fontMetrics()
    assert t.columnWidth(1) >= fm.horizontalAdvance("В сети") + 20          # заголовок влезает целиком
    assert t.columnWidth(0) >= fm.horizontalAdvance("HP LaserJet M404dn")   # содержимое влезает
    assert t.columnWidth(2) <= 300                                          # потолок соблюдён…
    assert t.rowHeight(0) > t.rowHeight(1)                                  # …а длинный текст перенёсся на 2–3 строки
    assert t.rowHeight(0) <= t.verticalHeader().defaultSectionSize() * 3
    t.close()


def test_fit_columns_respects_stretch_flag(qapp):
    from adk.widgets import fit_columns
    t = QTableWidget(1, 2)
    t.setHorizontalHeaderLabels(["A", "B"])
    t.setItem(0, 0, QTableWidgetItem("a"))
    t.setItem(0, 1, QTableWidgetItem("b"))
    fit_columns(t, stretch_last=True, wrap=False)
    assert t.horizontalHeader().stretchLastSection()
    fit_columns(t, stretch_last=False, wrap=False)
    assert not t.horizontalHeader().stretchLastSection()
    t.close()


# ------------------------------------------------------------------ 4. свой выбор цвета
def test_color_picker_sources_stay_in_sync(qapp):
    from adk.colorpicker import ColorPickerDialog
    d = ColorPickerDialog("#38bdf8", None, "Акцентный цвет")
    assert d.color() == "#38bdf8" and d.hex.text() == "#38bdf8" and d.sliders["r"].value() == 0x38
    d._from_hex("fb923c")                                # можно без решётки
    assert d.color() == "#fb923c" and d.sliders["g"].value() == 0x92 and d.lbl_new.text().endswith("#FB923C")
    d.sliders["b"].setValue(0)                           # ползунок → цвет и HEX
    assert d.color() == "#fb9200" and d.hex.text() == "#fb9200"
    d._from_hue(0.0)                                     # оттенок → красный при той же насыщенности/яркости
    assert d.color().startswith("#fb") and d.hex.text() == d.color()
    d._from_sv(0.0, 1.0)                                 # без насыщенности при полной яркости — белый
    assert d.color() == "#ffffff" and "светлый" in d.lbl_info.text()
    d.preset_btns[1].click()                             # пресет
    assert d.color() == "#34c759"                            # второй пресет палитры
    d.btn_reset.click()                                  # «Как было»
    assert d.color() == "#38bdf8"
    d._from_hex("#zzz")                                  # мусор игнорируется
    assert d.color() == "#38bdf8"
    d.close()


def test_design_dialog_uses_own_picker(qapp, monkeypatch):
    """«+ Свой…» в оформлении открывает наш ColorPickerDialog, а не стандартный QColorDialog."""
    from adk import colorpicker, dialogs
    from adk.dialogs import DesignSettingsDialog
    assert not hasattr(dialogs, "QColorDialog")
    opened = []

    def fake_get(initial, parent=None, title=""):
        opened.append((initial, title))
        return "#fb923c"
    monkeypatch.setattr(colorpicker.ColorPickerDialog, "get_color", classmethod(lambda cls, *a, **k: fake_get(*a, **k)))
    app = SimpleNamespace(on_theme_changed=lambda: None)
    d = DesignSettingsDialog(app, None)
    d.btn_accent_custom.click()
    assert opened and opened[0][1] and d.design["accent_color"] == "#fb923c"
    d.close()


# ------------------------------------------------------------------ 5. стили таблиц и чипов
def test_selected_rows_keep_separator():
    """Стиль таблицы: у выделенной ячейки остаётся нижняя рамка (цвет фона карточки), иначе несколько
    выбранных строк сливаются в одно пятно; левая цветная полоска-«скобка» убрана."""
    for is_dark in (True, False):
        css = theme.build_stylesheet("#101828", is_dark, "Segoe UI", 10, "#38bdf8")
        sel = css.split("QTableWidget::item:selected")[1].split("}")[0]
        assert "border-bottom: 1px solid" in sel and "border-right: 1px solid" in sel
        assert "item:selected:first" in css and "border-left: 4px solid" in css   # акцентная полоска у выбранной строки


def test_stylesheet_chips_grid_and_scrollbars():
    css = theme.build_stylesheet("#101828", True, "Segoe UI", 10, "#38bdf8")
    chip = css.split("QPushButton#chipBtn {")[1].split("}")[0]
    chip_on = css.split("QPushButton#chipBtn:checked {")[1].split("}")[0]
    assert "font-weight: 600" in chip and "font-weight: bold" not in chip_on      # ширина чипа не меняется при выборе
    item = css.split("QTableWidget::item, QTableView::item {")[1].split("}")[0]
    assert "border-right: 1px solid" in item and "border-bottom: 1px solid" in item   # сетка: столбцы и строки
    assert "item:selected:first" in css and "border-left: 4px solid" in css          # акцентная полоска выбранной строки
    handle = css.split("QScrollBar::handle:vertical {")[1].split("}")[0]
    assert "border-radius" in handle and "min-height: 32px" in handle
    assert theme.Palette(True, "#38bdf8").title_accent in handle                     # ручка — акцентного цвета


def test_color_picker_preview_both_labels_same_style(qapp):
    from adk.colorpicker import ColorPickerDialog
    d = ColorPickerDialog("#38bdf8", None, "Акцентный цвет")
    d._from_hex("#fb923c")
    assert d.lbl_old.text().startswith("было") and d.lbl_old.text().endswith("#38BDF8")
    assert d.lbl_new.text().startswith("стало") and d.lbl_new.text().endswith("#FB923C")
    assert "font-weight: bold" in d.lbl_old.styleSheet() and "font-size: 8.5pt" not in d.lbl_old.styleSheet()
    assert d.lbl_old.size() == d.lbl_new.size()
    d.close()


# ------------------------------------------------------------------ диалоги не ложатся на шапку главного окна
def test_dialog_opens_below_main_header(qapp):
    """Диалог, который помещается под шапкой, открывается под ней: его верх ≥ верх окна + 64 px,
    а крестик диалога не оказывается рядом с эмблемой ADK и кнопками «— ▢ ✕»."""
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
    from adk.widgets import FramelessDialog, FramelessMainWindow, TitleBar
    w = FramelessMainWindow()
    box = QWidget(); QVBoxLayout(box).addWidget(TitleBar(w, "", is_main=True)); w.setCentralWidget(box)
    w.resize(780, 580); w.show()                 # offscreen-экран 800×600 — окно целиком на экране
    w.move(0, 0)
    qapp.processEvents()
    d = FramelessDialog("Проба", w, (400, 500))  # по центру он лёг бы на y=40 — прямо на шапку
    d.show()
    qapp.processEvents()
    assert d.y() >= w.frameGeometry().y() + FramelessDialog.HEADER_H
    assert abs((d.x() + d.width() // 2) - (w.frameGeometry().x() + w.frameGeometry().width() // 2)) <= 2
    d.close()
    tall = FramelessDialog("Большой", w, (400, 560))    # не помещается — центрируем как раньше, экран не покидает
    tall.show()
    qapp.processEvents()
    assert tall.y() >= 0
    tall.close()
    w.close()


def test_login_error_box_not_clipped_and_legend_beside_map(qapp):
    """3.3.0: длинный текст ошибки входа переносится и окно подрастает (раньше вторую строку «съедало»);
    легенда карты подсети — справа от карты, а не под ней; между ячейками есть просвет."""
    from adk.dialogs import LoginDialog
    from adk.freeip_ui import FreeIPDialog, SubnetMap
    msg = "Контроллер домена недоступен: dc01\nСохранённые учётные данные подставлены — проверьте сеть и повторите."
    d = LoginDialog(msg, saved_user="CORP\\admin", saved_password="pw")
    d.show(); qapp.processEvents()
    assert "<br>" in d.lbl_error.text() and d.lbl_error.height() >= d.lbl_error.heightForWidth(d.lbl_error.width()) - 1
    assert d.height() >= d.sizeHint().height() - 1
    d.close()
    f = FreeIPDialog()
    f.show(); qapp.processEvents()
    assert f.legend_box.x() > f.map.x() + f.map.width() - 1 and abs(f.legend_box.y() - f.map.y()) < 40
    assert SubnetMap.GAP >= 4
    f.close()


def test_ten_unique_themes_without_navy_and_black():
    """Ровно 10 тем (5 тёмных / 5 светлых), среди них по одной градиентной на каждую сторону;
    все цвета разные, нет чистого чёрного и тёмно-синих фонов."""
    from PyQt6.QtGui import QColor
    from adk.theme import PRESET_THEMES, theme_colors, theme_design
    assert len(PRESET_THEMES) == 10
    darks = [k for k, t in PRESET_THEMES.items() if t["is_dark"]]
    assert len(darks) == 5
    grads = {k: t for k, t in PRESET_THEMES.items() if t["type"] == "grad"}
    assert len(grads) == 2 and sorted(t["is_dark"] for t in grads.values()) == [False, True]
    names = [t["name"] for t in PRESET_THEMES.values()]
    assert len(set(names)) == 10
    bgs = set()
    for key, t in PRESET_THEMES.items():
        c1, c2 = theme_colors(t)
        cols = [c1, c2, t["panel"], t["accent"], t["text"]]
        for c in cols:
            assert c.lower() != "#000000", key
        c = QColor(c1)
        # тёмно-синий: синий заметно доминирует над красным при тёмном фоне
        assert not (t["is_dark"] and c.blue() > c.red() + 25 and c.blue() > c.green() + 15), key
        bgs.add(c1.lower())
        d = theme_design(t)
        assert d["is_dark"] == t["is_dark"] and d["accent_color"] == t["accent"] and d["panel_color"] == t["panel"]
        if t["type"] == "grad":
            assert c1 != c2 and "qlineargradient" in d["bg_style"] and c1 in d["bg_style"] and c2 in d["bg_style"]
        else:
            assert d["bg_style"] == f"background-color: {c1};"
    assert len(bgs) == 10                                    # фоны не повторяются
    # темы различимы не только акцентом: фон и панель у любых двух тем одной «стороны» отличаются заметно
    def dist(a, b):
        ca, cb = QColor(a), QColor(b)
        return abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue())
    items = list(PRESET_THEMES.items())
    for i, (k1, t1) in enumerate(items):
        for k2, t2 in items[i + 1:]:
            if t1["is_dark"] != t2["is_dark"]:
                continue
            assert dist(theme_colors(t1)[0], theme_colors(t2)[0]) + dist(t1["panel"], t2["panel"]) >= 24, (k1, k2)
            assert dist(t1["accent"], t2["accent"]) >= 60, (k1, k2)


def test_gradient_theme_tile_and_preset_apply(qapp):
    """Дано: тема «Сумерки» (градиент). Выбираем её в «Оформлении».
    Ожидаем: bg_style — градиент с обоими цветами, is_dark=True, плитка помечена выбранной, «Графит» — нет."""
    from types import SimpleNamespace
    from adk import config
    from adk.dialogs import DesignSettingsDialog
    from adk.theme import PRESET_THEMES
    app = SimpleNamespace(on_theme_changed=lambda: None)
    d = DesignSettingsDialog(app)
    d.preset("dusk")
    bg = config.settings.design["bg_style"]
    t = PRESET_THEMES["dusk"]
    assert "qlineargradient" in bg and t["c1"] in bg and t["c2"] in bg
    assert config.settings.design["is_dark"] is True and config.settings.design["panel_color"] == t["panel"]
    assert d.tiles["dusk"]._selected and not d.tiles["dark"]._selected
    d.preset("dawn")
    assert config.settings.design["is_dark"] is False and PRESET_THEMES["dawn"]["c2"] in config.settings.design["bg_style"]
    d.preset("dark")
    d.close()


def test_buttons_get_outline_icons_instead_of_emoji(qapp):
    """Ведущий эмодзи в тексте кнопки/вкладки превращается в контурную иконку; сам текст остаётся."""
    from PyQt6.QtWidgets import QPushButton, QTabWidget, QWidget, QLabel
    from adk import icons
    b = QPushButton("📡 Пинг")
    assert b.text() == "Пинг" and not b.icon().isNull() and b._adk_icon == ("waveform.path.ecg", "info")
    b.setText("🔄 Обновить")
    assert b.text() == "Обновить" and b._adk_icon[0] == "arrow.clockwise"
    b.setText("Без иконки")
    assert b.text() == "Без иконки"
    t = QTabWidget()
    t.addTab(QWidget(), "🛡️ Безопасность")
    assert t.tabText(0) == "Безопасность" and not t.tabIcon(0).isNull()
    lbl = QLabel("👤 Иванов")
    assert "<img" in lbl.text() and "Иванов" in lbl.text() and "👤" not in lbl.text()
    assert icons.strip("🖨️ Принтеры") == "Принтеры"
    svg = icons.svg("person.crop.circle", "#ffffff")
    assert b"stroke-width" in svg and b"fill=\"none\"" in svg           # контур, а не заливка

    # 3.4.4: заливные кнопки (btnSuccess, btnDanger, btnPrimary...) получают контрастную иконку
    b_succ = QPushButton("➕ Добавить")
    b_succ.setObjectName("btnSuccess")
    assert icons.button_icon_color(b_succ, "success") == "#ffffff"
    b_pri = QPushButton("🔍 Найти")
    b_pri.setObjectName("btnPrimary")
    assert not b_pri.icon().isNull()
    assert icons.ICON_PX == 22 and icons.LABEL_PX == 19


def test_subnet_map_cells_fit_three_digits(qapp):
    from PyQt6.QtGui import QFontMetrics, QFont
    from adk.freeip_ui import SubnetMap
    m = SubnetMap()
    m.resize(1100, 320)
    assert SubnetMap.MAX_CELL >= 30 and SubnetMap.GAP >= 4
    f = QFont(); f.setPointSizeF(max(7, min(11, SubnetMap.MAX_CELL * 0.29)))
    assert QFontMetrics(f).horizontalAdvance("254") <= SubnetMap.MAX_CELL - SubnetMap.GAP - 1   # внутри ячейки


def test_plugins_dialog_and_puzzle_icon(qapp):
    """3.5.0: кнопка «Плагины» и менеджер расширений PluginsDialog."""
    from PyQt6.QtWidgets import QPushButton
    from adk.dialogs import PluginsDialog
    b = QPushButton("🧩 Плагины")
    assert b.text() == "Плагины" and not b.icon().isNull() and b._adk_icon[0] == "puzzlepiece"
    dlg = PluginsDialog()
    assert dlg.table.columnCount() == 4 and dlg.btn_template.text().endswith("Создать шаблон плагина")
    dlg.close()


def test_role_welcome_dialog(qapp, monkeypatch):
    """3.5.0: RoleWelcomeDialog после входа со справкой по роли и галочкой скрытия."""
    from adk.dialogs import RoleWelcomeDialog
    from adk import access, config
    access.set_rights(pc=True, ad=True)
    dlg = RoleWelcomeDialog("admin")
    assert "полный доступ" in dlg.body.itemAt(0).widget().layout().itemAt(0).widget().text()
    dlg.chk_dont_show.setChecked(True)
    dlg._save_and_close()
    assert config.settings.hide_role_welcome is True
    config.settings.hide_role_welcome = False


def test_diskmap_tabs_three_and_no_profiles(qapp):
    """3.5.0: в карте диска 3 вкладки («Папки», «Файлы», «Почистить»), вкладка «Профили» удалена."""
    from adk.health_ui import HealthDialog
    hd = HealthDialog("WS-101", None, None)
    assert hd.usage_tabs.count() == 3
    tab_titles = [hd.usage_tabs.tabText(i) for i in range(hd.usage_tabs.count())]
    assert "Профили" not in "".join(tab_titles)
    assert "Папки" in tab_titles[0] and "Файлы" in tab_titles[1] and "Почистить" in tab_titles[2]
    hd.close()


def test_freeip_legend_no_inventory(qapp):
    """3.5.0: из легенды поиска свободного IP удален пункт «ПК из скана»."""
    from adk.freeip_ui import FreeIPDialog
    fd = FreeIPDialog()
    labels = [fd.legend_box.layout().itemAtPosition(r, 1).widget().text() for r in range(fd.legend_box.layout().rowCount()) if fd.legend_box.layout().itemAtPosition(r, 1)]
    assert not any("скана" in t for t in labels)
    assert any("свободен" in t for t in labels)
    fd.close()


def test_table_item_no_double_checkmarks(qapp):
    """3.5.0: одиночный эмодзи (галочка) не дублируется текстом рядом с иконкой."""
    from PyQt6.QtWidgets import QTableWidgetItem
    it = QTableWidgetItem("✔")
    assert it.text() == "" and not it.icon().isNull()


def test_plugins_manager_create_toggle_delete(qapp, tmp_path, monkeypatch):
    """3.5.1: менеджер плагинов — создать шаблон, включить/выключить двойным кликом, удалить."""
    from adk import config
    from adk.dialogs import PluginsDialog
    monkeypatch.setattr(config.settings, "plugins_dir", str(tmp_path / "pl"))
    dlg = PluginsDialog()
    assert dlg.table.rowCount() == 0 and "пуста" in dlg.status.text()
    dlg.btn_template.click()
    assert dlg.table.rowCount() == 1 and dlg.table.item(0, 0).text() == "_template_plugin.py"
    assert "Выключен" in dlg.table.item(0, 3).text() and dlg.btn_toggle.text() == "Включить"
    dlg._toggle()
    assert dlg.table.item(0, 0).text() == "template_plugin.py" and "Включён" in dlg.table.item(0, 3).text()
    assert dlg.table.item(0, 1).text() == "Профиль пользователя" and dlg.table.item(0, 2).text() == "только чтение"
    monkeypatch.setattr("adk.widgets.MessageBox.question", lambda *a, **k: True)
    dlg._delete()
    assert dlg.table.rowCount() == 0
    dlg.close()


def test_role_welcome_shown_once_after_access_resolved(qapp, monkeypatch):
    """3.5.1: справка по роли показывается главным окном после определения роли, один раз, и не при hide_role_welcome."""
    from adk import config
    from adk.main_window import ADApp
    monkeypatch.setattr(config.settings, "hide_role_welcome", False)
    w = ADApp("admin", "pw")
    try:
        w.show_role_welcome()
        assert w.role_welcome.isVisible() and "Роль" in w.role_welcome.windowTitle() or w.role_welcome.isVisible()
        w.role_welcome.close()
        w._welcome_shown = False
        monkeypatch.setattr(config.settings, "hide_role_welcome", True)
        w.role_welcome = None
        w.show_role_welcome()
        assert w.role_welcome is None                      # выключено галочкой — не показываем
    finally:
        w.close()


def test_login_show_button_fits_its_caption(qapp):
    """3.5.5: кнопка «Показать» в окне входа была фиксированной ширины 100 px и при шрифте 11–12 pt
    обрезала подпись. Теперь ширина считается от текста."""
    from adk.dialogs import LoginDialog
    d = LoginDialog("", saved_user="", saved_password="")
    need = d.btn_eye.fontMetrics().horizontalAdvance("Показать") + 16
    assert d.btn_eye.width() >= need
    d.close()


def test_user_card_long_title_does_not_stretch_window(qapp):
    """3.5.5: очень длинная должность в подзаголовке карточки переносится, а не растягивает окно."""
    from types import SimpleNamespace
    from adk.dialogs import UserCardDialog
    from tests.test_gui import FakeEntry
    app = SimpleNamespace(get_conn=lambda: None, admin_name="admin")
    long_title = "Главный специалист по очень длинному названию должности для проверки переноса строки"
    e = FakeEntry("CN=Сидоров,OU=x", sAMAccountName="sidorov", displayName="Сидоров С.С.", sn="Сидоров",
                  givenName="Семён", userAccountControl=512, title=long_title, department="Бухгалтерия",
                  company="ООО «Пример»")
    d = UserCardDialog(e, app)
    d.show()
    qapp.processEvents()
    assert d.lbl_sub.wordWrap()
    assert d.width() <= 1300
    d.close()
