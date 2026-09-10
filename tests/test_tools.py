"""Инструменты набора: заметки, таймлайн ПК, подсказки, роли, плагины, обновления, экспорт, трей/хоткей,
системная тема, i18n, сравнение групп, массовые операции.

Как читать: каждый тест — «дано → действие → проверка». Виджеты offscreen, AD — заглушки.
"""
import os
import pytest
from types import SimpleNamespace

from adk import access, config, db, export, i18n, plugins, theme, updates
from adk.tray import parse_hotkey


def _inv(comp, login, ip, online=True):
    return {"Hostname": comp, "User": login, "ActualIp": ip, "Status": "ACTIVE" if online else "OFFLINE", "LastLogon": "—"}


ROWS = [{"login": "ivanov", "full_fio": "Иванов Иван Иванович", "is_disabled": False, "comp": "PC-01", "ip": "10.0.0.5",
         "is_online": True, "dept": "ИТ", "printers": [{"name": "HP LaserJet", "port": "192.168.1.50"}]},
        {"login": "petrov", "fio": "Петров П.П.", "is_disabled": True, "comp": "—", "ip": "Не найден", "is_online": False}]


class _Entry:
    def __init__(self, dn, login, groups):
        self.entry_dn = dn
        self._a = {"sAMAccountName": SimpleNamespace(value=login, values=[login]),
                   "memberOf": SimpleNamespace(value=groups[0] if groups else None, values=list(groups)),
                   "userAccountControl": SimpleNamespace(value=512, values=[512]), "cn": SimpleNamespace(value=login, values=[login])}

    def __getitem__(self, k):
        return self._a[k]


class _Conn:
    def __init__(self):
        self.entries, self.result, self.modified = [], {"controls": {}, "description": "success"}, []

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        self.entries = [_Entry("CN=VPN,OU=g", "VPN", []), _Entry("CN=IT,OU=g", "IT", [])] if "group" in flt \
            else [_Entry("CN=ref,OU=x", "ref", ["CN=VPN,OU=g", "CN=IT,OU=g"])]
        return True

    def modify(self, dn, changes):
        self.modified.append((dn, changes))
        return True

    def unbind(self):
        return True


def _stub_app():
    conn = _Conn()
    return SimpleNamespace(get_conn=lambda: conn, admin_name="admin", conn=conn)


def _spin(app, ms=400):
    import time
    t0 = time.time()
    while (time.time() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.01)


# ------------------------------------------------------------------ 1. заметки, таймлайн, подсказки
def test_notes_crud_and_count():
    nid = db.add_note("CORP\\Ivanov", "  выдан ноутбук  ", "admin")
    assert nid > 0
    notes = db.notes_for("ivanov")
    assert len(notes) == 1 and notes[0]["text"] == "выдан ноутбук" and notes[0]["admin"] == "admin"
    db.add_note("PC-0101.corp", "шумит кулер", "admin", kind="pc")
    assert db.notes_for("pc-0101", kind="pc")[0]["text"] == "шумит кулер"
    assert db.notes_count(["ivanov", "petrov"]) == {"ivanov": 1}
    db.delete_note(nid)
    assert db.notes_for("ivanov") == [] and db.notes_count(["ivanov"]) == {}
    # пустая заметка не сохраняется
    assert db.add_note("ivanov", "   ", "admin") == 0 and db.notes_for("ivanov") == []


def test_pc_history_timeline_records_changes_only():
    db.batch_update_inventory([_inv("PC-01", "ivanov", "10.0.0.5")], "2026-01-01 10:00:00")
    db.batch_update_inventory([_inv("PC-01", "ivanov", "10.0.0.5")], "2026-01-02 10:00:00")  # без изменений
    db.batch_update_inventory([_inv("PC-01", "petrov", "10.0.0.5")], "2026-01-03 10:00:00")  # сменился пользователь
    db.batch_update_inventory([_inv("PC-01", "petrov", "10.0.0.7")], "2026-01-04 10:00:00")  # сменился IP
    hist = db.history_for(computer_name="PC-01")
    assert [h["login"] for h in hist] == ["petrov", "petrov", "ivanov"]
    assert hist[-1]["first_seen"] == "2026-01-01 10:00:00" and hist[-1]["last_seen"] == "2026-01-02 10:00:00"
    assert hist[0]["ip"] == "10.0.0.7"
    assert len(db.history_for(login="ivanov")) == 1
    assert db.history_for(login="ivanov", computer_name="PC-01")[0]["comp"] == "PC-01"


def test_suggestions_ranked_by_hits_and_include_history():
    db.remember_terms(["Иванов", "ivanov", "PC-01", "x", ""])  # короткие/пустые отбрасываются
    db.remember_terms(["Иванов"])
    db.save_search_query("сидоров", "admin")
    s = db.suggestions()
    assert s[0] == "сидоров"  # недавние запросы — первыми
    assert s[1] == "Иванов" and "PC-01" in s and "x" not in s
    assert len(s) == len(set(s))


# ------------------------------------------------------------------ 2. роли
def test_access_evaluate_groups(monkeypatch):
    monkeypatch.setattr(config.settings, "readonly", False)
    monkeypatch.setattr(config.settings, "readonly_group", "ADK-ReadOnly")
    monkeypatch.setattr(config.settings, "admin_groups", ())
    assert access.evaluate_groups(["IT", "adk-readonly"]) == (True, "член группы ADK-ReadOnly")
    assert access.evaluate_groups(["IT"]) == (False, "")
    monkeypatch.setattr(config.settings, "admin_groups", ("Helpdesk", "Domain Admins"))
    ro, why = access.evaluate_groups(["IT"])
    assert ro and "Helpdesk" in why
    assert access.evaluate_groups(["helpdesk"]) == (False, "")
    monkeypatch.setattr(config.settings, "readonly", True)
    assert access.evaluate_groups(["Domain Admins"])[0]


def test_access_can_and_reset():
    access.reset()
    assert not access.is_readonly() and access.can("restart")
    access.set_readonly(True, "test")
    try:
        assert access.is_readonly() and access.reason() == "test"
        assert not access.can("restart") and not access.can("bulk_disable") and not access.can("plugin_modifying")
        assert access.can("ping") and access.can("export")
    finally:
        access.reset()


# ------------------------------------------------------------------ 3. плагины, обновления, экспорт
def test_plugins_load_run_and_isolate_broken(tmp_path):
    (tmp_path / "10_good.py").write_text(
        "from adk.plugins import Action\n"
        "class Hello(Action):\n"
        "    name = 'Hello'; icon = '👋'; needs_pc = True; order = 5\n"
        "    def run(self, ctx):\n        return 'hi ' + ctx['comp']\n"
        "class Boom(Action):\n"
        "    name = 'Boom'; needs_pc = False; modifying = True; order = 1\n"
        "    def run(self, ctx):\n        raise RuntimeError('bad')\n", encoding="utf-8")
    (tmp_path / "20_broken.py").write_text("import this_module_does_not_exist\n", encoding="utf-8")
    (tmp_path / "_ignored.py").write_text("raise SystemExit\n", encoding="utf-8")
    acts = plugins.load_plugins(str(tmp_path))
    assert [a.name for a in acts] == ["Boom", "Hello"]
    hello = acts[1]
    assert hello.label == "👋 Hello"
    assert not hello.enabled({"comp": ""}) and hello.enabled({"comp": "PC-01"})
    assert plugins.run_action(hello, {"comp": "PC-01"}) == (True, "hi PC-01")
    ok, msg = plugins.run_action(acts[0], {})
    assert not ok and "bad" in msg
    assert plugins.load_plugins(str(tmp_path / "nope")) == []


def test_plugins_write_example(tmp_path):
    p = plugins.write_example(str(tmp_path / "plugins"))
    assert p and os.path.exists(p) and os.path.basename(p).startswith("_")
    open(p, "a", encoding="utf-8").write("# edited\n")
    assert plugins.write_example(str(tmp_path / "plugins")) == p and open(p, encoding="utf-8").read().endswith("# edited\n")  # не перезаписывает
    assert plugins.load_plugins(str(tmp_path / "plugins")) == []  # `_`-файлы не загружаются


def test_updates_version_compare_and_file(tmp_path):
    assert updates.parse_version("3.1.0") == (3, 1, 0) and updates.parse_version("v3.1") == (3, 1)
    assert updates.is_newer("3.1.0", "3.0.0") and not updates.is_newer("3.0.0", "3.0.0") and not updates.is_newer("2.9.9", "3.0.0")
    assert not updates.is_newer("garbage", "3.0.0")
    f = tmp_path / "version.txt"
    f.write_text("\ufeff9.9.9\n\\\\share\\ADK\\ADK.exe\n", encoding="utf-8")
    assert updates.read_version_file(str(f)) == ("9.9.9", "\\\\share\\ADK\\ADK.exe")
    (tmp_path / "v2.txt").write_text("9.9.8", encoding="utf-8")
    assert updates.read_version_file(str(tmp_path / "v2.txt")) == ("9.9.8", str(tmp_path))
    assert updates.read_version_file(str(tmp_path / "missing.txt")) == ("", "")
    assert updates.read_version_file("") == ("", "")


def test_updates_check_uses_settings(monkeypatch, tmp_path):
    f = tmp_path / "version.txt"
    f.write_text("99.0.0\nhttps://example.invalid/adk\n", encoding="utf-8")
    monkeypatch.setattr(config.settings, "version_file", str(f))
    info = updates.check()
    assert info and info["version"] == "99.0.0" and info["location"].endswith("/adk")
    f.write_text("0.0.1\n", encoding="utf-8")
    assert updates.check() is None
    monkeypatch.setattr(config.settings, "version_file", "")
    assert updates.check() is None


def test_export_rows_to_table_and_files(tmp_path):
    t = export.rows_to_table(ROWS)
    assert t[0][0] == "Логин" and len(t) == 3
    r1 = dict(zip(t[0], t[1]))
    assert r1["ФИО"] == "Иванов Иван Иванович" and r1["Учётка"] == "Активна" and r1["Сеть"] == "В сети" and "HP LaserJet" in r1["Принтеры"]
    r2 = dict(zip(t[0], t[2]))
    assert r2["ФИО"] == "Петров П.П." and r2["Учётка"] == "Не активна"
    csv_path = tmp_path / "out.csv"
    assert export.export_rows(ROWS, str(csv_path)) == 2
    raw = csv_path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"ivanov;" in raw
    openpyxl = pytest.importorskip("openpyxl")
    xlsx = tmp_path / "out.xlsx"
    assert export.export_rows(ROWS, str(xlsx)) == 2
    ws = openpyxl.load_workbook(xlsx).active
    assert ws.max_row == 3 and ws.cell(1, 1).value == "Логин" and ws.cell(2, 1).value == "ivanov"


# ------------------------------------------------------------------ 4. хоткей, тема, i18n
def test_parse_hotkey():
    mods, vk = parse_hotkey("Ctrl+Shift+A")
    assert mods == 0x0002 | 0x0004 and vk == ord("A")
    assert parse_hotkey("Win+Space") == (0x0008, 0x20)
    assert parse_hotkey("Alt+F9") == (0x0001, 0x78)
    assert parse_hotkey("A") is None and parse_hotkey("Ctrl+") is None and parse_hotkey("") is None
    assert parse_hotkey("Ctrl+Shift+Пробел") is None


def test_design_for_system(monkeypatch):
    base = {"bg_style": "background-color: #123456;", "is_dark": False, "font_family": "Segoe UI", "font_size": 10,
            "accent_color": "#2563eb", "follow_system": False}
    assert theme.design_for_system(base) == base
    d = dict(base, follow_system=True)
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: True)
    dark = theme.design_for_system(d)
    assert dark["is_dark"] is True and dark["font_family"] == "Segoe UI" and dark["bg_style"] != base["bg_style"]
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: False)
    assert theme.design_for_system(d)["is_dark"] is False
    monkeypatch.setattr(theme, "system_prefers_dark", lambda: None)
    assert theme.design_for_system(d)["bg_style"] == base["bg_style"]  # система не сообщила — оставляем как есть


def test_i18n_tr_switch():
    try:
        i18n.set_language("en")
        assert i18n.language() == "en" and i18n.tr("Найти") == "Find" and i18n.tr("нет такого ключа") == "нет такого ключа"
        i18n.set_language("ru")
        assert i18n.tr("Найти") == "Найти"
        i18n.set_language("xx")
        assert i18n.language() == "ru"
    finally:
        i18n.set_language("ru")


# ------------------------------------------------------------------ 5. диалоги набора и главное окно
def test_group_compare_diff_and_dialog(qapp, monkeypatch):
    from adk.ad import MODIFY_ADD as ad_add
    from adk.tools import GroupCompareDialog
    add, rm, same = GroupCompareDialog.diff({"IT": "CN=IT", "Old": "CN=Old"}, {"IT": "CN=IT", "VPN": "CN=VPN"})
    assert (add, rm, same) == (["VPN"], ["Old"], ["IT"])
    app = _stub_app()
    dlg = GroupCompareDialog(_Entry("CN=t,OU=x", "target", ["CN=IT,OU=g"]), app)
    dlg.ref.setText("ref")
    dlg.load_ref()
    _spin(qapp)
    assert dlg.missing.count() == 1 and dlg.missing.item(0).text() == "VPN" and dlg.common.count() == 1
    from adk.widgets import MessageBox
    monkeypatch.setattr(MessageBox, "question", staticmethod(lambda *a, **k: True))
    monkeypatch.setattr(MessageBox, "information", staticmethod(lambda *a, **k: None))
    dlg.apply(dlg.missing, ad_add)
    _spin(qapp)
    assert app.conn.modified and app.conn.modified[0][0] == "CN=VPN,OU=g"
    assert dlg.common.count() == 2 and dlg.missing.count() == 0
    assert db.audit_entries()[0][2] == "groups_sync"
    dlg.close()


def test_notes_history_health_dialogs_smoke(qapp):
    from adk.tools import HealthDialog, HistoryDialog, NotesDialog
    app = _stub_app()
    n = NotesDialog("ivanov", "user", app, title="Иванов")
    n.edit.setPlainText("тестовая заметка")
    n.add()
    assert n.list.count() == 1 and db.notes_for("ivanov")[0]["text"] == "тестовая заметка"
    n.close()
    db.batch_update_inventory([_inv("PC-01", "ivanov", "10.0.0.5")], "2026-01-01 10:00:00")
    h = HistoryDialog("ivanov", "PC-01")
    assert h.table.rowCount() == 1 and h.table.item(0, 0).text() == "PC-01"
    h.close()
    hd = HealthDialog("PC-01", app)
    _spin(qapp, 800)
    assert hd.lbl_os.text()  # либо название ОС, либо текст ошибки (не Windows)
    hd.close()


def test_bulk_dialog_disable_and_readonly(qapp, monkeypatch):
    from adk.tools import BulkOperationsDialog
    from adk.widgets import MessageBox
    app = _stub_app()
    users = [{"entry": _Entry("CN=a,OU=x", "a", []), "login": "a", "full_fio": "А"},
             {"entry": _Entry("CN=b,OU=x", "b", []), "login": "b", "full_fio": "Б"},
             {"entry": None, "login": "—", "full_fio": ""}]
    monkeypatch.setattr(MessageBox, "question", staticmethod(lambda *a, **k: True))
    warned = []
    monkeypatch.setattr(MessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))
    dlg = BulkOperationsDialog(users, app)
    _spin(qapp)
    assert dlg.table.rowCount() == 2 and dlg.groups.count() == 2
    dlg.op.setCurrentIndex(dlg.op.findData("disable"))
    dlg.run_op()
    _spin(qapp)
    assert len(app.conn.modified) == 2 and all(ok for _, ok, _ in dlg.results)
    assert {r[2] for r in db.audit_entries()} >= {"bulk_disable"}
    access.set_readonly(True, "test")
    try:
        dlg.run_op()
        assert warned
    finally:
        access.reset()
    dlg.close()


def test_main_window_kit_features(qapp, monkeypatch, tmp_path):
    from adk import ad, netutils
    from adk.main_window import ADApp
    from tests.test_gui import ENTRIES, FakeConn
    conn = FakeConn(ENTRIES)
    monkeypatch.setattr(ad, "make_connection", lambda *a, **k: conn)
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda n, **kw: ("10.0.0.9", True))
    monkeypatch.setattr(ADApp, "start_scan", lambda self: None)
    monkeypatch.setattr(config.settings, "minimize_to_tray", False)
    w = ADApp("CORP\\admin", "pwd")
    w.show()
    assert "Полный" in w.lbl_role_status.text()
    w.search_input.setText("иванов")
    w.start_search()
    _spin(qapp, 1500)
    assert w.table.rowCount() == 2
    sugg = {t.casefold() for t in db.suggestions()}
    assert {"ivanov", "иванов", "petrov", "петров"} <= sugg
    w.table.selectAll()
    assert {u["login"] for u in w.selected_users()} == {"ivanov", "petrov"}
    # экспорт без диалога
    out = tmp_path / "res.csv"
    from PyQt6.QtWidgets import QFileDialog
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "")))
    w.export_results()
    assert out.exists() and db.audit_entries()[0][2] == "export"
    # режим только чтение прячет изменяющие кнопки
    access.set_readonly(True, "test")
    try:
        w.apply_access()
        assert "чтение" in w.lbl_role_status.text().lower() and not w.action_buttons["power"].isVisible()
    finally:
        access.reset()
        w.apply_access()
    assert w.action_buttons["power"].isVisible() and "health" in w.action_buttons
    w.quit_app()


def test_plugins_template_is_self_documented_and_toggle(tmp_path):
    """3.5.1: «Создать шаблон плагина» — файл с документацией внутри, компилируется, выключен («_»),
    включается переименованием; list_plugin_files видит и выключенные файлы."""
    d = str(tmp_path / "plugins")
    p = plugins.write_template(d)
    assert p and os.path.basename(p) == "_template_plugin.py"
    src = open(p, encoding="utf-8").read()
    compile(src, p, "exec")                                             # валидный python
    for word in ("needs_pc", "modifying", "ctx[\"comp\"]", "conn_factory", "run(ctx)", "enabled(ctx)"):
        assert word in src                                              # документация — внутри файла
    p2 = plugins.write_template(d)                                      # второй раз — не затирает первый
    assert p2 != p and os.path.exists(p)
    files = plugins.list_plugin_files(d)
    assert [f["enabled"] for f in files] == [False, False] and files[0]["actions"][0].name == "Профиль пользователя"
    assert plugins.load_plugins(d) == []                                # выключенные не грузятся
    new = plugins.set_enabled(p, True)
    assert os.path.basename(new) == "template_plugin.py" and [a.name for a in plugins.load_plugins(d)] == ["Профиль пользователя"]
    assert os.path.basename(plugins.set_enabled(new, False)) == "_template_plugin.py"
