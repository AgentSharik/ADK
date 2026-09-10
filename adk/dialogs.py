"""Диалоговые окна приложения."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from datetime import datetime, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFontComboBox, QFormLayout, QGridLayout,
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QPushButton,
    QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from . import ad, db, netutils
from .config import ACCOUNT_DISABLE_FLAG, CREATE_NO_WINDOW, SMARTCARD_REQUIRED_FLAG, settings
from .credentials import clear_credentials, save_credentials
from .theme import PRESET_THEMES, is_color_dark
from .widgets import (
    FlowLayout, FramelessDialog, InputDialog, MessageBox, app_palette, apply_theme, fit_columns, make_badge, run_in_background,
    safe_rich,
)

log = logging.getLogger(__name__)


# ============================================================================ вход
class LoginDialog(FramelessDialog):
    """Окно входа (3.2.9 — в языке дизайна приложения: эмблема, карточка с полями, одна главная кнопка).

    Поведение прежнее: NTLM по логину/паролю, Windows SSO (Kerberos), «запомнить» — в защищённом хранилище.
    """

    def __init__(self, error_msg: str = "", saved_user: str | None = None, saved_password: str | None = None):
        super().__init__("🔑 Вход в ADK", None, (460, 520))
        self.username: str | None = None
        self.password: str | None = None
        self.result_conn = None
        pal = app_palette()
        self.body.setSpacing(10)
        self.body.setContentsMargins(18, 4, 18, 4)

        # --- бренд: эмблема + имя + подпись
        from .tray import asset_path
        logo_path = asset_path("logo.png")
        if os.path.exists(logo_path):
            logo = QLabel()
            logo.setObjectName("brandLogo")
            logo.setPixmap(QPixmap(logo_path).scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio,
                                                     Qt.TransformationMode.SmoothTransformation))
            logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.body.addWidget(logo)
        brand = QLabel("ADK")
        brand.setObjectName("brandLabel")
        brand.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(brand)
        sub = QLabel("Active Directory Kit · вход в домен " + settings.domain_netbios)
        sub.setObjectName("subtle")
        sub.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(sub)

        # --- карточка с полями
        card = QFrame()
        card.setObjectName("dashCard")
        card.setStyleSheet("#dashCard { padding: 4px; }")
        cl = QVBoxLayout(card)
        cl.setSpacing(6)
        if error_msg:
            self.lbl_error = QLabel(safe_rich("⚠ Ошибка:", error_msg).replace("\n", "<br>"))
            self.lbl_error.setObjectName("loginError")
            self.lbl_error.setStyleSheet(f"color: {pal.danger[0]}; background-color: {pal.danger[1]}; "
                                         f"border: 1px solid {pal.danger[2]}; border-radius: 8px; padding: 6px 10px;")
            self.lbl_error.setWordWrap(True)
            cl.addWidget(self.lbl_error)
        cl.addWidget(QLabel("<b>Логин</b>"))
        self.user_in = QLineEdit(saved_user or "")
        self.user_in.setPlaceholderText(f"{settings.domain_netbios}\\login или login")
        self.user_in.setMinimumHeight(36)
        self.user_in.setClearButtonEnabled(True)
        cl.addWidget(self.user_in)
        cl.addWidget(QLabel("<b>Пароль</b>"))
        prow = QHBoxLayout()
        prow.setSpacing(6)
        self.pass_in = QLineEdit(saved_password or "")
        self.pass_in.setEchoMode(QLineEdit.EchoMode.Password)
        self.pass_in.setPlaceholderText("пароль доменной учётной записи")
        self.pass_in.setMinimumHeight(36)
        self.btn_eye = QPushButton("Показать")
        self.btn_eye.setCheckable(True)
        self.btn_eye.setFixedWidth(100)
        self.btn_eye.setMinimumHeight(36)
        self.btn_eye.setToolTip("Показать/скрыть пароль")
        self.btn_eye.toggled.connect(lambda on: (self.pass_in.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password), self.btn_eye.setText("Скрыть" if on else "Показать")))
        prow.addWidget(self.pass_in, 1)
        prow.addWidget(self.btn_eye)
        cl.addLayout(prow)
        from .credentials import storage_name
        store = storage_name()
        self.remember = QCheckBox("Запомнить меня" + (f" ({store})" if store else ""))
        self.remember.setChecked(bool(saved_user))
        if not store:
            self.remember.setToolTip("Защищённое хранилище недоступно — пароль сохранить не получится")
        cl.addWidget(self.remember)
        self.body.addWidget(card)

        # --- действия: главная кнопка и SSO как альтернатива
        self.btn_login = QPushButton("Войти")
        self.btn_login.setObjectName("btnPrimary")
        self.btn_login.setMinimumHeight(42)
        self.btn_login.setToolTip("Вход по логину и паролю (NTLM)")
        self.btn_login.clicked.connect(self.try_login)
        self.body.addWidget(self.btn_login)
        self.btn_sso = QPushButton("🪟 Войти под текущим пользователем Windows")
        self.btn_sso.setObjectName("btnInfo")
        self.btn_sso.setMinimumHeight(36)
        self.btn_sso.setToolTip("Windows SSO (Kerberos): без ввода пароля, под учёткой, из-под которой запущена программа")
        self.btn_sso.clicked.connect(self.try_sso)
        self.body.addWidget(self.btn_sso)
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        self.status.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.status)
        self.pass_in.returnPressed.connect(self.try_login)
        self.user_in.returnPressed.connect(self.pass_in.setFocus)
        (self.pass_in if saved_user else self.user_in).setFocus()
        if error_msg:
            # плашка ошибки переносится на несколько строк — окно подрастает, чтобы текст не обрезался
            self.layout().activate()
            self.lbl_error.setMinimumHeight(self.lbl_error.heightForWidth(self.lbl_error.width() or 380) + 4)
            self.resize(self.width(), max(self.height(), self.sizeHint().height()))

    def _busy(self, on: bool, text: str = ""):
        self.btn_login.setEnabled(not on)
        self.btn_sso.setEnabled(not on)
        self.status.setText(text)

    def try_login(self):
        u, p = self.user_in.text().strip(), self.pass_in.text()
        if not u or not p:
            return
        self._busy(True, "Проверка учётных данных…")

        def bind():
            c = ad.make_connection(u, p)
            c.unbind()
            return True

        def ok(_):
            self.username, self.password = ad.qualify_user(u), p
            if self.remember.isChecked():
                if not save_credentials(self.username, p):
                    from .credentials import last_error
                    MessageBox.warning(self, "Хранилище", "Пароль не сохранён.\nПричина: " + (last_error() or "неизвестна")
                                       + "\nВход выполнен, но в следующий раз пароль придётся ввести снова.")
            else:
                clear_credentials()
            self.accept()

        def fail(msg):
            self._busy(False)
            MessageBox.critical(self, "Ошибка входа", msg)

        run_in_background(self, bind, ok, fail)

    def try_sso(self):
        self._busy(True, "Kerberos-аутентификация…")

        def bind():
            c = ad.make_connection()
            who = c.extend.standard.who_am_i() or ""
            c.unbind()
            return who

        def ok(who):
            self.username, self.password = None, None
            self.status.setText(f"SSO: {who}")
            self.accept()

        def fail(msg):
            self._busy(False)
            MessageBox.critical(self, "Ошибка SSO", msg)

        run_in_background(self, bind, ok, fail)


# ============================================================================ информация о роли
class RoleWelcomeDialog(FramelessDialog):
    """Справка по определенной роли после входа с галочкой «Больше не показывать»."""

    def __init__(self, admin_name: str = "", parent=None):
        super().__init__("🛡️ Роль и права доступа", parent, (560, 440))
        from . import access
        title, text = access.role_summary()
        pal = app_palette()
        self.body.setSpacing(10)
        self.body.setContentsMargins(16, 8, 16, 12)

        card = QFrame()
        card.setObjectName("dashCard")
        cl = QVBoxLayout(card)
        cl.setSpacing(6)

        lbl_title = QLabel(f"<b>{title}</b>")
        color = pal.accent if not access.is_limited() else pal.warning[0]
        lbl_title.setStyleSheet(f"font-size: 14px; color: {color}; font-weight: bold;")
        cl.addWidget(lbl_title)

        lbl_text = QLabel(text)
        lbl_text.setWordWrap(True)
        cl.addWidget(lbl_text)

        if admin_name:
            lbl_user = QLabel(f"<b>Пользователь:</b> {admin_name}")
            lbl_user.setObjectName("subtle")
            cl.addWidget(lbl_user)

        self.body.addWidget(card)

        cap_card = QFrame()
        cap_card.setObjectName("dashCard")
        cap_l = QVBoxLayout(cap_card)
        cap_l.setSpacing(6)
        cap_l.addWidget(QLabel("<b>Возможности в текущей сессии:</b>"))

        def cap(text: str) -> QLabel:           # длинные строки переносятся, а не обрезаются по краю окна
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            return lbl

        if access.can_ad():
            cap_l.addWidget(cap("✅ <b>Управление Active Directory:</b> объекты AD, сброс паролей, блокировка, создание пользователей, группы"))
        else:
            cap_l.addWidget(cap("🔒 <b>Active Directory:</b> только чтение (изменение объектов отключено)"))

        if access.can_pc():
            cap_l.addWidget(cap("✅ <b>Управление компьютерами:</b> перезагрузка/питание, RMS, S.M.A.R.T., ПО, карта диска, заметки"))
        else:
            cap_l.addWidget(cap("🔒 <b>Компьютеры:</b> только просмотр сетевого статуса и характеристик"))

        self.body.addWidget(cap_card)
        self.body.addStretch(1)

        self.chk_dont_show = QCheckBox("Больше не показывать при входе")
        self.chk_dont_show.setChecked(False)
        self.body.addWidget(self.chk_dont_show)

        self.btn_ok = QPushButton("Продолжить")
        self.btn_ok.setObjectName("btnPrimary")
        self.btn_ok.setMinimumHeight(38)
        self.btn_ok.clicked.connect(self._save_and_close)
        self.body.addWidget(self.btn_ok)

    def showEvent(self, ev):
        super().showEvent(ev)
        # переносимый текст в карточках знает свою высоту только при известной ширине —
        # после первого показа подгоняем высоту окна, чтобы ни одна строка не обрезалась
        self.layout().activate()
        need = self.layout().totalMinimumSize().height()
        for lbl in self.findChildren(QLabel):
            if lbl.wordWrap():
                lbl.setMinimumHeight(lbl.heightForWidth(max(lbl.width(), 200)))
        self.layout().activate()
        need = max(need, self.layout().totalSizeHint().height())
        if self.height() < need:
            self.resize(self.width(), need)

    def _save_and_close(self):
        if self.chk_dont_show.isChecked():
            settings.save_section("UI", {"hide_role_welcome": "true"})
            settings.hide_role_welcome = True
        self.accept()


class RoleInfoDialog(FramelessDialog):
    """Окно с подробной информацией о текущей роли и возможностях приложения."""

    def __init__(self, admin_name: str = "", parent=None):
        super().__init__("🛡️ Роль и права доступа", parent, (520, 420))
        from . import access
        title, text = access.role_summary()
        pal = app_palette()
        self.body.setSpacing(12)
        self.body.setContentsMargins(16, 12, 16, 12)

        card = QFrame()
        card.setObjectName("dashCard")
        cl = QVBoxLayout(card)
        cl.setSpacing(8)

        lbl_title = QLabel(f"<b>{title}</b>")
        color = pal.accent if not access.is_limited() else pal.warning[0]
        lbl_title.setStyleSheet(f"font-size: 15px; color: {color}; font-weight: bold;")
        cl.addWidget(lbl_title)

        lbl_text = QLabel(text)
        lbl_text.setWordWrap(True)
        cl.addWidget(lbl_text)

        if admin_name:
            lbl_user = QLabel(f"<b>Пользователь:</b> {admin_name}")
            lbl_user.setObjectName("subtle")
            cl.addWidget(lbl_user)

        if access.reason():
            lbl_reason = QLabel(f"<b>Основание:</b> {access.reason()}")
            lbl_reason.setObjectName("subtle")
            lbl_reason.setWordWrap(True)
            cl.addWidget(lbl_reason)

        self.body.addWidget(card)

        cap_card = QFrame()
        cap_card.setObjectName("dashCard")
        cap_l = QVBoxLayout(cap_card)
        cap_l.setSpacing(6)
        cap_l.addWidget(QLabel("<b>Возможности в текущей сессии:</b>"))

        if access.can_ad():
            cap_l.addWidget(QLabel("✅ <b>Управление Active Directory:</b> изменение атрибутов, сброс паролей, блокировка/разблокировка, создание пользователей, управление группами"))
        else:
            cap_l.addWidget(QLabel("🔒 <b>Active Directory:</b> только чтение (изменение объектов и сброс паролей отключены)"))

        if access.can_pc():
            cap_l.addWidget(QLabel("✅ <b>Управление компьютерами:</b> перезагрузка/выключение, RMS, S.M.A.R.T., опрос ПО, карта диска, заметки"))
        else:
            cap_l.addWidget(QLabel("🔒 <b>Компьютеры:</b> только просмотр сетевого статуса и характеристик"))

        self.body.addWidget(cap_card)
        self.body.addStretch()

        btn_ok = QPushButton("Понятно")
        btn_ok.setObjectName("btnPrimary")
        btn_ok.setMinimumHeight(38)
        btn_ok.clicked.connect(self.accept)
        self.body.addWidget(btn_ok)


# ============================================================================ плагины и расширения
class PluginsDialog(FramelessDialog):
    """Менеджер плагинов: список файлов из папки плагинов с переключателем «включён», кнопки «Создать шаблон»,
    «Папка плагинов», «Перечитать». Включение/выключение — переименованием файла («_» в начале = выключен).
    После изменений главное окно перечитывает плагины (``parent.reload_plugins()``), если умеет."""

    def __init__(self, parent=None):
        super().__init__("🧩 Плагины", parent, (760, 560))
        self._app = parent
        pal = app_palette()
        self.body.setSpacing(10)
        self.body.setContentsMargins(16, 8, 16, 12)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        lbl_head = QLabel("<b>Плагины — свои кнопки в инспекторе и в меню строки</b>")
        lbl_head.setStyleSheet(f"font-size: 13.5px; color: {pal.title_accent};")
        title_box.addWidget(lbl_head)
        self.lbl_dir = QLabel("")
        self.lbl_dir.setObjectName("subtle")
        self.lbl_dir.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        title_box.addWidget(self.lbl_dir)
        head.addLayout(title_box, 1)
        self.btn_template = QPushButton("➕ Создать шаблон плагина")
        self.btn_template.setObjectName("btnPrimary")
        self.btn_template.setToolTip("Создать файл-заготовку с полной документацией внутри (выключен, пока не переименован)")
        self.btn_template.clicked.connect(self._create_template)
        head.addWidget(self.btn_template)
        self.btn_folder = QPushButton("📁 Папка плагинов")
        self.btn_folder.clicked.connect(self._open_plugins_dir)
        head.addWidget(self.btn_folder)
        self.btn_reload = QPushButton("🔄 Перечитать")
        self.btn_reload.clicked.connect(self.reload)
        head.addWidget(self.btn_reload)
        self.body.addLayout(head)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Файл", "Действия (кнопки)", "Права", "Состояние"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(2, 130)
        self.table.setColumnWidth(3, 120)
        self.table.itemSelectionChanged.connect(self._sync_buttons)
        self.table.itemDoubleClicked.connect(lambda _it: self._toggle())
        self.body.addWidget(self.table, 1)

        act_row = QHBoxLayout()
        self.btn_toggle = QPushButton("Включить")
        self.btn_toggle.setObjectName("btnSuccess")
        self.btn_toggle.setEnabled(False)
        self.btn_toggle.clicked.connect(self._toggle)
        act_row.addWidget(self.btn_toggle)
        self.btn_edit = QPushButton("✏️ Открыть файл")
        self.btn_edit.setEnabled(False)
        self.btn_edit.clicked.connect(self._open_file)
        act_row.addWidget(self.btn_edit)
        self.btn_delete = QPushButton("🗑️ Удалить")
        self.btn_delete.setObjectName("btnDanger")
        self.btn_delete.setEnabled(False)
        self.btn_delete.clicked.connect(self._delete)
        act_row.addWidget(self.btn_delete)
        act_row.addStretch()
        self.lbl_hint = QLabel("Двойной клик по строке — включить/выключить. Выключенные файлы начинаются с «_».")
        self.lbl_hint.setObjectName("subtle")
        act_row.addWidget(self.lbl_hint)
        self.body.addLayout(act_row)

        info = QFrame()
        info.setObjectName("dashCard")
        il = QVBoxLayout(info)
        il.setSpacing(4)
        il.addWidget(QLabel("<b>Как сделать свой плагин</b>"))
        steps = QLabel("1. «Создать шаблон плагина» — в папке появится <code>_template_plugin.py</code>: в нём описано всё "
                       "(атрибуты, методы, что приходит в <code>ctx</code>, примеры).<br>"
                       "2. Откройте файл, переименуйте класс, впишите своё в <code>run(ctx)</code>.<br>"
                       "3. Уберите «_» из имени файла (или нажмите «Включить») и «Перечитать» — кнопка появится в инспекторе "
                       "и в меню строки. Действия с <code>modifying = True</code> видит только роль «ПК».")
        steps.setObjectName("subtle")
        steps.setWordWrap(True)
        il.addWidget(steps)
        self.body.addWidget(info)

        foot = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("subtle")
        foot.addWidget(self.status, 1)
        btn_close = QPushButton("Закрыть")
        btn_close.setMinimumHeight(34)
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        self.body.addLayout(foot)
        self.reload()

    # ---------------------------------------------------------------- данные
    def reload(self):
        from . import plugins
        d = settings.plugins_dir
        self.lbl_dir.setText(f"Папка: {d}")
        self.files = plugins.list_plugin_files(d)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.files))
        for r, f in enumerate(self.files):
            self.table.setItem(r, 0, QTableWidgetItem(f["file"]))
            names = ", ".join(a.name for a in f["actions"]) or ("— (нет классов Action)" if not f["error"] else "")
            it = QTableWidgetItem(f["error"] or names)
            if f["error"]:
                it.setForeground(QColor(app_palette().danger[0]))
                it.setToolTip(f["error"])
            self.table.setItem(r, 1, it)
            rights = "меняет (роль «ПК»)" if any(a.modifying for a in f["actions"]) else ("только чтение" if f["actions"] else "")
            self.table.setItem(r, 2, QTableWidgetItem(rights))
            st = QTableWidgetItem("● Включён" if f["enabled"] else "○ Выключен")
            st.setForeground(QColor(app_palette().success[0] if f["enabled"] else app_palette().subtext))
            st.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, 3, st)
        self.table.setSortingEnabled(False)
        on = sum(1 for f in self.files if f["enabled"])
        self.status.setText(f"Файлов: {len(self.files)} · включено: {on}" if self.files else
                            "Папка пуста — нажмите «Создать шаблон плагина».")
        self._sync_buttons()
        if self._app is not None and hasattr(self._app, "reload_plugins"):
            self._app.reload_plugins()

    def _current(self) -> dict | None:
        r = self.table.currentRow()
        return self.files[r] if 0 <= r < len(self.files) else None

    def _sync_buttons(self):
        f = self._current()
        for b in (self.btn_toggle, self.btn_edit, self.btn_delete):
            b.setEnabled(f is not None)
        if f is not None:
            self.btn_toggle.setText("Выключить" if f["enabled"] else "Включить")
            self.btn_toggle.setObjectName("btnWarning" if f["enabled"] else "btnSuccess")
            self.btn_toggle.style().unpolish(self.btn_toggle)
            self.btn_toggle.style().polish(self.btn_toggle)

    # ---------------------------------------------------------------- действия
    def _create_template(self):
        from . import plugins
        path = plugins.write_template(settings.plugins_dir)
        if not path:
            MessageBox.warning(self, "Плагины", f"Не удалось создать файл в папке {settings.plugins_dir}")
            return
        self.reload()
        for r, f in enumerate(self.files):
            if f["path"] == path:
                self.table.selectRow(r)
        self.status.setText(f"Создан шаблон: {os.path.basename(path)} — откройте его, документация внутри файла.")

    def _toggle(self):
        from . import plugins
        f = self._current()
        if f is None:
            return
        try:
            plugins.set_enabled(f["path"], not f["enabled"])
        except OSError as exc:
            MessageBox.warning(self, "Плагины", f"Не удалось переименовать файл: {exc}")
            return
        name = f["file"]
        self.reload()
        for r, x in enumerate(self.files):
            if x["file"].lstrip("_") == name.lstrip("_"):
                self.table.selectRow(r)
        self.status.setText(("Включён: " if not f["enabled"] else "Выключен: ") + name.lstrip("_"))

    def _open_file(self):
        f = self._current()
        if f is None:
            return
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(f["path"]))

    def _delete(self):
        f = self._current()
        if f is None:
            return
        if not MessageBox.question(self, "Удалить плагин", f"Удалить файл {f['file']} без возможности восстановления?"):
            return
        try:
            os.remove(f["path"])
        except OSError as exc:
            MessageBox.warning(self, "Плагины", f"Не удалось удалить: {exc}")
            return
        self.reload()

    def _open_plugins_dir(self):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        os.makedirs(settings.plugins_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(settings.plugins_dir))


# ============================================================================ карточка пользователя
_RU_LABELS = {
    "sAMAccountName": "Логин", "displayName": "Отображаемое имя", "mail": "Почта",
    "telephoneNumber": "Телефон", "ipPhone": "IP-телефон", "mobile": "Мобильный",
    "physicalDeliveryOfficeName": "Кабинет", "streetAddress": "Адрес", "company": "Организация",
    "department": "Отдел", "title": "Должность", "description": "Описание",
}


class UserCardDialog(FramelessDialog):
    """Карточка учётной записи AD (3.2.5 — переработана).

    Карточка — про учётку, а не про ПК: всё, что уже есть в быстрых действиях инспектора (RMS, C$, перезагрузка,
    здоровье, принтеры), здесь убрано. Осталось: профиль (две колонки полей), группы (две панели рядом),
    состояние учётки с действиями (пароль, блокировка, отключение), характеристики ПК только для чтения и привязка ПК.
    """

    def __init__(self, entry, app, parent=None):
        self.entry, self.app = entry, app
        self.dn = entry.entry_dn
        self._entry_changed = False      # были ли изменения состояния — главное окно тогда перечитает AD
        fio = ad.get_full_fio(entry, "Пользователь")
        super().__init__(f"👤 Карточка: {fio}", parent, (1000, 740), large_font=True)
        self.login = ad.get_ad_value(entry, "sAMAccountName")
        self.original_uac = ad.get_ad_int_value(entry, "userAccountControl")
        self.current_comp = db.get_computer_by_login(self.login)
        self.current_ip = "Не найден"
        self._ad_widgets: list = []          # скрываются без права «AD»
        pal = app_palette()
        from . import access as _access
        from .tools import GroupCompareDialog, HistoryDialog, NotesDialog

        # ---------------------------------------------------------------- шапка: кто это и в каком состоянии
        head = QFrame()
        head.setObjectName("cardHead")
        head.setStyleSheet(f"QFrame#cardHead {{ background-color: {pal.header}; border: 1.5px solid {pal.border}; border-radius: 8px; }}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(14, 10, 14, 10)
        avatar = QLabel(self._initials(fio))
        avatar.setFixedSize(52, 52)
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setStyleSheet(f"background-color: {pal.title_accent}; color: {pal.on_accent}; border-radius: 26px; "
                             "font-size: 15pt; font-weight: bold;")
        hl.addWidget(avatar)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.lbl_name = QLabel(fio)
        self.lbl_name.setStyleSheet(f"font-size: 14pt; font-weight: bold; color: {pal.title_accent};")
        self.lbl_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        names.addWidget(self.lbl_name)
        sub = " · ".join(x for x in (self.login, ad.get_ad_value(entry, "title"), ad.get_ad_value(entry, "department"),
                                     ad.get_ad_value(entry, "company")) if x)
        self.lbl_sub = QLabel(sub)
        self.lbl_sub.setObjectName("subtle")
        self.lbl_sub.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        names.addWidget(self.lbl_sub)
        hl.addLayout(names, 1)
        text, kind = ad.account_badge(entry)
        self.badge_state = make_badge(text, kind, pal)
        hl.addWidget(self.badge_state)
        self.body.addWidget(head)

        # ---------------------------------------------------------------- панель кнопок: только про учётку
        # 3.5.4: шрифт карточки 12 pt — семь кнопок с прежними подписями в одну строку уже не помещаются в 1000 px,
        # поэтому ряд переносится на вторую строку (FlowLayout), а не растягивает окно и не режет подписи
        bar_holder = QWidget()
        bar = FlowLayout(bar_holder, spacing=6)
        b_copy = QPushButton("📋 Копировать")
        b_copy.setObjectName("btnSuccess")
        b_copy.clicked.connect(self.copy_to_clipboard)
        bar.addWidget(b_copy)
        n_notes = len(db.notes_for(self.login, "user"))
        self.btn_notes = QPushButton(f"📝 Заметки ({n_notes})" if n_notes else "📝 Заметки")
        self.btn_notes.clicked.connect(lambda: (NotesDialog(self.login, "user", self.app, self, title=fio).exec(),
                                                self._refresh_notes_btn()))
        bar.addWidget(self.btn_notes)
        b_hist = QPushButton("🕓 История")
        b_hist.clicked.connect(lambda: HistoryDialog(self.login, self.current_comp, self).exec())
        bar.addWidget(b_hist)
        if _access.can("groups_sync"):
            b_cmp = QPushButton("🧬 Группы как у…")
            b_cmp.setToolTip("Сравнить группы с эталонным сотрудником и выровнять членство")
            b_cmp.clicked.connect(lambda: GroupCompareDialog(self.entry, self.app, self).exec())
            bar.addWidget(b_cmp)
        self.btn_reset = QPushButton("🔑 Смена пароля…")
        self.btn_reset.setObjectName("btnWarning")
        self.btn_reset.setToolTip("Новый пароль по политике или свой; крупно на экране, карточка для сотрудника, снятие блокировки")
        self.btn_reset.clicked.connect(self.reset_password)
        bar.addWidget(self.btn_reset)
        self._ad_widgets.append(self.btn_reset)
        st = ad.account_status(self.entry, settings.max_password_age_days)
        self.btn_unlock = QPushButton("🔓 Снять блокировку")
        self.btn_unlock.setObjectName("btnWarning")
        self.btn_unlock.setToolTip("lockoutTime = 0 — снимает блокировку после неверных паролей, учётку не включает")
        self.btn_unlock.clicked.connect(self.unlock)
        self.btn_unlock.setVisible(bool(st["locked"]))
        bar.addWidget(self.btn_unlock)
        self._ad_widgets.append(self.btn_unlock)
        disabled = bool(self.original_uac & ACCOUNT_DISABLE_FLAG)
        self.btn_toggle = QPushButton("✅ Включить учётную запись" if disabled else "⛔ Отключить учётную запись")
        self.btn_toggle.setObjectName("btnSuccess" if disabled else "btnDanger")
        self.btn_toggle.setToolTip("Включить учётную запись (UAC −= 2)" if disabled else
                                   "Отключить учётную запись — увольнение / декрет: UAC += 2" + (" и экспорт ящика в PST" if settings.pst_backup_base else ""))
        self.btn_toggle.clicked.connect(self.toggle_disabled)
        bar.addWidget(self.btn_toggle)
        self._ad_widgets.append(self.btn_toggle)
        self.body.addWidget(bar_holder)
        self.body.addSpacing(6)     # 3.5.4: при крупном шрифте вкладки упирались в ряд кнопок — воздух между ними

        tabs = self.tabs = QTabWidget()
        self.body.addWidget(tabs, 1)
        tabs.addTab(self._build_info_tab(), "👤 Профиль")
        tabs.addTab(self._build_groups_tab(), "👥 Группы")
        tabs.addTab(self._build_account_tab(), "🔐 Учётная запись")
        tabs.addTab(self._build_specs_tab(), "💻 Характеристики ПК")
        self._apply_ad_access()
        self._load_pc_state()

    @staticmethod
    def _initials(fio: str) -> str:
        parts = [p for p in fio.split() if p]
        return "".join(p[0].upper() for p in parts[:2]) or "?"

    # ------------------------------------------------------------------ вкладки
    def _build_info_tab(self) -> QWidget:
        """Профиль: поля в две колонки (контакты слева, место работы справа), под ними «способ входа» и «Сохранить»."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 8)
        cols = QHBoxLayout()
        cols.setSpacing(24)
        left, right = QFormLayout(), QFormLayout()
        for f in (left, right):
            f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            f.setVerticalSpacing(8)
        self.inputs: dict[str, QLineEdit] = {}
        left_keys = ("sAMAccountName", "displayName", "mail", "telephoneNumber", "ipPhone", "mobile")
        for attr, label in _RU_LABELS.items():
            le = QLineEdit(ad.get_ad_value(self.entry, attr))
            le.setReadOnly(attr == "sAMAccountName")
            (left if attr in left_keys else right).addRow(label + ":", le)
            self.inputs[attr] = le
        self.smartcard = QCheckBox("Только смарт-карта для входа")
        self.smartcard.setChecked(bool(self.original_uac & SMARTCARD_REQUIRED_FLAG))
        self.original_skype = ad.get_ad_value(self.entry, "msRTCSIP-UserEnabled").upper() == "TRUE"
        self.skype = QCheckBox("Lync / Skype for Business")
        self.skype.setChecked(self.original_skype)
        right.addRow("Способ входа:", self.smartcard)
        right.addRow("", self.skype)
        cols.addLayout(left, 1)
        cols.addLayout(right, 1)
        lay.addLayout(cols)
        lay.addStretch()
        foot = QHBoxLayout()
        hint = QLabel("Отправляются только изменённые поля; пустое значение очищает атрибут.")
        hint.setObjectName("subtle")
        hint.setWordWrap(True)          # 3.5.4: подсказка не должна диктовать минимальную ширину карточки
        foot.addWidget(hint, 1)
        save = QPushButton("💾 Сохранить изменения в AD")
        save.setObjectName("btnSuccess")
        save.clicked.connect(self.save_changes)
        foot.addWidget(save)
        self.btn_save = save
        self._ad_widgets.append(save)
        lay.addLayout(foot)
        return w

    def _build_account_tab(self) -> QWidget:
        """Состояние учётки: понятные строки «что / значение», действия — в панели кнопок сверху."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 8)
        st = ad.account_status(self.entry, settings.max_password_age_days)
        pal = app_palette()
        reason = ad.account_inactive_reason(self.entry)
        rows = [("Состояние", ("Не активна — " + reason) if reason else "Активна — пользователь работает", bool(reason)),
                ("Пароль", st["pwd_text"], bool(st["pwd_warn"]) or (st["pwd_days_left"] is not None and st["pwd_days_left"] <= 0)),
                ("Блокировка", st["locked_text"] if st["locked"] else "нет", bool(st["locked"])),
                ("Срок действия", st["expires_text"] or "бессрочно", bool(st["expired"])),
                ("Неудачных входов", str(st["bad_pwd"] or 0), bool(st["bad_pwd"])),
                ("Последний вход", st["last_logon_text"], False),
                ("Создана", st["created_text"], False),
                ("DN", self.dn, False)]
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        form.setVerticalSpacing(8)
        self.account_vals: dict[str, QLabel] = {}
        for name, value, warn in rows:
            v = QLabel(value)
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if warn:
                v.setStyleSheet(f"color: {pal.danger[0]}; font-weight: bold;")
            form.addRow(f"<b>{name}:</b>", v)
            self.account_vals[name] = v
        lay.addLayout(form)
        self.lbl_account = QLabel("\n".join(f"{n}: {v}" for n, v, _ in rows[:5]))   # текстовая сводка (копирование/тесты)
        self.lbl_account.setVisible(False)
        lay.addWidget(self.lbl_account)
        lay.addStretch()
        hint = QLabel("Смена пароля, снятие блокировки и отключение — кнопки в верхней панели карточки.")
        hint.setObjectName("subtle")
        lay.addWidget(hint)
        return w

    def _apply_ad_access(self):
        """Роль «ПК» (нет права «AD»): всё видно, но менять нечего — кнопки смены пароля / блокировки / отключения
        и «Сохранить» скрыты, поля профиля только для чтения, переключатели смарт-карты и Lync заморожены в том
        состоянии, в котором их оставил администратор с правом «AD», группы — только просмотр."""
        from . import access as _access
        if _access.can_ad():
            return
        for wdg in self._ad_widgets:
            wdg.setVisible(False)
        for le in self.inputs.values():
            le.setReadOnly(True)
            le.setToolTip("Только просмотр: изменение объектов AD недоступно для вашей роли")
        for cb in (self.smartcard, self.skype):
            cb.setEnabled(False)
            cb.setToolTip("Только просмотр: состояние задаёт администратор с правом «AD»")
        self.groups_all.setToolTip("Только просмотр: добавление в группы недоступно для вашей роли")
        self.groups_list.setToolTip("Только просмотр: двойной клик покажет участников группы")

    def reset_password(self, after_unlock: bool = False):
        """Смена пароля. ``after_unlock=True`` — вызвано из сценария «сняли блокировку → задать пароль?»:
        окно открывается с «Потребовать смену» выключенной и «Снять блокировку» включённой, обе зафиксированы."""
        if self._deny("reset_password"):
            return
        if not settings.use_ssl:
            MessageBox.critical(self, "Требуется LDAPS",
                                "AD принимает пароль только по защищённому каналу. Включите use_ssl в config.ini.")
            return
        dlg = ResetPasswordDialog(self.login, self, fio=ad.get_full_fio(self.entry, self.login), after_unlock=after_unlock)
        if not dlg.exec():
            return
        pwd, must_change = dlg.password.text(), dlg.must_change.isChecked()
        unlock = getattr(dlg, "unlock", None)
        unlock = unlock.isChecked() if unlock is not None else True

        def work():
            c = self.app.get_conn()
            try:
                ad.reset_password(c, self.dn, pwd, must_change=must_change, unlock=unlock)
            finally:
                c.unbind()

        def done(_):
            db.log_action(self.app.admin_name, "reset_password", self.login,
                          "смена при входе" if must_change else "без принудительной смены")
            self._forget_password_age(must_change)   # строка «Пароль» сразу показывает новую дату / «смена при входе»
            if unlock:
                self._forget_lockout()          # галочка «Снять блокировку» — упоминание блокировки исчезает сразу
            QApplication.clipboard().setText(pwd)
            MessageBox.information(self, "Пароль изменён",
                                   f"Новый пароль для {self.login} скопирован в буфер обмена.\n"
                                   + ("Пользователь сменит его при следующем входе." if must_change else ""))

        run_in_background(self, work, done)

    def _refresh_notes_btn(self):
        n = len(db.notes_for(self.login, "user"))
        self.btn_notes.setText(f"📝 Заметки ({n})" if n else "📝 Заметки")

    def _build_groups_tab(self) -> QWidget:
        """Две панели рядом: слева группы сотрудника, справа все группы домена с фильтром; кнопки между ними."""
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 8)
        lay.setSpacing(12)
        from . import access as _access
        left = QVBoxLayout()
        self.lbl_groups = QLabel("<b>Состоит в группах</b> · двойной клик — участники")
        left.addWidget(self.lbl_groups)
        self.groups_list = QListWidget()
        self.groups_list.itemDoubleClicked.connect(
            lambda it: GroupMembersDialog(self.group_dns[it.text()], it.text(), self.app, self).exec())
        left.addWidget(self.groups_list, 1)
        rm = self.btn_group_rm = QPushButton("➖ Удалить из выбранной")
        rm.setObjectName("btnDanger")
        rm.clicked.connect(self.remove_from_group)
        rm.setVisible(_access.can("group_remove"))
        left.addWidget(rm)
        lay.addLayout(left, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Все группы домена</b>"))
        self.group_filter = QLineEdit()
        self.group_filter.setPlaceholderText("Фильтр по имени группы…")
        self.group_filter.setClearButtonEnabled(True)
        self.group_filter.textChanged.connect(self._filter_groups)
        right.addWidget(self.group_filter)
        self.groups_all = QListWidget()
        self.groups_all.itemDoubleClicked.connect(lambda _it: _access.can("group_add") and self.add_to_group())
        right.addWidget(self.groups_all, 1)
        add = self.btn_group_add = QPushButton("➕ Добавить в выбранную")
        add.setObjectName("btnSuccess")
        add.clicked.connect(self.add_to_group)
        add.setVisible(_access.can("group_add"))
        right.addWidget(add)
        lay.addLayout(right, 1)

        self.group_dns: dict[str, str] = {}
        for gdn in ad.get_ad_list_value(self.entry, "memberOf"):
            cn = ad.dn_to_cn(gdn)
            self.groups_list.addItem(cn)
            self.group_dns[cn] = gdn
        self._update_groups_title()
        self.all_groups: dict[str, str] = {}

        def load():
            c = self.app.get_conn()
            try:
                return {ad.get_ad_value(e, "cn"): e.entry_dn
                        for e in ad.paged_search(c, "(objectClass=group)", ["cn"])}
            finally:
                c.unbind()

        def done(groups):
            self.all_groups = groups
            self._filter_groups(self.group_filter.text())

        run_in_background(self, load, done, lambda m: log.warning("groups: %s", m))
        return w

    def _update_groups_title(self):
        self.lbl_groups.setText(f"<b>Состоит в группах: {self.groups_list.count()}</b> · двойной клик — участники")

    def _deny(self, action: str) -> bool:
        from . import access as _access
        if _access.can(action):
            return False
        MessageBox.warning(self, "Недостаточно прав", _access.deny_text(action))
        return True

    def _build_specs_tab(self) -> QWidget:
        """Характеристики ПК только для чтения + привязка ПК. Действия с ПК — в инспекторе главного окна."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 12, 10, 8)
        from . import access as _access
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Связанный ПК:</b>"))
        self.pc_input = QLineEdit(self.current_comp)
        self.pc_input.setPlaceholderText("Имя ПК, например WS-101")
        self.pc_input.setMaximumWidth(220)
        row.addWidget(self.pc_input)
        save_pc = QPushButton("💾 Сохранить привязку")
        save_pc.clicked.connect(self.save_pc_binding)
        save_pc.setVisible(_access.can("bind_pc"))
        row.addWidget(save_pc)
        self.lbl_ip = QLabel(safe_rich("IP:", "…"))
        self.lbl_net = QLabel(safe_rich("Сеть:", "…"))
        row.addSpacing(12)
        row.addWidget(self.lbl_ip)
        row.addWidget(self.lbl_net)
        row.addStretch()
        lay.addLayout(row)
        self.specs_tree = QTreeWidget()
        self.specs_tree.setColumnCount(2)
        self.specs_tree.setHeaderLabels(["Параметр", "Значение"])
        self.specs_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.specs_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.specs_tree, 1)
        hint = QLabel("Данные из инвентарного снимка (только чтение). RMS, диски, перезагрузка, здоровье и принтеры — "
                      "в инспекторе главного окна.")
        hint.setObjectName("subtle")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        return w

    # ------------------------------------------------------------------ состояние ПК (в фоне)
    def _load_pc_state(self):
        comp = self.current_comp
        if not comp:
            self.lbl_ip.setText(safe_rich("IP:", "ПК не привязан"))
            self.lbl_net.setText(safe_rich("Сеть:", "—"))
            return

        def work():
            ip, online = netutils.get_computer_network_info(comp, use_cache=False)  # карточка — всегда свежий статус
            specs = netutils.get_computer_specs_dict(comp)
            if "error" not in specs:
                db.replace_printers(comp, netutils.printers_from_specs(specs))   # кэш для поиска «printer:»
            return ip, online, specs

        def done(res):
            ip, online, specs = res
            if comp != self.current_comp:
                return
            self.current_ip = ip
            self.lbl_ip.setText(safe_rich("IP:", ip))
            self.lbl_net.setText(safe_rich("Сеть:", "🟢 В сети" if online else "🔴 Не в сети"))
            self._populate_specs(specs)

        run_in_background(self, work, done, lambda m: log.warning("pc state: %s", m))

    def _populate_specs(self, d: dict):
        self.specs_tree.clear()
        if not d or "error" in d:
            QTreeWidgetItem(self.specs_tree, [d.get("error", "Нет данных") if d else "Нет данных", ""])
            return
        singles = [("os", "Операционная система"), ("board", "Материнская плата"), ("bios", "BIOS"),
                   ("cpu", "Процессор"), ("gpu", "Видеоконтроллер")]
        for key, name in singles:
            if d.get(key):
                item = QTreeWidgetItem(self.specs_tree, [name, ""])
                for k, v in d[key].items():
                    QTreeWidgetItem(item, [k, v])
                item.setExpanded(True)
        for key, name in (("rams", "Модуль памяти"), ("disks", "Диск"), ("logdisks", "Логический диск"),
                          ("adapters", "Сетевой адаптер"), ("printers", "Принтер")):
            if d.get(key):
                parent = QTreeWidgetItem(self.specs_tree, [name, ""])
                for idx, sub in d[key].items():
                    si = QTreeWidgetItem(parent, [f"#{idx}", ""])
                    for k, v in sub.items():
                        QTreeWidgetItem(si, [k, v])
                parent.setExpanded(True)

    # ------------------------------------------------------------------ действия
    def copy_to_clipboard(self):
        parts = [f"👤 {ad.get_full_fio(self.entry)}"]
        office = self.inputs["physicalDeliveryOfficeName"].text().strip()
        addr = self.inputs["streetAddress"].text().strip()
        loc = ", ".join(x for x in (addr, f"каб. {office}" if office else "") if x)
        if loc:
            parts.append(f"📍 {loc}")
        phones = [f"Тел. {self.inputs['telephoneNumber'].text()}" if self.inputs["telephoneNumber"].text() else "",
                  f"IP тел. {self.inputs['ipPhone'].text()}" if self.inputs["ipPhone"].text() else ""]
        if any(phones):
            parts.append("📞 " + " | ".join(p for p in phones if p))
        if self.current_comp:
            pc = [self.current_comp] + ([self.current_ip] if self.current_ip != "Не найден" else [])
            parts.append("💻 ПК: " + " | ".join(pc))
        QApplication.clipboard().setText("\n".join(parts))
        MessageBox.information(self, "Скопировано", "Карточка скопирована в буфер обмена.")

    def save_changes(self):
        if self._deny("modify_user"):
            return
        changes: dict = {}
        for attr, edit in self.inputs.items():
            if attr == "sAMAccountName":
                continue
            new, old = edit.text().strip(), ad.get_ad_value(self.entry, attr).strip()
            if new != old:
                changes[attr] = [(ad.MODIFY_REPLACE, [new] if new else [])]
        sc = self.smartcard.isChecked()
        if sc != bool(self.original_uac & SMARTCARD_REQUIRED_FLAG):
            uac = self.original_uac | SMARTCARD_REQUIRED_FLAG if sc else self.original_uac & ~SMARTCARD_REQUIRED_FLAG
            changes["userAccountControl"] = [(ad.MODIFY_REPLACE, [uac])]
        if self.skype.isChecked() != self.original_skype:
            changes["msRTCSIP-UserEnabled"] = [(ad.MODIFY_REPLACE, ["TRUE" if self.skype.isChecked() else "FALSE"])]
        if not changes:
            MessageBox.information(self, "Без изменений", "Ничего не изменено.")
            return

        def work():
            c = self.app.get_conn()
            try:
                c.modify(self.dn, changes)
            finally:
                c.unbind()

        def done(_):
            db.log_action(self.app.admin_name, "modify_user", self.login, ", ".join(changes))
            MessageBox.information(self, "Успех", "Данные обновлены в AD.")
            self.accept()

        run_in_background(self, work, done)

    def toggle_disabled(self):
        disabled = bool(self.original_uac & ACCOUNT_DISABLE_FLAG)
        if self._deny("enable_user" if disabled else "disable_user"):
            return
        verb = "Включить" if disabled else "Отключить"
        if not MessageBox.question(self, "Подтверждение", f"{verb} учётную запись {self.login}?"):
            return

        def work():
            c = self.app.get_conn()
            try:
                ad.set_account_disabled(c, self.dn, self.original_uac, not disabled)
            finally:
                c.unbind()
            if not disabled and settings.pst_backup_base:
                return self._export_pst()
            return ""

        def done(msg):
            db.log_action(self.app.admin_name, "enable_user" if disabled else "disable_user", self.login)
            self.original_uac = (self.original_uac & ~ACCOUNT_DISABLE_FLAG) if disabled else (self.original_uac | ACCOUNT_DISABLE_FLAG)
            try:
                attrs = getattr(self.entry, "_a", None)
                if isinstance(attrs, dict) and "userAccountControl" in attrs:
                    attrs["userAccountControl"].value = self.original_uac
                    attrs["userAccountControl"].values = [self.original_uac]
            except Exception as exc:  # noqa: BLE001
                log.debug("uac local update: %s", exc)
            now_disabled = not disabled
            self.btn_toggle.setText("✅ Включить учётную запись" if now_disabled else "⛔ Отключить учётную запись")
            self.btn_toggle.setObjectName("btnSuccess" if now_disabled else "btnDanger")
            self.btn_toggle.style().unpolish(self.btn_toggle)
            self.btn_toggle.style().polish(self.btn_toggle)
            self._entry_changed = True
            self.refresh_state()
            MessageBox.information(self, "Готово", f"Учётная запись {'включена' if disabled else 'отключена'}."
                                   + (f"\n{msg}" if msg else ""))

        run_in_background(self, work, done)

    def _export_pst(self) -> str:
        """Запрос экспорта ящика через Exchange Management Shell. Возвращает текст результата."""
        if not netutils.is_valid_hostname(self.login.replace(".", "-").replace("_", "-")):
            return "PST: логин содержит недопустимые символы — экспорт пропущен"
        now = datetime.now()
        folder = os.path.join(settings.pst_backup_base, f"{now:%Y-%m}", self.login)
        os.makedirs(folder, exist_ok=True)
        cmd = (f"New-MailboxExportRequest -Mailbox '{self.login}' "
               f"-FilePath '{os.path.join(folder, self.login)}.pst'")
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                 text=True, timeout=180, creationflags=CREATE_NO_WINDOW)
        except (subprocess.SubprocessError, OSError) as exc:
            return f"PST: не удалось запустить экспорт ({exc})"
        if res.returncode != 0 or res.stderr.strip():
            return f"PST: ошибка экспорта:\n{res.stderr.strip()[:500] or res.stdout.strip()[:500]}"
        return f"PST: запрос экспорта создан → {folder}"

    def unlock(self):
        if self._deny("unlock"):
            return

        def work():
            c = self.app.get_conn()
            try:
                ad.unlock_account(c, self.dn)
            finally:
                c.unbind()

        def done(_):
            db.log_action(self.app.admin_name, "unlock", self.login)
            self._forget_lockout()
            # блокировка почти всегда — забытый пароль: сразу предлагаем задать новый (3.2.9)
            if MessageBox.question(self, "Блокировка снята",
                                   f"Блокировка с {self.login} снята.\n\nЗадать пользователю новый пароль?"):
                self.reset_password(after_unlock=True)

        run_in_background(self, work, done)

    def _forget_lockout(self):
        """Локально отражаем lockoutTime = 0: бейдж, вкладка «Учётная запись», кнопка и главное окно
        обновляются сразу, не дожидаясь нового поиска в AD."""
        ad.set_local_attr(self.entry, "lockoutTime", [])
        ad.set_local_attr(self.entry, "badPwdCount", [0])
        self._entry_changed = True
        self.refresh_state()

    def _forget_password_age(self, must_change: bool):
        """Локально отражаем смену пароля: pwdLastSet = сейчас (или 0 — «смена при следующем входе»),
        счётчик неудачных входов — 0. Иначе вкладка «Учётная запись» и инспектор до нового поиска показывали
        бы старую дату пароля."""
        ad.set_local_attr(self.entry, "pwdLastSet", [0] if must_change else [datetime.now(timezone.utc)])
        ad.set_local_attr(self.entry, "badPwdCount", [0])
        self._entry_changed = True
        self.refresh_state()

    def refresh_state(self):
        """Пересчитать всё, что зависит от состояния учётки: бейдж в шапке, кнопки, строки вкладки «Учётная запись»."""
        pal = app_palette()
        text, kind = ad.account_badge(self.entry)
        fg, bg, bd = pal.badge(kind)
        self.badge_state.setText(text)
        self.badge_state.setStyleSheet(f"background-color: {bg}; color: {pal.text}; border: 1px solid {bd}; "
                                       "font-weight: bold; font-size: 9pt; border-radius: 4px; padding: 4px 10px;")
        st = ad.account_status(self.entry, settings.max_password_age_days)
        self.btn_unlock.setVisible(bool(st["locked"]) and self.btn_toggle.isVisible())
        reason = ad.account_inactive_reason(self.entry)
        vals = {"Состояние": ((("Не активна — " + reason) if reason else "Активна — пользователь работает"), bool(reason)),
                "Пароль": (st["pwd_text"], bool(st["pwd_warn"]) or (st["pwd_days_left"] is not None and st["pwd_days_left"] <= 0)),
                "Блокировка": ((st["locked_text"] if st["locked"] else "нет"), bool(st["locked"])),
                "Срок действия": (st["expires_text"] or "бессрочно", bool(st["expired"])),
                "Неудачных входов": (str(st["bad_pwd"] or 0), bool(st["bad_pwd"]))}
        for name, (value, warn) in vals.items():
            lbl = self.account_vals.get(name)
            if lbl is not None:
                lbl.setText(value)
                lbl.setStyleSheet(f"color: {pal.danger[0]}; font-weight: bold;" if warn else "")
        if hasattr(self, "lbl_account"):
            self.lbl_account.setText("\n".join(f"{n}: {v}" for n, (v, _) in vals.items()))
        if hasattr(self.app, "update_account_state_in_ui"):
            self.app.update_account_state_in_ui(self.login, self.entry)

    def _filter_groups(self, text: str):
        q = text.strip().lower()
        self.groups_all.clear()
        self.groups_all.addItems(sorted(cn for cn in self.all_groups if q in cn.lower() and cn not in self.group_dns))

    def _modify_group(self, cn: str, op, on_ok):
        gdn = self.all_groups.get(cn) or self.group_dns.get(cn)
        if not gdn or self._deny("group_add" if op == ad.MODIFY_ADD else "group_remove"):
            return

        def work():
            c = self.app.get_conn()
            try:
                c.modify(gdn, {"member": [(op, [self.dn])]})
            finally:
                c.unbind()

        run_in_background(self, work, lambda _: (on_ok(gdn), db.log_action(
            self.app.admin_name, "group_add" if op == ad.MODIFY_ADD else "group_remove", self.login, cn)))

    def add_to_group(self):
        if self._deny("group_add"):
            return
        items = self.groups_all.selectedItems()
        if not items:
            return
        cn = items[0].text()

        def ok(gdn):
            self.groups_list.addItem(cn)
            self.group_dns[cn] = gdn
            self._filter_groups(self.group_filter.text())
            self._update_groups_title()

        self._modify_group(cn, ad.MODIFY_ADD, ok)

    def remove_from_group(self):
        if self._deny("group_remove"):
            return
        items = self.groups_list.selectedItems()
        if not items or not MessageBox.question(self, "Подтверждение", f"Удалить из группы {items[0].text()}?"):
            return
        cn = items[0].text()

        def ok(_):
            self.groups_list.takeItem(self.groups_list.row(items[0]))
            self.group_dns.pop(cn, None)
            self._filter_groups(self.group_filter.text())
            self._update_groups_title()

        self._modify_group(cn, ad.MODIFY_DELETE, ok)

    def save_pc_binding(self):
        if self._deny("bind_pc"):
            return
        comp = db.clean_computer_name(self.pc_input.text())
        db.save_computer_for_login(self.login, comp)
        self.current_comp, self.current_ip = comp, "Не найден"
        db.log_action(self.app.admin_name, "bind_pc", self.login, comp)
        self._load_pc_state()
        MessageBox.information(self, "Успех", "Привязка сохранена.")


# ============================================================================ смена пароля
class ResetPasswordDialog(FramelessDialog):
    """Единое окно «Смена пароля»: пароль по политике крупно на экране (можно ввести свой), длина 10/12/16,
    «Потребовать смену при входе», снятие блокировки, «Копировать карточку» для сотрудника.
    Само в AD не пишет — вызывающая карточка после ``exec()`` читает ``password``/``must_change``/``unlock``."""

    LENGTHS = (10, 12, 16)

    def __init__(self, login: str, parent=None, fio: str = "", after_unlock: bool = False):
        super().__init__(f"🔑 Смена пароля: {fio or login}", parent, (600, 380))
        self.login = login
        self.after_unlock = after_unlock
        pal = app_palette()
        who = QLabel(f"Логин: <b>{settings.domain_netbios + chr(92) if settings.domain_netbios else ''}{login}</b>"
                     + (f" · {fio}" if fio else ""))
        who.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(who)

        self.password = QLineEdit(ad.generate_secure_password(12))
        self.password.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.password.setToolTip("Можно ввести свой пароль — минимум 8 символов")
        self.password.setStyleSheet(f"font-size: 20pt; font-weight: bold; letter-spacing: 3px; padding: 10px; "
                                    f"font-family: 'Consolas', 'DejaVu Sans Mono', monospace; color: {pal.title_accent};")
        self.password.textChanged.connect(self._check)
        self.body.addWidget(self.password)

        row = QHBoxLayout()
        row.addWidget(QLabel("Длина:"))
        self.len_btns: dict[int, QPushButton] = {}
        for n in self.LENGTHS:
            b = QPushButton(str(n))
            b.setCheckable(True)
            b.setObjectName("chipBtn")
            b.setChecked(n == 12)
            b.clicked.connect(lambda _c, k=n: self.generate(k))
            self.len_btns[n] = b
            row.addWidget(b)
        self.btn_gen = QPushButton("🎲 Другой")
        self.btn_gen.setToolTip("Сгенерировать заново")
        self.btn_gen.clicked.connect(lambda: self.generate())
        row.addWidget(self.btn_gen)
        row.addStretch()
        self.lbl_check = QLabel("")
        row.addWidget(self.lbl_check)
        self.body.addLayout(row)

        self.must_change = QCheckBox("Потребовать смену пароля при следующем входе")
        self.must_change.setChecked(True)
        self.unlock = QCheckBox("Снять блокировку (lockout), если была")
        self.unlock.setChecked(True)
        if after_unlock:
            # пришли из «снять блокировку → задать пароль»: смену при входе не требуем (человек и так только что
            # звонил), блокировку снимаем в любом случае — оба переключателя зафиксированы
            self.must_change.setChecked(False)
            self.unlock.setChecked(True)
            for cb in (self.must_change, self.unlock):
                cb.setEnabled(False)
            self.must_change.setToolTip("Задано сценарием «после снятия блокировки»: смена при входе не требуется")
            self.unlock.setToolTip("Задано сценарием «после снятия блокировки»: блокировка снимается")
        self.body.addWidget(self.must_change)
        self.body.addWidget(self.unlock)
        hint = QLabel("Пароль показывается только здесь и нигде не сохраняется. «Копировать карточку» — текст "
                      "с логином и паролем для передачи сотруднику; после смены пароль также попадёт в буфер обмена.")
        hint.setWordWrap(True)
        hint.setObjectName("subtle")
        self.body.addWidget(hint)
        self.body.addStretch()

        btns = QHBoxLayout()
        self.btn_card = QPushButton("📋 Копировать карточку")
        self.btn_card.clicked.connect(lambda: QApplication.clipboard().setText(self._card()))
        btns.addWidget(self.btn_card)
        btns.addStretch()
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.btn_ok = QPushButton("🔑 Сменить пароль")
        self.btn_ok.setObjectName("btnDanger")
        self.btn_ok.setDefault(True)
        self.btn_ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(self.btn_ok)
        self.body.addLayout(btns)
        self._check()

    def generate(self, length: int | None = None):
        if length is None:
            length = next((n for n, b in self.len_btns.items() if b.isChecked()), 12)
        for n, b in self.len_btns.items():
            b.setChecked(n == length)
        self.password.setText(ad.generate_secure_password(length))

    def _card(self) -> str:
        from .extras import password_card_text
        return password_card_text(self.login, self.password.text(), self.must_change.isChecked(), settings.domain_netbios)

    def _check(self, *_):
        p = self.password.text()
        pal = app_palette()
        ok = len(p) >= 8
        strong = ok and any(c.isdigit() for c in p) and any(c.isalpha() for c in p) and any(not c.isalnum() for c in p)
        self.lbl_check.setText("✔ надёжный" if strong else ("• простой" if ok else "✖ короче 8 символов"))
        self.lbl_check.setStyleSheet(f"color: {pal.success[0] if strong else pal.warning[0] if ok else pal.danger[0]}; font-weight: bold;")
        self.btn_ok.setEnabled(ok)

    def _accept(self):
        if len(self.password.text()) < 8:
            MessageBox.warning(self, "Пароль", "Минимум 8 символов.")
            return
        self.accept()


# ============================================================================ журнал действий
class AuditLogDialog(FramelessDialog):
    """Просмотр audit_log: фильтр по администратору, действию, тексту и периоду; экспорт CSV."""

    PERIODS = (("Сегодня", 0), ("7 дней", 7), ("30 дней", 30), ("Всё время", None))

    def __init__(self, app, parent=None):
        super().__init__("📜 Журнал действий администраторов", parent, (960, 600))
        self.app = app
        flt = QHBoxLayout()
        self.admin = QComboBox()
        self.admin.addItem("Все администраторы", "")
        for a in db.audit_admins():
            self.admin.addItem(a, a)
        self.action = QComboBox()
        self.action.addItem("Все действия", "")
        for code, label in db.ACTION_LABELS.items():
            self.action.addItem(label, code)
        self.period = QComboBox()
        for label, days in self.PERIODS:
            self.period.addItem(label, days)
        self.period.setCurrentIndex(2)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Поиск по логину / ПК / деталям…")
        for wdg in (self.admin, self.action, self.period):
            wdg.currentIndexChanged.connect(self.reload)
            flt.addWidget(wdg)
        self.text.textChanged.connect(self.reload)
        flt.addWidget(self.text, 1)
        self.btn_csv = QPushButton("📤 CSV")
        self.btn_csv.clicked.connect(self.export_csv)
        flt.addWidget(self.btn_csv)
        self.body.addLayout(flt)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Время", "Администратор", "Действие", "Объект", "Детали"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        for i, wd in enumerate((160, 150, 190, 140)):
            self.table.setColumnWidth(i, wd)
        self.body.addWidget(self.table, 1)
        self.status = QLabel("")
        self.body.addWidget(self.status)
        self.rows: list[tuple] = []
        self.reload()

    def _since(self) -> str | None:
        days = self.period.currentData()
        if days is None:
            return None
        start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if days:
            from datetime import timedelta
            start -= timedelta(days=days)
        return start.strftime("%Y-%m-%d %H:%M:%S")

    def reload(self):
        try:
            self.rows = db.audit_entries(limit=1000, admin=self.admin.currentData() or "",
                                         action=self.action.currentData() or "",
                                         text=self.text.text().strip(), since=self._since())
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"⚠️ {exc}")
            return
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for r, (ts, admin, action, target, details) in enumerate(self.rows):
            for c, val in enumerate((ts, admin, db.ACTION_LABELS.get(action, action), target, details or "")):
                self.table.setItem(r, c, QTableWidgetItem(str(val or "")))
        self.table.setSortingEnabled(True)
        fit_columns(self.table, max_width=380)
        self.status.setText(f"Записей: {len(self.rows)}" + (" (показаны первые 1000)" if len(self.rows) >= 1000 else ""))

    def export_csv(self):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт журнала", f"audit_{datetime.now():%Y-%m-%d}.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        self.write_csv(path)
        MessageBox.information(self, "Экспорт", f"Сохранено: {path}")

    def write_csv(self, path: str) -> None:
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh, delimiter=";")
            wr.writerow(["Время", "Администратор", "Действие", "Объект", "Детали"])
            for ts, admin, action, target, details in self.rows:
                wr.writerow([ts, admin, db.ACTION_LABELS.get(action, action), target, details or ""])


# ============================================================================ принтеры парка
class PrintersDialog(FramelessDialog):
    """Сводка принтеров по парку (из кэша pc_printers): тип, IP, число ПК; двойной клик — поиск «кто подключён»."""

    KIND_LABELS = {"network": "🌐 сетевой", "shared": "🔗 общий", "usb": "🔌 USB", "local": "🖨️ локальный"}

    def __init__(self, app, parent=None):
        super().__init__("🖨️ Принтеры парка", parent, (900, 560))
        self.app = app
        top = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Фильтр: модель / IP / имя ПК…")
        self.text.textChanged.connect(self.reload)
        top.addWidget(self.text, 1)
        self.kind = QComboBox()
        self.kind.addItem("Все типы", "")
        for code, label in self.KIND_LABELS.items():
            self.kind.addItem(label, code)
        self.kind.currentIndexChanged.connect(self.reload)
        top.addWidget(self.kind)
        self.btn_csv = QPushButton("📤 CSV")
        self.btn_csv.clicked.connect(self.export_csv)
        top.addWidget(self.btn_csv)
        self.body.addLayout(top)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Принтер", "Тип", "IP", "ПК", "В сети", "Компьютеры"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for i, wd in enumerate((260, 110, 120, 50, 60)):
            self.table.setColumnWidth(i, wd)
        self.table.itemDoubleClicked.connect(lambda _: self.open_owners())
        self.body.addWidget(self.table, 1)
        hint = QLabel("Данные — из инвентарных CSV (обновляются сканером и при открытии карточки). "
                      "Виртуальные принтеры (PDF, XPS, OneNote, факс) исключены. Двойной клик — кто подключён.")
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        self.status = QLabel("")
        self.body.addWidget(self.status)
        self.rows: list[dict] = []
        self.reload()

    def reload(self):
        q = self.text.text().strip().casefold()
        kind = self.kind.currentData() or ""
        try:
            data = db.printer_summary()
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"⚠️ {exc}")
            return
        self.rows = [r for r in data if (not kind or r["kind"] == kind)
                     and (not q or q in f"{r['name']} {r['ip']} {r['computers']}".casefold())]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = (r["name"], self.KIND_LABELS.get(r["kind"], r["kind"]), r["ip"], r["pcs"], r["online"], r["computers"])
            for c, v in enumerate(vals):
                it = QTableWidgetItem()
                if isinstance(v, int):
                    it.setData(Qt.ItemDataRole.DisplayRole, v)
                else:
                    it.setText(str(v))
                self.table.setItem(i, c, it)
        self.table.setSortingEnabled(True)
        fit_columns(self.table, max_width=420)
        total_pcs = sum(r["pcs"] for r in self.rows)
        self.status.setText(f"Принтеров: {len(self.rows)} · подключений: {total_pcs}")

    def selected(self) -> dict | None:
        r = self.table.currentRow()
        if r < 0:
            return None
        name = self.table.item(r, 0).text()
        ip = self.table.item(r, 2).text()
        return next((x for x in self.rows if x["name"] == name and x["ip"] == ip), None)

    def open_owners(self):
        r = self.selected()
        if not r or not hasattr(self.app, "search_printer"):
            return
        self.app.search_printer({"name": r["name"], "ip": r["ip"]})
        self.accept()

    def export_csv(self):
        if not self.rows:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Экспорт принтеров", f"printers_{datetime.now():%Y-%m-%d}.csv",
                                              "CSV (*.csv)")
        if not path:
            return
        self.write_csv(path)
        MessageBox.information(self, "Экспорт", f"Сохранено: {path}")

    def write_csv(self, path: str) -> None:
        import csv
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.writer(fh, delimiter=";")
            wr.writerow(["Принтер", "Тип", "IP", "ПК", "В сети", "Компьютеры", "Обновлено"])
            for r in self.rows:
                wr.writerow([r["name"], self.KIND_LABELS.get(r["kind"], r["kind"]).split(" ", 1)[-1], r["ip"],
                             r["pcs"], r["online"], r["computers"], r["updated"]])


# ============================================================================ участники группы
class GroupMembersDialog(FramelessDialog):
    def __init__(self, group_dn: str, group_name: str, app, parent=None):
        super().__init__(f"👥 Участники группы: {group_name}", parent, (680, 480))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Логин", "Имя", "Почта"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)          # как во всех таблицах ADK — без белой полосы номеров
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.body.addWidget(self.table)
        self.status = QLabel("Загрузка…")
        self.body.addWidget(self.status)

        def load():
            c = app.get_conn()
            try:
                flt = f"(&(objectClass=user)(memberOf={ad.escape_filter_chars(group_dn)}))"
                return [(ad.get_ad_value(e, "sAMAccountName"), ad.get_ad_value(e, "displayName"),
                         ad.get_ad_value(e, "mail"))
                        for e in ad.paged_search(c, flt, ["sAMAccountName", "displayName", "mail"])]
            finally:
                c.unbind()

        def done(rows):
            self.table.setRowCount(len(rows))
            for r, row in enumerate(sorted(rows, key=lambda x: x[1])):
                for col, val in enumerate(row):
                    self.table.setItem(r, col, QTableWidgetItem(val))
            fit_columns(self.table)
            self.status.setText(f"Участников: {len(rows)}")

        run_in_background(self, load, done, lambda m: self.status.setText(f"Ошибка: {m}"))


# ============================================================================ регистрация
class RegisterUserDialog(FramelessDialog):
    """Регистрация пользователя: слева — данные, справа — предпросмотр учётки и чек-лист готовности."""

    def __init__(self, app, parent=None):
        super().__init__("➕ Новый пользователь AD", parent, (1060, 700))
        self.app = app
        pal = app_palette()
        self.body.setContentsMargins(4, 0, 4, 0)
        from . import templates as _templates
        self._templates_mod = _templates
        self.templates: dict[str, dict] = _templates.load()
        self.template_groups: list[str] = []

        cols = QHBoxLayout()
        cols.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(8)

        # --- 1. Сотрудник
        box1 = self._section("1", "Сотрудник")
        f1 = QFormLayout()
        f1.setHorizontalSpacing(12)
        f1.setVerticalSpacing(6)
        self.surname, self.name, self.patronymic = QLineEdit(), QLineEdit(), QLineEdit()
        for le, ph in ((self.surname, "Иванов"), (self.name, "Иван"), (self.patronymic, "Петрович (необязательно)")):
            le.setPlaceholderText(ph)
            le.textChanged.connect(self._refresh)
        fio_row = QHBoxLayout()
        fio_row.addWidget(self.surname, 3)
        fio_row.addWidget(self.name, 2)
        fio_row.addWidget(self.patronymic, 3)
        f1.addRow("ФИО:", fio_row)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText("2554567")
        f1.addRow("Телефон:", self.phone)
        box1.layout().addLayout(f1)
        left.addWidget(box1)

        # --- 2. Учётная запись
        box2 = self._section("2", "Учётная запись")
        f2 = QFormLayout()
        f2.setHorizontalSpacing(12)
        f2.setVerticalSpacing(6)
        lrow = QHBoxLayout()
        self.login = QLineEdit()
        self.login.setMaxLength(20)
        self.login.setPlaceholderText("ivanov_i")
        self.login.textChanged.connect(self._refresh)
        gen = QPushButton("✨ Сгенерировать")
        gen.setToolTip("Логин из фамилии и имени (транслит) + надёжный пароль")
        gen.clicked.connect(self.generate)
        lrow.addWidget(self.login, 1)
        lrow.addWidget(gen)
        f2.addRow("Логин:", lrow)
        prow = QHBoxLayout()
        self.password = QLineEdit()
        self.password.setPlaceholderText("минимум 8 символов")
        self.password.textChanged.connect(self._refresh)
        self.btn_eye = QPushButton("Скрыть")
        self.btn_eye.setFixedWidth(96)
        self.btn_eye.setCheckable(True)
        self.btn_eye.setToolTip("Показать/скрыть пароль")
        self.btn_eye.toggled.connect(lambda on: (self.password.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password), self.btn_eye.setText("Скрыть" if on else "Показать")))
        self.btn_eye.setChecked(True)
        prow.addWidget(self.password, 1)
        prow.addWidget(self.btn_eye)
        f2.addRow("Пароль:", prow)
        self.ou = QLineEdit(settings.users_ou)
        self.ou.setPlaceholderText("OU=Employees,DC=example,DC=local")
        self.ou.textChanged.connect(self._refresh)
        f2.addRow("Контейнер (OU):", self.ou)
        box2.layout().addLayout(f2)
        left.addWidget(box2)

        # --- 3. Должность и место
        box3 = self._section("3", "Должность и место")
        f3 = QGridLayout()
        f3.setHorizontalSpacing(12)
        f3.setVerticalSpacing(6)
        self.combos: dict[str, QComboBox] = {}
        # две колонки — окно ниже, все поля на виду
        for i, (attr, label) in enumerate((("title", "Должность"), ("department", "Отдел"), ("company", "Организация"),
                                           ("streetAddress", "Адрес"), ("physicalDeliveryOfficeName", "Кабинет"))):
            cb = QComboBox()
            cb.setEditable(True)
            cb.lineEdit().setPlaceholderText("подсказки из AD…")
            cb.editTextChanged.connect(self._refresh)
            r, c = divmod(i, 2)
            f3.addWidget(QLabel(label + ":"), r, c * 2)
            f3.addWidget(cb, r, c * 2 + 1)
            self.combos[attr] = cb
        f3.setColumnStretch(1, 1)
        f3.setColumnStretch(3, 1)
        box3.layout().addLayout(f3)
        left.addWidget(box3)

        # --- 4. Шаблон / образец
        box4 = self._section("4", "Шаблон или образец")
        trow = QHBoxLayout()
        self.cb_template = QComboBox()
        self.cb_template.addItem("— без шаблона —", "")
        for name in sorted(self.templates, key=str.casefold):
            self.cb_template.addItem(name, name)
        self.cb_template.currentIndexChanged.connect(self.apply_template)
        trow.addWidget(self.cb_template, 1)
        b_save = QPushButton("Сохранить")
        b_save.setToolTip("Сохранить текущие поля (и группы образца) как шаблон")
        b_save.clicked.connect(self.save_template)
        b_del = QPushButton("Удалить")
        b_del.setToolTip("Удалить выбранный шаблон")
        b_del.clicked.connect(self.delete_template)
        tpl = QPushButton("👥 Как у сотрудника…")
        tpl.setToolTip("Скопировать должность, отдел, группы и OU у существующего сотрудника")
        tpl.clicked.connect(self.copy_template)
        trow.addWidget(b_save)
        trow.addWidget(b_del)
        trow.addWidget(tpl)
        box4.layout().addLayout(trow)
        left.addWidget(box4)
        left.addStretch()
        cols.addLayout(left, 3)

        # --- правая колонка: предпросмотр + чек-лист
        right = QVBoxLayout()
        right.setSpacing(10)
        card = QFrame()
        card.setObjectName("dashCard")
        cl = QVBoxLayout(card)
        self.pv_fio = QLabel("👤 Новый сотрудник")
        self.pv_fio.setStyleSheet("font-size: 14pt; font-weight: bold;")
        self.pv_fio.setWordWrap(True)
        self.pv_title = QLabel("—")
        self.pv_title.setStyleSheet(f"color: {pal.subtext};")
        self.pv_title.setWordWrap(True)
        cl.addWidget(self.pv_fio)
        cl.addWidget(self.pv_title)
        self.pv_rows: dict[str, QLabel] = {}
        pf = QFormLayout()
        pf.setHorizontalSpacing(10)
        for key, label in (("login", "Логин"), ("mail", "Почта"), ("ou", "OU"), ("groups", "Группы")):
            v = QLabel("—")
            v.setWordWrap(True)
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            pf.addRow(f"<b>{label}:</b>", v)
            self.pv_rows[key] = v
        cl.addLayout(pf)
        right.addWidget(card)

        chk = QFrame()
        chk.setObjectName("dashCard")
        kl = QVBoxLayout(chk)
        kl.addWidget(QLabel("<b>Готовность</b>"))
        self.checks: dict[str, QLabel] = {}
        self._check_text: dict[str, str] = {}
        for key, text in (("fio", "Фамилия и имя"), ("login", "Логин ≤ 20 символов, латиница"),
                          ("pwd", "Пароль ≥ 8 символов, буквы и цифры"), ("ou", "Контейнер OU"),
                          ("ssl", "LDAPS включён (use_ssl)")):
            lb = QLabel(f"○ {text}")
            self.checks[key] = lb
            self._check_text[key] = text
            kl.addWidget(lb)
        right.addWidget(chk)
        right.addStretch()
        self.btn_create = QPushButton("➕ Создать учётную запись")
        self.btn_create.setObjectName("btnPrimary")
        self.btn_create.setMinimumHeight(40)
        self.btn_create.clicked.connect(self.create)
        right.addWidget(self.btn_create)
        note = QLabel("Создаётся отключённой → пароль → включается. При сбое учётка удаляется.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {pal.subtext}; font-size: 9pt;")
        right.addWidget(note)
        cols.addLayout(right, 2)
        self.body.addLayout(cols)
        self._refresh()
        self._load_suggestions()

    @staticmethod
    def _section(num: str, title: str) -> QFrame:
        fr = QFrame()
        fr.setObjectName("dashCard")
        fr.setStyleSheet("#dashCard { padding: 2px; }")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(6)
        head = QLabel(f"<span style='color:{app_palette().title_accent}; font-weight:800;'>{num}</span>&nbsp;&nbsp;<b>{title}</b>")
        lay.addWidget(head)
        return fr

    # --- живой предпросмотр и чек-лист
    def _refresh(self, *_):
        pal = app_palette()
        s, n, p = self.surname.text().strip(), self.name.text().strip(), self.patronymic.text().strip()
        login, pwd = self.login.text().strip(), self.password.text()
        fio = " ".join(x for x in (s, n, p) if x)
        self.pv_fio.setText(f"👤 {fio}" if fio else "👤 Новый сотрудник")
        title, dept = self.combos["title"].currentText().strip(), self.combos["department"].currentText().strip()
        self.pv_title.setText(" · ".join(x for x in (title, dept, self.combos["company"].currentText().strip()) if x) or "—")
        self.pv_rows["login"].setText(f"{settings.domain_netbios}\\{login}" if login and settings.domain_netbios else (login or "—"))
        self.pv_rows["mail"].setText(f"{login}@{settings.mail_domain}" if login and getattr(settings, "mail_domain", "") else "—")
        self.pv_rows["ou"].setText(self.ou.text().strip() or "—")
        names = [ad.dn_to_cn(g) for g in self.template_groups]
        self.pv_rows["groups"].setText(", ".join(names) if names else "—")
        ok_fg, bad_fg = pal.success[0], pal.subtext
        state = {
            "fio": bool(s and n),
            "login": bool(login) and len(login) <= 20 and login.isascii(),
            "pwd": len(pwd) >= 8 and any(c.isdigit() for c in pwd) and any(c.isalpha() for c in pwd),
            "ou": bool(self.ou.text().strip()),
            "ssl": bool(settings.use_ssl),
        }
        for key, lb in self.checks.items():
            text = self._check_text[key]                      # подпись хранится отдельно: text() может быть HTML с иконкой
            lb.setText(("✅ " if state[key] else "○ ") + text)
            lb.setStyleSheet(f"color: {ok_fg if state[key] else bad_fg};")
        self.btn_create.setEnabled(all(state.values()))
        self.btn_create.setToolTip("" if all(state.values()) else "Заполните пункты чек-листа")

    # --- шаблоны (3.1)
    def apply_template(self, *_):
        name = self.cb_template.currentData()
        t = self.templates.get(name or "")
        if not t:
            self.template_groups = []
            self._refresh()
            return
        for a, v in t["attrs"].items():
            if a in self.combos:
                self.combos[a].setEditText(v)
        if t["ou"]:
            self.ou.setText(t["ou"])
        self.template_groups = list(t["groups"])
        self._refresh()

    def save_template(self):
        name, ok = InputDialog.get_text(self, "Шаблон", "Название шаблона (например, «Бухгалтер»):")
        if not ok or not name.strip():
            return
        t = self._templates_mod.normalize({"attrs": {a: cb.currentText() for a, cb in self.combos.items()},
                                           "groups": self.template_groups, "ou": self.ou.text()})
        self.templates[name.strip()] = t
        self._templates_mod.save(self.templates)
        if self.cb_template.findData(name.strip()) < 0:
            self.cb_template.addItem(name.strip(), name.strip())
        self.cb_template.setCurrentIndex(self.cb_template.findData(name.strip()))

    def delete_template(self):
        name = self.cb_template.currentData()
        if not name or not MessageBox.question(self, "Шаблон", f"Удалить шаблон «{name}»?"):
            return
        self.templates.pop(name, None)
        self._templates_mod.save(self.templates)
        self.cb_template.removeItem(self.cb_template.currentIndex())

    def _load_suggestions(self):
        attrs = list(self.combos)

        def load():
            c = self.app.get_conn()
            try:
                vals: dict[str, set[str]] = {a: set() for a in attrs}
                for e in ad.paged_search(c, "(&(objectCategory=person)(objectClass=user))", attrs):
                    for a in attrs:
                        v = ad.get_ad_value(e, a).strip()
                        if v:
                            vals[a].add(v)
                return {a: sorted(s) for a, s in vals.items()}
            finally:
                c.unbind()

        def done(vals):
            for a, items in vals.items():
                cur = self.combos[a].currentText()
                self.combos[a].clear()
                self.combos[a].addItems(items)
                self.combos[a].setEditText(cur)

        run_in_background(self, load, done, lambda m: log.warning("suggestions: %s", m))

    def copy_template(self):
        login, ok = InputDialog.get_text(self, "Образец", "Логин сотрудника-образца:")
        if not ok or not login.strip():
            return
        attrs = list(self.combos)

        def load():
            c = self.app.get_conn()
            try:
                e = ad.paged_search(c, f"(sAMAccountName={ad.escape_filter_chars(login.strip())})", attrs + ["memberOf"], limit=1)
                if not e:
                    return None
                return {"attrs": {a: ad.get_ad_value(e[0], a) for a in attrs},
                        "tpl": self._templates_mod.from_entry(e[0], ad.get_ad_value, ad.get_ad_list_value)}
            finally:
                c.unbind()

        def done(vals):
            if not vals:
                MessageBox.warning(self, "Внимание", "Пользователь не найден.")
                return
            for a, v in vals["attrs"].items():
                self.combos[a].setEditText(v)
            self.template_groups = list(vals["tpl"]["groups"])
            if vals["tpl"]["ou"]:
                self.ou.setText(vals["tpl"]["ou"])
            self._refresh()

        run_in_background(self, load, done)

    def generate(self):
        s, n = self.surname.text().strip(), self.name.text().strip()
        if not s or not n:
            MessageBox.warning(self, "Внимание", "Заполните фамилию и имя.")
            return
        try:
            self.login.setText(ad.sanitize_sam_account_name(f"{ad.transliterate(s)}_{ad.transliterate(n)[0]}"))
        except ValueError as exc:
            MessageBox.warning(self, "Внимание", str(exc))
            return
        self.password.setText(ad.generate_secure_password())

    def create(self):
        from . import access as _access
        if not _access.can("create_user"):
            MessageBox.warning(self, "Недостаточно прав", _access.deny_text("create_user"))
            return
        s, n, p = self.surname.text().strip(), self.name.text().strip(), self.patronymic.text().strip()
        login, pwd = self.login.text().strip(), self.password.text()
        if not (s and n and login and pwd):
            MessageBox.warning(self, "Внимание", "Заполните фамилию, имя, логин и пароль.")
            return
        if not settings.use_ssl:
            MessageBox.critical(self, "Требуется LDAPS",
                                "AD принимает пароль только по защищённому каналу. Включите use_ssl в config.ini.")
            return
        extra = {a: cb.currentText() for a, cb in self.combos.items()}
        if self.phone.text().strip():
            extra["telephoneNumber"] = self.phone.text().strip()
        ou = self.ou.text().strip()

        groups = list(self.template_groups)
        tpl_name = self.cb_template.currentData() or ""

        def work():
            c = self.app.get_conn()
            try:
                dn = ad.create_user(c, login=login, password=pwd, surname=s, name=n, patronymic=p, ou=ou, extra=extra)
                ok, bad = self._templates_mod.apply_groups(c, dn, groups, ad.MODIFY_ADD) if groups else ([], [])
                return dn, ok, bad
            finally:
                c.unbind()

        def done(res):
            dn, ok, bad = res
            db.log_action(self.app.admin_name, "create_user", login, dn)
            if groups:
                db.log_action(self.app.admin_name, "template_apply", login,
                              f"{tpl_name or 'образец'}: групп {len(ok)}" + (f", ошибок {len(bad)}" if bad else ""))
            msg = f"Логин: {login}\nПароль: {pwd}\nDN: {dn}"
            if ok:
                msg += f"\nГруппы: {', '.join(ad.dn_to_cn(g) for g in ok)}"
            if bad:
                msg += f"\n⚠️ Не удалось добавить: {', '.join(ad.dn_to_cn(g) for g in bad)}"
            MessageBox.information(self, "Пользователь создан", msg)
            self.accept()

        run_in_background(self, work, done)


# ============================================================================ пинг
# Окна пинга и свободного IP вынесены в отдельные модули; реэкспорт сохраняет старые импорты.
from .freeip_ui import FreeIPDialog  # noqa: E402
from .inventory_ui import InventoryDialog  # noqa: E402
from .pingui import PingDialog  # noqa: E402

__all__ = ["FreeIPDialog", "InventoryDialog", "PingDialog"]


# ============================================================================ дизайн
class Swatch(QPushButton):
    """Кружок цвета: клик — выбрать. Выбранный — с белой точкой внутри и кольцом акцента ."""

    def __init__(self, color: str, tooltip: str = "", size: int = 30, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip or color)
        self._selected = False
        self.set_selected(False)

    def set_selected(self, on: bool):
        self._selected = on
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        from PyQt6.QtGui import QPainter, QPen, QColor
        from PyQt6.QtCore import QRectF
        pal = app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height())
        r = QRectF((self.width() - s) / 2 + 2, (self.height() - s) / 2 + 2, s - 4, s - 4)
        p.setPen(QPen(QColor(pal.border), 1))
        p.setBrush(QColor(self.color))
        p.drawEllipse(r)
        if self._selected:
            p.setPen(QPen(QColor(pal.text), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(r.adjusted(-1, -1, 1, 1))
            dot = QColor("#ffffff" if is_color_dark(self.color) else "#1D1D1F")
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(dot)
            c = r.center()
            p.drawEllipse(QRectF(c.x() - 3.5, c.y() - 3.5, 7, 7))
        p.end()


class ThemeTile(QPushButton):
    """Плитка темы: мини-«окно» в цветах темы (фон, панель, акцентная кнопка) и подпись."""

    def __init__(self, key: str, t: dict, parent=None):
        super().__init__(parent)
        self.key = key
        self.t = t
        self.setFixedSize(132, 96)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(t["name"])
        self._selected = False
        self.set_selected(False)

    def set_selected(self, on: bool):
        self._selected = on
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        from PyQt6.QtGui import QPainter, QPen, QColor
        from PyQt6.QtCore import QRectF
        t, pal = self.t, app_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        # рамка выбора
        outer = QRectF(1, 1, w - 2, h - 2)
        p.setPen(QPen(QColor(pal.accent if self._selected else pal.border), 2.5 if self._selected else 1))
        p.setBrush(QColor(pal.card))
        p.drawRoundedRect(outer, 12, 12)
        # превью «экрана»
        scr = QRectF(8, 8, w - 16, h - 36)
        p.setPen(Qt.PenStyle.NoPen)
        from .theme import theme_colors
        c1, c2 = theme_colors(t)
        if c1 == c2:
            p.setBrush(QColor(c1))
        else:                                   # градиентная тема — на плитке тот же диагональный переход
            from PyQt6.QtGui import QLinearGradient
            g = QLinearGradient(scr.topLeft(), scr.bottomRight())
            g.setColorAt(0.0, QColor(c1))
            g.setColorAt(1.0, QColor(c2))
            p.setBrush(g)
        p.drawRoundedRect(scr, 7, 7)
        panel = QRectF(scr.left() + 8, scr.top() + 8, scr.width() - 16, scr.height() - 16)
        p.setBrush(QColor(t["panel"]))
        p.drawRoundedRect(panel, 5, 5)
        # «строки текста» и акцентная кнопка
        fg = QColor(t["text"]); fg.setAlpha(110)
        p.setBrush(fg)
        p.drawRoundedRect(QRectF(panel.left() + 7, panel.top() + 7, panel.width() * 0.45, 4), 2, 2)
        p.drawRoundedRect(QRectF(panel.left() + 7, panel.top() + 15, panel.width() * 0.65, 4), 2, 2)
        p.setBrush(QColor(t["accent"]))
        p.drawRoundedRect(QRectF(panel.right() - 30, panel.bottom() - 13, 24, 8), 4, 4)
        # подпись
        p.setPen(QColor(pal.text if self._selected else pal.subtext))
        f = self.font(); f.setPointSizeF(max(8.0, f.pointSizeF() - 0.5)); f.setBold(self._selected)
        p.setFont(f)
        p.drawText(QRectF(4, h - 26, w - 8, 20), Qt.AlignmentFlag.AlignCenter, t["name"])
        p.end()


class DesignSettingsDialog(FramelessDialog):
    """Оформление: вкладки «Тема» (плитки + акцент + свои цвета), «Шрифт», «Интерфейс». Всё применяется сразу."""

    # готовые акценты: синий, зелёный, индиго, оранжевый, розовый, фиолетовый, красный, бирюзовый, жёлтый, серый
    ACCENTS = (("#007AFF", "Синий"), ("#34C759", "Зелёный"), ("#5856D6", "Индиго"), ("#FF9500", "Оранжевый"),
               ("#FF2D55", "Розовый"), ("#AF52DE", "Фиолетовый"), ("#FF3B30", "Красный"), ("#5AC8FA", "Бирюзовый"),
               ("#FFCC00", "Жёлтый"), ("#8E8E93", "Серый"))
    # готовые фоны — заметно разные оттенки, а не семь почти одинаковых серых
    BACKGROUNDS = ("#1C1C1E", "#17140F", "#0E1813", "#1A1222", "#1E1115", "#12181F", "#2C2C2E",
                   "#F2F2F7", "#F3E9DA", "#E4F0E6", "#ECE7F7", "#FDE8E8", "#FFFFFF")

    def __init__(self, app, parent=None):
        super().__init__("🎨 Оформление", parent, (760, 620))
        self.app = app
        self.design = dict(settings.design)
        self._c1, self._c2 = "#1C1C1E", "#2C2C2E"
        self._mode = "solid"
        m = re.findall(r"#[0-9a-fA-F]{6}", self.design.get("bg_style", ""))
        if "qlineargradient" in self.design.get("bg_style", "") and len(m) >= 2:
            self._c1, self._c2, self._mode = m[0], m[1], "grad"
        elif m:
            self._c1 = m[0]

        tabs = self.tabs = QTabWidget()
        tabs.addTab(self._tab_theme(), "🎨 Тема")
        tabs.addTab(self._tab_font(), "🔤 Шрифт")
        tabs.addTab(self._tab_ui(), "⚙️ Интерфейс")
        self.body.addWidget(tabs, 1)
        foot = QHBoxLayout()
        self.lbl_state = QLabel("Изменения применяются сразу и сохраняются в config.ini")
        self.lbl_state.setStyleSheet(f"color: {app_palette().subtext};")
        foot.addWidget(self.lbl_state, 1)
        reset = QPushButton("↺ Тема по умолчанию")
        reset.clicked.connect(lambda: self.preset("dark"))
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        foot.addWidget(reset)
        foot.addWidget(close)
        self.body.addLayout(foot)
        self._sync_selection()

    # ---------------------------------------------------------------- вкладки
    def _tab_theme(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(12)
        lay.addWidget(QLabel("<b>Готовые темы</b>"))
        holder = QWidget()
        flow = FlowLayout(holder, spacing=8)
        self.tiles: dict[str, ThemeTile] = {}
        for key, t in PRESET_THEMES.items():
            tile = ThemeTile(key, t)
            tile.clicked.connect(lambda _, k=key: self.preset(k))
            flow.addWidget(tile)
            self.tiles[key] = tile
        lay.addWidget(holder)

        lay.addWidget(QLabel("<b>Акцентный цвет</b> — кнопки, заголовки, выделение"))
        arow = QHBoxLayout()
        arow.setSpacing(6)
        self.accent_swatches: list[Swatch] = []
        for color, name in self.ACCENTS:
            sw = Swatch(color, name)
            sw.clicked.connect(lambda _, c=color: self.set_accent(c))
            arow.addWidget(sw)
            self.accent_swatches.append(sw)
        b_more = QPushButton("+ Свой…")
        self.btn_accent_custom = b_more
        b_more.setToolTip("Выбрать акцент из палитры")
        b_more.clicked.connect(lambda: self._pick_into(self.set_accent, self.design["accent_color"], "Акцентный цвет"))
        arow.addWidget(b_more)
        arow.addStretch()
        lay.addLayout(arow)

        lay.addWidget(QLabel("<b>Фон окна</b>"))
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self.bg_swatches: list[Swatch] = []
        for color in self.BACKGROUNDS:
            sw = Swatch(color)
            sw.clicked.connect(lambda _, c=color: self.set_solid(c))
            brow.addWidget(sw)
            self.bg_swatches.append(sw)
        b_bg = QPushButton("+ Свой…")
        self.btn_bg_custom = b_bg
        b_bg.clicked.connect(lambda: self._pick_into(self.set_solid, self._c1, "Цвет фона"))
        brow.addWidget(b_bg)
        brow.addStretch()
        lay.addLayout(brow)

        grow = QHBoxLayout()
        grow.addWidget(QLabel("Градиент:"))
        self.sw_g1 = Swatch(self._c1, "Цвет 1 (верхний левый угол)", 34)
        self.sw_g1.clicked.connect(lambda: self._pick_into(lambda c: self.set_grad(1, c), self._c1, "Цвет градиента 1"))
        self.sw_g2 = Swatch(self._c2, "Цвет 2 (нижний правый угол)", 34)
        self.sw_g2.clicked.connect(lambda: self._pick_into(lambda c: self.set_grad(2, c), self._c2, "Цвет градиента 2"))
        self.grad_preview = QLabel()
        self.grad_preview.setFixedSize(180, 34)
        b_swap = QPushButton("Поменять")
        b_swap.setToolTip("Поменять цвета местами")
        b_swap.clicked.connect(lambda: self.set_grad(0, None))
        grow.addWidget(self.sw_g1)
        grow.addWidget(QLabel("→"))
        grow.addWidget(self.sw_g2)
        grow.addWidget(self.grad_preview)
        grow.addWidget(b_swap)
        b_apply_grad = QPushButton("Применить градиент")
        b_apply_grad.clicked.connect(lambda: self.set_grad(0, None, swap=False))
        grow.addWidget(b_apply_grad)
        grow.addStretch()
        lay.addLayout(grow)

        self.chk_follow = QCheckBox("Тёмная/светлая — как в Windows (следовать системной теме)")
        self.chk_follow.setChecked(bool(self.design.get("follow_system")))
        self.chk_follow.toggled.connect(self._follow_toggled)
        lay.addWidget(self.chk_follow)
        lay.addStretch()
        return w

    def _tab_font(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        f = QFormLayout()
        f.setHorizontalSpacing(14)
        self.font = QFontComboBox()
        self.font.setCurrentFont(QApplication.font())
        self.size = QSpinBox()
        self.size.setRange(8, 18)
        self.size.setSuffix(" pt")
        self.size.setValue(int(self.design["font_size"]))
        f.addRow("Семейство:", self.font)
        f.addRow("Размер:", self.size)
        lay.addLayout(f)
        self.font_preview = QLabel("Иванов Иван Петрович · WS-101 · 10.0.2.11 · Съешь же ещё этих мягких французских булок")
        self.font_preview.setWordWrap(True)
        self.font_preview.setObjectName("dashCard")
        self.font_preview.setMinimumHeight(80)
        self.font_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self.font_preview)
        self.font.currentFontChanged.connect(lambda _: self.apply())
        self.size.valueChanged.connect(lambda _: self.apply())
        lay.addStretch()
        return w

    def _tab_ui(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        uf = QFormLayout()
        uf.setHorizontalSpacing(14)
        self.lang = QComboBox()
        self.lang.addItem("Русский", "ru")
        self.lang.addItem("English", "en")
        self.lang.setCurrentIndex(max(0, self.lang.findData(settings.language)))
        self.chk_tray = QCheckBox("Сворачивать в трей при закрытии окна")
        self.chk_tray.setChecked(settings.minimize_to_tray)
        self.hotkey = QLineEdit(settings.global_hotkey)
        self.hotkey.setPlaceholderText("Ctrl+Shift+A (пусто — выключить)")
        self.hotkey.setToolTip("Сочетание клавиш, которое работает из любой программы Windows: разворачивает ADK "
                               "(в том числе из трея) и ставит курсор в строку поиска")
        uf.addRow("Язык:", self.lang)
        uf.addRow(self.chk_tray)
        uf.addRow("Клавиши вызова ADK:", self.hotkey)
        hk_hint = QLabel("Сочетание работает из любой программы: разворачивает ADK (даже из трея) и ставит курсор "
                         "в строку поиска. Например, Ctrl+Shift+A. Пусто — выключено.")
        hk_hint.setObjectName("subtle")
        hk_hint.setWordWrap(True)
        uf.addRow("", hk_hint)
        lay.addLayout(uf)
        save_ui = QPushButton("💾 Сохранить настройки интерфейса")
        save_ui.setObjectName("btnPrimary")
        save_ui.clicked.connect(self.save_ui)
        lay.addWidget(save_ui)
        note = QLabel("Язык, трей и горячая клавиша применяются после перезапуска ADK.")
        note.setStyleSheet(f"color: {app_palette().subtext};")
        lay.addWidget(note)
        lay.addStretch()
        return w

    # ---------------------------------------------------------------- действия
    def save_ui(self):
        from .tray import parse_hotkey
        hk = self.hotkey.text().strip()
        if hk and parse_hotkey(hk) is None:
            MessageBox.warning(self, "Горячая клавиша", "Формат: Ctrl+Shift+A, Alt+F9, Win+Space…")
            return
        settings.save_section("UI", {"language": self.lang.currentData(), "minimize_to_tray": str(self.chk_tray.isChecked()).lower(),
                                     "global_hotkey": hk})
        settings.language, settings.minimize_to_tray, settings.global_hotkey = self.lang.currentData(), self.chk_tray.isChecked(), hk
        self.lbl_state.setText("✅ Настройки интерфейса сохранены — вступят в силу после перезапуска")

    def _follow_toggled(self, on: bool):
        self.design["follow_system"] = on
        self.apply()

    def preset(self, key: str):
        from .theme import theme_design
        t = PRESET_THEMES[key]
        if t["type"] == "solid":
            self._c1, self._mode = t["bg"], "solid"
        else:
            self._c1, self._c2, self._mode = t["c1"], t["c2"], "grad"
        self.design.update(theme_design(t))
        self.apply()

    def set_accent(self, color: str):
        self.design["accent_color"] = color
        self.apply()

    def set_solid(self, color: str):
        from .theme import derive_panel
        self._c1, self._mode = color, "solid"
        dark = is_color_dark(color)
        self.design.update(bg_style=f"background-color: {color};", is_dark=dark, panel_color=derive_panel(color, dark),
                           text_color="", border_color="")
        self.apply()

    def set_grad(self, n: int, color: str | None, swap: bool = True):
        if n == 1 and color:
            self._c1 = color
        elif n == 2 and color:
            self._c2 = color
        elif n == 0 and swap:
            self._c1, self._c2 = self._c2, self._c1
        from .theme import derive_panel
        self._mode = "grad"
        dark = is_color_dark(self._c1)
        self.design.update(
            bg_style=f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {self._c1}, stop:1 {self._c2});",
            is_dark=dark, panel_color=derive_panel(self._c1, dark), text_color="", border_color="")
        self.apply()

    def _pick_into(self, setter, initial: str, title: str):
        from .colorpicker import ColorPickerDialog
        c = ColorPickerDialog.get_color(initial, self, title)
        if c:
            setter(c)

    def _sync_selection(self):
        """Подсветить выбранную плитку/свотчи по текущему design."""
        bg = self.design.get("bg_style", "")
        acc = self.design.get("accent_color", "")
        for key, tile in self.tiles.items():
            t = PRESET_THEMES[key]
            cur = (t["type"] == "solid" and f"background-color: {t['bg']};" == bg) or \
                  (t["type"] == "grad" and t["c1"] in bg and t["c2"] in bg)
            tile.set_selected(bool(cur) and t["accent"] == acc)
        for sw in self.accent_swatches:
            sw.set_selected(sw.color.lower() == acc.lower())
        for sw in self.bg_swatches:
            sw.set_selected(self._mode == "solid" and sw.color.lower() == self._c1.lower())
        self.sw_g1.color, self.sw_g2.color = self._c1, self._c2
        self.sw_g1.set_selected(self._mode == "grad")
        self.sw_g2.set_selected(self._mode == "grad")
        self.grad_preview.setStyleSheet(
            f"background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {self._c1}, stop:1 {self._c2}); "
            f"border-radius: 8px; border: 1.5px solid {app_palette().border};")

    def apply(self):
        self.design["font_family"] = self.font.currentFont().family()
        self.design["font_size"] = self.size.value()
        settings.save_design(self.design)
        apply_theme(self.design)
        self.app.on_theme_changed()
        self._sync_selection()
        self.lbl_state.setText("✅ Применено и сохранено")
