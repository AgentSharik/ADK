"""Юнит-тесты не требующие домена: MD4, БД, утилиты, разбор ping, формирование команд."""
import os
import sqlite3

import pytest

from adk import ad, config, db, dhcp, health, netutils
from adk.md4 import PureMD4

RFC1320 = {
    b"": "31d6cfe0d16ae931b73c59d7e0c089c0",
    b"a": "bde52cb31de33e46245e05fbdbd6fb24",
    b"abc": "a448017aaf21d8525fc10ae87aa6729d",
    b"message digest": "d9130a8164549fe818874806e1c7014b",
    b"abcdefghijklmnopqrstuvwxyz": "d79e1c308aa5bbcdeea8ed63df412da9",
    b"12345678901234567890123456789012345678901234567890123456789012345678901234567890":
        "e33b4ddc9c38f2199c3e7b164fcc0536",
}


@pytest.mark.parametrize("data,expected", RFC1320.items())
def test_md4_rfc_vectors(data, expected):
    assert PureMD4(data).hexdigest() == expected


def test_md4_nt_hash():
    # NT-хэш пароля "password"
    assert PureMD4("password".encode("utf-16-le")).hexdigest() == "8846f7eaee8fb117ad06bdd830b7586c"


# --------------------------------------------------------------------------- db
def test_mapping_roundtrip():
    db.save_computer_for_login("DOMAIN\\Ivanov", "ws-101.example.local")
    assert db.get_computer_by_login("ivanov") == "WS-101"
    assert db.get_computer_by_login("DOMAIN\\IVANOV") == "WS-101"


def test_like_escape_underscore_is_literal():
    db.save_computer_for_login("a", "WS_1")
    db.save_computer_for_login("b", "WSX1")
    assert db.logins_by_computer_or_ip("WS_1") == {"a"}


def test_search_history_dedup():
    db.save_search_query("Иванов", "admin")
    db.save_search_query("иванов", "admin")   # дубль без учёта регистра
    db.save_search_query("Петров", "admin")
    db.save_search_query("x", "admin")        # слишком короткий — игнорируется
    recent = db.get_recent_searches()
    assert len(recent) == 2 and {r.lower() for r in recent} == {"иванов", "петров"}


def test_batch_update_inventory_and_stale_removal():
    db.batch_update_inventory([{"Hostname": "WS-1", "ActualIp": "10.0.0.1", "Status": "ACTIVE", "User": "u1"},
                               {"Hostname": "WS-2", "ActualIp": "10.0.0.2", "Status": "OFFLINE", "User": ""}],
                              "2026-01-01 00:00:00")
    assert db.get_inventory_stats()[0] == 1
    db.batch_update_inventory([{"Hostname": "WS-1", "ActualIp": "10.0.0.1", "Status": "OFFLINE", "User": ""}],
                              "2026-01-02 00:00:00")
    rows = db.inventory_rows("all")
    assert [r[0] for r in rows] == ["WS-1"]          # WS-2 исчез из AD → удалён
    assert rows[0][3] == "u1"                          # пустой User не затирает известного
    seen = db.db_execute_with_retry("SELECT last_seen_online FROM pc_inventory", fetch="one")[0]
    assert seen == "2026-01-01 00:00:00"               # offline не обновляет last_seen_online


# --------------------------------------------------------------------------- ad helpers
def test_qualify_user(monkeypatch):
    monkeypatch.setattr(ad.settings, "domain_netbios", "CORP")
    assert ad.qualify_user("ivanov") == "CORP\\ivanov"
    assert ad.qualify_user("OTHER\\ivanov") == "OTHER\\ivanov"


def test_sanitize_sam_account_name():
    assert ad.sanitize_sam_account_name("Константинопольский_К") == "konstantinopolskij_k"[:20]
    assert len(ad.sanitize_sam_account_name("Константинопольский_К")) <= 20
    with pytest.raises(ValueError):
        ad.sanitize_sam_account_name("!!!")


def test_generate_secure_password_policy():
    for _ in range(20):
        p = ad.generate_secure_password()
        assert len(p) == 12 and any(c.isupper() for c in p) and any(c.isdigit() for c in p)


def test_describe_ldap_error():
    assert "пароль" in ad.describe_ldap_error(Exception("80090308: LdapErr: DSID-0C09044E, data 52e, v4563")).lower()
    assert "отключена" in ad.describe_ldap_error(Exception("data 533")).lower()


# --------------------------------------------------------------------------- netutils
def test_is_ip_query_vs_phone():
    assert netutils.is_ip_query("10.0.2")
    assert netutils.is_ip_query("192.168.1.15")
    assert not netutils.is_ip_query("2554567")       # телефон — не IP
    assert not netutils.is_ip_query("ws-101")


def test_remote_command_tokens():
    assert netutils.remote_command("restart", "WS-1") == ["shutdown", "/r", "/m", "\\\\WS-1", "/t", "0", "/f"]
    assert netutils.remote_command("compmgmt", "WS-1")[-1] == "/computer=\\\\WS-1"
    with pytest.raises(ValueError):
        netutils.remote_command("restart", "WS-1; del *")


@pytest.mark.parametrize("line,success", [
    ("Ответ от 10.0.0.5: число байт=32 время=1мс TTL=128", True),
    ("Reply from 10.0.0.5: bytes=32 time<1ms TTL=128", True),
    ("Превышен интервал ожидания для запроса.", False),
    ("Request timed out.", False),
    ("Ответ от 10.0.0.1: Заданный узел недоступен.", False),
])
def test_parse_ping_line(line, success):
    parsed = netutils.parse_ping_line(line, "10.0.0.5")
    assert parsed is not None and parsed["success"] is success


def test_parse_ping_info_line():
    assert netutils.parse_ping_line("Pinging 10.0.0.5 with 32 bytes of data:", "x")["is_info"]


def test_summarize_specs_no_invented_values():
    d = netutils._structure_rows([
        ["Операционная система", "Название", "0", "Windows 10 Pro"],
        ["Процессор", "Название", "0", "Intel Core i5"],
        ["Диск", "Наименование", "0", "Samsung SSD"],
    ])
    s = netutils.summarize_specs(d)
    assert "ОЗУ: Н/Д" in s and "Н/Д" in s.split("Диски:")[1]
    assert "447" not in s and "8.0" not in s and "DDR" not in s


# --------------------------------------------------------------------------- регрессии E2E, раунд 2
def test_qualify_user_upn_and_bare():
    from adk import ad, config
    d = config.settings.domain_netbios
    assert ad.qualify_user("admin@corp.example") == f"{d}\\admin"
    assert ad.qualify_user("  admin ") == f"{d}\\admin"
    assert ad.qualify_user("OTHER\\admin") == "OTHER\\admin"


def test_dn_to_cn_escaped_comma():
    from adk import ad
    assert ad.dn_to_cn("CN=Smith\\, John,OU=g,DC=x") == "Smith, John"
    assert ad.dn_to_cn("CN=IT,OU=g") == "IT"


def test_ping_parse_iputils_format():
    from adk import netutils
    p = netutils.parse_ping_line("64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.030 ms", "127.0.0.1")
    assert p["success"] and p["ttl"] == "64" and p["bytes"] == "64" and p["time"] == "0.030ms"


def test_specs_ram_in_bytes_and_printers(tmp_path, monkeypatch):
    from adk import config, netutils
    (tmp_path / "WS-1.csv").write_text(
        "Оперативная память;Размер;0;8589934592\nПринтер;Название;0;HP LaserJet\nПринтер;Порт;0;USB001\n",
        encoding="cp1251")
    monkeypatch.setattr(config.settings, "invent_hardware_dir", str(tmp_path))
    assert "8.0 ГБ" in netutils.get_computer_specs_summary("WS-1")
    assert netutils.get_computer_printers("WS-1") == ["HP LaserJet (USB)"]


def test_clear_credentials_removes_corrupted_file(tmp_path, monkeypatch):
    from adk import credentials
    f = tmp_path / "cred.json"
    f.write_text("{corrupted", encoding="utf-8")
    monkeypatch.setattr(credentials, "CRED_FILE", str(f))
    assert credentials.load_credentials() == (None, None)
    credentials.clear_credentials()
    assert not f.exists()


def test_search_worker_reports_truncation(monkeypatch):
    from adk import config
    from adk.workers import SearchWorker
    from test_gui import FakeConn, FakeEntry
    big = [FakeEntry(f"CN=U{i}", sAMAccountName=f"u{i}", displayName=f"U {i}", userAccountControl=512)
           for i in range(config.SEARCH_RESULT_LIMIT + 5)]
    conn = FakeConn(big)
    got = []
    w = SearchWorker(lambda: conn, "us")
    w.results_ready.connect(lambda rows, q, trunc: got.append((len(rows), trunc)))
    monkeypatch.setattr("adk.netutils.get_computer_network_info", lambda n, **kw: ("Не найден", False))
    w.run()
    assert got == [(config.SEARCH_RESULT_LIMIT, True)]
    got.clear()
    w2 = SearchWorker(lambda: FakeConn(big[:3]), "us")
    w2.results_ready.connect(lambda rows, q, trunc: got.append((len(rows), trunc)))
    w2.run()
    assert got == [(3, False)]


# --------------------------------------------------------------------------- 2.1.0: статус УЗ, сброс пароля, журнал
def test_filetime_conversion():
    from datetime import datetime, timezone
    from adk import ad
    # 133_000_000_000_000_000 → 2022-06-30 (проверено: (ft-116444736000000000)/1e7 = 1655526400)
    dt = ad.filetime_to_datetime(133000000000000000)
    assert dt == datetime(2022, 6, 18, 4, 26, 40, tzinfo=timezone.utc)
    assert ad.filetime_to_datetime(0) is None
    assert ad.filetime_to_datetime(0x7FFFFFFFFFFFFFFF) is None
    assert ad.filetime_to_datetime("мусор") is None


def test_account_status_password_expiry_and_lockout():
    from datetime import datetime, timedelta, timezone
    from adk import ad
    from test_gui import FakeEntry
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    set_80_days_ago = now - timedelta(days=80)
    e = FakeEntry("CN=x", userAccountControl=512, pwdLastSet=set_80_days_ago, badPwdCount=3)
    st = ad.account_status(e, 90, now=now)
    assert st["pwd_days_left"] == 10 and "истекает через 10" in st["pwd_text"] and not st["pwd_warn"]
    assert st["bad_pwd"] == 3 and not st["locked"]
    st = ad.account_status(FakeEntry("CN=x", userAccountControl=512, pwdLastSet=now - timedelta(days=95)), 90, now=now)
    assert st["pwd_days_left"] == -5 and "ИСТЁК" in st["pwd_text"] and st["pwd_warn"]
    st = ad.account_status(FakeEntry("CN=x", userAccountControl=512 | 0x10000, pwdLastSet=set_80_days_ago), 90, now=now)
    assert st["pwd_text"].startswith("не истекает")
    st = ad.account_status(FakeEntry("CN=x", userAccountControl=512, pwdLastSet=0), 90, now=now)
    assert "смена при следующем входе" in st["pwd_text"] and st["pwd_warn"]
    st = ad.account_status(FakeEntry("CN=x", userAccountControl=512, lockoutTime=now - timedelta(hours=1),
                                     accountExpires=now - timedelta(days=1)), 90, now=now)
    assert st["locked"] and "ЗАБЛОКИРОВАНА" in st["locked_text"] and st["expired"]
    st = ad.account_status(FakeEntry("CN=x", userAccountControl=512), 90, now=now)
    assert st["pwd_text"] == "—" and not st["locked"] and st["expires_text"] == ""


def test_reset_password_requires_ldaps_and_sets_flags(monkeypatch):
    from types import SimpleNamespace
    from adk import ad, config
    calls = []
    conn = SimpleNamespace(
        extend=SimpleNamespace(microsoft=SimpleNamespace(modify_password=lambda dn, p: calls.append(("pwd", dn, p)))),
        modify=lambda dn, ch: calls.append(("modify", dn, ch)))
    monkeypatch.setattr(config.settings, "use_ssl", False)
    with pytest.raises(RuntimeError):
        ad.reset_password(conn, "CN=x", "Secret1!")
    assert not calls
    monkeypatch.setattr(config.settings, "use_ssl", True)
    ad.reset_password(conn, "CN=x", "Secret1!", must_change=True, unlock=True)
    assert calls[0] == ("pwd", "CN=x", "Secret1!")
    assert calls[1][2] == {"pwdLastSet": [(ad.MODIFY_REPLACE, [0])], "lockoutTime": [(ad.MODIFY_REPLACE, [0])]}
    calls.clear()
    ad.reset_password(conn, "CN=x", "Secret1!", must_change=False, unlock=False)
    assert [c[0] for c in calls] == ["pwd"]  # без лишнего modify


def test_audit_entries_filters_and_labels():
    from adk import db
    db.log_action("adm1", "restart", "WS-1")
    db.log_action("adm2", "reset_password", "ivanov", "смена при входе")
    db.log_action("adm1", "group_add", "ivanov", "VPN_50%")
    assert len(db.audit_entries()) == 3
    assert [r[2] for r in db.audit_entries(admin="adm1")] == ["group_add", "restart"]
    assert [r[3] for r in db.audit_entries(action="reset_password")] == ["ivanov"]
    assert [r[4] for r in db.audit_entries(text="50%")] == ["VPN_50%"]  # LIKE-экранирование
    assert db.audit_entries(since="2999-01-01 00:00:00") == []
    assert db.audit_admins() == ["adm1", "adm2"]
    assert db.ACTION_LABELS["reset_password"] == "Сброс пароля"


def test_humanize_since():
    from datetime import datetime
    from adk import db
    now = datetime(2026, 9, 4, 12, 0, 0)
    assert db.humanize_since(None) == "—"
    assert db.humanize_since("2026-09-04 11:59:30", now) == "только что"
    assert db.humanize_since("2026-09-04 11:15:00", now) == "45 мин. назад"
    assert db.humanize_since("2026-09-04 09:00:00", now) == "3 ч. назад"
    assert db.humanize_since("2026-09-01 10:00:00", now) == "3 дн. назад (01.09.2026)"
    assert db.humanize_since("garbage", now) == "garbage"


def test_last_seen_online_roundtrip():
    from adk import db
    db.batch_update_inventory([{"Hostname": "WS-7", "ActualIp": "10.0.0.7", "Status": "ACTIVE", "User": "u"}], "2026-09-04 10:00:00")
    db.batch_update_inventory([{"Hostname": "WS-7", "ActualIp": "10.0.0.7", "Status": "OFFLINE", "User": "u"}], "2026-09-04 11:00:00")
    assert db.last_seen_online("ws-7.example.local") == "2026-09-04 10:00:00"
    inv, _, _ = db.load_inventory_maps()
    assert inv["WS-7"]["last_seen_online"] == "2026-09-04 10:00:00"
    assert db.inventory_rows("offline")[0][7] == "2026-09-04 10:00:00"


def test_logging_uses_rotating_handler(tmp_path, monkeypatch):
    import logging
    from logging.handlers import RotatingFileHandler
    from adk import config
    monkeypatch.setattr(config, "LOG_FILE", str(tmp_path / "a.log"))
    root = logging.getLogger()
    old = list(root.handlers)
    for h in old:
        root.removeHandler(h)
    try:
        config.setup_logging()
        rot = [h for h in root.handlers if isinstance(h, RotatingFileHandler)]
        assert rot and rot[0].maxBytes == 2 * 1024 * 1024 and rot[0].backupCount == 5
    finally:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in old:
            root.addHandler(h)


# --------------------------------------------------------------------------- 2.2.0: принтеры и поиск
def test_printer_classification_and_virtual_filter():
    from adk import netutils
    assert netutils.classify_printer_port("IP_10.0.2.50") == ("network", "10.0.2.50")
    assert netutils.classify_printer_port("10.0.2.51_1") == ("network", "10.0.2.51")
    assert netutils.classify_printer_port("WSD-3f2a") == ("network", "")
    assert netutils.classify_printer_port("\\\\PRINTSRV\\HP_2floor") == ("shared", "")
    assert netutils.classify_printer_port("USB001") == ("usb", "")
    assert netutils.classify_printer_port("DOT4_001") == ("usb", "")
    assert netutils.classify_printer_port("LPT1:") == ("local", "")
    assert netutils.classify_printer_port("PORTPROMPT:") == ("virtual", "")
    for name in ("Microsoft Print to PDF", "Microsoft XPS Document Writer", "OneNote (Desktop)", "Fax", "Факс",
                 "Adobe PDF", "PDF24", "Send To OneNote 16", "Foxit Reader PDF Printer"):
        assert netutils.is_virtual_printer(name), name
    assert not netutils.is_virtual_printer("HP LaserJet Pro M404dn")
    assert not netutils.is_virtual_printer("Kyocera ECOSYS P3145dn")


def test_printers_from_specs_skips_virtual_and_marks_default():
    from adk import netutils
    d = {"printers": {
        "0": {"Название": "HP LaserJet M404", "Порт": "IP_10.0.2.50", "По умолчанию": "Да"},
        "1": {"Название": "Microsoft Print to PDF", "Порт": "PORTPROMPT:"},
        "2": {"Название": "Canon LBP6030", "Порт": "USB001"},
        "3": {"Название": "Kyocera на PRINTSRV", "Порт": "\\\\PRINTSRV\\KYO3"},
    }}
    ps = netutils.printers_from_specs(d)
    assert [p["name"] for p in ps] == ["HP LaserJet M404", "Canon LBP6030", "Kyocera на PRINTSRV"]
    assert ps[0]["kind"] == "network" and ps[0]["ip"] == "10.0.2.50" and ps[0]["is_default"]
    assert ps[1]["kind"] == "usb" and not ps[1]["is_default"]
    assert ps[2]["kind"] == "shared"
    assert netutils.printer_label(ps[0]) == "HP LaserJet M404 (сетевой · 10.0.2.50)"
    assert netutils.printer_label(ps[1]) == "Canon LBP6030 (USB)"


def test_printer_cache_and_owner_search():
    from adk import db
    db.batch_update_inventory([{"Hostname": "WS-1", "ActualIp": "10.0.0.1", "Status": "ACTIVE", "User": "CORP\\ivanov"},
                               {"Hostname": "WS-2", "ActualIp": "10.0.0.2", "Status": "OFFLINE", "User": "petrov"}], "2026-09-04 10:00:00")
    hp = {"name": "HP LaserJet M404", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50", "is_default": True}
    db.replace_printers("ws-1.example.local", [hp, {"name": "Canon LBP", "port": "USB001", "kind": "usb", "ip": ""}])
    db.replace_printers("WS-2", [hp])
    by_pc = db.printers_for_computers(["WS-1", "WS-2", "WS-9"])
    assert [p["name"] for p in by_pc["WS-1"]] == ["HP LaserJet M404", "Canon LBP"]  # default первым
    assert "WS-9" not in by_pc
    owners = db.printer_owners("10.0.2.50")
    assert {(o["comp"], o["user"]) for o in owners} == {("WS-1", "ivanov"), ("WS-2", "petrov")}
    assert db.logins_by_printer("laserjet") == {"ivanov", "petrov"}
    assert db.logins_by_printer("canon") == {"ivanov"}
    assert db.logins_by_printer("hp") == set()          # < 3 символов — не ищем
    db.replace_printers("WS-1", [])                     # полная замена → USB исчез
    assert db.printers_for_computers(["WS-1"]) == {}


def test_search_filter_no_phone_and_ip_modes(monkeypatch):
    from adk import db
    from adk.workers import SearchWorker
    w = SearchWorker(lambda: None, "2554567")
    flt = w._build_filter()
    assert "telephoneNumber" not in flt and "ipPhone" not in flt and "mobile" not in flt
    assert "(sAMAccountName=*2554567*)" in flt          # числа всё ещё ищутся в логине/имени ПК
    # IP без привязанных пользователей → None (в AD не ходим, показываем ПК)
    assert SearchWorker(lambda: None, "10.0.2")._build_filter() is None
    db.batch_update_inventory([{"Hostname": "WS-1", "ActualIp": "10.0.2.11", "Status": "ACTIVE", "User": "ivanov"}], "2026-09-04 10:00:00")
    flt = SearchWorker(lambda: None, "10.0.2")._build_filter()
    assert flt and "(sAMAccountName=ivanov)" in flt and "displayName" not in flt   # IP-запрос: только логины
    # режим printer:
    db.replace_printers("WS-1", [{"name": "HP M404", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50"}])
    pw = SearchWorker(lambda: None, "printer: 10.0.2.50")
    assert pw.printer_query == "10.0.2.50"
    flt = pw._build_filter()
    assert flt == "(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2))(|(sAMAccountName=ivanov)))"
    assert SearchWorker(lambda: None, "printer: nonexistent")._build_filter() is None


def test_search_worker_free_pc_rows_for_ip_query(monkeypatch):
    from adk import db
    from adk.workers import SearchWorker
    db.batch_update_inventory([{"Hostname": "WS-77", "ActualIp": "10.0.9.77", "Status": "OFFLINE", "User": ""}], "2026-09-04 10:00:00")
    got = []
    w = SearchWorker(lambda: (_ for _ in ()).throw(AssertionError("LDAP не должен вызываться")), "10.0.9")
    w.results_ready.connect(lambda rows, q, t: got.append(rows))
    w.run()
    assert got and got[0][0]["comp"] == "WS-77" and got[0][0]["login"] == "—"


def test_ip_prefix_matching_is_octet_aware():
    """Точность: «10.0.2» должен находить 10.0.2.x, но не 10.0.20.x и не 110.0.2.x."""
    from adk import db
    assert db.ip_like_patterns("10.0.2") == ("10.0.2", "10.0.2.%")
    assert db.ip_like_patterns("10.0.2.") == ("10.0.2.%", "10.0.2.%")
    assert db.ip_like_patterns("ws-10") is None and db.ip_like_patterns("2554567") is None
    db.batch_update_inventory([
        {"Hostname": "A1", "ActualIp": "10.0.2.15", "Status": "ACTIVE", "User": "a1"},
        {"Hostname": "A2", "ActualIp": "10.0.20.15", "Status": "ACTIVE", "User": "a2"},
        {"Hostname": "A3", "ActualIp": "110.0.2.15", "Status": "ACTIVE", "User": "a3"},
    ], "2026-09-04 10:00:00")
    assert db.logins_by_computer_or_ip("10.0.2") == {"a1"}
    assert db.logins_by_computer_or_ip("10.0.2.15") == {"a1"}
    assert db.logins_by_computer_or_ip("0.2.15") == set()          # середина адреса — не совпадение
    assert {r[0] for r in db.inventory_rows_matching("10.0.2")} == {"A1"}
    assert {r[0] for r in db.inventory_rows_matching("10.0.2.")} == {"A1"}
    assert {r[0] for r in db.inventory_rows_matching("A")} == {"A1", "A2", "A3"}   # имя ПК — подстрока



def test_network_info_cache(monkeypatch):
    """Производительность: повторные запросы к одному ПК в течение TTL не бьют в DNS/ping."""
    import socket
    from adk import netutils
    calls = []
    monkeypatch.setattr(socket, "gethostbyname", lambda n: calls.append(n) or "10.0.0.5")
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip: True)
    netutils.clear_network_cache()
    assert netutils.get_computer_network_info("ws-5.example.local") == ("10.0.0.5", True)
    assert netutils.get_computer_network_info("WS-5") == ("10.0.0.5", True)
    assert calls == ["WS-5"]                                      # второй раз — из кэша
    assert netutils.get_computer_network_info("WS-5", use_cache=False) == ("10.0.0.5", True)
    assert calls == ["WS-5", "WS-5"]
    monkeypatch.setattr(netutils, "NET_CACHE_TTL", 0.0)
    netutils.get_computer_network_info("WS-5")
    assert len(calls) == 3                                        # TTL истёк
    netutils.clear_network_cache()


def test_normalization_accuracy():
    """Точность: формы логинов/имён ПК, которые реально приходят из AD, сканера и буфера обмена."""
    from adk import db
    from adk.workers import SearchWorker
    assert db.normalize_login("CORP\\Ivanov ") == "ivanov"
    assert db.normalize_login("ivanov@corp.example") == "ivanov"
    assert db.normalize_login("ИВАНОВ") == "иванов"
    assert db.clean_computer_name("ws-101.corp.example") == "WS-101"
    assert db.clean_computer_name("WS-101$") == "WS-101"
    assert db.clean_computer_name("10.0.2.15") == "10.0.2.15"          # раньше превращалось в «10»
    assert db.clean_computer_name("Не найден") == ""
    assert SearchWorker.normalize_query("  Иванов   Иван ") == "Иванов Иван"
    assert SearchWorker.normalize_query("CORP\\ivanov") == "ivanov"
    assert SearchWorker.normalize_query("ivanov@corp.example") == "ivanov"
    assert SearchWorker.normalize_query("printer: HP\\M404") == "printer: HP\\M404"   # режим printer: не трогаем
    assert SearchWorker(lambda: None, "CORP\\ivanov").query == "ivanov"


# ------------------------------------------------------------------ защита БД: копия перед миграцией, quick_check, модули только читают
def test_backup_before_migration(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE pc_inventory (computer_name TEXT PRIMARY KEY, ip_address TEXT, is_online INTEGER, "
                  "current_user TEXT, last_checked TEXT, specs TEXT, last_logon TEXT, last_seen_online TEXT)")
        c.execute("INSERT INTO pc_inventory (computer_name) VALUES ('WS-1')")
        c.execute("PRAGMA user_version=0")
    monkeypatch.setattr(config.settings, "db_path", str(path))
    db.init_db()
    baks = [f for f in os.listdir(tmp_path) if f.startswith("old.db.bak-")]
    assert len(baks) == 1
    with sqlite3.connect(tmp_path / baks[0]) as c:
        assert c.execute("SELECT computer_name FROM pc_inventory").fetchone() == ("WS-1",)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db.init_db()      # повторный запуск той же версии копию не плодит
    assert len([f for f in os.listdir(tmp_path) if f.startswith("old.db.bak-")]) == 1
    assert db.quick_check(str(path)) == "ok"


def test_quick_check_reports_corruption(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"SQLite format 3\0" + b"\xff" * 4096)
    assert db.quick_check(str(bad)) != "ok"


def test_new_modules_do_not_write(monkeypatch):
    """Страховка: health/dhcp/pingui-график/live-принтеры не содержат путей записи в БД или файлы."""
    import inspect
    from adk import pingui
    for mod, allowed in ((health, ()), (dhcp, ()), (pingui, ("set_pc_online",))):
        src = inspect.getsource(mod)
        for token in ("INSERT", "UPDATE ", "DELETE ", 'open(', ".to_csv", "writerow"):
            assert token not in src, f"{mod.__name__}: {token}"
        for tok in ("db.",):
            uses = [ln for ln in src.splitlines() if tok in ln and "import" not in ln]
            for ln in uses:
                assert any(a in ln for a in allowed), f"{mod.__name__}: {ln.strip()}"


def test_remote_command_any_drive_letter():
    """3.2.9: «Диск» умеет открывать любой том — disk_c, disk_d, disk_e… → \\\\ПК\\<буква>$."""
    from adk import netutils
    assert netutils.remote_command("disk_c", "WS-1") == ["explorer.exe", "\\\\WS-1\\c$"]
    assert netutils.remote_command("disk_e", "WS-1") == ["explorer.exe", "\\\\WS-1\\e$"]
    assert netutils.remote_command("disk_cc", "WS-1") is None       # только одна буква


def test_known_volumes_from_hardware_csv(tmp_path, monkeypatch):
    """Тома берутся из CSV инвентаризации («Логический диск»); без CSV — пустой список (тогда откроется C$)."""
    from adk import config, netutils
    (tmp_path / "WS-7.csv").write_text(
        "Логический диск;Буква;0;C:\nЛогический диск;Метка тома;0;System\nЛогический диск;Размер;0;511705088000\n"
        "Логический диск;Буква;1;D:\nЛогический диск;Метка тома;1;Data\nЛогический диск;Размер;1;1000202039296\n",
        encoding="cp1251")
    monkeypatch.setattr(config.settings, "invent_hardware_dir", str(tmp_path))
    vols = netutils.known_volumes("WS-7")
    assert [v["letter"] for v in vols] == ["C", "D"]
    assert vols[0]["label"] == "System" and vols[1]["size"] == "932 ГБ"
    assert netutils.known_volumes("NO-SUCH") == []
