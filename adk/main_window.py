"""Главное окно: дашборд, единый поиск, таблица результатов и инспектор."""
from __future__ import annotations

import html
import logging
import os
import subprocess
import time

from PyQt6.QtCore import QRectF, QSettings, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QCursor, QFont, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QCompleter, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import access, ad, attention, db, export, nettools, netutils, plugins, updates
from .attention_ui import AttentionDialog
from .config import APP_TITLE, CREATE_NO_WINDOW, SEARCH_RESULT_LIMIT, settings
from .dialogs import (
    AuditLogDialog, DesignSettingsDialog, FreeIPDialog, InventoryDialog, PingDialog, PrintersDialog,
    RegisterUserDialog, UserCardDialog,
)
from .extras import NotifySettingsDialog
from .fleet import ComparePCDialog, LogonsDialog, MassPingDialog, SoftwareDialog, wake_single
from .i18n import tr
from .tools import BulkOperationsDialog, GroupCompareDialog, HealthDialog, HistoryDialog, NotesDialog
from .tray import GlobalHotkey, Tray, app_icon
from .widgets import (
    BadgeButton, FlowLayout, FramelessMainWindow, MessageBox, TitleBar, app_palette, fit_columns, run_in_background,
)
from .workers import PCScannerWorker, SearchWorker

log = logging.getLogger(__name__)

COLUMNS = ["Логин", "ФИО", "Учётка", "Имя ПК", "Сеть", "Телефон", "IP-тел", "Кабинет", "Адрес",
           "Организация", "Отдел", "Должность", "Последний вход"]
COL_LOGIN, COL_FIO, COL_UZ, COL_PC, COL_NET = 0, 1, 2, 3, 4
# Колонки, видимые «из коробки»; остальные скрыты, но включаются правой кнопкой по заголовку.
DEFAULT_VISIBLE = ("Логин", "ФИО", "Учётка", "Имя ПК", "Сеть", "Телефон", "IP-тел")
DEFAULT_HIDDEN = {i for i, name in enumerate(COLUMNS) if name not in DEFAULT_VISIBLE}
COLUMNS_KEY = "hidden_columns_v3"   # новый ключ: старые сохранённые наборы не переопределяют стандартный набор


BADGE_ROLE = Qt.ItemDataRole.UserRole + 7  # вид бейджа: online / offline / active / disabled


class StatusItem(QTableWidgetItem):
    """Ячейка статуса — обычный item (корректно сортируется, в отличие от cellWidget).
    Рисуется как «пилюля» делегатом :class:`BadgeDelegate`; вид хранится в ``BADGE_ROLE``."""

    def __init__(self, text: str, kind: str):
        super().__init__(text)
        self.kind = kind
        self.setData(BADGE_ROLE, kind)
        fg, _bg, _ = app_palette().badge(kind)
        self.setForeground(QColor(fg))
        f = self.font()
        f.setBold(True)
        self.setFont(f)
        self.setTextAlignment(Qt.AlignmentFlag.AlignCenter)


def parse_color(css: str) -> QColor:
    """'#rrggbb' или 'rgba(r,g,b,a)' (доля 0..1) → QColor."""
    if css.startswith("rgba"):
        r, g, b, a = [x.strip() for x in css[css.index("(") + 1:css.rindex(")")].split(",")]
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(css)


class BadgeDelegate(QStyledItemDelegate):
    """Рисует статус в виде скруглённой «пилюли»: заливка + рамка 1px + жирный текст по центру."""

    RADIUS, PAD_X, PAD_Y = 4, 8, 3

    def paint(self, painter: QPainter, option, index):
        kind = index.data(BADGE_ROLE)
        if not kind:
            return super().paint(painter, option, index)
        opt = option
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)  # фон/выделение строки
        fg, bg, bd = app_palette().badge(kind)
        font = QFont(opt.font)
        font.setBold(True)
        font.setPointSizeF(max(font.pointSizeF() - 1, 7))
        fm_w = painter.fontMetrics().horizontalAdvance(text)
        painter.save()
        painter.setFont(font)
        w = painter.fontMetrics().horizontalAdvance(text) + self.PAD_X * 2
        w = min(max(w, 40), opt.rect.width() - 4) if opt.rect.width() > 44 else fm_w
        h = min(painter.fontMetrics().height() + self.PAD_Y * 2, opt.rect.height() - 4)
        pill = QRectF(opt.rect.center().x() - w / 2, opt.rect.center().y() - h / 2, w, h)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(parse_color(bd), 1))
        painter.setBrush(parse_color(bg))
        painter.drawRoundedRect(pill, self.RADIUS, self.RADIUS)
        self.last_pill = pill  # для тестов/отладки
        painter.setPen(QColor(fg))
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ADApp(FramelessMainWindow):
    def __init__(self, username: str | None, password: str | None):
        super().__init__()
        self.username, self.password = username, password
        self.admin_name = username or os.environ.get("USERNAME", "sso")
        self.results: list[dict] = []
        self._shown: dict | None = None
        self.active_ad_total = 0
        self.search_worker: SearchWorker | None = None
        self.scanner: PCScannerWorker | None = None
        self._threads: list = []

        self.setWindowTitle(APP_TITLE)
        self.setWindowIcon(app_icon())
        self.tray: Tray | None = None
        self.hotkey: GlobalHotkey | None = None
        self._quitting = False
        self.plugin_actions = plugins.load_plugins(settings.plugins_dir)
        self.setMinimumSize(1100, 660)
        self.qsettings = QSettings("ADK", "MainWindow")
        geo = self.qsettings.value("geometry")
        self.restoreGeometry(geo) if geo else self.resize(1320, 800)

        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.timeout.connect(self.start_search)

        self._build_ui()
        self._build_shortcuts()
        self._build_tray()
        self.refresh_dashboard()
        self.apply_access()
        QTimer.singleShot(2500, self.resolve_access)
        QTimer.singleShot(4000, self.check_updates)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.start_scan)
        self.scan_timer.start(settings.auto_scan_interval_ms)
        QTimer.singleShot(300, self.load_ad_count)
        QTimer.singleShot(1500, self.start_scan)
        # 3.1: сводка «Внимание» — в фоне, по таймеру из [Attention] refresh_min
        self.attention_items: list[dict] = []
        self.attention_timer = QTimer(self)
        self.attention_timer.timeout.connect(self.refresh_attention)
        if settings.attention.get("refresh_min", 0):
            self.attention_timer.start(int(settings.attention["refresh_min"]) * 60_000)
        QTimer.singleShot(6000, self.refresh_attention)

    # ------------------------------------------------------------------ горячие клавиши
    SHORTCUTS = (
        ("Ctrl+F", "focus_search", "Перейти в строку поиска"),
        ("Ctrl+L", "focus_search", "Перейти в строку поиска"),
        ("Esc", "escape", "Очистить поиск / вернуться на дашборд"),
        ("Return", "open_card", "Открыть карточку выбранного"),
        ("Ctrl+C", "copy_card_if_table", "Копировать карточку"),
        ("Ctrl+P", "ping_selected", "Пинг ПК выбранного"),
        ("F5", "start_scan", "Запустить сканирование парка"),
        ("Ctrl+N", "new_user", "Новый пользователь"),
        ("Ctrl+J", "audit_log", "Журнал действий"),
        ("Ctrl+Shift+P", "printers_overview", "Принтеры парка"),
        ("Ctrl+D", "show_dashboard", "Дашборд"),
        ("Ctrl+E", "export_results", "Экспорт результатов"),
        ("Ctrl+B", "bulk_operations", "Массовые операции"),
        ("Ctrl+M", "notes_selected", "Заметки по выбранному"),
        ("Ctrl+H", "history_selected", "История ПК/пользователя"),
        ("Ctrl+I", "show_attention", "Сводка «Внимание»"),
        ("Ctrl+Shift+G", "mass_ping", "Массовый пинг выделенных ПК"),
        ("Ctrl+Q", "quit_app", "Выход из приложения"),
        ("F11", "toggle_fullscreen", "Во весь экран / обратно"),
    )

    # ------------------------------------------------------------------ трей / хоткей / роли / обновления
    def _build_tray(self):
        if settings.minimize_to_tray:
            self.tray = Tray(self, self.show_from_tray, self.show_and_search, self.start_scan, self.quit_app)
        if settings.global_hotkey:
            self.hotkey = GlobalHotkey(self, settings.global_hotkey)
            self.hotkey.activated.connect(self.show_and_search)

    def show_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def show_and_search(self):
        self.show_from_tray()
        self.focus_search()

    def quit_app(self):
        self._quitting = True
        self.close()

    def apply_access(self):
        """Скрывает кнопки запрещённых действий (ПК / AD) и показывает бейдж роли в шапке."""
        self.lbl_readonly.setText(tr(access.badge_text()))
        self.lbl_readonly.setVisible(access.is_limited())
        self.lbl_readonly.setToolTip(access.reason())
        for b, action in getattr(self, "_modifying_buttons", []):
            b.setVisible(access.can(action))
        if hasattr(self, "role_card"):
            title, text = access.role_summary()
            self.lbl_role_title.setText(tr(title))
            self.lbl_role_text.setText(tr(text))
            self.role_card.setProperty("limited", access.is_limited())
            self.role_card.setStyleSheet("#dashCard { border: 1.5px solid %s; }" % (
                "#f59e0b" if access.is_limited() else "transparent") if access.is_limited() else "")

    def _deny(self, action: str) -> bool:
        """True — действие запрещено ролью (предупреждение уже показано)."""
        if access.can(action):
            return False
        MessageBox.warning(self, tr("Недостаточно прав"), tr(access.deny_text(action)))
        return True

    def resolve_access(self):
        if not access.policy_configured():
            return
        run_in_background(self, lambda: access.resolve(self.get_conn, self.admin_name), lambda _: self.apply_access(),
                          lambda m: log.warning("access: %s", m))

    def check_updates(self):
        if not settings.version_file:
            return

        def done(info):
            if info:
                self.update_info = info
                self.lbl_update.setText(f"⬆️ {tr('Доступна новая версия')} {info['version']}")
                self.lbl_update.setVisible(True)
                if self.tray:
                    self.tray.notify("ADK", f"{tr('Доступна новая версия')} {info['version']}")

        run_in_background(self, updates.check, done, lambda m: log.debug("updates: %s", m))

    def open_update(self, *_):
        loc = getattr(self, "update_info", {}).get("location") or ""
        if loc:
            try:
                if os.name == "nt":
                    os.startfile(loc)  # noqa: S606 — путь из доверенного файла версии
                else:
                    subprocess.Popen(["xdg-open", loc])
            except OSError as exc:
                MessageBox.warning(self, "Обновление", str(exc))

    def _build_shortcuts(self):
        self._shortcuts = []
        for keys, method, _ in self.SHORTCUTS:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(getattr(self, method))
            self._shortcuts.append(sc)
        self.search_input.setToolTip("Ctrl+F — фокус, Esc — очистить, Enter — искать")

    def focus_search(self):
        self.search_input.setFocus()
        self.search_input.selectAll()

    def escape(self):
        if self.search_input.text():
            self.search_input.clear()  # textChanged → дашборд
        else:
            self.show_dashboard()

    def show_dashboard(self):
        self.debounce.stop()
        self._cancel_search()
        self.stack.setCurrentIndex(0)
        self.refresh_dashboard()

    def copy_card_if_table(self):
        # Ctrl+C в строке поиска должен копировать текст, а не карточку
        if self.search_input.hasFocus() and self.search_input.hasSelectedText():
            self.search_input.copy()
            return
        if self.stack.currentIndex() == 1:
            self.copy_card()

    def new_user(self):
        if self._deny("create_user"):
            return
        RegisterUserDialog(self, self).exec()

    def audit_log(self):
        AuditLogDialog(self, self).exec()

    def printers_overview(self):
        PrintersDialog(self, self).exec()

    # ------------------------------------------------------------------ подключение
    def get_conn(self):
        return ad.make_connection(self.username, self.password)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("bgWidget")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.title_bar = TitleBar(self, "Active Directory Kit", is_main=True)
        root.addWidget(self.title_bar)
        content = QVBoxLayout()
        content.setContentsMargins(12, 10, 12, 12)
        content.setSpacing(10)
        root.addLayout(content)

        top = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("🔍 Фамилия, логин, почта, отдел, кабинет, имя ПК, IP, printer:…"))
        self.search_input.setClearButtonEnabled(True)
        self.completer = QCompleter(db.suggestions(), self.search_input)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(8)
        self.completer.activated.connect(lambda _: QTimer.singleShot(0, self.start_search))
        self.search_input.setCompleter(self.completer)
        self.search_input.textChanged.connect(self.on_text_changed)
        self.search_input.returnPressed.connect(self.start_search)
        btn_search = QPushButton(tr("Найти"))
        btn_search.setObjectName("btnPrimary")
        btn_search.clicked.connect(self.start_search)
        chk = QVBoxLayout()
        self.chk_archive = QCheckBox(tr("📦 Архивы (все ПК пользователя)"))
        self.chk_disabled = QCheckBox(tr("🚷 Отключённые учётки"))
        for c in (self.chk_archive, self.chk_disabled):
            c.toggled.connect(self.start_search)
            chk.addWidget(c)
        btn_design = QPushButton(tr("🎨 Дизайн"))
        btn_design.clicked.connect(lambda: DesignSettingsDialog(self, self).exec())
        btn_export = QPushButton(tr("📤 Экспорт"))
        btn_export.setToolTip("Ctrl+E — результаты поиска в Excel/CSV")
        btn_export.clicked.connect(self.export_results)
        self.lbl_readonly = QLabel(tr("🔒 Только чтение"))
        self.lbl_readonly.setObjectName("readonlyBadge")
        self.lbl_readonly.setVisible(False)
        top.addWidget(self.search_input, 1)
        top.addWidget(btn_search)
        top.addLayout(chk)
        top.addWidget(self.lbl_readonly)
        top.addWidget(btn_export)
        top.addWidget(btn_design)
        content.addLayout(top)
        self.lbl_update = QLabel("")
        self.lbl_update.setObjectName("updateLabel")
        self.lbl_update.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_update.setVisible(False)
        self.lbl_update.mousePressEvent = self.open_update
        content.addWidget(self.lbl_update)

        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self.stack.addWidget(self._build_dashboard())
        self.stack.addWidget(self._build_results())

        bottom = QHBoxLayout()
        self.btn_scan = QPushButton(tr("🔄 Обновить статус сети ПК"))
        self.btn_scan.clicked.connect(self.start_scan)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("statusLabel")
        bottom.addWidget(self.btn_scan)
        bottom.addWidget(self.lbl_status, 1)
        content.addLayout(bottom)

    def _build_dashboard(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)
        lay.addWidget(QLabel(tr("<b>📊 Состояние компьютеров домена</b> (клик по карточке — список):")))
        self.cards = QHBoxLayout()
        lay.addLayout(self.cards)
        self.attention_card = QFrame()
        self.attention_card.setObjectName("dashCard")
        self.attention_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attention_card.mousePressEvent = lambda e: self.show_attention()  # type: ignore[method-assign]
        arow = QHBoxLayout(self.attention_card)
        self.lbl_attention = QLabel("🔔 Внимание: сводка собирается…")
        self.lbl_attention.setStyleSheet("background: transparent; border: none; font-weight: bold;")
        self.lbl_attention.setWordWrap(True)
        arow.addWidget(self.lbl_attention, 1)
        b_att = QPushButton("Открыть")
        b_att.setObjectName("historyBtn")
        b_att.clicked.connect(self.show_attention)
        arow.addWidget(b_att)
        lay.addWidget(self.attention_card)
        self.role_card = QFrame()
        self.role_card.setObjectName("dashCard")
        rrow = QHBoxLayout(self.role_card)
        self.lbl_role_title = QLabel("")
        self.lbl_role_title.setStyleSheet("background: transparent; border: none; font-weight: bold;")
        self.lbl_role_text = QLabel("")
        self.lbl_role_text.setObjectName("subtle")
        self.lbl_role_text.setWordWrap(True)
        self.lbl_role_text.setStyleSheet("background: transparent; border: none;")
        rcol = QVBoxLayout()
        rcol.setSpacing(2)
        rcol.addWidget(self.lbl_role_title)
        rcol.addWidget(self.lbl_role_text)
        rrow.addLayout(rcol, 1)
        lay.addWidget(self.role_card)
        lay.addWidget(QLabel(tr("<b>🕒 Недавние поиски:</b>")))
        frame = QFrame()
        frame.setObjectName("dashCard")
        self.history = QHBoxLayout(frame)
        lay.addWidget(frame)
        lay.addWidget(QLabel(tr("<b>⚡ Быстрый доступ:</b>")))
        quick_box = QWidget()
        quick = FlowLayout(quick_box, spacing=8)          # переносится на новую строку, если окно узкое — подписи не режутся
        self._modifying_buttons: list = []  # (кнопка, действие) — скрываются, если access.can(действие) == False
        for text, fn, action in (("🔍 Свободный IP-адрес", lambda: FreeIPDialog(self).exec(), ""),
                                 ("📊 Excel-опись ПК", lambda: InventoryDialog(self, self).exec(), ""),
                                 ("🖨️ Принтеры парка", self.printers_overview, ""),
                                 ("➕ Новый пользователь AD", self.new_user, "create_user"),
                                 ("📜 Журнал действий", lambda: AuditLogDialog(self, self).exec(), ""),
                                 ("📦 ПО парка", lambda: SoftwareDialog("", self, self).exec(), ""),
                                 ("📣 Уведомления", lambda: NotifySettingsDialog(self, self).exec(), "")):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            if action:
                self._modifying_buttons.append((b, action))
            quick.addWidget(b)
        lay.addWidget(quick_box)
        lay.addStretch()
        return w

    def _build_results(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)  # Ctrl/Shift — массовые операции
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._badge_delegate = BadgeDelegate(self.table)
        for col in (COL_UZ, COL_NET):
            self.table.setItemDelegateForColumn(col, self._badge_delegate)
        hdr = self.table.horizontalHeader()
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for i, wd in enumerate([105, 160, 95, 110, 105, 90, 75, 75, 120, 130, 150, 130, 140]):
            self.table.setColumnWidth(i, wd)
        hdr.setStretchLastSection(True)
        hdr.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self.column_menu)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.row_menu)
        self.table.itemSelectionChanged.connect(self.on_selection)
        # после сортировки выделение остаётся на том же номере строки, но там уже другой человек
        hdr.sortIndicatorChanged.connect(lambda *_: QTimer.singleShot(0, self.on_selection))
        self.table.itemDoubleClicked.connect(lambda _: self.open_card())
        self.restore_columns()
        self.splitter.addWidget(self.table)

        insp = QFrame()
        insp.setObjectName("sideCard")
        insp.setMinimumWidth(440)
        il = QVBoxLayout(insp)
        head = QHBoxLayout()
        self.lbl_fio = QLabel("👤 Выберите сотрудника")
        self.lbl_fio.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self.lbl_fio.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_copy = QPushButton("📋 Копировать")
        self.btn_copy.setObjectName("btnSuccess")
        self.btn_copy.clicked.connect(self.copy_card)
        head.addWidget(self.lbl_fio)
        head.addWidget(self.btn_copy)
        head.addStretch()
        il.addLayout(head)
        self.lbl_sub = QLabel("")
        self.lbl_sub.setObjectName("subtle")
        il.addWidget(self.lbl_sub)

        self.details = QWidget()
        dl = QVBoxLayout(self.details)
        dl.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 130)
        grid.setColumnStretch(1, 1)
        self.vals: dict[str, QLabel] = {}
        for r, (key, label) in enumerate((("login", "Логин:"), ("pc", "Связанный ПК:"), ("status", "Статус сети:"),
                                          ("phone", "Телефон:"), ("mail", "Почта:"), ("addr", "Адрес:"),
                                          ("dept", "Отдел:"), ("logon", "Последний вход:"),
                                          ("account", "Учётная запись:"))):
            grid.addWidget(QLabel(f"<b>{label}</b>"), r, 0)
            v = QLabel("—")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.vals[key] = v
            if key == "status":
                box = QHBoxLayout()
                box.setContentsMargins(0, 0, 0, 0)
                box.addWidget(v)
                self.btn_ping = QPushButton("📡 Пинг")
                self.btn_ping.setObjectName("btnInfo")
                self.btn_ping.setMinimumWidth(110)
                self.btn_ping.clicked.connect(self.ping_selected)
                box.addWidget(self.btn_ping)
                box.addStretch()
                grid.addLayout(box, r, 1)
            else:
                grid.addWidget(v, r, 1)
        dl.addLayout(grid)
        dl.addWidget(QLabel(tr("<b>⚡ Действия с ПК:</b>")))
        bg = QGridLayout()
        self.action_buttons: dict[str, QPushButton] = {}
        for i, (text, name, action) in enumerate((("🖥️ RMS", "btnInfo", "rms"), ("📁 Диск", "", "disk"),
                                                  ("⚙️ Управление ПК", "", "compmgmt"),
                                                  ("⏻ Питание ПК", "btnDanger", "power"),
                                                  ("🩺 Здоровье ПК", "", "health"),
                                                  ("📦 Установленное ПО", "", "software"),
                                                  ("🔐 Входы за 24 ч", "", "logons"))):
            b = QPushButton(tr(text))
            if name:
                b.setObjectName(name)
            if action == "power":
                b.setToolTip("Разбудить (WoL), заблокировать экран, выйти из пользователя, спящий режим, перезагрузить, выключить")
            b.clicked.connect(lambda _, a=action: self.remote_action(a))
            self.action_buttons[action] = b
            if access.action_class(action):
                self._modifying_buttons.append((b, action))
            bg.addWidget(b, i // 2, i % 2)
        for i, act in enumerate(self.plugin_actions, start=len(self.action_buttons)):
            b = QPushButton(act.label)
            b.setToolTip(f"Плагин: {act.name}")
            b.clicked.connect(lambda _, a=act: self.run_plugin(a))
            if act.modifying:
                self._modifying_buttons.append((b, "plugin_modifying"))
            self.action_buttons[f"plugin:{act.name}"] = b
            bg.addWidget(b, i // 2, i % 2)
        dl.addLayout(bg)
        dl.addWidget(QLabel(tr("<b>🖨️ Принтеры</b> (клик — кто ещё подключён):")))
        self.printers_box = QWidget()
        self.printers_flow = FlowLayout(self.printers_box)
        dl.addWidget(self.printers_box)
        # кнопка живого опроса — отдельной строкой под бейджами: в заголовке она не помещалась по ширине панели
        self.btn_live_printers = QPushButton("📡 Опросить принтеры сейчас")
        self.btn_live_printers.setObjectName("historyBtn")
        self.btn_live_printers.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_live_printers.setMinimumWidth(120)   # не распирает панель инспектора длинной подписью
        self.btn_live_printers.setToolTip("Спросить ПК напрямую (CIM Win32_Printer), без записи: инвентарь и БД не меняются.")
        self.btn_live_printers.clicked.connect(self.live_printers)
        dl.addWidget(self.btn_live_printers)
        self.lbl_live_printers = QLabel("")
        self.lbl_live_printers.setObjectName("subtle")
        self.lbl_live_printers.setWordWrap(True)
        self.lbl_live_printers.setVisible(False)
        dl.addWidget(self.lbl_live_printers)
        notes_head = QHBoxLayout()
        notes_head.addWidget(QLabel(tr("<b>📝 Заметки:</b>")))
        self.btn_notes = QPushButton(tr("➕ Добавить заметку…"))
        self.btn_notes.setObjectName("historyBtn")
        self.btn_notes.clicked.connect(self.notes_selected)
        notes_head.addWidget(self.btn_notes)
        notes_head.addStretch()
        dl.addLayout(notes_head)
        self.lbl_notes = QLabel("—")
        self.lbl_notes.setWordWrap(True)
        self.lbl_notes.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.lbl_notes)
        dl.addWidget(QLabel(tr("<b>💻 Характеристики:</b>")))
        self.lbl_specs = QLabel("—")
        self.lbl_specs.setObjectName("specBox")
        self.lbl_specs.setWordWrap(True)
        self.lbl_specs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        dl.addWidget(self.lbl_specs)
        full = QPushButton(tr("👤 Открыть полную карточку AD…"))
        full.clicked.connect(self.open_card)
        dl.addWidget(full)
        extra = QHBoxLayout()
        self.btn_history = QPushButton("🕓 История")
        self.btn_history.clicked.connect(self.history_selected)
        self.btn_compare = QPushButton("🧬 Группы как у…")
        self.btn_compare.clicked.connect(self.compare_groups)
        self._modifying_buttons.append((self.btn_compare, "groups_sync"))
        extra.addWidget(self.btn_history)
        extra.addWidget(self.btn_compare)
        dl.addLayout(extra)
        dl.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details.setObjectName("detailsPane")
        scroll.viewport().setObjectName("detailsViewport")
        scroll.setWidget(self.details)
        self.details_scroll = scroll
        il.addWidget(scroll, 1)
        self.printer_pane = self._build_printer_pane()
        self.printer_pane.setVisible(False)
        il.addWidget(self.printer_pane, 1)
        self.splitter.addWidget(insp)
        self.splitter.setSizes([760, 480])
        lay.addWidget(self.splitter)
        return w

    # ------------------------------------------------------------------ дашборд
    def load_ad_count(self):
        def work():
            c = self.get_conn()
            try:
                entries = ad.paged_search(
                    c, "(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                       "(!(operatingSystem=*Server*)))", ["name"])
            finally:
                c.unbind()
            return len(PCScannerWorker.workstation_names(entries))

        def done(n):
            self.active_ad_total = n
            self.refresh_dashboard()

        run_in_background(self, work, done, lambda m: self.lbl_status.setText(f"⚠️ AD: {m}"))

    def refresh_dashboard(self):
        try:
            summ = db.get_inventory_summary()
        except Exception as exc:  # noqa: BLE001
            summ = {"online": 0, "offline": 0, "total": 0, "last": None}
            log.warning("inventory stats: %s", exc)
        online, last = summ["online"], summ["last"]
        # «всего» — рабочие станции в AD (если AD уже ответил), но не меньше, чем есть в инвентаре;
        # «не в сети» — реальные строки инвентаря без ответа плюс ПК из AD, которых сканер ещё не видел
        total = max(self.active_ad_total, summ["total"])
        offline = max(summ["offline"], total - online)
        self.dashboard_counts = {"online": online, "offline": offline, "total": total}   # для тестов и статуса
        self.lbl_status.setText(f"Последнее сканирование: {last}" if last else "Готово к работе")
        while self.cards.count():
            it = self.cards.takeAt(0)
            if it.widget():
                it.widget().hide()          # до deleteLater виджет ещё дочерний — иначе «призрак» до возврата в цикл событий
                it.widget().deleteLater()
        pct = f"{round(online / total * 100)}%" if total else "0%"
        pal = app_palette()
        unseen = max(0, offline - summ["offline"])   # ПК из AD, которых сканер ещё не проверял
        off_sub = f"{summ['offline']} по сканеру · {unseen} ещё не сканированы" if unseen else "выключены / недоступны"
        all_sub = f"в инвентаре {summ['total']}, остальные ещё не сканированы" if total > summ["total"] else "по фильтру host_pattern"
        for title, count, sub, kind, color in (
                ("🟢 ПК В СЕТИ", f"{online}", f"{pct} активны", "online", pal.success[0]),
                ("🔴 ПК НЕ В СЕТИ", f"{offline}", off_sub, "offline", pal.danger[0]),
                ("💻 РАБОЧИХ СТАНЦИЙ В AD", f"{total}", all_sub, "all", pal.info[0])):
            self.cards.addWidget(self._stat_card(title, count, sub, kind, color))
        while self.history.count():
            it = self.history.takeAt(0)
            if it.widget():
                it.widget().hide()
                it.widget().deleteLater()
        for q in db.get_recent_searches(6) or []:
            b = QPushButton(f"🔍 {q}")
            b.setObjectName("historyBtn")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _, s=q: (self.search_input.setText(s), self.start_search()))
            self.history.addWidget(b)
        self.history.addStretch()

    def _stat_card(self, title, count, sub, kind, color) -> QFrame:
        pal = app_palette()
        card = QFrame()
        card.setObjectName("dashCard")
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setStyleSheet(f"#dashCard {{ border-left: 4px solid {color}; }}")
        card.mousePressEvent = lambda e, k=kind: self.show_category(k)  # type: ignore[method-assign]
        lay = QVBoxLayout(card)
        t = QLabel(title)
        t.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 9.5pt; background: transparent; border: none;")
        c = QLabel(count)
        c.setObjectName("statValue")
        c.setStyleSheet(f"QLabel#statValue {{ color: {pal.text}; font-weight: bold; font-size: 20pt; "
                        f"background: transparent; border: none; }}")
        s = QLabel(sub)
        s.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt; background: transparent; border: none;")
        for x in (t, c, s):
            lay.addWidget(x)
        return card

    def on_theme_changed(self):
        self.table.verticalHeader().setDefaultSectionSize(max(36, int(settings.design["font_size"] * 3.2)))
        self.refresh_dashboard()
        if self.results:
            self.fill_table(self.results)

    # ------------------------------------------------------------------ категории (drill-down)
    def show_category(self, kind: str):
        self.debounce.stop()
        self._cancel_search()
        tag = {"online": "[В сети]", "offline": "[Не в сети]", "all": "[Все ПК]"}[kind]
        self.search_input.blockSignals(True)
        self.search_input.setText(tag)
        self.search_input.blockSignals(False)
        rows: list[dict] = []
        try:
            for name, ip, on, user, specs, _last, last_logon, seen in db.inventory_rows(kind):
                user = (user or "").strip()
                rows.append({"entry": None, "login": user or "—", "fio": "—" if user else "— (свободный ПК)",
                             "full_fio": user, "is_disabled": False, "comp": name, "ip": ip or "Не найден",
                             "is_online": bool(on), "phone": "", "ip_phone": "", "office": "", "address": "",
                             "company": f"Категория {tag}", "dept": "", "title": "", "mail": "",
                             "specs_custom": specs or "", "last_logon": last_logon or "Нет данных",
                             "last_seen_online": seen})
        except Exception as exc:  # noqa: BLE001
            MessageBox.critical(self, "База данных", str(exc))
        self.results = rows
        self.stack.setCurrentIndex(1)
        self.fill_table(rows)
        self.lbl_status.setText(f"Компьютеров {tag}: {len(rows)}")

    # ------------------------------------------------------------------ поиск
    def on_text_changed(self, text: str):
        q = text.strip()
        if not q:
            self.debounce.stop()
            self._cancel_search()
            self.stack.setCurrentIndex(0)
            self.refresh_dashboard()
            return
        if q.startswith("["):
            return
        if len(q) >= 2:
            self.debounce.start(450)

    def _cancel_search(self):
        for t in list(self._threads):
            if isinstance(t, SearchWorker):
                try:
                    if t.isRunning():
                        t.cancel()  # поток доживёт сам, удалится по finished
                except RuntimeError:  # C++-объект уже удалён
                    self._threads.remove(t)
        self.search_worker = None

    def start_search(self):
        self.debounce.stop()
        q = self.search_input.text().strip()
        if len(q) < 2 or q.startswith("["):
            return
        self._cancel_search()
        self.lbl_status.setText("🔍 Поиск…")
        w = SearchWorker(self.get_conn, q, self.chk_archive.isChecked(), self.chk_disabled.isChecked(), parent=self)
        w.results_ready.connect(self.on_results)
        w.error.connect(lambda m: self.lbl_status.setText(f"⚠️ {m}"))
        self._threads.append(w)

        def _cleanup():
            if w in self._threads:
                self._threads.remove(w)
            if self.search_worker is w:
                self.search_worker = None
            w.deleteLater()

        w.finished.connect(_cleanup)
        self.search_worker = w
        w.start()

    def on_results(self, rows: list, query: str, truncated: bool = False):
        if self.search_input.text().strip() != query:
            return  # устаревший ответ
        db.save_search_query(query, self.admin_name)
        terms = {t for r in rows for t in (r.get("login"), r.get("comp"), (r.get("full_fio") or "").split(" ")[0]) if t and t != "—"}
        db.remember_terms(terms)
        model = self.completer.model()
        if model is not None and hasattr(model, "setStringList"):
            model.setStringList(db.suggestions())
        self.results = rows
        self.stack.setCurrentIndex(1)
        self.fill_table(rows)
        if truncated:
            self.lbl_status.setText(
                f"Найдено: {len(rows)} — показаны первые {SEARCH_RESULT_LIMIT} учётных записей, уточните запрос")
        else:
            printers = [r for r in rows if r.get("kind") == "printer"]
            people = len(rows) - len(printers)
            if printers:
                pcs = sum(len((r.get("printer") or {}).get("pcs") or []) for r in printers)
                self.lbl_status.setText(f"🖨️ Принтеров: {len(printers)} (подключено ПК — {pcs}) · людей/ПК: {people}")
            else:
                self.lbl_status.setText(f"Найдено: {len(rows)}")

    def fill_table(self, rows: list[dict]):
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        # новые результаты приходят уже отсортированными (онлайн → имя ПК); сбрасываем пользовательскую сортировку
        self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setRowCount(len(rows))
        bold = QFont(settings.design["font_family"], int(settings.design["font_size"]), QFont.Weight.Bold)
        for r, u in enumerate(rows):
            login = QTableWidgetItem(u["login"])
            login.setData(Qt.ItemDataRole.UserRole, r)
            fio = QTableWidgetItem(u["fio"])
            fio.setFont(bold)
            if u.get("kind") == "printer":
                login.setText("🖨️")
                cells = [login, fio, StatusItem("Принтер", "info"), QTableWidgetItem(u.get("ip") or "—"),
                         StatusItem("● В сети" if u["is_online"] else "● Не в сети", "online" if u["is_online"] else "offline")]
                cells += [QTableWidgetItem("") for _ in range(7)] + [QTableWidgetItem(u.get("last_logon") or "")]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)
                continue
            cells = [login, fio,
                     StatusItem(u.get("account_text") or ("Не активна" if u["is_disabled"] else "Активна"),
                                u.get("account_kind") or ("disabled" if u["is_disabled"] else "active")),
                     QTableWidgetItem(u["comp"] or "—"),
                     StatusItem("● В сети" if u["is_online"] else "● Не в сети",
                                "online" if u["is_online"] else "offline")]
            cells += [QTableWidgetItem(str(u.get(k) or "")) for k in
                      ("phone", "ip_phone", "office", "address", "company", "dept", "title", "last_logon")]
            for c, item in enumerate(cells):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        fit_columns(self.table, max_width=280, min_width=70, wrap=False, stretch_last=True)
        self.table.setUpdatesEnabled(True)
        if rows:
            self._shown = None
            self.select_row(0)
        else:
            self.lbl_fio.setText("❌ Ничего не найдено")
            self.lbl_sub.setText("Измените запрос")
            self.details.setVisible(False)

    # ------------------------------------------------------------------ инспектор
    def selected(self) -> dict | None:
        items = self.table.selectedItems()
        if not items:
            return None
        it = self.table.item(items[0].row(), COL_LOGIN)
        idx = it.data(Qt.ItemDataRole.UserRole) if it else None
        return self.results[idx] if isinstance(idx, int) and 0 <= idx < len(self.results) else None

    def on_selection(self):
        u = self.selected()
        if u is not None and u is not self._shown:
            self.show_user(u)

    def select_row(self, row: int):
        """Программный выбор строки с гарантированным обновлением инспектора."""
        self.table.selectRow(row)
        self.on_selection()

    def show_user(self, u: dict):
        self._shown = u
        if u.get("kind") == "printer":
            self.show_printer(u)
            return
        self.details.setVisible(True)
        self.details_scroll.setVisible(True)
        self.printer_pane.setVisible(False)
        pal = app_palette()
        login = u.get("login", "—")
        comp = db.clean_computer_name(u.get("comp", ""))
        if login and login != "—" and u.get("entry") is None and not u.get("_ldap_loaded"):
            self._enrich_from_ad(u)
        if not login or login == "—":
            self.lbl_fio.setText(f"💻 {comp} (свободный ПК)")
            self.lbl_sub.setText("Пользователь не залогинен")
        else:
            self.lbl_fio.setText(f"👤 {u.get('full_fio') or u.get('fio') or login}")
            sub = " · ".join(x for x in (u.get("title"), u.get("company")) if x)
            self.lbl_sub.setText(sub or f"Пользователь: {login}")
        self.vals["login"].setText(login or "—")
        self.vals["pc"].setText(f"{comp} ({u.get('ip', 'Не найден')})" if comp else "—")
        on = bool(u.get("is_online"))
        self.vals["status"].setText("● В сети" if on else "● Не в сети")
        self.vals["status"].setStyleSheet(f"color: {pal.success[0] if on else pal.danger[0]}; font-weight: bold;")
        self.btn_ping.setVisible(bool(comp))
        phones = [p for p in (u.get("phone"), f"(IP: {u['ip_phone']})" if u.get("ip_phone") else "") if p]
        self.vals["phone"].setText(" ".join(phones) or "—")
        self.vals["mail"].setText(u.get("mail") or "—")
        addr = " / ".join(x for x in (u.get("address"), _office_label(u.get("office"))) if x)
        self.vals["addr"].setText(addr or "—")
        self.vals["dept"].setText(u.get("dept") or "—")
        self.vals["logon"].setText(u.get("last_logon") or "—")
        self._show_account_status(u)
        self._show_printers(u, comp)
        self.refresh_notes_badge()
        if comp and on and u.get("ip") and u["ip"] != "Не найден" and not db.get_mac(comp):
            run_in_background(self, lambda: nettools.learn_mac(comp, u["ip"]), lambda m: None, lambda m: None)
        if u.get("specs_custom"):
            self.lbl_specs.setText(u["specs_custom"])
        elif comp:
            self.lbl_specs.setText("…")
            run_in_background(self, lambda: netutils.get_computer_specs_summary(comp),
                              lambda s: self.lbl_specs.setText(s) if self.selected() is u else None,
                              lambda m: log.debug("specs: %s", m))
        else:
            self.lbl_specs.setText("ПК не привязан")

    def _build_printer_pane(self) -> QWidget:
        """Инспектор для строки-принтера: адрес, доступность, кто подключён (клик по ПК — поиск)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 130)
        grid.setColumnStretch(1, 1)
        self.pvals: dict[str, QLabel] = {}
        for r, (key, label) in enumerate((("ip", "IP-адрес:"), ("status", "Доступность:"), ("kind", "Подключение:"),
                                          ("port", "Порт:"), ("count", "Подключено ПК:"))):
            grid.addWidget(QLabel(f"<b>{label}</b>"), r, 0)
            v = QLabel("—")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.pvals[key] = v
            if key == "status":
                box = QHBoxLayout()
                box.setContentsMargins(0, 0, 0, 0)
                box.addWidget(v)
                self.btn_printer_ping = QPushButton("📡 Пинг")
                self.btn_printer_ping.setObjectName("btnInfo")
                self.btn_printer_ping.clicked.connect(self.ping_printer)
                box.addWidget(self.btn_printer_ping)
                self.btn_printer_web = QPushButton("🌐 Веб-панель")
                self.btn_printer_web.clicked.connect(self.open_printer_web)
                box.addWidget(self.btn_printer_web)
                box.addStretch()
                grid.addLayout(box, r, 1)
            else:
                grid.addWidget(v, r, 1)
        lay.addLayout(grid)
        lay.addWidget(QLabel("<b>💻 Кто подключён</b> (клик — открыть ПК):"))
        self.printer_pcs = QTableWidget(0, 4)
        self.printer_pcs.setHorizontalHeaderLabels(["ПК", "Пользователь", "Сеть", "По умолч."])
        self.printer_pcs.verticalHeader().setVisible(False)
        self.printer_pcs.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.printer_pcs.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.printer_pcs.setItemDelegateForColumn(2, self._badge_delegate)
        self.printer_pcs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.printer_pcs.setColumnWidth(0, 120)
        self.printer_pcs.setColumnWidth(2, 100)
        self.printer_pcs.setColumnWidth(3, 80)
        self.printer_pcs.itemDoubleClicked.connect(lambda it: self._open_printer_pc(it.row()))
        lay.addWidget(self.printer_pcs, 1)
        hint = QLabel("Принтеры берутся из инвентарных CSV (кэш pc_printers). Доступность — TCP 9100/631/80, затем ping.")
        hint.setObjectName("subtle")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    def show_printer(self, u: dict):
        g = u.get("printer") or {}
        pal = app_palette()
        self.details_scroll.setVisible(False)
        self.printer_pane.setVisible(True)
        self.lbl_fio.setText(f"🖨️ {g.get('name') or u.get('fio')}")
        kind = {"network": "сетевой", "shared": "общий (через сервер)", "usb": "USB", "local": "локальный"}.get(g.get("kind", ""), "—")
        self.lbl_sub.setText(f"Принтер · {kind}")
        ip = g.get("ip") or ""
        self.pvals["ip"].setText(ip or "— (не сетевой)")
        on = bool(u.get("is_online"))
        self.pvals["status"].setText(("● В сети" if on else "● Не в сети") if ip else "—")
        self.pvals["status"].setStyleSheet(f"color: {pal.success[0] if on else pal.danger[0]}; font-weight: bold;" if ip else "")
        self.btn_printer_ping.setVisible(bool(ip))
        self.btn_printer_web.setVisible(bool(ip))
        self.pvals["kind"].setText(kind)
        self.pvals["port"].setText(g.get("port") or "—")
        pcs = g.get("pcs") or []
        self.pvals["count"].setText(f"{len(pcs)} (в сети: {sum(1 for x in pcs if x['is_online'])})")
        self.printer_pcs.setRowCount(len(pcs))
        for r, x in enumerate(pcs):
            self.printer_pcs.setItem(r, 0, QTableWidgetItem(x["comp"]))
            self.printer_pcs.setItem(r, 1, QTableWidgetItem(x.get("user") or "—"))
            self.printer_pcs.setItem(r, 2, StatusItem("● В сети" if x["is_online"] else "● Не в сети", "online" if x["is_online"] else "offline"))
            self.printer_pcs.setItem(r, 3, QTableWidgetItem("★" if x.get("is_default") else ""))
        fit_columns(self.printer_pcs, wrap=False, stretch_last=False)
        self.printer_pcs.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.btn_ping.setVisible(False)

    def _open_printer_pc(self, row: int):
        it = self.printer_pcs.item(row, 0)
        if it and it.text():
            self.search_input.setText(it.text())
            self.start_search()

    def ping_printer(self):
        u = self.selected()
        g = (u or {}).get("printer") or {}
        if g.get("ip"):
            PingDialog(g.get("name") or "Принтер", g["ip"], self, self).exec()

    def open_printer_web(self):
        u = self.selected()
        g = (u or {}).get("printer") or {}
        if g.get("ip"):
            from PyQt6.QtGui import QDesktopServices
            from PyQt6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl(f"http://{g['ip']}/"))

    def _show_printers(self, u: dict, comp: str):
        """Бейджи принтеров: из строки результата (кэш) или дочитываем CSV в фоне."""
        self.printers_flow.clear()
        self.lbl_live_printers.setVisible(False)
        self.btn_live_printers.setVisible(bool(comp) and u.get("type") != "printer")
        if not comp:
            self.printers_box.setVisible(False)
            return
        self.printers_box.setVisible(True)
        printers = u.get("printers")
        if printers is None:
            self.printers_flow.addWidget(QLabel("…"))
            run_in_background(self, lambda: netutils.get_computer_printers_detailed(comp),
                              lambda ps: (u.__setitem__("printers", ps), self._show_printers(u, comp))
                              if self.selected() is u else None,
                              lambda m: log.debug("printers: %s", m))
            return
        if not printers:
            self.printers_flow.addWidget(QLabel("нет (или CSV не собран)"))
            return
        pal = app_palette()
        icon = {"network": "🌐", "shared": "🔗", "usb": "🔌", "local": "🖨️"}
        kind_badge = {"network": "info", "shared": "info", "usb": "warning", "local": "neutral"}
        for p in printers:
            text = f"{icon.get(p['kind'], '🖨️')} {p['name']}" + (f" · {p['ip']}" if p.get("ip") else "")
            if p.get("is_default"):
                text += " ★"
            tip = f"{netutils.printer_label(p)}\nПорт: {p.get('port') or '—'}" + ("\nПо умолчанию" if p.get("is_default") else "")
            b = BadgeButton(text, kind_badge.get(p["kind"], "neutral"), pal, tip)
            b.clicked.connect(lambda _, pp=p: self.search_printer(pp))
            self.printers_flow.addWidget(b)

    def live_printers(self):
        """«Опросить сейчас»: принтеры ПК прямо с него, минуя инвентарный CSV.
        Результат показывается рядом и **не сохраняется** — ни в pc_printers, ни в CSV."""
        u = self.selected()
        comp = db.clean_computer_name(u["comp"]) if u and u.get("comp") else ""
        if not comp:
            return
        self.btn_live_printers.setEnabled(False)
        self.lbl_live_printers.setVisible(True)
        self.lbl_live_printers.setText("⏳ Опрашиваю ПК напрямую…")
        run_in_background(self, lambda: netutils.get_live_printers(comp),
                          lambda r: self._show_live_printers(u, comp, r) if self.selected() is u else self.btn_live_printers.setEnabled(True),
                          lambda m: self._show_live_printers(u, comp, {"error": m}))

    def _show_live_printers(self, u: dict, comp: str, r: dict):
        self.btn_live_printers.setEnabled(True)
        self.lbl_live_printers.setVisible(True)
        if "error" in r:
            self.lbl_live_printers.setText(f"⚠️ Живой опрос не удался: {r['error']}. Показан инвентарный снимок.")
            return
        live = r.get("printers") or []
        cached = {p["name"].lower() for p in (u.get("printers") or [])}
        live_names = {p["name"].lower() for p in live}
        added = [p for p in live if p["name"].lower() not in cached]
        gone = sorted(cached - live_names)
        icon = {"network": "🌐", "shared": "🔗", "usb": "🔌", "local": "🖨️"}
        lines = [f"<b>📡 Сейчас на {html.escape(comp)}</b> ({time.strftime('%H:%M:%S')}, только просмотр — инвентарь не изменён):"]
        for p in live:
            st = p.get("status_text") or "—"
            mark = "🟢" if p["status"] in (3, 4) and not p["offline"] else "🔴" if p["offline"] or p["status"] == 7 else "⚪"
            lines.append(f"{mark} {icon.get(p['kind'], '🖨️')} {p['name']}" + (f" · {p['ip']}" if p.get("ip") else "")
                         + (" ★" if p.get("is_default") else "") + f" — {st}")
        if not live:
            lines.append("принтеров не найдено (виртуальные скрыты)")
        if added:
            lines.append(f"➕ Нет в инвентаре: {', '.join(p['name'] for p in added)}")
        if gone:
            lines.append(f"➖ Есть в инвентаре, но уже нет на ПК: {', '.join(gone)}")
        if not added and not gone and live:
            lines.append("✅ Совпадает с инвентарным снимком")
        self.lbl_live_printers.setText("<br>".join(html.escape(x) if i else x for i, x in enumerate(lines)))

    def search_printer(self, p: dict):
        """Клик по бейджу: поиск «кто подключён к этому принтеру» (по IP для сетевых, по имени для остальных)."""
        key = p.get("ip") or f"printer: {p['name']}"   # по IP принтер находится сам; по имени — строго среди принтеров
        self.search_input.blockSignals(True)
        self.search_input.setText(key)
        self.search_input.blockSignals(False)
        self.start_search()

    def _show_account_status(self, u: dict):
        """Строка «Учётная запись»: блокировка / срок пароля / истечение — из атрибутов Entry."""
        lbl = self.vals["account"]
        e = u.get("entry")
        if e is None:
            lbl.setText("—")
            lbl.setStyleSheet("")
            return
        pal = app_palette()
        st = ad.account_status(e, settings.max_password_age_days)
        parts, warn = [], False
        reason = ad.account_inactive_reason(e)
        if reason and not st["locked"]:
            parts.append(reason)   # «отключена» / «истекла»; про блокировку скажет строка ниже, а «Не активна» — бейдж в таблице
            warn = True
        if st["locked"]:
            parts.append(st["locked_text"] + " — вход невозможен до снятия блокировки")
            warn = True
        if st["expires_text"]:
            parts.append(st["expires_text"])
            warn = warn or st["expired"]
        parts.append(f"пароль: {st['pwd_text']}")
        warn = warn or st["pwd_warn"] or (st["pwd_days_left"] is not None and st["pwd_days_left"] <= 0)
        if st["bad_pwd"]:
            parts.append(f"неудачных входов: {st['bad_pwd']}")
        lbl.setText("; ".join(parts))
        lbl.setStyleSheet(f"color: {pal.danger[0]}; font-weight: bold;" if warn else "")

    def _enrich_from_ad(self, u: dict):
        """Для строк из инвентаря (без Entry) подтягиваем карточку из AD в фоне."""
        u["_ldap_loaded"] = True
        login = u["login"]

        def work():
            c = self.get_conn()
            try:
                e = ad.paged_search(c, f"(sAMAccountName={ad.escape_filter_chars(login)})", ad.USER_ATTRS, limit=1)
                return e[0] if e else None
            finally:
                c.unbind()

        def done(e):
            if e is None:
                return
            u.update(entry=e, full_fio=ad.get_full_fio(e, login), fio=ad.short_fio(ad.get_full_fio(e, login)),
                     title=ad.get_ad_value(e, "title"), company=ad.get_ad_value(e, "company"),
                     dept=ad.get_ad_value(e, "department"), mail=ad.get_ad_value(e, "mail"),
                     phone=ad.get_ad_value(e, "telephoneNumber"), ip_phone=ad.get_ad_value(e, "ipPhone"),
                     address=ad.get_ad_value(e, "streetAddress"),
                     office=ad.get_ad_value(e, "physicalDeliveryOfficeName"))
            if self.selected() is u:
                self.show_user(u)

        run_in_background(self, work, done, lambda m: log.debug("enrich: %s", m))

    def update_pc_status_in_ui(self, computer_name: str, is_online: bool):
        comp = db.clean_computer_name(computer_name)
        for u in self.results:
            if db.clean_computer_name(u.get("comp", "")) == comp:
                u["is_online"] = is_online
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_PC)
            if it and db.clean_computer_name(it.text()) == comp:
                self.table.setItem(r, COL_NET, StatusItem("● В сети" if is_online else "● Не в сети",
                                                          "online" if is_online else "offline"))
        u = self.selected()
        if u and db.clean_computer_name(u.get("comp", "")) == comp:
            self.show_user(u)

    # ------------------------------------------------------------------ действия
    def copy_card(self):
        u = self.selected()
        if not u:
            return
        if u.get("kind") == "printer":
            g = u.get("printer") or {}
            pcs = g.get("pcs") or []
            text = "\n".join([f"🖨️ {g.get('name')}" + (f" · {g['ip']}" if g.get("ip") else ""),
                              f"Подключено ПК: {len(pcs)}: " + ", ".join(f"{x['comp']} ({x['user'] or '—'})" for x in pcs)])
            QApplication.clipboard().setText(text)
            self.lbl_status.setText("📋 Карточка принтера скопирована")
            return
        lines = [f"👤 {u.get('full_fio') or u.get('fio')}"]
        loc = ", ".join(x for x in (u.get("address"), _office_label(u.get("office"))) if x)
        if loc:
            lines.append(f"📍 {loc}")
        ph = " | ".join(x for x in (f"Тел. {u['phone']}" if u.get("phone") else "",
                                    f"IP тел. {u['ip_phone']}" if u.get("ip_phone") else "") if x)
        if ph:
            lines.append(f"📞 {ph}")
        comp = db.clean_computer_name(u.get("comp", ""))
        pc = [x for x in (comp, u.get("ip") if u.get("ip") != "Не найден" else "") if x]
        os_line = next((l.replace("ОС:", "").strip() for l in self.lbl_specs.text().split("\n") if l.startswith("ОС:")), "")
        if os_line and os_line != "Н/Д":
            pc.append(os_line)
        if pc:
            lines.append("💻 ПК: " + " | ".join(pc))
        printers = u.get("printers") or []
        if printers:
            lines.append("🖨️ " + "; ".join(netutils.printer_label(p) for p in printers))
        QApplication.clipboard().setText("\n".join(lines))
        self.lbl_status.setText("📋 Карточка скопирована")

    def ping_selected(self):
        u = self.selected()
        if not u or not db.clean_computer_name(u.get("comp", "")):
            return
        PingDialog(db.clean_computer_name(u["comp"]), u.get("ip", ""), self, self).exec()

    def remote_action(self, action: str):
        u = self.selected()
        comp = db.clean_computer_name(u.get("comp", "")) if u else ""
        if not comp:
            MessageBox.warning(self, "Внимание", "У выбранной строки нет ПК.")
            return
        ip = u.get("ip", "Не найден")
        target = ip if ip != "Не найден" else comp
        if action == "health":
            HealthDialog(comp, self, self).exec()
            return
        if action == "software":
            SoftwareDialog(comp, self, self).exec()
            return
        if action == "logons":
            LogonsDialog(comp, self, self).exec()
            return
        if action == "disk":
            self.open_disk(comp, target)
            return
        if action == "power":
            self.open_power_menu(comp)
            return
        if self._deny(action):
            return
        if action == "wol":
            wake_single(comp, self, self)
            return
        try:
            if action == "rms":
                subprocess.Popen(netutils.rms_command(comp, ip))
                return
            if action in netutils.POWER_ACTIONS:
                self.power_action(action, comp, target)
                return
            argv = netutils.remote_command(action, target)
            if argv:
                subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW)
                db.log_action(self.admin_name, action, comp)
        except (OSError, ValueError) as exc:
            MessageBox.critical(self, "Ошибка", str(exc))

    def open_power_menu(self, comp: str) -> None:
        """«Питание ПК» — стилизованное меню: WoL, блокировка экрана, выход, сон, перезагрузка, выключение."""
        menu = QMenu(self)
        menu.setObjectName("diskMenu")
        head = menu.addAction(tr(f"Питание {comp}:"))
        head.setEnabled(False)
        for key, (label, hint) in netutils.POWER_ACTIONS.items():
            if key == "restart":
                menu.addSeparator()
            act = menu.addAction(tr(label))
            act.setToolTip(hint)
            act.triggered.connect(lambda _c=False, k=key: self.remote_action(k))
        menu.setToolTipsVisible(True)
        btn = self.action_buttons.get("power")
        pos = btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None and btn.isVisible() else QCursor.pos()
        self._power_menu = menu
        menu.popup(pos)

    def power_action(self, action: str, comp: str, target: str) -> None:
        """Выполнить пункт меню «Питание ПК» (кроме WoL): подтверждение для необратимых, шаги по очереди, запись в журнал."""
        label = netutils.POWER_ACTIONS[action][0].split(" ", 1)[1]
        if action in netutils.POWER_CONFIRM and not MessageBox.question(self, "Подтверждение", f"{label}: {comp}?"):
            return
        for argv in netutils.power_commands(action, target):
            subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW)
        db.log_action(self.admin_name, action, comp)
        self.lbl_status.setText(f"⏻ {label}: команда отправлена на {comp}")

    def open_disk(self, comp: str, target: str) -> None:
        """«Диск»: один том у ПК → сразу C$; несколько → стилизованное меню с выбором тома (буква, метка, размер)."""
        vols = netutils.known_volumes(comp)
        if len(vols) <= 1:
            self.remote_action(f"disk_{(vols[0]['letter'] if vols else 'C').lower()}")
            return
        menu = QMenu(self)
        menu.setObjectName("diskMenu")
        head = menu.addAction(tr(f"Открыть диск на {comp}:"))
        head.setEnabled(False)
        for v in vols:
            hint = " · ".join(x for x in (v.get("label"), v.get("size")) if x)
            act = menu.addAction(f"💽  {v['letter']}:$" + (f"    {hint}" if hint else ""))
            act.triggered.connect(lambda _c=False, l=v["letter"].lower(): self.remote_action(f"disk_{l}"))
        btn = self.action_buttons.get("disk")
        pos = btn.mapToGlobal(btn.rect().bottomLeft()) if btn is not None and btn.isVisible() else QCursor.pos()
        self._disk_menu = menu
        menu.popup(pos)

    def open_card(self):
        if self.search_input.hasFocus() or self.stack.currentIndex() != 1:
            return
        u = self.selected()
        if not u:
            return
        if u.get("kind") == "printer":
            self.ping_printer()
            return
        if u.get("entry") is None:
            MessageBox.information(self, "Карточка", "Данные из AD ещё загружаются или пользователь не найден.")
            return
        dlg = UserCardDialog(u["entry"], self, self)
        if dlg.exec() or getattr(dlg, "_entry_changed", False):
            self.start_search()
        dlg.deleteLater()

    def update_account_state_in_ui(self, login: str, entry) -> None:
        """Карточка сняла блокировку / включила-отключила учётку: сразу обновляем бейдж в таблице и инспектор,
        не дожидаясь нового поиска. Учётка — тот же объект Entry, что и в self.results."""
        text, kind = ad.account_badge(entry)
        for u in self.results:
            if u.get("login") == login:
                u["entry"] = entry
                u["account_text"], u["account_kind"] = text, kind
                u["is_disabled"] = ad.is_disabled(entry)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_LOGIN)
            if it and it.text() == login:
                self.table.setItem(r, 2, StatusItem(text, kind))
        u = self.selected()
        if u and u.get("login") == login:
            self._shown = None
            self.show_user(u)

    def row_menu(self, pos):
        u = self.selected()
        if not u:
            return
        menu = QMenu(self)
        menu.addAction(tr("👤 Открыть карточку"), self.open_card)
        menu.addAction("📝 Заметки", self.notes_selected)
        menu.addAction("🕓 История", self.history_selected)
        if access.can("groups_sync"):
            menu.addAction("🧬 Группы как у…", self.compare_groups)
        if db.clean_computer_name(u.get("comp", "")):
            menu.addSeparator()
            menu.addAction(tr("📡 Пинг"), self.ping_selected)
            menu.addAction("🩺 Здоровье ПК", lambda: self.remote_action("health"))
            menu.addAction("📦 Установленное ПО", lambda: self.remote_action("software"))
            menu.addAction("🔐 Входы за 24 ч", lambda: self.remote_action("logons"))
            menu.addAction("🖥️ RMS", lambda: self.remote_action("rms"))
            menu.addAction(tr("📁 Диск…"), lambda: self.remote_action("disk"))
            menu.addAction(tr("⚙️ Управление ПК"), lambda: self.remote_action("compmgmt"))
            if access.can("power"):
                pm = menu.addMenu(tr("⏻ Питание ПК"))
                for key, (label, _hint) in netutils.POWER_ACTIONS.items():
                    pm.addAction(tr(label), lambda k=key: self.remote_action(k))
            for act in self.plugin_actions:
                if not act.modifying or access.can("plugin_modifying"):
                    menu.addAction(act.label, lambda a=act: self.run_plugin(a))
        menu.addSeparator()
        comps = self.selected_computers()
        if comps:
            menu.addAction(f"📡 Массовый пинг / WoL ({len(comps)})…", self.mass_ping)
        if len(comps) == 2:
            menu.addAction(f"⚖️ Сравнить {comps[0]} ↔ {comps[1]}", self.compare_pcs)
        n = len(self.selected_users())
        if access.can("bulk_disable"):
            menu.addAction(f"🧰 Массовые операции ({n})…", self.bulk_operations)
        menu.addAction(tr("📤 Экспорт результатов…"), self.export_results)
        menu.addSeparator()
        cols = self.build_column_menu(self.table.columnAt(pos.x()), menu)
        cols.setTitle("📑 Колонки таблицы")
        menu.addMenu(cols)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------------ 3.0: инструменты
    def selected_users(self) -> list[dict]:
        """Все выделенные строки (Ctrl/Shift) → записи результатов, без дублей, с данными AD."""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        out, seen = [], set()
        for r in rows:
            it = self.table.item(r, COL_LOGIN)
            idx = it.data(Qt.ItemDataRole.UserRole) if it else None
            if isinstance(idx, int) and 0 <= idx < len(self.results):
                u = self.results[idx]
                if u.get("entry") is not None and u["login"] not in seen:
                    seen.add(u["login"])
                    out.append(u)
        return out

    def selected_computers(self) -> list[str]:
        """Имена ПК выделенных строк (без дублей, в порядке строк)."""
        rows = sorted({i.row() for i in self.table.selectedItems()})
        out: list[str] = []
        for r in rows:
            it = self.table.item(r, COL_LOGIN)
            idx = it.data(Qt.ItemDataRole.UserRole) if it else None
            if isinstance(idx, int) and 0 <= idx < len(self.results):
                comp = db.clean_computer_name(self.results[idx].get("comp", ""))
                if comp and comp not in out:
                    out.append(comp)
        return out

    # ------------------------------------------------------------------ 3.1: парк и сводка
    def mass_ping(self):
        comps = self.selected_computers() or [db.clean_computer_name(r.get("comp", "")) for r in self.results]
        comps = [c for c in comps if c]
        if not comps:
            MessageBox.information(self, "Массовый пинг", "Нет ПК в выделении/результатах.")
            return
        MassPingDialog(comps, self, self).exec()

    def compare_pcs(self):
        comps = self.selected_computers()
        if len(comps) != 2:
            MessageBox.information(self, "Сравнение ПК", "Выделите ровно две строки с ПК (Ctrl+клик).")
            return
        db.log_action(self.admin_name, "compare_pc", comps[0], comps[1])
        ComparePCDialog(comps[0], comps[1], self, self).exec()

    def search_text(self, text: str):
        """Из диалогов: подставить запрос и выполнить поиск."""
        self.show()
        self.search_input.setText(text)
        self.start_search()

    def refresh_attention(self):
        run_in_background(self, lambda: attention.collect_from_settings(self.get_conn, settings.attention),
                          self.set_attention_items, lambda m: log.debug("attention: %s", m))

    def set_attention_items(self, items: list[dict]):
        self.attention_items = list(items)
        pal = app_palette()
        high = sum(1 for i in items if i["severity"] == "high")
        color = pal.danger[0] if high else (pal.warning[0] if items else pal.success[0])
        self.lbl_attention.setText(f"🔔 {attention.summary_line(items)}")
        self.attention_card.setStyleSheet(f"#dashCard {{ border-left: 4px solid {color}; }}")
        if self.tray and high and not getattr(self, "_attention_notified", False):
            self._attention_notified = True
            self.tray.notify("ADK", attention.summary_line(items))

    def show_attention(self):
        dlg = AttentionDialog(self, self, items=self.attention_items or None)
        dlg.exec()
        dlg.deleteLater()

    def bulk_operations(self):
        if self._deny("bulk_disable"):
            return
        users = self.selected_users()
        if not users:
            MessageBox.information(self, "Массовые операции", "Выделите строки (Ctrl/Shift + клик) с учётными записями AD.")
            return
        dlg = BulkOperationsDialog(users, self, self)
        dlg.exec()
        dlg.deleteLater()
        self.start_search()

    def export_results(self):
        if not self.results:
            MessageBox.information(self, "Экспорт", "Нет результатов для экспорта.")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт результатов", f"adk_{self.search_input.text().strip()[:30] or 'results'}.xlsx",
                                              "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        try:
            n = export.export_rows(self.results, path)
        except Exception as exc:  # noqa: BLE001
            MessageBox.critical(self, "Экспорт", str(exc))
            return
        db.log_action(self.admin_name, "export", self.search_input.text().strip(), f"{n} строк → {os.path.basename(path)}")
        self.lbl_status.setText(f"📤 Экспортировано строк: {n} → {path}")

    def notes_selected(self):
        u = self.selected()
        if not u:
            return
        login = u.get("login", "")
        comp = db.clean_computer_name(u.get("comp", ""))
        if login and login != "—":
            dlg = NotesDialog(login, "user", self, self, title=u.get("full_fio") or u.get("fio") or login)
        elif comp:
            dlg = NotesDialog(comp, "pc", self, self, title=comp)
        else:
            return
        dlg.exec()
        dlg.deleteLater()
        self.refresh_notes_badge()

    def refresh_notes_badge(self):
        u = self._shown
        if not u:
            return
        login = u.get("login", "")
        comp = db.clean_computer_name(u.get("comp", ""))
        notes = db.notes_for(login, "user") if login and login != "—" else (db.notes_for(comp, "pc") if comp else [])
        if not notes:
            self.lbl_notes.setText("—")
            self.btn_notes.setText(tr("➕ Добавить заметку…"))
            return
        last = notes[0]
        more = f" (+{len(notes) - 1})" if len(notes) > 1 else ""
        self.lbl_notes.setText(f"{escape(last['ts'][:16])} · {escape(last['admin'])}: {escape(last['text'])}{more}")
        self.btn_notes.setText(f"📝 {len(notes)}")

    def history_selected(self):
        u = self.selected()
        if not u:
            return
        login = u.get("login", "")
        HistoryDialog("" if login == "—" else login, db.clean_computer_name(u.get("comp", "")), self).exec()

    def compare_groups(self):
        u = self.selected()
        if not u or u.get("entry") is None:
            MessageBox.information(self, "Группы", "Выберите пользователя с данными AD.")
            return
        if self._deny("groups_sync"):
            return
        GroupCompareDialog(u["entry"], self, self).exec()

    def run_plugin(self, act):
        u = self.selected()
        if not u:
            return
        if act.modifying and self._deny("plugin_modifying"):
            return
        comp = db.clean_computer_name(u.get("comp", ""))
        ctx = {"login": u.get("login", ""), "comp": comp, "ip": u.get("ip") if u.get("ip") != "Не найден" else "",
               "fio": u.get("full_fio") or u.get("fio") or "", "admin": self.admin_name, "entry": u.get("entry"),
               "conn_factory": self.get_conn}
        if not act.enabled(ctx):
            MessageBox.information(self, act.name, "Действие недоступно для этой строки (нужен ПК).")
            return
        ok, msg = plugins.run_action(act, ctx)
        self.lbl_status.setText(("✅ " if ok else "⚠️ ") + msg)
        db.log_action(self.admin_name, "plugin", ctx["login"] or comp, f"{act.name}: {msg[:120]}")

    # ------------------------------------------------------------------ колонки
    def build_column_menu(self, at_col: int = -1, parent=None) -> QMenu:
        """Контекстное меню столбцов (ПКМ по заголовку или пункт «Колонки» в меню строки).

        Простой список всех столбцов с галочками: галочка — показать, снять — скрыть.
        Внизу «↺ Стандартный набор». Последний видимый столбец скрыть нельзя.
        """
        menu = QMenu(parent or self)
        title = menu.addAction("Столбцы таблицы")
        title.setEnabled(False)
        menu.addSeparator()
        visible = sum(1 for c in range(self.table.columnCount()) if not self.table.isColumnHidden(c))
        for c in range(self.table.columnCount()):
            a = QAction(COLUMNS[c], menu)
            a.setCheckable(True)
            a.setChecked(not self.table.isColumnHidden(c))
            if a.isChecked() and visible <= 1:
                a.setEnabled(False)
            a.toggled.connect(lambda checked, col=c: self.toggle_column(col, checked))
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction("↺ Стандартный набор", self.reset_columns)
        return menu

    def column_menu(self, pos):
        hdr = self.table.horizontalHeader()
        self.build_column_menu(hdr.logicalIndexAt(pos)).exec(hdr.mapToGlobal(pos))

    def toggle_column(self, col: int, visible: bool):
        self.table.setColumnHidden(col, not visible)
        self.qsettings.setValue(COLUMNS_KEY, [c for c in range(self.table.columnCount())
                                              if self.table.isColumnHidden(c)])

    def restore_columns(self):
        hidden = self.qsettings.value(COLUMNS_KEY)
        try:
            hidden = {int(h) for h in hidden} if hidden is not None else set(DEFAULT_HIDDEN)
        except (TypeError, ValueError):
            hidden = set(DEFAULT_HIDDEN)
        for c in range(self.table.columnCount()):
            self.table.setColumnHidden(c, c in hidden)

    def reset_columns(self):
        self.qsettings.remove(COLUMNS_KEY)
        self.restore_columns()

    # ------------------------------------------------------------------ сканер
    def start_scan(self):
        if self.scanner and self.scanner.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.scanner = PCScannerWorker(self.get_conn, parent=self)
        self.scanner.progress.connect(self.lbl_status.setText)
        self.scanner.error.connect(lambda m: self.lbl_status.setText(f"⚠️ {m}"))
        self.scanner.finished_scan.connect(self.on_scan_done)
        self.scanner.start()

    def on_scan_done(self, total: int):
        self.btn_scan.setEnabled(True)
        if total:
            self.active_ad_total = total
        self.refresh_dashboard()

    # ------------------------------------------------------------------ закрытие
    def closeEvent(self, event):  # noqa: N802
        if self.tray and self.tray.available and not self._quitting and settings.minimize_to_tray:
            event.ignore()
            self.hide()
            if not self.qsettings.value("tray_hint_shown", False, type=bool):
                self.tray.notify("ADK", tr("ADK свёрнут в трей. Выход — через меню трея."))
                self.qsettings.setValue("tray_hint_shown", True)
            return
        self.qsettings.setValue("geometry", self.saveGeometry())
        if self.hotkey:
            self.hotkey.unregister()
        if self.tray:
            self.tray.hide()
        self.scan_timer.stop()
        self.debounce.stop()
        if hasattr(self, "attention_timer"):
            self.attention_timer.stop()
        for w in [self.scanner, *self._threads, *getattr(self, "_bg_workers", [])]:
            if w is not None and w.isRunning():
                w.cancel()
        for w in [self.scanner, *self._threads, *getattr(self, "_bg_workers", [])]:
            if w is not None and w.isRunning():
                w.wait(3000)
        event.accept()


def escape(text: str) -> str:
    return html.escape(str(text))


def _office_label(office: str | None) -> str:
    """«310» → «каб. 310»; «каб. 310» / «офис 12» остаются как есть."""
    o = (office or "").strip()
    if not o:
        return ""
    return o if o[0].isalpha() else f"каб. {o}"
