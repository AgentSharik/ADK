"""Главное окно: дашборд, единый поиск, таблица результатов и инспектор."""
from __future__ import annotations

import html
import contextlib
import logging
import os
import platform
import subprocess
import time

from PyQt6.QtCore import QRectF, QSettings, QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QCursor, QFont, QKeySequence, QPainter, QPalette, QPen, QShortcut
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QCompleter, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter, QStackedWidget, QStyle, QStyledItemDelegate,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import access, ad, attention, db, export, icons, nettools, netutils, plugins, updates
from .attention_ui import AttentionDialog
from .config import APP_TITLE, CREATE_NO_WINDOW, SEARCH_RESULT_LIMIT, settings
from .dialogs import (
    AuditLogDialog, DesignSettingsDialog, FreeIPDialog, InventoryDialog, PingDialog, PluginsDialog, PrintersDialog,
    RegisterUserDialog, RoleInfoDialog, RoleWelcomeDialog, UserCardDialog,
)
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
COL_PC = COLUMNS.index("Имя ПК")
# принтер в результатах поиска: вместо имени ПК — способ подключения (иконка + подпись). Определяется по порту
# очереди печати (netutils.printer_port_kind): IP_/TCP-порт → сетевой, \\server\queue → общий, USB → USB.
PRINTER_CONN = {"network": ("printer.network", "сетевой"), "shared": ("printer.shared", "общий (через сервер)"),
                "usb": ("printer.usb", "USB"), "local": ("printer", "локальный")}
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
    def __init__(self, username: str | None, password: str | None, initial_fill: bool = False,
                 startup_choice: str = ""):
        super().__init__()
        self.username, self.password = username, password
        self.initial_fill = initial_fill          # 3.5.11: база только что создана — заполнить её (сканер + принтеры)
        self.startup_choice = startup_choice      # 3.9.0: ответ на стартовый вопрос (full · pcs · '' — не задан/пропущен)
        self.fill_worker = None
        self.full_worker = None          # 3.12.0: полный опрос без окна — прогресс в строке статуса
        self.admin_name = username or os.environ.get("USERNAME", "sso")
        self.scan_owner = f"{os.environ.get('COMPUTERNAME') or platform.node()}\\{self.admin_name}"
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
        # 3.6.0: резервная копия базы по расписанию ([Paths] backup_every_hours); проверка раз в 15 минут — дёшево
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(lambda: run_in_background(self, db.backup_periodic, lambda _p: None, lambda _m: None))
        self.backup_timer.start(15 * 60_000)
        QTimer.singleShot(300, self.load_ad_count)
        # 3.9.0: вопрос «что собрать при старте» задаётся ДО главного окна и только при пустой базе (см. __main__);
        # здесь лишь запускаем выбранное. Ответ «Не сейчас» ничего не запускает — вопрос больше не появляется,
        # обновление по кнопке «Обновить статус сети ПК» и по расписанию auto_scan_interval_min.
        if startup_choice == "full":
            QTimer.singleShot(1500, self.start_full_scan)
        elif startup_choice == "pcs":
            QTimer.singleShot(1500, self.start_initial_fill)
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
        if settings.minimize_to_tray:   # 3.6.3: теперь это «показывать значок в трее», а не «сворачивать по крестику»
            self.tray = Tray(self, self.show_from_tray, self.show_and_search, self.start_scan, self.quit_app)
        if settings.global_hotkey:
            self.hotkey = GlobalHotkey(self, settings.global_hotkey)
            self.hotkey.activated.connect(self.show_and_search)

    def reapply_hotkey(self):
        """Перерегистрировать глобальную горячую клавишу после смены в настройках (3.6.3)."""
        if self.hotkey:
            self.hotkey.unregister()
            self.hotkey = None
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

    def retranslate(self):
        """Применить выбранный язык к главному окну без перезапуска (3.6.3)."""
        self.search_input.setPlaceholderText(tr("Фамилия, логин, почта, отдел, кабинет, имя ПК, IP, printer:…"))
        self.btn_search.setText(tr("Найти"))
        self.btn_settings.setText(tr("⚙️ Настройки"))
        self.btn_plugins.setText(tr("🧩 Плагины"))
        self.btn_scan.setText(tr("🔄 Обновить статус сети ПК"))
        self.btn_fill_stop.setText(tr("⏹ Остановить наполнение"))
        self.chk_archive.setText(tr("📦 Архивы (все ПК пользователя)"))
        self.chk_disabled.setText(tr("🚷 Отключённые учётки"))
        self.lbl_role_status.setText(tr(access.role_title_short()))
        for lbl, src in self._ru_labels:
            lbl.setText(tr(src))
        for b, src in self.quick_buttons:
            b.setText(tr(src))
        self.table.setHorizontalHeaderLabels([tr(c) for c in COLUMNS])
        if self.tray:
            self.tray.retranslate()

    def quit_app(self):
        self._quitting = True
        self.close()
        # страховка: если фоновые потоки/опрашиваемые ПК не дают циклу событий завершиться — через 3 с выходим принудительно
        # (в тестах не срабатывает — иначе os._exit убивает сам pytest)
        if not os.environ.get("PYTEST_CURRENT_TEST"):
            QTimer.singleShot(3000, lambda: os._exit(0))

    def apply_access(self):
        """Скрывает кнопки запрещённых действий (ПК / AD); роль показывается одним местом — в строке состояния
        (3.5.4: дублирующий бейдж «AD: только чтение» из шапки убран — внизу и так написано)."""
        if hasattr(self, "lbl_role_status"):
            self.lbl_role_status.setText(tr(access.role_title_short()))
            self.lbl_role_status.setToolTip(tr("{0}\n(Клик — подробнее о возможностях роли)").format(access.role_summary()[1]))
        for b, action in getattr(self, "_modifying_buttons", []):
            b.setVisible(access.can(action))

    def show_role_info(self):
        """Показывает модальное окно с описанием роли и доступных возможностей."""
        dlg = RoleInfoDialog(self.admin_name, self)
        dlg.exec()
        dlg.deleteLater()

    def _deny(self, action: str) -> bool:
        """True — действие запрещено ролью (предупреждение уже показано)."""
        if access.can(action):
            return False
        MessageBox.warning(self, tr("Недостаточно прав"), tr(access.deny_text(action)))
        return True

    def resolve_access(self):
        """Определяет роль по группам AD в фоне; когда роль известна — обновляет кнопки и показывает справку по роли."""
        if not access.policy_configured():
            self.show_role_welcome()          # политика групп не настроена: роль известна сразу (полный доступ / readonly)
            return
        run_in_background(self, lambda: access.resolve(self.get_conn, self.admin_name),
                          lambda _: (self.apply_access(), self.show_role_welcome()),
                          lambda m: (log.warning("access: %s", m), self.show_role_welcome()))

    def show_role_welcome(self):
        """Окно «Роль и права доступа» после входа: программа сама определила роль (AD / ПК / только чтение)
        и показывает, что доступно. Галочка «Больше не показывать» пишет UI.hide_role_welcome в config.ini."""
        if settings.hide_role_welcome or getattr(self, "_welcome_shown", False):
            return
        self._welcome_shown = True
        self.role_welcome = RoleWelcomeDialog(self.admin_name, self)
        self.role_welcome.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.role_welcome.open()             # модально к главному окну, но не блокирует цикл событий

    def check_updates(self):
        if not settings.version_file:
            return

        def done(info):
            if info:
                self.update_info = info
                self.lbl_update.setText(tr("⬆️ {0} {1}").format(tr("Доступна новая версия"), info["version"]))
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
                MessageBox.warning(self, tr("Обновление"), str(exc))

    def _build_shortcuts(self):
        self._shortcuts = []
        for keys, method, _ in self.SHORTCUTS:
            sc = QShortcut(QKeySequence(keys), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(getattr(self, method))
            self._shortcuts.append(sc)
        self.search_input.setToolTip(tr("Ctrl+F — фокус, Esc — очистить, Enter — искать"))

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

        # 3.9.0: панель поиска в две строки — раньше строка ввода, кнопка, два чекбокса в столбик и ещё две
        # кнопки были в одном ряду: на узком окне всё сплющивалось. Теперь вводу — вся ширина первой строки,
        # фильтры и кнопки — ровная вторая строка.
        top = QVBoxLayout()
        top.setSpacing(6)
        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr("Фамилия, логин, почта, отдел, кабинет, имя ПК, IP, printer:…"))
        self.search_input.setClearButtonEnabled(True)
        self.completer = QCompleter(db.suggestions(), self.search_input)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(8)
        self.completer.activated.connect(lambda _: QTimer.singleShot(0, self.start_search))
        self.search_input.setCompleter(self.completer)
        self._style_completer()
        self.search_input.textChanged.connect(self.on_text_changed)
        self.search_input.returnPressed.connect(self.start_search)
        self.btn_search = QPushButton(tr("Найти"))
        self.btn_search.setObjectName("btnPrimary")
        self.btn_search.setMinimumWidth(110)
        self.btn_search.clicked.connect(self.start_search)
        row1.addWidget(self.search_input, 1)
        row1.addWidget(self.btn_search)
        top.addLayout(row1)
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.chk_archive = QCheckBox(tr("📦 Архивы (все ПК пользователя)"))
        self.chk_disabled = QCheckBox(tr("🚷 Отключённые учётки"))
        for c in (self.chk_archive, self.chk_disabled):
            c.toggled.connect(self.start_search)
            row2.addWidget(c)
        row2.addStretch(1)
        self.btn_settings = QPushButton(tr("⚙️ Настройки"))
        self.btn_settings.clicked.connect(lambda: DesignSettingsDialog(self, self).exec())
        self.btn_plugins = QPushButton(tr("🧩 Плагины"))          # 3.9.1: атрибут окна — retranslate меняет язык и ей
        self.btn_plugins.setToolTip(tr("Плагины и модули автоматизации ADK"))
        self.btn_plugins.clicked.connect(lambda: PluginsDialog(self).exec())
        row2.addWidget(self.btn_plugins)
        row2.addWidget(self.btn_settings)
        top.addLayout(row2)
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
        self.btn_fill_stop = QPushButton(tr("⏹ Остановить наполнение"))
        self.btn_fill_stop.setObjectName("btnDanger")
        self.btn_fill_stop.setToolTip(tr("Прервать первичное наполнение новой базы. Дозаполнить можно позже: " "«Обновить статус сети ПК» и «Принтеры парка → Опросить парк»."))
        self.btn_fill_stop.clicked.connect(self.stop_initial_fill)
        self.btn_fill_stop.hide()
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("statusLabel")
        self.lbl_role_status = QLabel(tr(access.role_title_short()))
        self.lbl_role_status.setObjectName("roleStatusLabel")
        self.lbl_role_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lbl_role_status.setToolTip(tr("{0}\n(Клик — подробнее о возможностях роли)").format(access.role_summary()[1]))
        self.lbl_role_status.mousePressEvent = lambda e: self.show_role_info()
        bottom.addWidget(self.btn_scan)
        bottom.addWidget(self.btn_fill_stop)
        bottom.addWidget(self.lbl_status, 1)
        bottom.addWidget(self.lbl_role_status)
        content.addLayout(bottom)

    def _build_dashboard(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(14)
        self._ru_labels: list[tuple[object, str]] = []
        lbl = QLabel(tr("<b>📊 Состояние компьютеров домена</b> (клик по карточке — список):"))
        self._ru_labels.append((lbl, "<b>📊 Состояние компьютеров домена</b> (клик по карточке — список):"))
        lay.addWidget(lbl)
        self.cards = QHBoxLayout()
        lay.addLayout(self.cards)
        self.attention_card = QFrame()
        self.attention_card.setObjectName("dashCard")
        self.attention_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.attention_card.mousePressEvent = lambda e: self.show_attention()  # type: ignore[method-assign]
        arow = QHBoxLayout(self.attention_card)
        self.lbl_attention = QLabel(tr("🔔 Внимание: сводка собирается…"))
        self.lbl_attention.setStyleSheet("background: transparent; border: none; font-weight: bold;")
        self.lbl_attention.setWordWrap(True)
        arow.addWidget(self.lbl_attention, 1)
        b_att = QPushButton(tr("Открыть"))
        b_att.setObjectName("historyBtn")
        b_att.clicked.connect(self.show_attention)
        arow.addWidget(b_att)
        lay.addWidget(self.attention_card)
        lbl = QLabel(tr("<b>🕒 Недавние поиски:</b>"))
        self._ru_labels.append((lbl, "<b>🕒 Недавние поиски:</b>"))
        lay.addWidget(lbl)
        frame = QFrame()
        frame.setObjectName("dashCard")
        self.history = QHBoxLayout(frame)
        lay.addWidget(frame)
        lbl = QLabel(tr("<b>⚡ Быстрый доступ:</b>"))
        self._ru_labels.append((lbl, "<b>⚡ Быстрый доступ:</b>"))
        lay.addWidget(lbl)
        quick_box = QWidget()
        quick = FlowLayout(quick_box, spacing=8)          # переносится на новую строку, если окно узкое — подписи не режутся
        self._modifying_buttons: list = []  # (кнопка, действие) — скрываются, если access.can(действие) == False
        self.quick_buttons: list[tuple[QPushButton, str]] = []
        for text, fn, action in (("🔍 Свободный IP-адрес", lambda: FreeIPDialog(self).exec(), ""),
                                 ("📊 Excel-опись ПК", lambda: InventoryDialog(self, self).exec(), ""),
                                 ("🖨️ Принтеры парка", self.printers_overview, ""),
                                 ("➕ Новый пользователь AD", self.new_user, "create_user"),
                                 ("📜 Журнал действий", lambda: AuditLogDialog(self, self).exec(), "")):
            b = QPushButton(tr(text))
            b.clicked.connect(fn)
            if action:
                self._modifying_buttons.append((b, action))
            self.quick_buttons.append((b, text))
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
        self.table.setShowGrid(True)
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
        self.lbl_fio = QLabel(tr("👤 Выберите сотрудника"))
        self.lbl_fio.setStyleSheet("font-size: 13pt; font-weight: bold;")
        self.lbl_fio.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.btn_copy = QPushButton(tr("📋 Копировать"))
        self.btn_copy.setObjectName("btnSuccess")
        self.btn_copy.clicked.connect(self.copy_card)
        head.addWidget(self.lbl_fio)
        head.addWidget(self.btn_copy)
        self._header_plugins = QHBoxLayout()       # кнопки плагинов с place = "header" — рядом с ФИО
        self._header_plugins.setSpacing(6)
        head.addLayout(self._header_plugins)
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
                self.btn_ping = QPushButton(tr("📡 Пинг"))
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
                b.setToolTip(tr("Разбудить (WoL), заблокировать экран, выйти из пользователя, спящий режим, перезагрузить, выключить"))
            b.clicked.connect(lambda _, a=action: self.remote_action(a))
            self.action_buttons[action] = b
            if access.action_class(action):
                self._modifying_buttons.append((b, action))
            bg.addWidget(b, i // 2, i % 2)
        self._actions_grid = bg
        self._add_plugin_buttons()
        dl.addLayout(bg)
        dl.addWidget(QLabel(tr("<b>🖨️ Принтеры</b> (клик — кто ещё подключён):")))
        self.printers_box = QWidget()
        self.printers_flow = FlowLayout(self.printers_box)
        dl.addWidget(self.printers_box)
        # кнопка живого опроса — отдельной строкой под бейджами: в заголовке она не помещалась по ширине панели
        self.btn_live_printers = QPushButton(tr("📡 Опросить принтеры сейчас"))
        self.btn_live_printers.setObjectName("historyBtn")
        self.btn_live_printers.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_live_printers.setMinimumWidth(120)   # не распирает панель инспектора длинной подписью
        self.btn_live_printers.setToolTip(tr("Спросить ПК напрямую (CIM Win32_Printer), без записи: инвентарь и БД не меняются."))
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
        self.btn_history = QPushButton(tr("🕓 История"))
        self.btn_history.clicked.connect(self.history_selected)
        self.btn_compare = QPushButton(tr("🧬 Группы как у…"))
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

        run_in_background(self, work, done, lambda m: self.lbl_status.setText(tr("⚠️ AD: {0}").format(m)))

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
        # строку состояния дашборд трогает, только если там его же прежний текст: результат поиска, прогресс
        # наполнения или предупреждение не должны пропадать из-за фонового пересчёта карточек (load_ad_count)
        dash_text = f"Последнее сканирование: {last}" if last else "Готово к работе"
        if self.lbl_status.text() in ("", getattr(self, "_dash_status", "")):
            self.lbl_status.setText(dash_text)
        self._dash_status = dash_text
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

    def _style_completer(self) -> None:
        """3.5.10: список подсказок — это отдельное всплывающее окно (Qt::Popup); на Windows оно не всегда наследует
        стиль приложения и появлялось белой полосой с невидимым текстом. Красим его напрямую: палитра + свой QSS."""
        pop = self.completer.popup()
        if pop is None:
            return
        pal = app_palette()
        pop.setObjectName("completerPopup")
        pop.setAutoFillBackground(True)
        qp = pop.palette()
        for role, color in ((QPalette.ColorRole.Base, pal.card), (QPalette.ColorRole.Window, pal.card),
                            (QPalette.ColorRole.Text, pal.text), (QPalette.ColorRole.WindowText, pal.text),
                            (QPalette.ColorRole.Highlight, pal.selection), (QPalette.ColorRole.HighlightedText, pal.text)):
            qp.setColor(role, QColor(color))
        pop.setPalette(qp)
        pop.viewport().setPalette(qp)
        pop.setStyleSheet(
            f"QListView#completerPopup {{ background-color: {pal.card}; color: {pal.text}; border: 1px solid {pal.border}; "
            f"border-radius: 8px; padding: 4px; outline: none; font-size: {settings.design['font_size']}pt; }}"
            f"QListView#completerPopup::item {{ padding: 6px 10px; border-radius: 4px; color: {pal.text}; }}"
            f"QListView#completerPopup::item:hover, QListView#completerPopup::item:selected "
            f"{{ background-color: {pal.selection}; color: {pal.text}; }}")

    def on_theme_changed(self):
        self._style_completer()
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
            MessageBox.critical(self, tr("База данных"), str(exc))
        self.results = rows
        self.stack.setCurrentIndex(1)
        self.fill_table(rows)
        self.lbl_status.setText(tr("Компьютеров {0}: {1} · подтягиваю карточки из AD…").format(tag, len(rows)))
        self._enrich_rows_from_ad(rows, tag)

    def _enrich_rows_from_ad(self, rows: list[dict], tag: str) -> None:
        """3.5.10: строки категории дашборда получают те же данные, что и результат поиска (ФИО, телефоны, отдел,
        учётка) — одним LDAP-запросом по всем логинам, а не по клику на каждую строку."""
        logins = sorted({db.normalize_login(r["login"]) for r in rows if r.get("login") and r["login"] != "—"})
        if not logins:
            self.lbl_status.setText(tr("Компьютеров {0}: {1}").format(tag, len(rows)))
            return

        def work():
            found: dict[str, object] = {}
            c = self.get_conn()
            try:
                for i in range(0, len(logins), 100):     # LDAP-фильтр не резиновый — порциями по 100 логинов
                    chunk = logins[i:i + 100]
                    flt = "(&(objectCategory=person)(objectClass=user)(|" + "".join(
                        f"(sAMAccountName={ad.escape_filter_chars(l)})" for l in chunk) + "))"
                    for e in ad.paged_search(c, flt, ad.USER_ATTRS):
                        found[db.normalize_login(ad.get_ad_value(e, "sAMAccountName"))] = e
            finally:
                c.unbind()
            return found

        def done(found: dict):
            if self.results is not rows:
                return                                   # пользователь уже ушёл в другой поиск
            for r in rows:
                e = found.get(db.normalize_login(r.get("login") or ""))
                if e is None:
                    continue
                fio_full = ad.get_full_fio(e, r["login"])
                badge_text, badge_kind = ad.account_badge(e)
                r.update(entry=e, full_fio=fio_full, fio=ad.short_fio(fio_full), is_disabled=ad.is_disabled(e),
                         account_text=badge_text, account_kind=badge_kind, _ldap_loaded=True,
                         title=ad.get_ad_value(e, "title"), company=ad.get_ad_value(e, "company"),
                         dept=ad.get_ad_value(e, "department"), mail=ad.get_ad_value(e, "mail"),
                         phone=ad.get_ad_value(e, "telephoneNumber"), ip_phone=ad.get_ad_value(e, "ipPhone"),
                         address=ad.get_ad_value(e, "streetAddress"),
                         office=ad.get_ad_value(e, "physicalDeliveryOfficeName"))
            sel = self.selected()
            self.fill_table(rows)
            if sel is not None and sel in rows:
                self._shown = None
                self.select_row(rows.index(sel))
            self.lbl_status.setText(tr("Компьютеров {0}: {1}").format(tag, len(rows)))

        run_in_background(self, work, done, lambda m: self.lbl_status.setText(tr("Компьютеров {0}: {1} · AD: {2}").format(tag, len(rows), m)))

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
        self.lbl_status.setText(tr("🔍 Поиск…"))
        w = SearchWorker(self.get_conn, q, self.chk_archive.isChecked(), self.chk_disabled.isChecked(), parent=self)
        w.results_ready.connect(self.on_results)
        w.net_ready.connect(self.on_net_ready)
        w.error.connect(lambda m: self.lbl_status.setText(tr("⚠️ {0}").format(m)))
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
        self.results = rows
        self.stack.setCurrentIndex(1)
        self.fill_table(rows)
        # 3.5.4: история/подсказки пишутся ПОСЛЕ отрисовки таблицы — три записи в SQLite на медленном диске
        # (десятки мс каждая) раньше стояли между нажатием «Найти» и появлением строк
        terms = {t for r in rows for t in (r.get("login"), r.get("comp"), (r.get("full_fio") or "").split(" ")[0]) if t and t != "—"}
        QTimer.singleShot(0, lambda: self._after_results(query, terms))
        if truncated:
            self.lbl_status.setText(
                tr("Найдено: {0} — показаны первые {1} учётных записей, уточните запрос").format(len(rows), SEARCH_RESULT_LIMIT))
        else:
            printers = [r for r in rows if r.get("kind") == "printer"]
            people = len(rows) - len(printers)
            if printers:
                pcs = sum(len((r.get("printer") or {}).get("pcs") or []) for r in printers)
                self.lbl_status.setText(tr("🖨️ Принтеров: {0} (подключено ПК — {1}) · людей/ПК: {2}").format(len(printers), pcs, people))
            else:
                self.lbl_status.setText(tr("Найдено: {0}").format(len(rows)))

    @staticmethod
    def net_badge(u: dict) -> "StatusItem":
        """Ячейка «Сеть»: до проверки — «Проверка…» (нейтральная), потом честный статус."""
        if u.get("net_pending"):
            return StatusItem("● Проверка…", "checking")
        if u.get("kind") == "printer":
            if not ((u.get("printer") or {}).get("ip")):
                return StatusItem("—", "checking")  # USB/локальный принтер: сетевого статуса у него нет (3.5.6)
            return StatusItem(tr("● В сети") if u.get("is_online") else tr("● Не в сети"), "online" if u.get("is_online") else "offline")
        # 3.5.10: в строках поиска ПК лежит в ключе «comp», а IP без ПК = «Не найден» — раньше проверялись другие ключи,
        # и у сотрудника без ПК горело красное «Не в сети» вместо нейтрального прочерка
        comp = (u.get("comp") or u.get("computer_name") or "").strip()
        ip = (u.get("ip_address") or u.get("ip") or "").strip()
        if (not comp or comp == "—") and ip in ("", "Не найден", "Не указан", "—"):
            return StatusItem("—", "checking")      # У сотрудника нет ПК и IP — сети нет, нейтральный прочерк
        return StatusItem(tr("● В сети") if u.get("is_online") else tr("● Не в сети"), "online" if u.get("is_online") else "offline")

    def on_net_ready(self, net: dict, query: str) -> None:
        """Второй шаг поиска (3.5.4): пришли DNS/доступность — обновляем IP, бейджи «Сеть» и инспектор,
        строки не пересортировываем (таблица не должна «прыгать» под курсором)."""
        if self.search_input.text().strip() != query:
            return
        sel = self.selected()
        sel_was_pending = bool(sel and sel.get("net_pending"))
        SearchWorker.apply_net(self.results, net)   # ПК и принтеры (3.5.6: принтеры тоже проверяются вторым шагом)
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_LOGIN)
            idx = it.data(Qt.ItemDataRole.UserRole) if it else None
            if isinstance(idx, int) and 0 <= idx < len(self.results):
                u = self.results[idx]
                cell = self.table.item(r, COL_NET)
                if cell is None or cell.data(BADGE_ROLE) == "checking":
                    self.table.setItem(r, COL_NET, self.net_badge(u))
        if sel is not None and sel_was_pending:
            self._shown = None
            self.show_user(sel)

    def _after_results(self, query: str, terms: set[str]) -> None:
        """Отложенная часть on_results: история поиска и словарь подсказок (БД), когда таблица уже на экране."""
        try:
            db.save_search_query(query, self.admin_name)
            db.remember_terms(terms)
            model = self.completer.model()
            if model is not None and hasattr(model, "setStringList"):
                model.setStringList(db.suggestions())
        except Exception as exc:  # noqa: BLE001
            log.debug("after_results: %s", exc)

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
                login.setText("—")
                pinfo = u.get("printer") or {}
                pkind = pinfo.get("kind") or ("network" if u.get("ip") else "")
                conn = QTableWidgetItem(PRINTER_CONN.get(pkind, ("", tr("локальный")))[1])
                if pkind in PRINTER_CONN:
                    conn.setIcon(icons.icon(PRINTER_CONN[pkind][0], role="text"))
                cells = [login, fio, StatusItem("Принтер", "info"), conn, self.net_badge(u)]
                cells += [QTableWidgetItem("") for _ in range(7)] + [QTableWidgetItem(u.get("last_logon") or "")]
                for c, item in enumerate(cells):
                    self.table.setItem(r, c, item)
                continue
            cells = [login, fio,
                     StatusItem(u.get("account_text") or ("Не активна" if u["is_disabled"] else "Активна"),
                                u.get("account_kind") or ("disabled" if u["is_disabled"] else "active")),
                     QTableWidgetItem(u["comp"] or "—"),
                     self.net_badge(u)]
            cells += [QTableWidgetItem(str(u.get(k) or "")) for k in
                      ("phone", "ip_phone", "office", "address", "company", "dept", "title", "last_logon")]
            for c, item in enumerate(cells):
                self.table.setItem(r, c, item)
        self.table.setSortingEnabled(True)
        # найден принтер (по IP показывается только он) — столбец «Имя ПК» становится «Подключение»
        only_printers = bool(rows) and all(x.get("kind") == "printer" for x in rows)
        self.table.horizontalHeaderItem(COL_PC).setText(tr("Подключение") if only_printers else COLUMNS[COL_PC])
        self._apply_printer_columns(only_printers)
        fit_columns(self.table, max_width=280, min_width=70, wrap=False, stretch_last=True)
        self.table.setUpdatesEnabled(True)
        if rows:
            self._shown = None
            self.select_row(0)
        else:
            self.lbl_fio.setText(tr("❌ Ничего не найдено"))
            self.lbl_sub.setText(tr("Измените запрос"))
            self.details.setVisible(False)
            self._set_header_actions_visible(False)   # копировать нечего — кнопка рядом с «Ничего не найдено» сбивала с толку

    def _set_header_actions_visible(self, on: bool) -> None:
        """«Копировать» и кнопки плагинов рядом с ФИО: прячутся при пустом результате, возвращаются при показе записи."""
        self.btn_copy.setVisible(on)
        for i in range(self._header_plugins.count()):
            b = self._header_plugins.itemAt(i).widget()
            if b is not None:
                b.setVisible(on)

    PRINTER_IRRELEVANT = ("Телефон", "IP-тел")     # у принтера их нет — в режиме принтера столбцы прячутся

    def _apply_printer_columns(self, printer_mode: bool):
        """Результат — только принтеры: столбцы «Телефон»/«IP-тел» временно скрыты (настройка пользователя
        не трогается: при обычном поиске они возвращаются, как были)."""
        if printer_mode == getattr(self, "_printer_mode", False):
            return
        self._printer_mode = printer_mode
        if printer_mode:
            self._cols_before_printer = {c: self.table.isColumnHidden(c) for c in range(self.table.columnCount())}
            for name in self.PRINTER_IRRELEVANT:
                self.table.setColumnHidden(COLUMNS.index(name), True)
        else:
            for c, hidden in (getattr(self, "_cols_before_printer", None) or {}).items():
                self.table.setColumnHidden(c, hidden)

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
        self._set_header_actions_visible(True)
        pal = app_palette()
        login = u.get("login", "—")
        comp = db.clean_computer_name(u.get("comp", ""))
        if login and login != "—" and u.get("entry") is None and not u.get("_ldap_loaded"):
            self._enrich_from_ad(u)
        if not login or login == "—":
            self.lbl_fio.setText(tr("💻 {0} (свободный ПК)").format(comp))
            self.lbl_sub.setText(tr("Пользователь не залогинен"))
        else:
            self.lbl_fio.setText(f"👤 {u.get('full_fio') or u.get('fio') or login}")
            sub = " · ".join(x for x in (u.get("title"), u.get("company")) if x)
            self.lbl_sub.setText(sub or tr("Пользователь: {0}").format(login))
        self.vals["login"].setText(login or "—")
        self.vals["pc"].setText(tr("{0} ({1})").format(comp, u.get("ip", tr("Не найден"))) if comp else "—")
        on = bool(u.get("is_online"))
        if u.get("net_pending"):
            self.vals["status"].setText(tr("● Проверка…"))
            self.vals["status"].setStyleSheet(f"color: {pal.subtext}; font-weight: bold;")
        else:
            self.vals["status"].setText(tr("● В сети") if on else tr("● Не в сети"))
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
            txt = u["specs_custom"]
            if txt.strip().startswith("{"):
                # 3.9.1: полный опрос хранит характеристики как JSON — показывать человеческую сводку
                # («ОС · CPU · ОЗУ · Диски»), как выглядело в 3.8, а не сырой текст базы
                try:
                    txt = netutils.summarize_specs(netutils.parse_specs_json(txt)) or txt
                except Exception:  # noqa: BLE001
                    pass
            self.lbl_specs.setText(txt)
        elif comp:
            self.lbl_specs.setText("…")
            run_in_background(self, lambda: netutils.get_computer_specs_summary(comp),
                              lambda s: self.lbl_specs.setText(s) if self.selected() is u else None,
                              lambda m: log.debug("specs: %s", m))
        else:
            self.lbl_specs.setText(tr("ПК не привязан"))

    def _build_printer_pane(self) -> QWidget:
        """Инспектор для строки-принтера: адрес, доступность, кто подключён (клик по ПК — поиск)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setColumnMinimumWidth(0, 130)
        grid.setColumnStretch(1, 1)
        self.pvals: dict[str, QLabel] = {}
        for r, (key, label) in enumerate((("ip", "IP-адрес:"), ("status", "Доступность:"), ("probe", "Проверка:"),
                                          ("kind", "Подключение:"), ("port", "Порт:"), ("count", "Подключено ПК:"))):
            grid.addWidget(QLabel(f"<b>{label}</b>"), r, 0)
            v = QLabel("—")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.pvals[key] = v
            if key == "status":
                box = QHBoxLayout()
                box.setContentsMargins(0, 0, 0, 0)
                box.addWidget(v)
                self.btn_printer_ping = QPushButton(tr("📡 Пинг"))
                self.btn_printer_ping.setObjectName("btnInfo")
                self.btn_printer_ping.clicked.connect(self.ping_printer)
                box.addWidget(self.btn_printer_ping)
                self.btn_printer_web = QPushButton(tr("🌐 Веб-панель"))
                self.btn_printer_web.clicked.connect(self.open_printer_web)
                box.addWidget(self.btn_printer_web)
                box.addStretch()
                grid.addLayout(box, r, 1)
            else:
                grid.addWidget(v, r, 1)
        lay.addLayout(grid)
        lay.addWidget(QLabel(tr("<b>💻 Кто подключён</b> (клик — открыть ПК):")))
        self.printer_pcs = QTableWidget(0, 4)
        self.printer_pcs.setHorizontalHeaderLabels([tr("ПК"), tr("Пользователь"), tr("Сеть"), tr("По умолч.")])
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
        hint = QLabel(tr("Принтеры берутся из инвентарных CSV. Доступность — TCP 9100/631/80, затем ping. «Проверка» при " "поиске по IP: порты печати 9100/631 или веб-панель принтера — значит принтер; открытые 445/3389 — "
                      "это уже компьютер."))
        hint.setObjectName("subtle")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    def show_printer(self, u: dict):
        g = u.get("printer") or {}
        pal = app_palette()
        self.details_scroll.setVisible(False)
        self.printer_pane.setVisible(True)
        self._set_header_actions_visible(True)
        self.lbl_fio.setText(f"🖨️ {g.get('name') or u.get('fio')}")
        kind = {"network": "сетевой", "shared": "общий (через сервер)", "usb": "USB", "local": "локальный"}.get(g.get("kind", ""), "—")
        self.lbl_sub.setText(tr("Принтер · {0}").format(kind))
        ip = g.get("ip") or ""
        if ip:
            self.pvals["ip"].setText(ip)
        elif g.get("kind") in ("usb", "local"):
            self.pvals["ip"].setText(tr("— (не сетевой)"))
        else:
            self.pvals["ip"].setText(tr("IP не указан в инвентаре"))
        on = bool(u.get("is_online"))
        if ip and u.get("net_pending"):
            self.pvals["status"].setText(tr("● Проверка…"))
            self.pvals["status"].setStyleSheet(f"color: {pal.subtext}; font-weight: bold;")
        else:
            self.pvals["status"].setText((tr("● В сети") if on else tr("● Не в сети")) if ip else "—")
            self.pvals["status"].setStyleSheet(f"color: {pal.success[0] if on else pal.danger[0]}; font-weight: bold;" if ip else "")
        self.btn_printer_ping.setVisible(bool(ip))
        self.btn_printer_web.setVisible(bool(ip))
        self.pvals["kind"].setText(kind)
        if g.get("discovered"):
            # 3.5.10: принтер найден прямо по адресу (в базе его не было) — честно говорим откуда модель и имя узла
            self.pvals["port"].setText((tr("узел {0} · ").format(g["port"]) if g.get("port") else "") + tr("модель: {0}").format(g.get("source", "")))
            self.lbl_sub.setText(tr("Принтер · {0} · в базе не числится — найден по адресу").format(kind))
        else:
            self.pvals["port"].setText(g.get("port") or "—")
        pr = u.get("probe")
        if not ip:
            self.pvals["probe"].setText(tr("не сетевой — проверка по IP не применима"))
            self.pvals["probe"].setStyleSheet("")
        elif u.get("net_pending"):
            self.pvals["probe"].setText(tr("проверяется…"))
            self.pvals["probe"].setStyleSheet(f"color: {pal.subtext};")
        elif not pr:
            self.pvals["probe"].setText(tr("по данным инвентаря (запрос не по IP — устройство по адресу не проверялось)"))
            self.pvals["probe"].setStyleSheet("")
        elif pr.get("is_printer") is True:
            self.pvals["probe"].setText(tr("по адресу действительно принтер — {0}").format(pr.get("evidence", "")))
            self.pvals["probe"].setStyleSheet(f"color: {pal.success[0]}; font-weight: bold;")
        elif pr.get("is_printer") is False:
            self.pvals["probe"].setText(tr("внимание: по адресу сейчас НЕ принтер — {0}. Возможно, адрес переназначен — проверьте DHCP/DNS")
                                        .format(pr.get("evidence", "")))
            self.pvals["probe"].setStyleSheet(f"color: {pal.danger[0]}; font-weight: bold;")
        else:
            self.pvals["probe"].setText(tr("не проверено — {0}").format(pr.get("evidence", tr("узел не отвечает"))))
            self.pvals["probe"].setStyleSheet(f"color: {pal.warning[0]}; font-weight: bold;")
        pcs = g.get("pcs") or []
        if g.get("discovered"):
            self.pvals["count"].setText(tr("неизвестно — ПК с этим принтером в инвентаре нет (опросите парк в «Принтеры парка»)"))
        else:
            self.pvals["count"].setText(tr("{0} (в сети: {1})").format(len(pcs), sum(1 for x in pcs if x["is_online"])))
        self.printer_pcs.setRowCount(len(pcs))
        for r, x in enumerate(pcs):
            self.printer_pcs.setItem(r, 0, QTableWidgetItem(x["comp"]))
            self.printer_pcs.setItem(r, 1, QTableWidgetItem(x.get("user") or "—"))
            self.printer_pcs.setItem(r, 2, StatusItem("● В сети" if x["is_online"] else "● Не в сети", "online" if x["is_online"] else "offline"))
            self.printer_pcs.setItem(r, 3, QTableWidgetItem(tr("да") if x.get("is_default") else ""))
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
            self.printers_flow.addWidget(QLabel(tr("нет (или CSV не собран)")))
            return
        pal = app_palette()
        kind_badge = {"network": "info", "shared": "info", "usb": "warning", "local": "neutral"}
        for p in printers:
            text = p["name"] + (f" · {p['ip']}" if p.get("ip") else "") + (" · по умолчанию" if p.get("is_default") else "")
            tip = f"{netutils.printer_label(p)}\nПорт: {p.get('port') or '—'}" + ("\nПринтер по умолчанию" if p.get("is_default") else "")
            b = BadgeButton(text, kind_badge.get(p["kind"], "neutral"), pal, tip)
            b.setIcon(icons.icon(PRINTER_CONN.get(p["kind"], PRINTER_CONN["local"])[0], role="text"))
            b.setIconSize(QSize(18, 18))
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
        self.lbl_live_printers.setText(tr("⏳ Опрашиваю ПК напрямую…"))
        run_in_background(self, lambda: netutils.get_live_printers(comp),
                          lambda r: self._show_live_printers(u, comp, r) if self.selected() is u else self.btn_live_printers.setEnabled(True),
                          lambda m: self._show_live_printers(u, comp, {"error": m}))

    def _show_live_printers(self, u: dict, comp: str, r: dict):
        self.btn_live_printers.setEnabled(True)
        self.lbl_live_printers.setVisible(True)
        if "error" in r:
            self.lbl_live_printers.setText(tr("⚠️ Живой опрос не удался: {0}. Показан инвентарный снимок.").format(r["error"]))
            return
        live = r.get("printers") or []
        cached = {p["name"].lower() for p in (u.get("printers") or [])}
        live_names = {p["name"].lower() for p in live}
        added = [p for p in live if p["name"].lower() not in cached]
        gone = sorted(cached - live_names)
        kind_txt = {"network": "сетевой", "shared": "общий", "usb": "USB", "local": "локальный"}
        lines = [f"<b>📡 Сейчас на {html.escape(comp)}</b> ({time.strftime('%H:%M:%S')}, только просмотр — инвентарь не изменён):"]
        for p in live:
            st = p.get("status_text") or "—"
            mark = "🟢" if p["status"] in (3, 4) and not p["offline"] else "🔴" if p["offline"] or p["status"] == 7 else "⚪"
            lines.append(f"{mark} {p['name']}" + (f" · {p['ip']}" if p.get("ip") else "") + f" · {kind_txt.get(p['kind'], 'локальный')}"
                         + (" · по умолчанию" if p.get("is_default") else "") + f" — {st}")
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
            self.lbl_status.setText(tr("📋 Карточка принтера скопирована"))
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
        self.lbl_status.setText(tr("📋 Карточка скопирована"))

    def ping_selected(self):
        u = self.selected()
        if not u or not db.clean_computer_name(u.get("comp", "")):
            return
        PingDialog(db.clean_computer_name(u["comp"]), u.get("ip", ""), self, self).exec()

    def remote_action(self, action: str):
        u = self.selected()
        comp = db.clean_computer_name(u.get("comp", "")) if u else ""
        if not comp:
            MessageBox.warning(self, tr("Внимание"), tr("У выбранной строки нет ПК."))
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
            MessageBox.critical(self, tr("Ошибка"), str(exc))

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
        self._power_menu = menu
        self._popup_below(menu, self.action_buttons.get("power"))

    def power_action(self, action: str, comp: str, target: str) -> None:
        """Выполнить пункт меню «Питание ПК» (кроме WoL): подтверждение для необратимых, шаги по очереди, запись в журнал."""
        label = netutils.POWER_ACTIONS[action][0].split(" ", 1)[1]
        if action in netutils.POWER_CONFIRM and not MessageBox.question(self, tr("Подтверждение"), f"{label}: {comp}?"):
            return
        for argv in netutils.power_commands(action, target):
            subprocess.Popen(argv, creationflags=CREATE_NO_WINDOW)
        db.log_action(self.admin_name, action, comp)
        self.lbl_status.setText(tr("⏻ {0}: команда отправлена на {1}").format(label, comp))

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
        self._disk_menu = menu
        self._popup_below(menu, self.action_buttons.get("disk"))

    def _popup_below(self, menu: QMenu, btn) -> None:
        """Открыть меню под кнопкой; если до нижнего края экрана места меньше, чем высота меню, — над кнопкой.
        3.5.7: «Питание ПК» из шести пунктов открывалось вниз и последние пункты уходили за край окна/экрана."""
        if btn is None or not btn.isVisible():
            menu.popup(QCursor.pos())
            return
        pos = btn.mapToGlobal(btn.rect().bottomLeft())
        screen = btn.screen() or QApplication.primaryScreen()
        bottom = screen.availableGeometry().bottom() if screen is not None else 10 ** 6
        bottom = min(bottom, self.frameGeometry().bottom())      # и за нижний край главного окна не вылезать
        need = menu.sizeHint().height()
        top = btn.mapToGlobal(btn.rect().topLeft()).y() - need
        if pos.y() + need > bottom and top >= self.frameGeometry().top():
            pos.setY(top)
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
            MessageBox.information(self, tr("Карточка"), tr("Данные из AD ещё загружаются или пользователь не найден."))
            return
        comp = u.get("comp") or u.get("computer") or ""
        dlg = UserCardDialog(u["entry"], self, self, initial_comp=comp)
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
            MessageBox.information(self, tr("Массовый пинг"), tr("Нет ПК в выделении/результатах."))
            return
        MassPingDialog(comps, self, self).exec()

    def compare_pcs(self):
        comps = self.selected_computers()
        if len(comps) != 2:
            MessageBox.information(self, tr("Сравнение ПК"), tr("Выделите ровно две строки с ПК (Ctrl+клик)."))
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
        self.lbl_attention.setText(tr("🔔 {0}").format(attention.summary_line(items)))
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
            MessageBox.information(self, tr("Массовые операции"), tr("Выделите строки (Ctrl/Shift + клик) с учётными записями AD."))
            return
        dlg = BulkOperationsDialog(users, self, self)
        dlg.exec()
        dlg.deleteLater()
        self.start_search()

    def export_results(self):
        if not self.results:
            MessageBox.information(self, tr("Экспорт"), tr("Нет результатов для экспорта."))
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт результатов", f"adk_{self.search_input.text().strip()[:30] or 'results'}.xlsx",
                                              "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        try:
            n = export.export_rows(self.results, path)
        except Exception as exc:  # noqa: BLE001
            MessageBox.critical(self, tr("Экспорт"), str(exc))
            return
        db.log_action(self.admin_name, "export", self.search_input.text().strip(), f"{n} строк → {os.path.basename(path)}")
        self.lbl_status.setText(tr("📤 Экспортировано строк: {0} → {1}").format(n, path))

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
        self.btn_notes.setText(tr("📝 {0}").format(len(notes)))

    def history_selected(self):
        u = self.selected()
        if not u:
            return
        login = u.get("login", "")
        HistoryDialog("" if login == "—" else login, db.clean_computer_name(u.get("comp", "")), self).exec()

    def compare_groups(self):
        u = self.selected()
        if not u or u.get("entry") is None:
            MessageBox.information(self, tr("Группы"), tr("Выберите пользователя с данными AD."))
            return
        if self._deny("groups_sync"):
            return
        GroupCompareDialog(u["entry"], self, self).exec()

    def _add_plugin_buttons(self):
        """Кнопки плагинов в сетке «Действия с ПК» (после встроенных действий)."""
        bg = self._actions_grid
        i = sum(1 for k in self.action_buttons if not k.startswith("plugin:"))
        for act in self.plugin_actions:
            b = QPushButton(act.label)
            b.setToolTip(tr("Плагин: {0}").format(act.name))
            b.clicked.connect(lambda _, a=act: self.run_plugin(a))
            if act.modifying:
                self._modifying_buttons.append((b, "plugin_modifying"))
            self.action_buttons[f"plugin:{act.name}"] = b
            if getattr(act, "place", "actions") == "header":
                b.setObjectName("historyBtn")
                self._header_plugins.addWidget(b)
            else:
                bg.addWidget(b, i // 2, i % 2)
                i += 1

    def reload_plugins(self):
        """Перечитать папку плагинов (менеджер плагинов): старые кнопки убрать, новые добавить, права применить."""
        for key in [k for k in self.action_buttons if k.startswith("plugin:")]:
            b = self.action_buttons.pop(key)
            self._modifying_buttons = [(x, a) for x, a in self._modifying_buttons if x is not b]
            self._actions_grid.removeWidget(b)
            self._header_plugins.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self.plugin_actions = plugins.load_plugins(settings.plugins_dir)
        self._add_plugin_buttons()
        self.apply_access()

    def run_plugin(self, act):
        u = self.selected()
        if not u:
            return
        if act.modifying and self._deny("plugin_modifying"):
            return
        comp = db.clean_computer_name(u.get("comp", ""))
        ctx = {"login": u.get("login", ""), "comp": comp, "ip": u.get("ip") if u.get("ip") != "Не найден" else "",
               "fio": u.get("full_fio") or u.get("fio") or "", "admin": self.admin_name, "entry": u.get("entry"),
               "conn_factory": self.get_conn, "window": self, "mail": u.get("mail") or ""}
        if not act.enabled(ctx):
            MessageBox.information(self, act.name, tr("Действие недоступно для этой строки (нужен ПК)."))
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
        if getattr(self, "full_dialog", None) and self.full_dialog.worker.isRunning():
            return      # идёт полный опрос (ПК+принтеры+ПО) — отдельный запуск сканера не нужен
        # 3.6.0: общую базу сканирует один ADK — остальные не пишут в неё одновременно
        try:
            ok, holder, until = db.scan_lease_acquire(self.scan_owner)
        except Exception as exc:  # noqa: BLE001
            self.lbl_status.setText(tr("⚠️ База: {0}").format(exc))
            return
        if not ok:
            self.lbl_status.setText(tr("⏳ Парк сканирует {0} (до {1}) — база общая, повторный опрос не нужен").format(holder, until[11:16]))
            self.refresh_dashboard()
            return
        self.btn_scan.setEnabled(False)
        self.scanner = PCScannerWorker(self.get_conn, parent=self, deep=False)   # 3.9.1: фон — лёгкий, как в 3.8
        self.scanner.progress.connect(self.lbl_status.setText)
        self.scanner.error.connect(lambda m: self.lbl_status.setText(tr("⚠️ {0}").format(m)))
        self.scanner.finished_scan.connect(self.on_scan_done)
        self.scanner.start()

    def on_scan_done(self, total: int):
        self.btn_scan.setEnabled(True)
        with contextlib.suppress(Exception):
            db.scan_lease_release(self.scan_owner)
        if total:
            self.active_ad_total = total
        self.lbl_status.setText("")          # после сканера в строке состояния — «Последнее сканирование: …»
        self.refresh_dashboard()

    # ------------------------------------------------------------------ 3.8.0: полный опрос (стартовый вопрос — в __main__, до окна)
    def start_full_scan(self):
        """Полный опрос парка (ПК → принтеры → программы) — 3.12.0: без отдельного окна, прогресс в строке
        статуса рядом с кнопкой «Обновить парк»: сколько просканировано на каждом шаге + кнопка «Стоп»."""
        if self.full_worker and self.full_worker.isRunning():
            return
        if self.scanner and self.scanner.isRunning():
            return
        try:
            ok, holder, until = db.scan_lease_acquire(self.scan_owner)
        except Exception as exc:  # noqa: BLE001
            self.lbl_status.setText(tr("⚠️ База: {0}").format(exc))
            return
        if not ok:
            self.lbl_status.setText(tr("⏳ Парк сканирует {0} (до {1}) — база общая, повторный опрос не нужен").format(holder, until[11:16]))
            self.refresh_dashboard()
            return
        self.btn_scan.setEnabled(False)
        self.btn_fill_stop.show()
        self.lbl_status.setText(tr("🔄 Полный опрос парка: подготовка…"))
        self._planned, self._full_done = {}, {}
        from .scan_ui import FullScanWorker
        self.full_worker = FullScanWorker(self.get_conn, "full", parent=self)
        self._planned: dict[str, int] = {}
        self.full_worker.plan.connect(self._full_on_plan)
        self.full_worker.unit.connect(self._full_on_unit)
        self.full_worker.step_text.connect(self._full_on_step)
        self.full_worker.finished_full.connect(self.on_full_scan_done)
        self.full_worker.error.connect(lambda m: self.lbl_status.setText(tr("⚠️ {0}").format(m)))
        self._threads.append(self.full_worker)
        self.full_worker.start()

    # ---- прогресс полного опроса в строке статуса
    def _full_step_names(self) -> dict[str, str]:
        return {"pcs": "ПК", "printers": "принтеры", "specs": "характеристики"}

    def _full_on_plan(self, phase: str, total: int):
        self._planned[phase] = total
        self._full_done.setdefault(phase, 0)

    def _full_on_unit(self, phase: str, done: int, host: str):
        self._full_done[phase] = done
        names = self._full_step_names()
        bits = [f"{names.get(k, k)} {self._full_done.get(k, 0)}/{self._planned.get(k, 0)}"
                for k in self._planned]
        extra = f" · {host}" if host and "/" not in host else ""
        self.lbl_status.setText(tr("🔄 Полный опрос: ") + ", ".join(bits) + extra)

    def _full_on_step(self, text: str):
        self.lbl_status.setText(tr("🔄 ") + text if not text.startswith(("⚠️", "👥", "🔄")) else text)

    def on_full_scan_done(self, summary: dict):
        self.btn_scan.setEnabled(True)
        self.btn_fill_stop.hide()
        self.btn_fill_stop.setEnabled(True)
        self.full_worker = None
        with contextlib.suppress(Exception):
            db.scan_lease_release(self.scan_owner)
        if summary.get("pcs"):
            self.active_ad_total = summary["pcs"]
        from .scan_ui import full_summary_text
        self.lbl_status.setText(tr(full_summary_text(summary)))
        self.refresh_dashboard()

    # ------------------------------------------------------------------ 3.5.11: первичное наполнение новой базы
    def start_initial_fill(self):
        """База создана мастером первого запуска: сканер парка + принтеры со всех ПК в сети, с прогрессом и стопом."""
        from .setup_ui import InitialFillWorker
        if self.fill_worker and self.fill_worker.isRunning():
            return
        self.btn_scan.setEnabled(False)
        self.btn_fill_stop.show()
        self.lbl_status.setText(tr("🗄️ Новая база: первичное наполнение…"))
        self.fill_worker = InitialFillWorker(self.get_conn, parent=self)
        self.fill_worker.progress.connect(self.lbl_status.setText)
        self.fill_worker.finished_fill.connect(self.on_initial_fill_done)
        self._threads.append(self.fill_worker)
        self.fill_worker.start()

    def stop_initial_fill(self):
        if self.fill_worker and self.fill_worker.isRunning():
            self.fill_worker.cancel()
            self.btn_fill_stop.setEnabled(False)
            self.lbl_status.setText(tr("⏹ Останавливаю наполнение — начатые ПК дорабатывают…"))
        if self.full_worker and self.full_worker.isRunning():   # 3.12.0: «Стоп» останавливает и полный опрос
            self.full_worker.cancel()
            self.btn_fill_stop.setEnabled(False)
            self.lbl_status.setText(tr("⏹ Останавливаю опрос — начатые ПК дорабатывают…"))

    def on_initial_fill_done(self, summary: dict):
        from .setup_ui import fill_summary_text
        self.btn_fill_stop.hide()
        self.btn_fill_stop.setEnabled(True)
        self.btn_scan.setEnabled(True)
        self.initial_fill = False
        if summary.get("pcs"):
            self.active_ad_total = summary["pcs"]
        self.refresh_dashboard()
        self.lbl_status.setText(tr(fill_summary_text(summary)))     # после дашборда: он пишет своё «Готово к работе»

    # ------------------------------------------------------------------ закрытие
    def closeEvent(self, event):  # noqa: N802
        # 3.6.3: крестик полностью закрывает приложение (раньше уходил в трей и «висел» в диспетчере задач).
        # 3.12.1: выход стал быстрым. Раньше аренда базы отпускалась синхронно (сетевая база могла
        # держать закрытие по 10+ секунд), а каждый поток ждал по 3 с отдельно — окно «подвисало».
        self.qsettings.setValue("geometry", self.saveGeometry())
        if self.hotkey:
            self.hotkey.unregister()
        if self.tray:
            self.tray.hide()
        for t in (self.scan_timer, self.backup_timer, self.debounce, getattr(self, "attention_timer", None)):
            if t is not None:
                t.stop()
        # аренду отпускаем в фоне: сетевая база не должна задерживать закрытие окна;
        # не успеет — не страшно, аренда истекает сама по своему сроку
        import threading

        def _release_lease():
            with contextlib.suppress(Exception):
                db.scan_lease_release(self.scan_owner)
        threading.Thread(target=_release_lease, daemon=True).start()
        workers = [w for w in [self.scanner, *self._threads, *getattr(self, "_bg_workers", []),
                               getattr(getattr(self, "full_dialog", None), "worker", None),
                               getattr(self, "full_worker", None)]
                   if w is not None and w.isRunning()]
        for w in workers:
            w.cancel()
        deadline = time.monotonic() + 2.0              # общий бюджет на все потоки, не по 3 с на каждый
        for w in workers:
            w.wait(max(0, int((deadline - time.monotonic()) * 1000)))
        event.accept()
        # 3.9.1: главное. При включённом трее setQuitOnLastWindowClosed(False) — окно закрывалось,
        # а цикл событий продолжал жить: процесс оставался в диспетчере задач без окна. По правилу
        # «крестик — всегда полный выход» явно завершаем приложение (+ та же страховка, что у quit_app).
        if not getattr(self, "_quitting", False):
            self._quitting = True
            QApplication.quit()
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                QTimer.singleShot(3000, lambda: os._exit(0))


def escape(text: str) -> str:
    return html.escape(str(text))


def _office_label(office: str | None) -> str:
    """«310» → «каб. 310»; «каб. 310» / «офис 12» остаются как есть."""
    o = (office or "").strip()
    if not o:
        return ""
    return o if o[0].isalpha() else f"каб. {o}"
