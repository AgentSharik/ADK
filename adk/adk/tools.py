"""Инструменты «набора» (3.0): массовые операции, заметки, сравнение групп, таймлайн, здоровье ПК.

Вынесены из ``dialogs.py``, чтобы тот не разрастался. Каждый диалог — ``FramelessDialog``;
LDAP-работа — в фоне через ``run_in_background``; все изменения пишутся в ``audit_log``.
"""
from __future__ import annotations

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from . import access, ad, db, health
from .i18n import tr
from .widgets import FramelessDialog, MessageBox, fit_columns, run_in_background

log = logging.getLogger(__name__)


# ============================================================================ массовые операции
class BulkOperationsDialog(FramelessDialog):
    """Одна операция над N учётными записями с одним подтверждением и построчным отчётом.

    Операции: добавить/удалить из группы, отключить/включить УЗ, снять блокировку, сбросить пароль
    (каждому — свой сгенерированный, список паролей копируется в буфер один раз).
    """

    OPS = (("group_add", "➕ Добавить в группу"), ("group_remove", "➖ Удалить из группы"),
           ("disable", "🚷 Отключить УЗ"), ("enable", "✅ Включить УЗ"),
           ("unlock", "🔓 Снять блокировку"), ("reset_password", "🔑 Сбросить пароль (каждому свой)"))

    def __init__(self, users: list[dict], app, parent=None):
        super().__init__(f"🧰 Массовые операции — {len(users)} уч. зап.", parent, (760, 560))
        self.app = app
        self.users = [u for u in users if u.get("entry") is not None]
        self.results: list[tuple[str, bool, str]] = []
        self.passwords: dict[str, str] = {}

        top = QHBoxLayout()
        self.op = QComboBox()
        for code, label in self.OPS:
            self.op.addItem(label, code)
        self.op.currentIndexChanged.connect(self._op_changed)
        top.addWidget(QLabel("Операция:"))
        top.addWidget(self.op, 1)
        self.body.addLayout(top)

        self.group_row = QHBoxLayout()
        self.group_filter = QLineEdit()
        self.group_filter.setPlaceholderText("Фильтр групп…")
        self.group_filter.textChanged.connect(self._filter_groups)
        self.group_row.addWidget(self.group_filter)
        self.body.addLayout(self.group_row)
        self.groups = QListWidget()
        self.groups.setMaximumHeight(140)
        self.body.addWidget(self.groups)
        self.must_change = QCheckBox("Потребовать смену пароля при входе")
        self.must_change.setChecked(True)
        self.must_change.setVisible(False)
        self.body.addWidget(self.must_change)

        self.table = QTableWidget(len(self.users), 3)
        self.table.setHorizontalHeaderLabels(["Логин", "ФИО", "Результат"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 220)
        for r, u in enumerate(self.users):
            self.table.setItem(r, 0, QTableWidgetItem(u.get("login", "")))
            self.table.setItem(r, 1, QTableWidgetItem(u.get("full_fio") or u.get("fio") or ""))
            self.table.setItem(r, 2, QTableWidgetItem("—"))
        fit_columns(self.table, wrap=False)
        self.body.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.status = QLabel("")
        btns.addWidget(self.status, 1)
        self.btn_copy = QPushButton("📋 Скопировать пароли")
        self.btn_copy.setVisible(False)
        self.btn_copy.clicked.connect(self.copy_passwords)
        btns.addWidget(self.btn_copy)
        self.btn_run = QPushButton("▶ Выполнить")
        self.btn_run.setObjectName("btnDanger")
        self.btn_run.clicked.connect(self.run_op)
        btns.addWidget(self.btn_run)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        btns.addWidget(close)
        self.body.addLayout(btns)

        self.all_groups: dict[str, str] = {}
        run_in_background(self, self._load_groups, self._groups_loaded, lambda m: self.status.setText(f"⚠️ {m}"))
        self._op_changed()

    # ---- группы
    def _load_groups(self):
        c = self.app.get_conn()
        try:
            return {ad.get_ad_value(e, "cn"): e.entry_dn for e in ad.paged_search(c, "(objectClass=group)", ["cn"])}
        finally:
            c.unbind()

    def _groups_loaded(self, groups):
        self.all_groups = groups
        self._filter_groups(self.group_filter.text())

    def _filter_groups(self, text: str):
        self.groups.clear()
        t = text.casefold()
        for cn in sorted(self.all_groups):
            if t in cn.casefold():
                self.groups.addItem(cn)

    def _op_changed(self):
        code = self.op.currentData()
        needs_group = code in ("group_add", "group_remove")
        self.groups.setVisible(needs_group)
        self.group_filter.setVisible(needs_group)
        self.must_change.setVisible(code == "reset_password")
        self.btn_copy.setVisible(False)

    # ---- выполнение
    def run_op(self):
        code = self.op.currentData()
        if not access.can(f"bulk_{code}"):
            MessageBox.warning(self, tr("Недостаточно прав"), tr(access.deny_text(f"bulk_{code}")))
            return
        if not self.users:
            MessageBox.information(self, "Массовые операции", "Нет учётных записей с данными AD.")
            return
        gdn, cn = "", ""
        if code in ("group_add", "group_remove"):
            items = self.groups.selectedItems()
            if not items:
                MessageBox.warning(self, "Группа", "Выберите группу.")
                return
            cn = items[0].text()
            gdn = self.all_groups[cn]
        if code == "reset_password" and not ad.settings.use_ssl:
            MessageBox.critical(self, "LDAPS", "Сброс пароля возможен только по LDAPS (use_ssl=true).")
            return
        label = dict(self.OPS)[code]
        if not MessageBox.question(self, "Подтверждение",
                                   f"{label}{f' «{cn}»' if cn else ''}\nдля {len(self.users)} учётных записей?"):
            return
        self.btn_run.setEnabled(False)
        self.status.setText("⏳ Выполняется…")
        must_change = self.must_change.isChecked()
        users = list(self.users)

        def work():
            out: list[tuple[str, bool, str]] = []
            pwds: dict[str, str] = {}
            c = self.app.get_conn()
            try:
                for u in users:
                    e, login, dn = u["entry"], u["login"], u["entry"].entry_dn
                    try:
                        if code == "group_add":
                            c.modify(gdn, {"member": [(ad.MODIFY_ADD, [dn])]})
                        elif code == "group_remove":
                            c.modify(gdn, {"member": [(ad.MODIFY_DELETE, [dn])]})
                        elif code in ("disable", "enable"):
                            ad.set_account_disabled(c, dn, ad.get_ad_int_value(e, "userAccountControl"), code == "disable")
                        elif code == "unlock":
                            ad.unlock_account(c, dn)
                        elif code == "reset_password":
                            pwd = ad.generate_secure_password()
                            ad.reset_password(c, dn, pwd, must_change=must_change)
                            pwds[login] = pwd
                        ok, msg = True, "✅ выполнено"
                        if hasattr(c, "result") and isinstance(c.result, dict) and c.result.get("description") not in (None, "success"):
                            ok, msg = False, f"⚠️ {c.result.get('description')}: {c.result.get('message', '')}"
                    except Exception as exc:  # noqa: BLE001
                        ok, msg = False, f"❌ {ad.describe_ldap_error(exc)}"
                    out.append((login, ok, msg))
                    db.log_action(self.app.admin_name, f"bulk_{code}", login, cn or ("смена при входе" if must_change and code == "reset_password" else ""))
            finally:
                c.unbind()
            return out, pwds

        run_in_background(self, work, self._done, lambda m: (self.status.setText(f"⚠️ {m}"), self.btn_run.setEnabled(True)))

    def _done(self, res):
        self.results, self.passwords = res
        by_login = {l: (ok, m) for l, ok, m in self.results}
        for r in range(self.table.rowCount()):
            login = self.table.item(r, 0).text()
            ok, m = by_login.get(login, (False, "—"))
            self.table.setItem(r, 2, QTableWidgetItem(m))
        good = sum(1 for _, ok, _ in self.results if ok)
        self.status.setText(f"Готово: {good}/{len(self.results)} успешно")
        self.btn_run.setEnabled(True)
        if self.passwords:
            self.btn_copy.setVisible(True)
            self.copy_passwords()

    def copy_passwords(self):
        text = "\n".join(f"{l}\t{p}" for l, p in self.passwords.items())
        QApplication.clipboard().setText(text)
        self.status.setText(f"📋 Пароли ({len(self.passwords)}) скопированы в буфер — вставьте в защищённое место и очистите буфер")


# ============================================================================ заметки
class NotesDialog(FramelessDialog):
    """Заметки по пользователю (по логину) или ПК: «менял клавиатуру 03.09». Видны всем администраторам."""

    def __init__(self, subject: str, kind: str, app, parent=None, title: str = ""):
        super().__init__(f"📝 Заметки: {title or subject}", parent, (560, 460))
        self.subject, self.kind, self.app = subject, kind, app
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.body.addWidget(self.list, 1)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("Новая заметка… (Ctrl+Enter — сохранить)")
        self.edit.setMaximumHeight(90)
        self.body.addWidget(self.edit)
        btns = QHBoxLayout()
        self.btn_del = QPushButton("🗑 Удалить выбранную")
        self.btn_del.clicked.connect(self.delete_selected)
        btns.addWidget(self.btn_del)
        btns.addStretch()
        self.btn_add = QPushButton("💾 Сохранить заметку")
        self.btn_add.setObjectName("btnSuccess")
        self.btn_add.clicked.connect(self.add)
        btns.addWidget(self.btn_add)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        self.body.addLayout(btns)
        if not access.can("note_add"):
            self.edit.setEnabled(False)
            self.btn_add.setEnabled(False)
            self.btn_del.setEnabled(False)
        self.reload()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.add()
            return
        super().keyPressEvent(e)

    def reload(self):
        self.list.clear()
        for n in db.notes_for(self.subject, self.kind):
            it = QListWidgetItem(f"{n['ts']} · {n['admin']}\n{n['text']}")
            it.setData(Qt.ItemDataRole.UserRole, n["id"])
            self.list.addItem(it)

    def add(self):
        text = self.edit.toPlainText().strip()
        if not text:
            return
        db.add_note(self.subject, text, self.app.admin_name, self.kind)
        db.log_action(self.app.admin_name, "note_add", self.subject, text[:120])
        self.edit.clear()
        self.reload()
        if hasattr(self.app, "refresh_notes_badge"):
            self.app.refresh_notes_badge()

    def delete_selected(self):
        it = self.list.currentItem()
        if not it or not MessageBox.question(self, "Заметка", "Удалить заметку?"):
            return
        db.delete_note(it.data(Qt.ItemDataRole.UserRole))
        db.log_action(self.app.admin_name, "note_delete", self.subject, "")
        self.reload()
        if hasattr(self.app, "refresh_notes_badge"):
            self.app.refresh_notes_badge()


# ============================================================================ сравнение групп
class GroupCompareDialog(FramelessDialog):
    """«Сделай как у Петрова»: diff memberOf двух пользователей, применить недостающие/лишние группы."""

    def __init__(self, target_entry, app, parent=None):
        self.target = target_entry
        self.app = app
        login = ad.get_ad_value(target_entry, "sAMAccountName")
        super().__init__(f"🧬 Группы: сделать как у… → {login}", parent, (820, 560))
        top = QHBoxLayout()
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("Логин эталонного пользователя (например, petrov)")
        self.ref.returnPressed.connect(self.load_ref)
        btn = QPushButton("Сравнить")
        btn.setObjectName("btnPrimary")
        btn.clicked.connect(self.load_ref)
        top.addWidget(QLabel("Эталон:"))
        top.addWidget(self.ref, 1)
        top.addWidget(btn)
        self.body.addLayout(top)

        cols = QHBoxLayout()
        self.missing = QListWidget()
        self.extra = QListWidget()
        self.common = QListWidget()
        for lst, title in ((self.missing, "➕ Есть у эталона, нет у цели (добавить)"),
                           (self.extra, "➖ Есть у цели, нет у эталона (удалить)"),
                           (self.common, "= Общие")):
            col = QVBoxLayout()
            col.addWidget(QLabel(f"<b>{title}</b>"))
            lst.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
            col.addWidget(lst)
            cols.addLayout(col)
        self.body.addLayout(cols, 1)

        btns = QHBoxLayout()
        self.status = QLabel("")
        btns.addWidget(self.status, 1)
        self.btn_add = QPushButton("➕ Добавить выбранные")
        self.btn_add.setObjectName("btnSuccess")
        self.btn_add.clicked.connect(lambda: self.apply(self.missing, ad.MODIFY_ADD))
        self.btn_rm = QPushButton("➖ Удалить выбранные")
        self.btn_rm.setObjectName("btnDanger")
        self.btn_rm.clicked.connect(lambda: self.apply(self.extra, ad.MODIFY_DELETE))
        btns.addWidget(self.btn_add)
        btns.addWidget(self.btn_rm)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        self.body.addLayout(btns)
        self.target_groups = {ad.dn_to_cn(g): g for g in ad.get_ad_list_value(self.target, "memberOf")}
        self.ref_groups: dict[str, str] = {}
        self._fill()

    @staticmethod
    def diff(target: dict[str, str], ref: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
        """(добавить, удалить, общие) по CN — чистая функция для тестов."""
        t, r = set(target), set(ref)
        return sorted(r - t), sorted(t - r), sorted(t & r)

    def _fill(self):
        add, rm, same = self.diff(self.target_groups, self.ref_groups)
        for lst, items in ((self.missing, add), (self.extra, rm), (self.common, same)):
            lst.clear()
            lst.addItems(items)
        for i in range(self.missing.count()):
            self.missing.item(i).setSelected(True)
        self.status.setText(f"Добавить: {len(add)} · удалить: {len(rm)} · общих: {len(same)}" if self.ref_groups else "")

    def load_ref(self):
        login = self.ref.text().strip()
        if not login:
            return

        def work():
            c = self.app.get_conn()
            try:
                es = ad.paged_search(c, f"(&(objectClass=user)(sAMAccountName={ad.escape_filter_chars(login)}))", ["memberOf"], limit=1)
            finally:
                c.unbind()
            if not es:
                raise ValueError(f"Пользователь {login} не найден")
            return {ad.dn_to_cn(g): g for g in ad.get_ad_list_value(es[0], "memberOf")}

        def done(groups):
            self.ref_groups = groups
            self._fill()

        run_in_background(self, work, done, lambda m: self.status.setText(f"⚠️ {m}"))

    def apply(self, lst: QListWidget, op):
        if not access.can("groups_sync"):
            MessageBox.warning(self, tr("Недостаточно прав"), tr(access.deny_text("groups_sync")))
            return
        cns = [it.text() for it in lst.selectedItems()]
        if not cns:
            return
        verb = "Добавить в" if op == ad.MODIFY_ADD else "Удалить из"
        if not MessageBox.question(self, "Подтверждение", f"{verb} {len(cns)} групп(ы)?"):
            return
        dn = self.target.entry_dn
        login = ad.get_ad_value(self.target, "sAMAccountName")
        src = self.ref_groups if op == ad.MODIFY_ADD else self.target_groups

        def work():
            c = self.app.get_conn()
            done_cns = []
            try:
                for cn in cns:
                    c.modify(src[cn], {"member": [(op, [dn])]})
                    done_cns.append(cn)
                    db.log_action(self.app.admin_name, "groups_sync", login, f"{'+' if op == ad.MODIFY_ADD else '-'}{cn}")
            finally:
                c.unbind()
            return done_cns

        def ok(done_cns):
            for cn in done_cns:
                if op == ad.MODIFY_ADD:
                    self.target_groups[cn] = src[cn]
                else:
                    self.target_groups.pop(cn, None)
            self._fill()
            self.status.setText(f"✅ Применено: {len(done_cns)}")

        run_in_background(self, work, ok, lambda m: self.status.setText(f"⚠️ {m}"))


# ============================================================================ таймлайн
class HistoryDialog(FramelessDialog):
    """Кто за каким ПК и с каким IP — по данным сканера (pc_history)."""

    def __init__(self, login: str, comp: str, parent=None):
        super().__init__(f"🕓 История: {login or comp}", parent, (720, 440))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["ПК", "Пользователь", "IP", "С", "По"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.body.addWidget(self.table, 1)
        self.status = QLabel("")
        self.body.addWidget(self.status)
        rows = db.history_for(login=login, computer_name=comp)
        self.table.setRowCount(len(rows))
        for r, h in enumerate(rows):
            for c, v in enumerate((h["comp"], h["login"], h["ip"], h["first_seen"], h["last_seen"])):
                self.table.setItem(r, c, QTableWidgetItem(v))
        fit_columns(self.table)
        self.status.setText(f"Записей: {len(rows)}" if rows else "Истории пока нет — она накапливается сканером парка")


# ============================================================================ здоровье ПК
# Окно переехало в health_ui.py (обзор, S.M.A.R.T., карта диска); имя оставлено для обратной совместимости.
from .health_ui import HealthDialog  # noqa: E402

__all__ = ["BulkOperationsDialog", "NotesDialog", "GroupCompareDialog", "HistoryDialog", "HealthDialog", "format_health_rich"]


def format_health_rich(h: dict) -> str:
    import html
    return "<br>".join(html.escape(line) for line in health.format_health(h).split("\n"))
