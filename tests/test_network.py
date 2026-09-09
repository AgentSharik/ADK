"""Сеть: разбор и классификация DHCP, свободный IP с учётом аренд/резервов, карта подсети, живой опрос принтеров
(без записи в БД).
"""
import json
import os

from adk import config, db, dhcp, netutils, workers


DHCP_JSON = json.dumps({
    "server": "dhcp01",
    "scopes": [{
        "scope": "10.0.2.0", "name": "Офис", "start": "10.0.2.20", "end": "10.0.2.250", "state": "Active",
        "leases": [{"ip": "10.0.2.21", "mac": "aa-bb", "host": "ws-21", "state": "Active", "expires": "2026-09-06"},
                   {"ip": "10.0.2.22", "mac": "cc-dd", "host": "old-pc", "state": "Expired"}],
        "reservations": [{"ip": "10.0.2.30", "mac": "ee-ff", "name": "printer-hall"}],
        "exclusions": [{"start": "10.0.2.20", "end": "10.0.2.25"}],
    }],
})


LIVE_JSON = json.dumps([
    {"name": "HP LaserJet M404", "port": "IP_10.0.2.50", "default": True, "status": 3, "offline": False, "driver": "HP", "host": "10.0.2.50"},
    {"name": "Microsoft Print to PDF", "port": "PORTPROMPT:", "default": False, "status": 3},
    {"name": "Canon USB", "port": "USB001", "default": False, "status": 7, "offline": True},
])


# ------------------------------------------------------------------ 1. DHCP
def test_dhcp_parse_and_classify():
    d = dhcp.parse_dhcp_json(DHCP_JSON)
    assert d["server"] == "dhcp01" and len(d["scopes"]) == 1
    s = d["scopes"][0]
    assert set(s["leases"]) == {"10.0.2.21", "10.0.2.22"} and "10.0.2.30" in s["reservations"]
    # активная аренда → занят; резервирование → занят; исключение → статика; просроченная аренда → свободен
    assert dhcp.classify("10.0.2.21", d)["status"] == "lease"
    assert dhcp.classify("10.0.2.30", d)["status"] == "reserved"
    assert dhcp.classify("10.0.2.22", d)["status"] == "free"
    assert dhcp.classify("10.0.2.24", d)["status"] == "excluded"
    assert dhcp.classify("10.0.2.100", d)["status"] == "free"
    assert dhcp.classify("10.0.2.5", d)["status"] == "outside"     # до начала области
    assert dhcp.classify("10.0.9.1", d)["status"] == "outside"


def test_dhcp_query_without_servers_or_windows():
    assert dhcp.query("10.0.2", servers=()) == {"scopes": [], "servers": [], "errors": []}
    if os.name != "nt":
        r = dhcp.query("10.0.2", servers=("dhcp01",))
        assert r["errors"] and not r["scopes"]
    # недопустимое имя сервера отклоняется до запуска PowerShell
    r = dhcp.query("10.0.2", servers=("bad;name",))
    assert any("Недопустимое" in e or "Windows" in e for e in r["errors"])


def test_free_ip_worker_respects_dhcp(monkeypatch):
    """Адрес, выданный DHCP, не должен считаться свободным даже если не пингуется и нет PTR."""
    from adk.workers import FreeIPWorker

    data = dhcp.parse_dhcp_json(DHCP_JSON)
    monkeypatch.setattr(config.settings, "dhcp_servers", ("dhcp01",))
    monkeypatch.setattr(dhcp, "query", lambda prefix, servers=None, timeout=40: {**data, "servers": ["dhcp01"], "errors": []})
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip, timeout=1.0: False)
    import socket
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: (_ for _ in ()).throw(OSError()))
    FreeIPWorker._dhcp_cache.clear()
    w = FreeIPWorker("10.0.2", 21)
    w._load_dhcp()
    assert w._is_free("10.0.2.21") is False       # аренда
    assert w._is_free("10.0.2.30") is False       # резервирование
    assert w._is_free("10.0.2.100") is True
    assert w.verdict("10.0.2.100")["status"] == "free"
    assert w.verdict("10.0.2.24")["status"] == "excluded"
    # без настроенных серверов сверка выключена, вердикт «n/a»
    monkeypatch.setattr(config.settings, "dhcp_servers", ())
    w2 = FreeIPWorker("10.0.2", 1)
    assert w2.use_dhcp is False and w2.verdict("10.0.2.1")["status"] == "n/a"


def test_config_dhcp_servers_parsed(tmp_path, monkeypatch):
    ini = tmp_path / "config.ini"
    ini.write_text("[Scanner]\ndhcp_servers = dhcp01, dhcp02 ;dhcp03\n", encoding="utf-8")
    monkeypatch.setattr(config, "INI_FILE", str(ini))
    s = config.Settings()
    assert s.dhcp_servers == ("dhcp01", "dhcp02", "dhcp03")


# ------------------------------------------------------------------ 2. живой опрос принтеров
def test_parse_live_printers_excludes_virtual():
    ps = netutils.parse_live_printers_json(LIVE_JSON)
    assert [p["name"] for p in ps] == ["HP LaserJet M404", "Canon USB"]     # виртуальный скрыт, по умолчанию — первый
    assert ps[0]["ip"] == "10.0.2.50" and ps[0]["kind"] == "network" and ps[0]["status_text"] == "готов"
    assert ps[1]["offline"] and "офлайн" in ps[1]["status_text"]


def test_get_live_printers_is_read_only(monkeypatch, tmp_path):
    """Живой опрос не трогает ни pc_printers, ни CSV: до и после — одинаковое содержимое."""
    db.db_execute_with_retry("INSERT OR REPLACE INTO pc_printers (computer_name, name, port, kind, ip_address, is_default, updated) "
                             "VALUES ('WS-1','Old','IP_1','network','10.0.2.9',0,'2026-01-01')")
    csv_file = tmp_path / "WS-1_printers.csv"
    csv_file.write_text("a;b\n", encoding="utf-8")
    before_db = db.db_execute_with_retry("SELECT * FROM pc_printers", fetch="all")
    before_csv = csv_file.read_bytes()
    r = netutils.get_live_printers("WS-1")
    if os.name != "nt":
        assert "Windows" in r["error"]
    assert netutils.get_live_printers("bad name;")["error"].startswith("Недопустимое")
    assert db.db_execute_with_retry("SELECT * FROM pc_printers", fetch="all") == before_db
    assert csv_file.read_bytes() == before_csv


def _dhcp_data():
    return dhcp.parse_dhcp_json(json.dumps({"server": "dhcp01", "scopes": [{
        "scope": "10.0.2.0", "name": "Офис", "start": "10.0.2.20", "end": "10.0.2.250", "state": "Active",
        "leases": [{"ip": "10.0.2.61", "mac": "00-1a-2b-3c-4d-5e", "host": "ws-61", "state": "Active"}],
        "reservations": [{"ip": "10.0.2.64", "mac": "00-1a-2b-3c-4d-40", "name": "printer"}],
        "exclusions": []}]}))


# ------------------------------------------------------------------ 3. свободный IP и карта подсети
def test_freeip_reason_order(monkeypatch):
    """Причина занятости выбирается от дешёвой проверки к дорогой: инвентарь → DHCP → ping → PTR."""
    db.db_execute_with_retry("INSERT INTO pc_inventory (computer_name, ip_address) VALUES ('WS-1', '10.0.2.60')")
    monkeypatch.setattr(config.settings, "dhcp_servers", ("dhcp01",))
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip, timeout=1.0: ip.endswith(".70"))
    import socket

    def fake_ptr(ip):
        if ip.endswith(".71"):
            return ("host71", [], [])
        raise OSError

    monkeypatch.setattr(socket, "gethostbyaddr", fake_ptr)
    w = workers.FreeIPWorker("10.0.2", 60)
    w.dhcp_data = _dhcp_data()
    assert w._reason("10.0.2.60") == "inventory"
    assert w._reason("10.0.2.61") == "lease"
    assert w._reason("10.0.2.64") == "reserved"
    assert w._reason("10.0.2.70") == "alive"
    assert w._reason("10.0.2.71") == "ptr"
    assert w._reason("10.0.2.72") == "free"


def test_freeip_emits_status_for_every_host(monkeypatch):
    """Карта подсети получает статус каждого проверенного адреса (сигнал host_checked)."""
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip, timeout=1.0: False)
    import socket
    monkeypatch.setattr(socket, "gethostbyaddr", lambda ip: (_ for _ in ()).throw(OSError()))
    w = workers.FreeIPWorker("10.0.2", 5, use_dhcp=False)
    got = []
    w.host_checked.connect(lambda h, st: got.append((h, st)))
    assert w._check("10.0.2.5") == "free"
    assert got == [(5, "free")]


def test_subnet_map_click_sets_start(qapp):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtGui import QMouseEvent
    from adk.freeip_ui import SubnetMap

    m = SubnetMap()
    m.resize(660, 180)
    m.show()
    picked = []
    m.picked.connect(picked.append)
    ox, oy, c = m._cell()
    assert (m.COLS, m.ROWS) == (32, 8) and m.height() < m.width() / 3      # 3.2.9: широкая матрица, не квадрат
    # ячейка хоста 33 — вторая строка, второй столбец (32 в ряду)
    pos = QPoint(int(ox + c * 1.5), int(oy + c * 1.5))
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, pos.toPointF(), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    m.mousePressEvent(ev)
    assert picked == [33]
    m.mark(33, "lease")
    assert m.status[33] == "lease"
    m.close()


# ------------------------------------------------------------------ найденный адрес на карте — зелёный с рамкой, не синий
def test_subnet_map_found_cell_stays_green(qapp):
    """Раньше найденный адрес заливался акцентным (синим) цветом и его путали с «в инвентаре» — тоже синим.
    Теперь заливка = статус («свободен» → зелёный), а «это он» показывает только контрастная рамка."""
    from PyQt6.QtGui import QImage, QPainter
    from adk.freeip_ui import SubnetMap
    from adk.widgets import app_palette
    m = SubnetMap()
    m.resize(800, 220)
    m.mark(71, "free")
    m.found = 71
    img = QImage(800, 220, QImage.Format.Format_ARGB32)
    p = QPainter(img); m.render(p); p.end()
    ox, oy, c = m._cell()
    row, col = divmod(71, m.COLS)
    centre = img.pixelColor(int(ox + col * c + c / 2), int(oy + row * c + c / 2))
    pal = app_palette()
    from PyQt6.QtGui import QColor
    green, accent, frame = m._color(pal, "success"), m._color(pal, "accent"), QColor(pal.text)
    def dist(a, b): return abs(a.red() - b.red()) + abs(a.green() - b.green()) + abs(a.blue() - b.blue())
    # в теме с зелёным акцентом («Тёмная мята») green == accent — тогда достаточно, что центр именно зелёный
    assert dist(centre, green) <= 12, "центр найденной ячейки должен быть зелёным"
    assert green == accent or dist(centre, green) < dist(centre, accent), "…а не акцентным"
    edge = img.pixelColor(int(ox + col * c + 2), int(oy + row * c + c / 2))     # у левого края — контрастная рамка
    assert dist(edge, frame) < dist(edge, green)
