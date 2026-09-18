"""3.5.10 — проверки по замечаниям с реального домена (партия 28).

Каждый тест — маленький сценарий «как в жизни»: подкладываем в тестовую БД пару записей, зовём функцию, сверяем ответ.
Сеть и PowerShell здесь не нужны: SNMP-агент поднимается прямо в тесте на локальном UDP-порту, а PowerShell на Linux
честно отвечает «только с Windows».
"""
from __future__ import annotations

import contextlib
import socket
import threading
from datetime import datetime, timedelta

import pytest

import adk.netutils as _nu

# настоящие функции — conftest подменяет их на заглушки для всех тестов, а здесь проверяем именно их
REAL_DISCOVER = _nu.discover_printer
REAL_PROBE = _nu.probe_printer


# --------------------------------------------------------------------------- архивы: полгода, а не «последняя привязка»
def test_parse_ts_accepts_db_and_journal_formats():
    from adk.db import parse_ts
    assert parse_ts("2026-09-04 10:00:00") == datetime(2026, 9, 4, 10, 0, 0)
    assert parse_ts("04.09.2026 10:00") == datetime(2026, 9, 4, 10, 0)
    assert parse_ts("04.09.2026") == datetime(2026, 9, 4)
    assert parse_ts("2026-09-04T10:00:00") == datetime(2026, 9, 4, 10, 0)
    assert parse_ts("") is None and parse_ts(None) is None and parse_ts("Неизвестно") is None


def _seed_history(rows):
    from adk import db
    with contextlib.closing(db.get_db_connection()) as conn:
        conn.executemany("INSERT INTO pc_history (computer_name, login, ip_address, first_seen, last_seen) "
                         "VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()


def _seed_inventory(rows):
    from adk import db
    with contextlib.closing(db.get_db_connection()) as conn:
        conn.executemany("INSERT INTO pc_inventory (computer_name, ip_address, is_online, current_user, last_checked, "
                         "last_logon, last_seen_online) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        conn.commit()


def test_recent_links_keeps_half_year_and_drops_older():
    """Связка ivanov↔WS-NEW (вчера) — текущая; ivanov↔WS-OLD (год назад) — архив и в ответ не попадает."""
    from adk import db
    now = datetime(2026, 9, 18, 12, 0, 0)
    fresh = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    old = (now - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_history([("WS-NEW", "CORP\\ivanov", "10.0.0.5", fresh, fresh), ("WS-OLD", "ivanov", "10.0.0.6", old, old)])
    links = db.recent_links(now=now)
    assert links["ivanov"] == {"WS-NEW": fresh}
    assert "WS-OLD" not in links["ivanov"]


def test_recent_links_pc_online_recently_is_current_even_with_old_link():
    """Правило «ПК недавно был в сети → не архив» работает для любого ПК: связка старая, но pc_inventory видел ПК
    неделю назад — связка остаётся текущей (пользователь просил именно так, а не для одного примера)."""
    from adk import db
    now = datetime(2026, 9, 18, 12, 0, 0)
    old = (now - timedelta(days=300)).strftime("%Y-%m-%d %H:%M:%S")
    seen = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    _seed_history([("WS-77", "petrov", "10.0.0.7", old, old)])
    _seed_inventory([("WS-77", "10.0.0.7", 0, "", seen, "Неизвестно", seen)])
    links = db.recent_links(now=now)
    assert "WS-77" in links["petrov"]
    # а ПК, которого сканер полгода не видел, — архив
    _seed_history([("WS-78", "petrov", "10.0.0.8", old, old)])
    _seed_inventory([("WS-78", "10.0.0.8", 0, "", old, "Неизвестно", old)])
    assert "WS-78" not in db.recent_links(now=now)["petrov"]


def test_get_computer_by_login_matches_domain_prefixed_login():
    """В pc_history логин часто хранится как DOMAIN\\login — поиск по «login» обязан его находить."""
    from adk import db
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _seed_history([("WS-DOM", "CORP\\sidorov", "10.0.0.9", ts, ts)])
    assert db.get_computer_by_login("sidorov") == "WS-DOM"
    assert db.get_computer_by_login("CORP\\sidorov") == "WS-DOM"


# --------------------------------------------------------------------------- опись: ПК сотрудника выбирается как в поиске
def test_pick_computer_priority_matches_search(monkeypatch):
    from adk import ad, workers
    from types import SimpleNamespace
    e = SimpleNamespace()
    monkeypatch.setattr(ad, "get_full_fio", lambda entry: "Иванов Иван Иванович")
    monkeypatch.setattr(ad, "get_ad_value", lambda entry, attr: "WS-AD" if attr == "userWorkstations" else "")
    inv = {"WS-A": {"is_online": False}, "WS-B": {"is_online": True}}
    inv_by_user = {"ivanov": ["WS-A", "WS-B"]}
    # 1) постоянная привязка важнее всего
    assert workers.pick_computer(e, "ivanov", "", inv, inv_by_user, {"ivanov": {"comp": "WS-PERM"}}, {}, {}) == "WS-PERM"
    # 2) затем pc_mapping
    assert workers.pick_computer(e, "ivanov", "", inv, inv_by_user, {}, {"ivanov": "WS-MAP"}, {}) == "WS-MAP"
    # 3) затем инвентарь: где сейчас залогинен, ПК в сети — первым
    assert workers.pick_computer(e, "CORP\\ivanov", "", inv, inv_by_user, {}, {}, {}) == "WS-B"
    # 4) затем связки за полгода (самая свежая), в т.ч. по ФИО
    recent = {"иванов иван иванович": {"WS-R1": "2026-01-01 10:00:00", "WS-R2": "2026-08-01 10:00:00"}}
    assert workers.pick_computer(e, "ivanov", "", inv, {}, {}, {}, recent) == "WS-R2"
    # 5) в самом конце — AD userWorkstations
    assert workers.pick_computer(e, "nobody", "", inv, {}, {}, {}, {}) == "WS-AD"


# --------------------------------------------------------------------------- SNMP без библиотек + принтер по IP
def _mini_snmp_agent(answer: bytes):
    """Локальный «принтер»: слушает UDP, на любой GET отвечает OCTET STRING с моделью. Возвращает (port, stop)."""
    from adk.netutils import _ber, _ber_oid
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)
    port = sock.getsockname()[1]
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except OSError:
                continue
            vb = _ber(0x30, _ber_oid("1.3.6.1.2.1.25.3.2.1.3.1") + _ber(0x04, answer))
            pdu = _ber(0xA2, _ber(0x02, b"\x01") + _ber(0x02, b"\x00") + _ber(0x02, b"\x00") + _ber(0x30, vb))
            sock.sendto(_ber(0x30, _ber(0x02, b"\x00") + _ber(0x04, b"public") + pdu), addr)
        sock.close()

    threading.Thread(target=serve, daemon=True).start()
    return port, stop.set


def test_ber_oid_encoding_is_standard():
    from adk.netutils import _ber_oid
    # 1.3.6.1.2.1.1.1.0 → 06 08 2B 06 01 02 01 01 01 00 (классический sysDescr)
    assert _ber_oid("1.3.6.1.2.1.1.1.0") == bytes.fromhex("06082b060102010101 00".replace(" ", ""))
    # многобайтовый субидентификатор (43 → 0x2B, 65535 → 83 FF 7F)
    assert _ber_oid("1.3.65535") == bytes.fromhex("06042b83ff7f")


def test_snmp_get_string_reads_model_and_handles_silence():
    from adk import netutils
    port, stop = _mini_snmp_agent("HP LaserJet M1536dnf MFP".encode())
    try:
        assert netutils.snmp_get_string("127.0.0.1", "1.3.6.1.2.1.25.3.2.1.3.1", port=port) == "HP LaserJet M1536dnf MFP"
    finally:
        stop()
    # молчащий порт — пустая строка за timeout, без исключений
    assert netutils.snmp_get_string("127.0.0.1", "1.3.6.1.2.1.1.1.0", timeout=0.2, port=1) == ""


def test_discover_printer_uses_probe_then_snmp(monkeypatch):
    """Поиск по IP, которого нет в базе: сначала честная проверка «а принтер ли это», потом модель по SNMP."""
    from adk import netutils
    monkeypatch.setattr(netutils, "probe_printer", lambda ip, **kw: {"alive": True, "is_printer": True, "evidence": "открыт порт печати 9100"})
    monkeypatch.setattr(netutils, "snmp_get_string", lambda ip, oid, **kw: "Kyocera ECOSYS M2040dn" if oid == netutils._SNMP_OIDS[0] else "")
    monkeypatch.setattr(netutils.socket, "gethostbyaddr", lambda ip: ("prn-201.corp.example", [], [ip]))
    d = REAL_DISCOVER("10.0.15.154")
    assert d["name"] == "Kyocera ECOSYS M2040dn" and d["source"] == "SNMP" and d["host"] == "prn-201" and d["kind"] == "network"
    # это компьютер, а не принтер → None (строка принтера не рисуется)
    monkeypatch.setattr(netutils, "probe_printer", lambda ip, **kw: {"alive": True, "is_printer": False, "evidence": "открыт порт 445"})
    assert REAL_DISCOVER("10.0.15.155") is None
    assert REAL_DISCOVER("не-ip") is None


def test_search_by_unknown_ip_shows_discovered_printer_row(monkeypatch):
    from adk import netutils
    from adk.workers import SearchWorker
    monkeypatch.setattr(netutils, "discover_printer", lambda ip: {
        "name": "HP LaserJet M1536dnf MFP", "ip": ip, "kind": "network", "host": "prn-15",
        "probe": {"alive": True, "is_printer": True, "evidence": "открыт порт печати 9100"}, "source": "SNMP"})
    rows = SearchWorker(lambda: None, "10.0.15.154")._discovered_printer_rows()
    assert len(rows) == 1
    r = rows[0]
    assert r["kind"] == "printer" and r["fio"] == "HP LaserJet M1536dnf MFP" and r["ip"] == "10.0.15.154"
    assert r["printer"]["discovered"] is True and "в базе не числится" in r["last_logon"]
    assert r["net_pending"] is False and r["is_online"] is True
    # не IP — ничего не опрашиваем
    assert SearchWorker(lambda: None, "иванов")._discovered_printer_rows() == []


def test_probe_printer_silent_host_is_fast():
    """Все порты проверяются одновременно: молчащий адрес отвечает за ~timeout, а не за 7×timeout."""
    import time
    t = time.monotonic()
    r = REAL_PROBE("10.255.255.254", timeout=0.3)
    assert r["is_printer"] is None and r["alive"] is False
    assert time.monotonic() - t < 2.0


# --------------------------------------------------------------------------- единый запуск PowerShell
def test_psrun_interpret_json_error_and_empty_output():
    from adk import psrun
    r = psrun.interpret('{"error": "Access is denied."}', "", 0)
    assert not r.ok and "доступ" in r.error.lower() or "прав" in r.error.lower()
    r = psrun.interpret("", "", 1)
    assert not r.ok and "WinRM" in r.error
    r = psrun.interpret('{"cpu": 5}', "", 0)
    assert r.ok and r.stdout == '{"cpu": 5}'


def test_psrun_explain_error_knows_winrm_and_rpc():
    from adk import psrun
    assert "WinRM" in psrun.explain_error("The WinRM client cannot process the request")
    assert "RPC" in psrun.explain_error("The RPC server is unavailable") or "DCOM" in psrun.explain_error("The RPC server is unavailable")
    assert psrun.explain_error("что-то своё") == "что-то своё"


def test_psrun_encodes_unicode_script_and_refuses_non_windows():
    import os
    from adk import psrun
    argv, tmp = psrun.build_argv("Write-Output 'Привет'")
    assert argv[0].lower().startswith("powershell") and any(a in ("-EncodedCommand", "-File") for a in argv)
    if tmp:
        os.remove(tmp)
    if os.name != "nt":
        r = psrun.run("Write-Output 1", timeout=1)
        assert not r.ok and "Windows" in r.error


def test_describe_ldap_error_leaves_powershell_timeouts_alone():
    """Ошибка опроса ПК со словом «timeout» не должна превращаться в «Контроллер домена недоступен»."""
    from adk import ad
    from ldap3.core.exceptions import LDAPSocketOpenError
    assert ad.describe_ldap_error(RuntimeError("WS-1: нет ответа за 40 с (timeout)")) == "WS-1: нет ответа за 40 с (timeout)"
    assert "Контроллер домена недоступен" in ad.describe_ldap_error(LDAPSocketOpenError("socket connection error"))


# --------------------------------------------------------------------------- опрос парка без базы
def test_fleetpoll_group_and_summarize():
    from adk import fleetpoll
    results = {
        "WS-1": {"printers": [{"name": "HP LaserJet M1536dnf MFP", "kind": "network", "ip": "10.0.15.154"}]},
        "WS-2": {"printers": [{"name": "hp laserjet m1536dnf mfp", "kind": "network", "ip": "10.0.15.154"},
                              {"name": "Kyocera ECOSYS", "kind": "usb", "ip": ""}]},
        "WS-3": {"error": "не в сети", "skipped": True},
        "WS-4": {"error": "WinRM недоступен"},
    }
    rows = fleetpoll.group_printers(results)
    assert rows[0]["name"] == "HP LaserJet M1536dnf MFP" and rows[0]["pcs"] == 2 and rows[0]["computers"] == "WS-1, WS-2"
    assert rows[1]["name"] == "Kyocera ECOSYS" and rows[1]["pcs"] == 1 and rows[0]["live"] is True
    assert fleetpoll.summarize(results) == {"total": 4, "ok": 2, "skipped": 1, "failed": 1}


def test_fleet_hosts_prefers_inventory_then_ad(monkeypatch):
    from adk import fleetpoll
    _seed_inventory([("WS-ON", "10.0.0.1", 1, "", "2026-09-18 10:00:00", "x", "2026-09-18 10:00:00"),
                     ("WS-OFF", "10.0.0.2", 0, "", "2026-09-18 10:00:00", "x", None)])
    hosts = fleetpoll.fleet_hosts(None, online_only=False)
    assert hosts[0] == "WS-ON" and "WS-OFF" in hosts
    assert fleetpoll.fleet_hosts(None, online_only=True) == ["WS-ON"]


def test_printers_live_skips_offline_without_powershell(monkeypatch):
    from adk import fleetpoll, netutils
    monkeypatch.setattr(netutils, "get_computer_network_info", lambda h, **kw: ("", False))
    called = []
    monkeypatch.setattr(netutils, "get_live_printers", lambda h, **kw: called.append(h) or {"printers": []})
    assert fleetpoll.printers_live("WS-OFF") == {"error": "не в сети", "skipped": True} and called == []


# --------------------------------------------------------------------------- свободный IP: скользящее окно
def test_freeip_worker_reports_first_free_in_order(qapp, monkeypatch):
    """Ответ — первый по порядку свободный адрес, даже если более дальние проверились раньше (окно 48 адресов)."""
    import time
    from adk import netutils, workers
    busy = set(range(1, 40)) - {20}

    def alive(ip, timeout=1.0):
        h = int(ip.rsplit(".", 1)[1])
        time.sleep(0.3 if h == 20 else 1.0 if h > 100 else 0.01)   # .20 отвечает медленно, «дальний» хвост — очень медленно
        return h in busy
    monkeypatch.setattr(netutils, "is_host_alive", alive)
    monkeypatch.setattr(workers, "_ptr_exists", lambda ip, timeout=1.5: False)
    w = workers.FreeIPWorker("10.0.2", 1, use_dhcp=False)
    marks, res = {}, []
    w.host_checked.connect(lambda h, s: marks.__setitem__(h, s))
    w.finished_search.connect(res.append)
    w.start()
    t0 = time.time()
    while w.isRunning() and time.time() - t0 < 10:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert res == ["10.0.2.20"]
    assert marks[1] == "alive" and marks[20] == "free"
    assert len(marks) < 254                        # после ответа остаток подсети не сканировался


def test_freeip_worker_uses_inventory_before_network(qapp, monkeypatch):
    from adk import netutils, workers
    pinged = []
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip, timeout=1.0: pinged.append(ip) or False)
    monkeypatch.setattr(workers, "_ptr_exists", lambda ip, timeout=1.5: False)
    w = workers.FreeIPWorker("10.0.3", 1, use_dhcp=False)
    w.known = {"10.0.3.1"}
    assert w._reason("10.0.3.1") == "inventory" and pinged == []
    assert w._reason("10.0.3.2") == "free" and pinged == ["10.0.3.2"]


def test_freeip_legend_explains_every_cell_colour(qapp):
    from adk.freeip_ui import CELL, SubnetMap
    from adk.widgets import app_palette
    m = SubnetMap()
    for key in CELL:
        assert m._color(app_palette(), CELL[key][1]).isValid()
    assert "found" not in CELL and "inventory" in CELL


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
