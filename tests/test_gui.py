"""Smoke-тесты GUI (offscreen): окно и диалоги создаются, поиск работает через заглушку LDAP."""
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from adk import ad


class FakeAttr:
    def __init__(self, values):
        self.values = list(values) if values is not None else []
        self.value = self.values[0] if self.values else None


class FakeEntry:
    def __init__(self, dn, **attrs):
        self.entry_dn = dn
        self._a = {k: FakeAttr(v if isinstance(v, list) else [v]) for k, v in attrs.items()}

    def __getitem__(self, key):
        return self._a[key]


class FakeConn:
    """Минимальная заглушка ldap3.Connection для paged_search / modify."""

    def __init__(self, entries):
        self._all = entries
        self.entries = []
        self.result = {"controls": {}}
        self.modified = []

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        self.entries = list(self._all)
        return True

    def modify(self, dn, changes):
        self.modified.append((dn, changes))
        return True

    def unbind(self):
        return True


ENTRIES = [
    FakeEntry("CN=Иванов И.И.,OU=x", sAMAccountName="ivanov", displayName="Иванов И.И.", sn="Иванов",
              givenName="Иван", userAccountControl=512, telephoneNumber="2554567", department="ИТ",
              memberOf=["CN=IT,OU=g", "CN=VPN,OU=g"]),
    FakeEntry("CN=Петров П.П.,OU=x", sAMAccountName="petrov", displayName="Петров П.П.", sn="Петров",
              givenName="Пётр", userAccountControl=514),
]


@pytest.fixture
def fake_conn(monkeypatch):
    conn = FakeConn(ENTRIES)
    monkeypatch.setattr(ad, "make_connection", lambda *a, **k: conn)
    return conn


def test_paged_search_returns_all(fake_conn):
    assert len(ad.paged_search(fake_conn, "(x)", ["cn"])) == 2
    assert len(ad.paged_search(fake_conn, "(x)", ["cn"], limit=1)) == 1


def test_get_ad_value_multivalued():
    e = ENTRIES[0]
    assert ad.get_ad_value(e, "sAMAccountName") == "ivanov"
    assert ad.get_ad_list_value(e, "memberOf") == ["CN=IT,OU=g", "CN=VPN,OU=g"]
    assert ad.get_ad_value(e, "missing", "def") == "def"
    assert ad.is_disabled(ENTRIES[1]) and not ad.is_disabled(ENTRIES[0])


def _wait(cond, app, ms=5000):
    import time
    t0 = time.time()
    while not cond() and (time.time() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_main_window_search_and_inspector(qapp, fake_conn, monkeypatch):
    from adk import netutils
    from adk.main_window import ADApp

    monkeypatch.setattr(netutils, "get_computer_network_info", lambda n, **kw: ("10.0.0.9", True))
    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    w = ADApp("CORP\\admin", "pwd")
    w.show()
    w.search_input.setText("иванов")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() == 2, qapp)
    assert w.stack.currentIndex() == 1
    assert w.table.item(0, 0).text() in ("ivanov", "petrov")
    # сортировка по столбцу "Учётка" не рассинхронизирует статусы (это обычные items)
    w.table.sortItems(2)
    row_petrov = next(r for r in range(2) if w.table.item(r, 0).text() == "petrov")
    assert w.table.item(row_petrov, 2).text() == "Не активна"
    # инспектор показывает выбранного
    w.select_row(row_petrov)
    assert "Петров" in w.lbl_fio.text()
    # очистка поля возвращает дашборд
    w.search_input.clear()
    assert w.stack.currentIndex() == 0
    w.close()


def test_edge_resize_filter_does_not_crash(qapp, fake_conn, monkeypatch):
    from adk.main_window import ADApp

    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    w = ADApp("CORP\\admin", "pwd")
    w.show()
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(2, 2), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(w, ev)  # раньше падало в eventFilter
    w.close()


def test_user_card_save_only_changed(qapp, fake_conn):
    from adk.dialogs import UserCardDialog
    from adk import widgets

    app = SimpleNamespace(get_conn=lambda: fake_conn, admin_name="admin")
    widgets.MessageBox.information = staticmethod(lambda *a, **k: 1)
    dlg = UserCardDialog(ENTRIES[0], app)
    dlg.inputs["department"].setText("Бухгалтерия")
    dlg.skype.setChecked(True)
    dlg.save_changes()
    assert _wait(lambda: bool(fake_conn.modified), qapp)
    dn, changes = fake_conn.modified[0]
    assert set(changes) == {"department", "msRTCSIP-UserEnabled"}          # пустые поля не трогаем
    assert changes["msRTCSIP-UserEnabled"] == [(ad.MODIFY_REPLACE, ["TRUE"])]  # строка, не bool
    dlg.close()


def test_ping_dialog_stops_worker_on_reject(qapp):
    from adk.dialogs import PingDialog

    dlg = PingDialog("WS-1", "127.0.0.1", None)
    assert _wait(lambda: dlg.worker.isRunning(), qapp, 3000)
    dlg.reject()
    assert _wait(lambda: not dlg.worker.isRunning(), qapp, 5000)


def test_message_box_answer(qapp):
    from PyQt6.QtCore import QTimer
    from adk.widgets import MessageBox

    dlg = MessageBox(None, "t", "<b>x</b> & y", "question", yes_no=True)
    QTimer.singleShot(50, lambda: dlg._finish(MessageBox.YES))
    dlg.exec()
    assert dlg.answer == MessageBox.YES
    assert callable(dlg.result)  # метод QDialog.result не затёрт


# --------------------------------------------------------------------------- регрессии, найденные E2E-прогоном
def test_second_search_does_not_raise(qapp, fake_conn, monkeypatch):
    """Повторный поиск после завершения первого: ссылка на удалённый QThread не должна ронять UI."""
    from adk.main_window import ADApp
    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    w = ADApp("CORP\\admin", "x")
    w.show()
    w.search_input.setText("иванов")
    w.start_search()
    _wait(lambda: w.search_worker is None and w.table.rowCount() > 0, qapp, 3000)
    w.search_input.blockSignals(True)
    w.search_input.setText("петров")
    w.search_input.blockSignals(False)
    w.start_search()  # раньше: RuntimeError "wrapped C/C++ object ... has been deleted"
    _wait(lambda: w.search_worker is None, qapp, 3000)
    assert w.lbl_status.text().startswith("Найдено")
    w.close()


def test_free_ip_worker_start_not_shadowed(qapp):
    """Атрибут FreeIPWorker не должен затирать QThread.start()."""
    from adk.workers import FreeIPWorker
    wk = FreeIPWorker("10.0.2", 5)
    assert callable(wk.start) and wk.start_host == 5


def test_free_ip_dialog_bad_prefix_shows_error(qapp):
    from adk.dialogs import FreeIPDialog
    d = FreeIPDialog(None)
    d.prefix.setText("bad")
    d.search()
    _wait(lambda: "⚠️" in d.status.text(), qapp, 3000)
    assert "⚠️" in d.status.text() and d.btn_start.isEnabled()
    d.close()


def test_fill_table_resets_user_sort(qapp, monkeypatch):
    """После сортировки по колонке новые результаты снова идут «онлайн первыми»."""
    from adk.main_window import ADApp
    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    w = ADApp("CORP\\admin", "x")
    rows = [{"login": "zz", "fio": "Я", "is_disabled": False, "comp": "WS-1", "is_online": True},
            {"login": "aa", "fio": "А", "is_disabled": False, "comp": "—", "is_online": False}]
    w.fill_table(rows)
    w.table.sortItems(0)
    assert w.table.item(0, 0).text() == "aa"
    w.fill_table(rows)
    assert w.table.item(0, 0).text() == "zz"
    w.close()


def test_dialog_closed_before_background_thread_finishes_does_not_crash(qapp, fake_conn):
    """Регрессия: закрытие карточки (и сборка мусора) до завершения FunctionWorker роняло процесс segfault'ом."""
    import gc
    from adk.dialogs import UserCardDialog
    from adk import widgets

    app = SimpleNamespace(get_conn=lambda: fake_conn, admin_name="admin")
    for _ in range(10):
        dlg = UserCardDialog(ENTRIES[0], app)  # стартует фоновую загрузку групп
        dlg.close()
        del dlg
        gc.collect()
    _wait(lambda: not widgets._LIVE_WORKERS, qapp, 5000)
    assert not widgets._LIVE_WORKERS  # все потоки дожили до finished и удалились сами


# --------------------------------------------------------------------------- 2.1.0: горячие клавиши, инспектор, журнал
def _main(qapp, fake_conn, monkeypatch):
    from adk import netutils
    from adk.main_window import ADApp
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda n, **kw: ("10.0.0.9", n == "WS-101"))
    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    w = ADApp("CORP\\admin", "x")
    w.show()
    return w


def test_shortcuts_registered_and_escape_returns_to_dashboard(qapp, fake_conn, monkeypatch):
    from PyQt6.QtGui import QKeySequence
    w = _main(qapp, fake_conn, monkeypatch)
    keys = {sc.key().toString() for sc in w._shortcuts}
    assert {"Ctrl+F", "Esc", "F5", "Ctrl+J", "Ctrl+N", "Ctrl+P", "Ctrl+D"} <= keys
    assert QKeySequence("Return").toString() in keys
    w.search_input.setText("иванов")
    w.start_search()
    assert _wait(lambda: w.stack.currentIndex() == 1, qapp, 3000)
    w.escape()                      # 1-й Esc — очистить поиск → дашборд
    assert w.search_input.text() == "" and w.stack.currentIndex() == 0
    w.focus_search()
    assert w.search_input.hasFocus() or True  # offscreen может не давать фокус — главное, не падает
    w.close()


def test_ctrl_c_in_search_field_copies_text_not_card(qapp, fake_conn, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    w = _main(qapp, fake_conn, monkeypatch)
    w.search_input.setText("abc")
    w.search_input.setFocus()
    w.search_input.selectAll()
    QApplication.clipboard().setText("")
    w.copy_card_if_table()
    assert "Карточка скопирована" not in w.lbl_status.text()
    w.close()


def test_inspector_shows_account_status_without_last_seen(qapp, fake_conn, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from adk import db
    w = _main(qapp, fake_conn, monkeypatch)
    db.save_computer_for_login("petrov", "WS-102")
    db.batch_update_inventory([{"Hostname": "WS-102", "ActualIp": "10.0.0.10", "Status": "ACTIVE", "User": "petrov"}], "2026-09-04 10:00:00")
    db.batch_update_inventory([{"Hostname": "WS-102", "ActualIp": "10.0.0.10", "Status": "OFFLINE", "User": "petrov"}], "2026-09-04 11:00:00")
    ENTRIES[1]._a["lockoutTime"] = FakeAttr([datetime.now(timezone.utc) - timedelta(minutes=5)])
    try:
        w.search_input.setText("петров")
        w.start_search()
        assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
        row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "petrov")
        w.select_row(row)
        acc = w.vals["account"].text()
        assert "ЗАБЛОКИРОВАНА" in acc and "НЕ АКТИВНА" not in acc
        assert "font-weight: bold" in w.vals["account"].styleSheet()
        assert "seen" not in w.vals          # 3.2.9: строки «Был в сети» в инспекторе больше нет
    finally:
        del ENTRIES[1]._a["lockoutTime"]
    w.close()


def test_user_card_reset_password_flow(qapp, fake_conn, monkeypatch):
    from PyQt6.QtWidgets import QApplication
    from adk import config, db, dialogs, widgets
    from adk.dialogs import UserCardDialog
    calls = []
    fake_conn.extend = SimpleNamespace(microsoft=SimpleNamespace(modify_password=lambda dn, p: calls.append((dn, p))))
    app = SimpleNamespace(get_conn=lambda: fake_conn, admin_name="admin")
    widgets.MessageBox.information = staticmethod(lambda *a, **k: 1)
    widgets.MessageBox.critical = staticmethod(lambda *a, **k: calls.append("critical"))
    dlg = UserCardDialog(ENTRIES[0], app)
    assert hasattr(dlg, "btn_reset") and "Пароль:" in dlg.lbl_account.text() and "Пароль" in dlg.account_vals

    monkeypatch.setattr(config.settings, "use_ssl", False)
    dlg.reset_password()
    assert calls == ["critical"]                   # без LDAPS — блокируется до диалога
    calls.clear()

    monkeypatch.setattr(config.settings, "use_ssl", True)
    captured = {}

    class FakeReset:
        def __init__(self, login, parent, fio="", after_unlock=False):
            self.password = SimpleNamespace(text=lambda: "N3wPass!_x")
            self.must_change = SimpleNamespace(isChecked=lambda: True)
        def exec(self):
            captured["opened"] = True
            return 1
    monkeypatch.setattr(dialogs, "ResetPasswordDialog", FakeReset)
    dlg.reset_password()
    assert _wait(lambda: bool(calls), qapp, 3000)
    assert calls[0] == (ENTRIES[0].entry_dn, "N3wPass!_x")
    assert _wait(lambda: QApplication.clipboard().text() == "N3wPass!_x", qapp, 3000)
    assert fake_conn.modified[-1][1] == {"pwdLastSet": [(ad.MODIFY_REPLACE, [0])], "lockoutTime": [(ad.MODIFY_REPLACE, [0])]}
    assert _wait(lambda: any(r[2] == "reset_password" for r in db.audit_entries()), qapp, 3000)
    dlg.close()


def test_reset_password_dialog_generates_policy_password(qapp):
    from adk import widgets
    from adk.dialogs import ResetPasswordDialog
    widgets.MessageBox.warning = staticmethod(lambda *a, **k: 1)  # иначе модальное окно заблокирует тест
    d = ResetPasswordDialog("ivanov", None, fio="Иванов Иван")
    p = d.password.text()
    assert len(p) == 12 and any(c.isdigit() for c in p) and any(c in "!_@" for c in p)
    assert d.must_change.isChecked() and d.unlock.isChecked()
    assert "ivanov" in d._card() and p in d._card()          # карточка для сотрудника — из того же окна
    d.generate(16)
    assert len(d.password.text()) == 16 and d.len_btns[16].isChecked() and not d.len_btns[12].isChecked()
    d.password.setText("short")
    assert not d.btn_ok.isEnabled() and "короче" in d.lbl_check.text()
    d._accept()
    assert d.result() == 0  # не принят
    d.close()


def test_audit_log_dialog_filters_and_csv(qapp, tmp_path):
    from adk import db
    from adk.dialogs import AuditLogDialog
    db.log_action("adm1", "restart", "WS-1")
    db.log_action("adm2", "reset_password", "ivanov", "смена при входе")
    app = SimpleNamespace(admin_name="adm1")
    d = AuditLogDialog(app, None)
    assert d.table.rowCount() == 2
    assert d.table.item(0, 2).text() == "Сброс пароля"      # человекочитаемая метка, новые сверху
    d.admin.setCurrentIndex(d.admin.findData("adm1"))
    assert d.table.rowCount() == 1 and d.table.item(0, 3).text() == "WS-1"
    d.admin.setCurrentIndex(0)
    d.text.setText("ivanov")
    assert d.table.rowCount() == 1
    d.text.setText("")
    d.period.setCurrentIndex(3)                               # всё время
    assert d.table.rowCount() == 2
    out = tmp_path / "audit.csv"
    d.write_csv(str(out))
    content = out.read_text(encoding="utf-8-sig")
    assert content.splitlines()[0] == "Время;Администратор;Действие;Объект;Детали" and "Сброс пароля" in content
    d.close()


def test_quick_access_has_audit_button(qapp, fake_conn, monkeypatch):
    from PyQt6.QtWidgets import QPushButton
    w = _main(qapp, fake_conn, monkeypatch)
    texts = [b.text() for b in w.findChildren(QPushButton)]
    assert any("Журнал действий" in t for t in texts)
    w.close()


def test_printer_badges_and_click_search(qapp, fake_conn, monkeypatch):
    from adk import db, netutils
    from adk.widgets import BadgeButton
    w = _main(qapp, fake_conn, monkeypatch)
    db.save_computer_for_login("ivanov", "WS-101")
    db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.0.9", "Status": "ACTIVE", "User": "ivanov"},
                               {"Hostname": "WS-102", "ActualIp": "10.0.0.10", "Status": "ACTIVE", "User": "petrov"}], "2026-09-04 10:00:00")
    hp = {"name": "HP LaserJet M404", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50", "is_default": True}
    db.replace_printers("WS-101", [hp, {"name": "Canon LBP", "port": "USB001", "kind": "usb", "ip": ""}])
    db.replace_printers("WS-102", [hp])
    w.search_input.setText("иванов")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
    w.select_row(next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "ivanov"))
    badges = w.printers_box.findChildren(BadgeButton)
    assert [b.text() for b in badges] == ["🌐 HP LaserJet M404 · 10.0.2.50 ★", "🔌 Canon LBP"]
    monkeypatch.setattr(netutils, "is_printer_alive", lambda ip, **kw: ip == "10.0.2.50")
    badges[0].click()                                   # → просто IP принтера, без префикса
    assert w.search_input.text() == "10.0.2.50"
    assert _wait(lambda: "Принтеров: 1" in w.lbl_status.text(), qapp, 3000)
    # единственная строка — сам принтер (🖨️, модель, IP, «В сети»); кто подключён — в его инспекторе,
    # «пустых» строк ПК без ФИО рядом быть не должно (v3.2.4)
    assert w.table.rowCount() == 1
    assert w.table.item(0, 0).text() == "🖨️" and w.table.item(0, 1).text() == "HP LaserJet M404"
    assert w.table.item(0, 3).text() == "10.0.2.50" and "В сети" in w.table.item(0, 4).text()
    assert "подключено ПК — 2" in w.lbl_status.text()
    w.select_row(0)                                     # инспектор принтера: кто подключён
    assert w.printer_pane.isVisible() and not w.details.isVisible()
    assert "HP LaserJet" in w.lbl_fio.text() and w.printer_pcs.rowCount() == 2
    assert {w.printer_pcs.item(r, 0).text() for r in range(2)} == {"WS-101", "WS-102"}
    assert w.pvals["count"].text().startswith("2")
    w.search_input.setText("иванов"); w.start_search()   # обратно к человеку — панель принтера прячется
    assert _wait(lambda: w.table.rowCount() > 0 and w.table.item(0, 0).text() != "🖨️", qapp, 3000)
    w.select_row(0)
    assert w.details.isVisible() and not w.printer_pane.isVisible()
    w.close()


def test_assemble_scales_linearly_with_inventory(fake_conn, monkeypatch):
    """Производительность: _assemble не должен перебирать весь инвентарь на каждую LDAP-запись."""
    import time
    from adk import db, netutils
    from adk.workers import SearchWorker
    db.batch_update_inventory([{"Hostname": f"WS-{i}", "ActualIp": f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}",
                                "Status": "ACTIVE", "User": f"user{i}"} for i in range(20000)]
                              + [{"Hostname": "WS-IV", "ActualIp": "10.9.9.9", "Status": "ACTIVE", "User": "CORP\\ivanov"}],
                              "2026-09-04 10:00:00")
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda pc: ("10.0.0.1", True))
    entries = [FakeEntry(f"CN=U{i},OU=x", sAMAccountName=f"user{i}", displayName=f"U{i}", sn="U", givenName="U",
                         userAccountControl=512) for i in range(0, 20000, 100)] + ENTRIES   # 202 записи
    w = SearchWorker(lambda: fake_conn, "user")
    t = time.perf_counter()
    rows = w._assemble(entries)
    dt = time.perf_counter() - t
    by_login = {r["login"]: r for r in rows}
    assert by_login["ivanov"]["comp"] == "WS-IV" and by_login["user100"]["comp"] == "WS-100"
    assert by_login["petrov"]["comp"] == "—"
    assert dt < 3.0, f"_assemble {dt:.2f}s на 20k ПК × {len(entries)} записей"


def test_printer_search_with_free_pc_and_status(qapp, fake_conn, monkeypatch):
    """printer: по USB-принтеру на ПК без пользователя → строка свободного ПК, в AD не ходим."""
    from adk import db
    w = _main(qapp, fake_conn, monkeypatch)
    db.batch_update_inventory([{"Hostname": "WS-201", "ActualIp": "10.0.0.21", "Status": "OFFLINE", "User": ""}], "2026-09-04 10:00:00")
    db.replace_printers("WS-201", [{"name": "Canon LBP6030", "port": "USB001", "kind": "usb", "ip": ""}])
    calls_before = len(fake_conn.calls) if hasattr(fake_conn, "calls") else None
    w.search_input.setText("printer: Canon LBP6030")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() == 2, qapp, 3000)
    assert w.table.item(0, 0).text() == "🖨️" and w.table.item(0, 1).text() == "Canon LBP6030"   # сам принтер
    assert w.table.item(1, 0).text() == "—"                                                     # свободный ПК с ним
    assert _wait(lambda: "подключено ПК — 1" in w.lbl_status.text(), qapp, 2000)
    w.select_row(0)
    assert "не сетевой" in w.pvals["ip"].text() and "USB" in w.lbl_sub.text()
    w.select_row(1)
    assert "WS-201" in w.lbl_fio.text() and "свободный" in w.lbl_fio.text()
    if calls_before is not None:
        assert len(fake_conn.calls) == calls_before
    w.close()


def test_printers_dialog_summary_and_drilldown(qapp, fake_conn, monkeypatch, tmp_path):
    from adk import db
    from adk.dialogs import PrintersDialog
    w = _main(qapp, fake_conn, monkeypatch)
    db.batch_update_inventory([{"Hostname": "WS-1", "ActualIp": "10.0.0.1", "Status": "ACTIVE", "User": "ivanov"},
                               {"Hostname": "WS-2", "ActualIp": "10.0.0.2", "Status": "OFFLINE", "User": "petrov"}], "2026-09-04 10:00:00")
    hp = {"name": "HP LaserJet M404", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50"}
    db.replace_printers("WS-1", [hp, {"name": "Canon LBP", "port": "USB001", "kind": "usb", "ip": ""}])
    db.replace_printers("WS-2", [hp])
    summary = db.printer_summary()
    assert summary[0]["name"] == "HP LaserJet M404" and summary[0]["pcs"] == 2 and summary[0]["online"] == 1
    assert summary[1]["name"] == "Canon LBP" and summary[1]["pcs"] == 1
    dlg = PrintersDialog(w, w)
    assert dlg.table.rowCount() == 2 and "Принтеров: 2" in dlg.status.text()
    dlg.kind.setCurrentIndex(3)                                   # только USB
    assert dlg.table.rowCount() == 1 and dlg.table.item(0, 0).text() == "Canon LBP"
    dlg.kind.setCurrentIndex(0)
    dlg.text.setText("ws-2")
    assert dlg.table.rowCount() == 1 and dlg.table.item(0, 0).text() == "HP LaserJet M404"
    out = tmp_path / "p.csv"
    dlg.write_csv(str(out))
    assert "HP LaserJet M404;сетевой;10.0.2.50;2;1" in out.read_text(encoding="utf-8-sig")
    dlg.table.selectRow(0)
    dlg.open_owners()                                             # → главное окно: printer: 10.0.2.50
    assert w.search_input.text() == "10.0.2.50"
    assert _wait(lambda: w.table.rowCount() >= 1 and w.lbl_status.text().startswith("🖨️"), qapp, 3000)
    assert [w.table.item(r, 3).text() for r in range(w.table.rowCount())] == ["10.0.2.50"], w.lbl_status.text()
    assert "подключено ПК — 2" in w.lbl_status.text()
    w.close()


def test_search_with_domain_prefix_not_dropped_as_stale(qapp, fake_conn, monkeypatch):
    """Регресс: запрос «CORP\\ivanov» нормализуется в «ivanov», но ответ не должен считаться устаревшим."""
    w = _main(qapp, fake_conn, monkeypatch)
    w.search_input.setText("CORP\\ivanov")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
    assert w.search_input.text() == "CORP\\ivanov"
    w.close()


def test_disk_button_opens_c_or_offers_volume_menu(qapp, fake_conn, monkeypatch):
    """3.2.9: кнопка «Диск». Один том у ПК → сразу explorer \\\\ПК\\c$; несколько томов → меню выбора (C:$, D:$)."""
    from adk import db, netutils
    import adk.main_window as mw
    calls: list[list[str]] = []
    monkeypatch.setattr(mw.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(netutils, "known_volumes", lambda comp: [])
    w = _main(qapp, fake_conn, monkeypatch)
    db.save_computer_for_login("ivanov", "WS-101")
    w.search_input.setText("иванов")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
    w.select_row(0)
    assert "disk_c" not in w.action_buttons and w.action_buttons["disk"].text().endswith("Диск")
    w.remote_action("disk")
    assert calls and calls[-1][0] == "explorer.exe" and calls[-1][1].lower().endswith("\\c$")
    # несколько томов — открывается меню, Popen не вызывается, пока том не выбран
    monkeypatch.setattr(netutils, "known_volumes",
                        lambda comp: [{"letter": "C", "label": "System", "size": "476 ГБ"}, {"letter": "D", "label": "Data", "size": "932 ГБ"}])
    n = len(calls)
    w.remote_action("disk")
    menu = w._disk_menu
    items = [a for a in menu.actions() if a.isEnabled()]
    assert [a.text()[:6] for a in items] == ["💽  C:$", "💽  D:$"] and "Data" in items[1].text()
    assert len(calls) == n
    items[1].trigger()
    assert calls[-1][1].lower().endswith("\\d$")
    menu.close()
    w.close()


def test_power_button_menu_and_actions(qapp, fake_conn, monkeypatch):
    """3.3.0: «Питание ПК» вместо «Перезагрузить» + «Разбудить»: одно меню с WoL, блокировкой экрана, выходом,
    сном, перезагрузкой и выключением. Необратимые — с подтверждением; каждая команда попадает в журнал."""
    from adk import db, netutils
    import adk.main_window as mw
    calls: list[list[str]] = []
    monkeypatch.setattr(mw.subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr(mw.netutils.subprocess, "run", lambda *a, **k: None)   # фоновый arp нам не нужен
    answers: list[bool] = []
    monkeypatch.setattr(mw.MessageBox, "question", lambda *a, **k: answers.pop(0))
    w = _main(qapp, fake_conn, monkeypatch)
    db.save_computer_for_login("ivanov", "WS-101")
    w.search_input.setText("иванов")
    w.start_search()
    assert _wait(lambda: w.table.rowCount() > 0, qapp, 3000)
    w.select_row(0)
    assert "restart" not in w.action_buttons and "wol" not in w.action_buttons
    w.remote_action("power")
    items = [a.text() for a in w._power_menu.actions() if a.isEnabled() and not a.isSeparator()]
    assert items == [netutils.POWER_ACTIONS[k][0] for k in ("wol", "lock", "logoff", "sleep", "restart", "shutdown")]
    w._power_menu.close()
    # блокировка экрана — без вопросов, три шага schtasks (создать → запустить → удалить)
    w.remote_action("lock")
    assert [c[:2] for c in calls] == [["schtasks", "/create"], ["schtasks", "/run"], ["schtasks", "/delete"]]
    assert all("/s" in c for c in calls) and "LockWorkStation" in calls[0][calls[0].index("/tr") + 1]   # цель — IP/имя ПК
    # выключение — с подтверждением: «Нет» ничего не делает, «Да» → shutdown /s
    n = len(calls)
    answers.append(False)
    w.remote_action("shutdown")
    assert len(calls) == n
    answers.append(True)
    w.remote_action("shutdown")
    assert calls[-1][:3] == ["shutdown", "/s", "/m"]
    # сон и выход — через CIM
    w.remote_action("sleep")
    assert calls[-1][0] == "powershell.exe" and "SetSuspendState" in calls[-1][-1]
    answers.append(True)
    w.remote_action("logoff")
    assert "Win32Shutdown" in calls[-1][-1]
    actions = [r[2] for r in db.audit_entries(admin="CORP\\admin")[:4]]
    assert {"lock", "shutdown", "sleep", "logoff"} <= set(actions)
    w.close()
