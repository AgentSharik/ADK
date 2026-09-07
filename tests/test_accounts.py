"""Учётные записи и поиск: состояние учётки (два состояния + причина), полное ФИО, карточка пользователя, единое
окно смены пароля, поиск принтера по IP, поиск по организации, честный дашборд.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication

from adk import ad, db, export

from tests.test_gui import ENTRIES, FakeAttr, FakeEntry, _main, _wait, fake_conn

__all__ = ["fake_conn"]   # фикстура из test_gui нужна тестам ниже (реэкспорт, чтобы pyflakes не ругался)


# ------------------------------------------------------------------ 1. смена пароля
def test_temp_password_dialog_removed_and_merged(qapp):
    import adk.extras as extras
    from adk.dialogs import ResetPasswordDialog, UserCardDialog
    from tests.test_gui import ENTRIES
    assert not hasattr(extras, "TempPasswordDialog")
    d = ResetPasswordDialog("ivanov", None, fio="Иванов Иван")
    assert "Смена пароля" in d.title_bar.label.text()
    assert d.must_change.isChecked() and d.unlock.isChecked() and d.btn_ok.isEnabled()
    for n in (10, 16):
        d.generate(n)
        assert len(d.password.text()) == n
    d.btn_card.click()
    assert "ivanov" in QApplication.clipboard().text() and d.password.text() in QApplication.clipboard().text()
    d.close()
    app = SimpleNamespace(get_conn=lambda: None, admin_name="admin")
    card = UserCardDialog(ENTRIES[0], app)
    assert not hasattr(card, "btn_qr") and "Смена пароля" in card.btn_reset.text()
    assert not hasattr(card, "temp_password")
    card.close()


def _inventory(rows):
    for name, ip, online in rows:
        db.db_execute_with_retry(
            "INSERT OR REPLACE INTO pc_inventory (computer_name, ip_address, is_online, last_checked) VALUES (?,?,?,?)",
            (name, ip, 1 if online else 0, "2026-09-05 10:00:00"))


# ------------------------------------------------------------------ 2. дашборд
def test_inventory_summary_counts_offline_rows():
    """Дано 3 ПК в инвентаре, из них 1 не отвечает → сводка: online=2, offline=1, total=3."""
    _inventory([("WS-1", "10.0.2.11", True), ("WS-2", "10.0.2.12", True), ("WS-3", "10.0.2.13", False)])
    s = db.get_inventory_summary()
    assert (s["online"], s["offline"], s["total"]) == (2, 1, 3) and s["last"] == "2026-09-05 10:00:00"


def test_dashboard_shows_real_offline_count(qapp, fake_conn, monkeypatch):
    """Регресс к скриншоту «ПК не в сети 0» при живых офлайн-ПК: карточки берут числа из инвентаря,
    а не «всего в AD минус в сети» (пока AD не ответил, это давало 0)."""
    _inventory([("WS-1", "10.0.2.11", True), ("WS-2", "10.0.2.12", False), ("WS-3", "10.0.2.13", False)])
    w = _main(qapp, fake_conn, monkeypatch)
    w.active_ad_total = 0                      # AD ещё «не ответил»
    w.refresh_dashboard()
    assert w.dashboard_counts == {"online": 1, "offline": 2, "total": 3}
    w.active_ad_total = 5                      # в AD 5 рабочих станций, 2 из них сканер ещё не видел
    w.refresh_dashboard()
    assert w.dashboard_counts == {"online": 1, "offline": 4, "total": 5}
    w.close()


# ------------------------------------------------------------------ 3. поиск: принтер по IP, организация
def test_ip_of_printer_returns_only_printer_row(qapp, fake_conn, monkeypatch):
    """Дано: сетевой принтер 10.0.2.50 стоит на WS-101 (ivanov) и WS-102 (petrov).
    Запрос «10.0.2.50» → одна строка 🖨️, без строк людей/ПК; владельцы — в инспекторе принтера."""
    from adk import netutils
    hp = {"name": "HP LaserJet M404", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50", "default": True}
    db.replace_printers("WS-101", [hp])
    db.replace_printers("WS-102", [hp])
    monkeypatch.setattr(netutils, "is_printer_alive", lambda ip, **kw: True)
    w = _main(qapp, fake_conn, monkeypatch)
    w.search_input.setText("10.0.2.50")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() >= 1 and "Принтеров: 1" in w.lbl_status.text(), qapp, 3000)
    assert w.table.rowCount() == 1 and w.table.item(0, 0).text() == "🖨️"
    assert "подключено ПК — 2" in w.lbl_status.text()
    w.select_row(0)
    assert w.printer_pane.isVisible() and w.printer_pcs.rowCount() == 2
    w.close()


def test_printer_prefix_query_still_lists_owners(qapp, fake_conn, monkeypatch):
    """Явный режим «printer: …» по-прежнему показывает принтер и тех, у кого он стоит (это отдельный запрос)."""
    from adk import netutils
    hp = {"name": "Kyocera P2040", "port": "IP_10.0.2.60", "kind": "network", "ip": "10.0.2.60", "default": False}
    db.replace_printers("WS-101", [hp])
    db.db_execute_with_retry("INSERT OR REPLACE INTO pc_inventory (computer_name, ip_address, current_user, is_online, last_checked) "
                             "VALUES ('WS-101', '10.0.2.11', 'ivanov', 1, '2026-09-05 10:00:00')")
    monkeypatch.setattr(netutils, "is_printer_alive", lambda ip, **kw: True)
    w = _main(qapp, fake_conn, monkeypatch)
    w.search_input.setText("printer: Kyocera")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() >= 2, qapp, 3000)
    assert w.table.item(0, 0).text() == "🖨️" and "ivanov" in {w.table.item(r, 0).text() for r in range(w.table.rowCount())}
    w.close()


def test_search_filter_includes_company_and_department():
    """Запрос «логистика» ищется и по отделу, и по организации (company) — как «отдел» и фамилия."""
    from adk.workers import SearchWorker
    f = SearchWorker(lambda: None, "логистика")._build_filter()
    assert "(department=*логистика*)" in f and "(company=*логистика*)" in f and "telephoneNumber" not in f


# ------------------------------------------------------------------ 4. состояние учётки и инспектор
def test_account_badge_priority():
    """Бейдж: только «Активна» / «Не активна»; причина (отключена > истекла > заблокирована) — для инспектора."""
    now = datetime.now(timezone.utc)
    e = FakeEntry("CN=a", sAMAccountName="a", userAccountControl=512)
    assert ad.account_badge(e, now) == ("Активна", "active")
    e._a["lockoutTime"] = FakeAttr([now - timedelta(minutes=3)])
    assert ad.account_inactive_reason(e, now) == "заблокирована" and ad.account_badge(e, now) == ("Не активна", "disabled")
    e._a["accountExpires"] = FakeAttr([now - timedelta(days=1)])
    assert ad.account_inactive_reason(e, now) == "истекла" and ad.account_badge(e, now) == ("Не активна", "disabled")
    e._a["userAccountControl"] = FakeAttr([514])
    assert ad.account_inactive_reason(e, now) == "отключена" and ad.account_badge(e, now) == ("Не активна", "disabled")
    assert ad.account_badge(None) == ("Активна", "active")


def test_locked_account_badge_matches_inspector(qapp, fake_conn, monkeypatch):
    """Регресс к скриншоту: в таблице «Активна», а в инспекторе «ЗАБЛОКИРОВАНА». Теперь оба говорят одно."""
    ENTRIES[0]._a["lockoutTime"] = FakeAttr([datetime.now(timezone.utc) - timedelta(minutes=5)])
    try:
        w = _main(qapp, fake_conn, monkeypatch)
        w.search_input.setText("иванов")
        w.start_search()
        assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
        row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "ivanov")
        assert w.table.item(row, 2).text() == "Не активна"          # бейдж — одно из двух состояний
        w.select_row(row)
        acc = w.vals["account"].text()
        assert "ЗАБЛОКИРОВАНА" in acc and "НЕ АКТИВНА" not in acc   # причину говорит строка блокировки, «Не активна» — бейдж
        # экспорт использует то же слово
        assert export.rows_to_table([w.results[row]])[1][2] == "Не активна"
        w.close()
    finally:
        del ENTRIES[0]._a["lockoutTime"]


def test_account_badge_has_two_states_only():
    now = datetime.now(timezone.utc)
    e = FakeEntry("CN=a", sAMAccountName="a", userAccountControl=512)
    assert ad.account_badge(e, now) == ("Активна", "active") and ad.account_inactive_reason(e, now) == ""
    e._a["lockoutTime"] = FakeAttr([now - timedelta(minutes=3)])
    assert ad.account_badge(e, now) == ("Не активна", "disabled") and ad.account_inactive_reason(e, now) == "заблокирована"
    e._a["userAccountControl"] = FakeAttr([514])
    assert ad.account_badge(e, now) == ("Не активна", "disabled") and ad.account_inactive_reason(e, now) == "отключена"
    # других слов бейдж не знает
    assert {ad.account_badge(x, now)[0] for x in (e, None)} <= {"Активна", "Не активна"}


# ------------------------------------------------------------------ 5. ФИО и карточка
def test_full_fio_takes_patronymic_from_display_name():
    """sn/givenName без middleName, displayName «Сидоров Пётр Ильич» → полное ФИО с отчеством; короткое — «Сидоров П.И.»."""
    e = FakeEntry("CN=s", sAMAccountName="sidorov", sn="Сидоров", givenName="Пётр", displayName="Сидоров Пётр Ильич")
    assert ad.get_full_fio(e) == "Сидоров Пётр Ильич" and ad.short_fio(ad.get_full_fio(e)) == "Сидоров П.И."
    e2 = FakeEntry("CN=x", sAMAccountName="x", sn="Иванов", givenName="Иван", displayName="Ivanov Ivan (IT)")
    assert ad.get_full_fio(e2) == "Иванов Иван"                     # чужой displayName не подмешиваем


def test_user_card_reworked(qapp, fake_conn, monkeypatch):
    from adk import netutils
    from adk.dialogs import UserCardDialog
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda n, **kw: ("10.0.0.9", True))
    app = SimpleNamespace(get_conn=lambda: fake_conn, admin_name="admin")
    d = UserCardDialog(ENTRIES[0], app)
    # шапка: ФИО, инициалы-аватар, бейдж состояния
    assert d.lbl_name.text() and d.badge_state.text() in ("Активна", "Не активна")
    # вкладок 4; ничего из быстрых действий инспектора
    assert [d.tabs.tabText(i) for i in range(d.tabs.count())] == ["Профиль", "Группы", "Учётная запись", "Характеристики ПК"]
    assert all(not d.tabs.tabIcon(i).isNull() for i in range(d.tabs.count()))   # 3.4.0: эмодзи стали иконками
    assert not hasattr(d, "remote_action") and not hasattr(d, "printers")
    # поля профиля — все на месте, в двух колонках
    assert set(d.inputs) >= {"sAMAccountName", "mail", "department", "company", "title"}
    # «Учётная запись» — понятные строки
    assert {"Состояние", "Пароль", "Блокировка", "Последний вход"} <= set(d.account_vals)
    assert d.account_vals["Состояние"].text().startswith("Активна")
    # группы: счётчик в заголовке, кнопки есть
    assert "Состоит в группах: 2" in d.lbl_groups.text()
    d.close()
    d2 = UserCardDialog(ENTRIES[1], app)                          # petrov — отключена
    assert d2.badge_state.text() == "Не активна" and d2.account_vals["Состояние"].text() == "Не активна — отключена"
    assert d2.btn_toggle.text().startswith("Включить") and d2.btn_toggle._adk_icon[0] == "checkmark.circle"
    d2.close()


# ------------------------------------------------------------------ 6. карточка обновляет состояние после действий
def test_card_unlock_clears_lockout_everywhere(qapp, fake_conn, monkeypatch):
    """Сняли блокировку в карточке → сразу: бейдж «Активна», вкладка «Учётная запись» без «ЗАБЛОКИРОВАНА»,
    кнопка «Снять блокировку» спряталась, в таблице и инспекторе главного окна упоминания блокировки нет."""
    from datetime import datetime, timedelta, timezone
    from adk import dialogs
    from adk.dialogs import UserCardDialog
    from tests.test_gui import FakeAttr
    ENTRIES[0]._a["lockoutTime"] = FakeAttr([datetime.now(timezone.utc) - timedelta(minutes=5)])
    try:
        w = _main(qapp, fake_conn, monkeypatch)
        w.search_input.setText("ivanov")
        w.start_search()
        assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
        row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == ENTRIES[0]["sAMAccountName"].value)
        w.select_row(row)
        assert "ЗАБЛОКИРОВАНА" in w.vals["account"].text()
        monkeypatch.setattr(dialogs.MessageBox, "information", lambda *a, **k: None)
        monkeypatch.setattr(dialogs.MessageBox, "question", lambda *a, **k: False)     # «задать пароль?» — нет
        monkeypatch.setattr(dialogs, "run_in_background", lambda owner, work, done, *a, **k: done(work()))
        monkeypatch.setattr(ad, "unlock_account", lambda conn, dn: None)
        d = UserCardDialog(w.results[row]["entry"], w, w)
        assert d.btn_unlock.isVisible() or not d.btn_toggle.isVisible()
        assert d.account_vals["Блокировка"].text().startswith("ЗАБЛОКИРОВАНА")
        d.unlock()
        assert d.badge_state.text() == "Активна" and d.account_vals["Блокировка"].text() == "нет"
        assert d.account_vals["Состояние"].text().startswith("Активна")
        assert d.btn_unlock.isHidden() and d._entry_changed
        # главное окно узнало сразу, без нового поиска
        assert w.table.item(row, 2).text() == "Активна"
        assert "ЗАБЛОКИРОВАНА" not in w.vals["account"].text() and "НЕ АКТИВНА" not in w.vals["account"].text()
        d.close()
        w.close()
    finally:
        ENTRIES[0]._a.pop("lockoutTime", None)


def test_card_toggle_updates_state_without_closing(qapp, fake_conn, monkeypatch):
    """Отключили учётку → карточка остаётся открытой, бейдж «Не активна — отключена», кнопка стала «Включить»;
    включили обратно — всё вернулось."""
    from adk import dialogs
    from adk.dialogs import UserCardDialog
    monkeypatch.setattr(dialogs.MessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(dialogs.MessageBox, "question", lambda *a, **k: True)
    monkeypatch.setattr(dialogs, "run_in_background", lambda owner, work, done, *a, **k: done(work()))
    monkeypatch.setattr(ad, "set_account_disabled", lambda conn, dn, uac, disabled: (uac | 2) if disabled else (uac & ~2))
    w = _main(qapp, fake_conn, monkeypatch)
    d = UserCardDialog(ENTRIES[0], w, w)
    assert d.badge_state.text() == "Активна" and d.btn_toggle._adk_icon[0] == "nosign"
    d.toggle_disabled()
    assert d.result() == 0                       # окно не закрылось само (accept не вызывается)
    assert d.badge_state.text() == "Не активна" and d.account_vals["Состояние"].text() == "Не активна — отключена"
    assert d.btn_toggle.text().startswith("Включить") and d.btn_toggle._adk_icon[0] == "checkmark.circle"
    d.toggle_disabled()
    assert d.badge_state.text() == "Активна" and d.btn_toggle._adk_icon[0] == "nosign"
    ENTRIES[0]._a["userAccountControl"] = type(ENTRIES[0]._a["userAccountControl"])([512])
    d.close()
    w.close()


def test_unlock_offers_to_set_password_with_locked_switches(qapp, fake_conn, monkeypatch):
    """3.2.9: сняли блокировку → вопрос «Задать пароль?». «Нет» — только снятие. «Да» — окно смены пароля, где
    «Потребовать смену» выключена, «Снять блокировку» включена, и обе нельзя переключить."""
    from adk import config, dialogs
    from adk.dialogs import ResetPasswordDialog, UserCardDialog
    asked: list[str] = []
    opened: list[ResetPasswordDialog] = []
    answers = [False, True]
    monkeypatch.setattr(dialogs.MessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(dialogs.MessageBox, "question", lambda parent, title, text: asked.append(text) or answers.pop(0))
    monkeypatch.setattr(dialogs, "run_in_background", lambda owner, work, done, *a, **k: done(work()))
    monkeypatch.setattr(ad, "unlock_account", lambda conn, dn: None)
    monkeypatch.setattr(config.settings, "use_ssl", True)

    def fake_exec(self):
        opened.append(self)
        return 0                                   # «Отмена» — в AD ничего не пишем
    monkeypatch.setattr(ResetPasswordDialog, "exec", fake_exec)
    w = _main(qapp, fake_conn, monkeypatch)
    d = UserCardDialog(ENTRIES[0], w, w)
    d.unlock()                                     # ответ «Нет»
    assert len(asked) == 1 and "Задать пользователю новый пароль?" in asked[0] and not opened
    d.unlock()                                     # ответ «Да»
    assert len(asked) == 2 and len(opened) == 1
    dlg = opened[0]
    assert dlg.after_unlock and not dlg.must_change.isChecked() and dlg.unlock.isChecked()
    assert not dlg.must_change.isEnabled() and not dlg.unlock.isEnabled()
    # обычная смена пароля — переключатели свободны и по умолчанию как раньше
    plain = ResetPasswordDialog("ivanov", None)
    assert plain.must_change.isChecked() and plain.must_change.isEnabled() and plain.unlock.isEnabled()
    plain.close()
    d.close()
    w.close()


# ------------------------------------------------------------------ 3.3.0. роль «ПК»: карточка видна, но не редактируется
def test_pc_role_card_is_view_only(qapp, fake_conn, monkeypatch):
    """Роль «ПК» (админ на компьютерах, AD только чтение): карточку открыть можно, всё видно —
    но кнопки смены пароля / блокировки / отключения / «Сохранить» скрыты, поля только для чтения,
    смарт-карта и Lync показывают состояние, но не переключаются, группы — без «добавить/удалить».
    Роль «AD» — всё как было."""
    from adk import access, netutils
    from adk.dialogs import UserCardDialog
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda n, **kw: ("10.0.0.9", True))
    app = SimpleNamespace(get_conn=lambda: fake_conn, admin_name="admin")
    access.set_rights(pc=True, ad=False)
    try:
        e = FakeEntry("CN=Сидоров С.С.,OU=x", sAMAccountName="sidorov", displayName="Сидоров С.С.", sn="Сидоров",
                      givenName="Семён", userAccountControl=512 | 0x40000)   # смарт-карту включил админ AD
        d = UserCardDialog(e, app)
        d.show()
        for b in (d.btn_reset, d.btn_unlock, d.btn_toggle, d.btn_save, d.btn_group_add, d.btn_group_rm):
            assert b.isHidden(), b.text()
        assert d.lbl_ro_hint.isVisible() and "Роль «ПК»" in d.lbl_ro_hint.text()
        assert all(le.isReadOnly() for le in d.inputs.values())
        assert not d.smartcard.isEnabled() and d.smartcard.isChecked()            # состояние видно
        assert not d.skype.isEnabled()
        # двойной клик по группе домена не добавляет (раньше обходил скрытую кнопку)
        calls = []
        monkeypatch.setattr(d, "add_to_group", lambda: calls.append(1))
        d.groups_all.itemDoubleClicked.emit(d.groups_all.item(0) or __import__("PyQt6.QtWidgets").QtWidgets.QListWidgetItem())
        assert calls == []
        # вкладки все на месте — в т.ч. характеристики ПК
        assert d.tabs.count() == 4
        d.close()
    finally:
        access.set_rights(pc=True, ad=True)
    d = UserCardDialog(e, app)
    d.show()
    assert not d.btn_reset.isHidden() and not d.btn_save.isHidden() and d.smartcard.isEnabled()
    assert d.lbl_ro_hint.isHidden()
    d.close()


def test_saved_password_survives_network_error(tmp_path, monkeypatch):
    """3.3.0: «пароль сохранялся через раз и не подставлялся». Причины и проверки:
    1) без win32crypt/keyring сохранение молча падало → теперь last_error() объясняет, что не так;
    2) сохранённые данные стирались при ЛЮБОЙ ошибке автовхода → теперь только при ошибке учётных данных;
    3) окно входа получает и подставляет сохранённый пароль."""
    import importlib
    from adk import ad, credentials
    importlib.reload(credentials)
    credentials.CRED_FILE = str(tmp_path / "cred.json")
    credentials.IS_WINDOWS = False
    credentials.keyring = None
    assert credentials.save_credentials("CORP\\admin", "pw") is False
    assert "хранилища" in credentials.last_error()
    assert credentials.storage_name() == ""

    class MemKeyring:                       # простейший keyring в памяти
        store: dict = {}
        def get_keyring(self): return self
        def set_password(self, s, u, p): self.store[(s, u)] = p
        def get_password(self, s, u): return self.store.get((s, u))
        def delete_password(self, s, u): self.store.pop((s, u), None)
    credentials.keyring = MemKeyring()
    assert credentials.save_credentials("CORP\\admin", "pw") is True and credentials.last_error() == ""
    assert credentials.load_credentials() == ("CORP\\admin", "pw")
    # сеть недоступна — это не повод забывать пароль; неверный пароль — повод
    assert not ad.is_auth_error(Exception("socket connection error while opening: timeout"))
    assert ad.is_auth_error(Exception("invalidCredentials: 80090308: LdapErr: DSID-0C09044E, comment: AcceptSecurityContext error, data 52e"))
    assert ad.is_auth_error(Exception("data 775 lockout"))


def test_login_dialog_prefills_saved_password(qapp):
    from adk.dialogs import LoginDialog
    d = LoginDialog("Контроллер домена недоступен", saved_user="CORP\\admin", saved_password="pw")
    assert d.user_in.text() == "CORP\\admin" and d.pass_in.text() == "pw" and d.remember.isChecked()
    d.close()


def test_dashboard_role_card_differs_by_role(qapp, fake_conn, monkeypatch):
    """3.3.0: разница ролей видна уже на стартовом экране — карточка «Роль» на дашборде."""
    from adk import access
    w = _main(qapp, fake_conn, monkeypatch)
    assert "полный доступ" in w.lbl_role_title.text()
    access.set_rights(pc=True, ad=False, reason="нет в группах — AD: IT-Admins")
    try:
        w.apply_access()
        assert "Роль «ПК»" in w.lbl_role_title.text() and "Скрыто" in w.lbl_role_text.text()
        assert w.role_card.styleSheet()   # подсвечена — ограниченная роль
    finally:
        access.reset(); w.apply_access()
    assert "полный доступ" in w.lbl_role_title.text() and not w.role_card.styleSheet()
    w.close()
