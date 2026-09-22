"""3.7.0: живой сбор характеристик (CSV → кэш БД → CIM/WMI), IP принтера из «Расположения»,
миграция старого журнала действий (audit_log без столбца admin), устойчивый разбор «Обзора».
"""
import json
import sqlite3

from adk import db, health, netutils


# --------------------------------------------------------------------------- parse_specs_json
def test_parse_specs_json_structure_like_csv():
    """Живой опрос отдаёт ту же структуру разделов, что инвентарный CSV — иначе карточка/опись сломаются."""
    live = {
        "os": {"Название": "Windows 10 Pro", "Версия": "10.0.19045", "Сборка": "19045", "Установлена": "2024-05-01 10:00:00"},
        "system": {"Производитель": "Lenovo", "Модель": "20T5000ART"},
        "board": {"Производитель": "Lenovo", "Модель": "20T5"},
        "bios": {"Производитель": "LENOVO", "Версия": "R0IET72W", "Дата": "2023-04-12 00:00:00"},
        "cpu": {"Название": "Intel(R) Core(TM) i5-10400", "Частота": "2900 МГц", "Ядра": "6", "Потоки": "12"},
        "rams": [{"Объём": "8 ГБ", "Частота": "2666 МГц"}, {"Объём": "8 ГБ", "Частота": "2666 МГц"}],
        "disks": [{"Наименование": "Samsung SSD 860", "Тип носителя": "SSD", "Интерфейс": "SATA", "Размер": "476 ГБ",
                   "Серийный номер": "S4EWNX0N"}],
        "logdisks": [{"Буква": "C:", "Метка": "System", "Файловая система": "NTFS", "Размер": "476 ГБ", "Свободно": "150 ГБ"}],
        "gpu": {"Название": "Intel(R) UHD Graphics 630", "Объём памяти": "1 ГБ"},
        "adapters": [{"Название": "Intel(R) Ethernet", "MAC": "AA:BB:CC:DD:EE:FF", "IP": "10.0.69.5", "DHCP": "да"}],
        "printers": [{"Наименование": "HP LaserJet 1020", "Порт": "USB001", "По умолчанию": "да",
                      "Расположение": "", "Драйвер": "HP"}],
    }
    d = netutils.parse_specs_json(json.dumps(live))
    assert set(d) >= {"os", "board", "bios", "cpu", "rams", "disks", "logdisks", "gpu", "adapters", "printers", "system"}
    assert d["os"]["Название"] == "Windows 10 Pro"
    assert d["rams"]["0"]["Объём"] == "8 ГБ" and d["rams"]["1"]["Объём"] == "8 ГБ"
    assert d["disks"]["0"]["Тип носителя"] == "SSD"
    assert d["printers"]["0"]["Порт"] == "USB001"


def test_parse_specs_json_single_element_and_empty():
    """ConvertTo-Json схлопывает массив из одного объекта в объект, пустой — в []: оба случая валидны."""
    d = netutils.parse_specs_json(json.dumps({"rams": {"Объём": "16 ГБ"}, "disks": []}))
    assert d["rams"] == {"0": {"Объём": "16 ГБ"}}
    assert d.get("disks", {}) == {}


# --------------------------------------------------------------------------- сводка как в описи
def test_summarize_specs_units_and_live_shape():
    d = {"os": {"Версия": "10.0.19045", "Название": "Windows 10 Pro"},
         "cpu": {"Частота": "2900 МГц", "Название": "Intel i5-10400"},
         "rams": {"0": {"Объём": "8 ГБ", "Частота": "2666 МГц"}, "1": {"Объём": "8192 МБ", "Частота": "2666 МГц"}},
         "disks": {"0": {"Наименование": "Samsung SSD", "Размер": "476,9 ГБ", "Тип носителя": "SSD"}}}
    s = netutils.summarize_specs(d)
    assert "ОС: Windows 10 Pro" in s
    assert "CPU: Intel i5-10400" in s
    assert "ОЗУ: 16.0 ГБ (2666 МГц)" in s
    assert "Диски: 476.9 ГБ SSD (Samsung SSD)" in s


def test_to_gb_units():
    assert netutils._to_gb("476,9 ГБ") == 476.9
    assert netutils._to_gb("476.9GB") == 476.9
    assert netutils._to_gb("1 ТБ") == 1024.0
    assert netutils._to_gb("512110190592") == 476.9
    assert netutils._to_gb("мусор") is None


def test_to_mb_units():
    assert netutils._to_mb("8 ГБ") == 8192
    assert netutils._to_mb("8192 МБ") == 8192
    assert netutils._to_mb(8589934592) == 8192
    assert netutils._to_mb("512") == 512
    assert netutils._to_mb("нет") == 0


# --------------------------------------------------------------------------- CSV → кэш → живой опрос
class _PsOk:
    ok = True

    def __init__(self, payload):
        self.stdout = json.dumps(payload)
        self.error = ""


class _PsFail:
    ok = False
    stdout = ""
    error = "ПК не отвечает"


_LIVE_PAYLOAD = {
    "os": {"Название": "Windows 10 Pro"},
    "cpu": {"Название": "Intel i5"},
    "rams": [{"Объём": "8 ГБ"}],
    "disks": [{"Наименование": "SSD", "Размер": "476 ГБ"}],
    "printers": [],
}


def test_specs_chain_collects_and_caches(monkeypatch, tmp_path):
    monkeypatch.setattr(netutils.settings, "invent_hardware_dir", "")      # CSV нет вообще
    calls = {"n": 0}

    def fake_run(script, timeout=60, **kw):
        calls["n"] += 1
        assert "__HOST__" not in script.replace("'$c = '__HOST__'", "")   # хост подставлен
        return _PsOk(_LIVE_PAYLOAD)

    from adk import psrun
    monkeypatch.setattr(psrun, "run", fake_run)

    d = netutils.get_computer_specs_dict("PC-0396")
    assert "error" not in d and d["os"]["Название"] == "Windows 10 Pro"
    assert calls["n"] == 1
    # собранное легло в базу…
    row = db.db_execute_with_retry("SELECT specs FROM pc_inventory WHERE computer_name = 'PC-0396'", fetch="one")
    assert row and row[0].startswith("{")
    # …и второй вызов берётся из кэша, без нового опроса
    d2 = netutils.get_computer_specs_dict("PC-0396")
    assert calls["n"] == 1 and d2["os"]["Название"] == "Windows 10 Pro"
    # сводка для описи Excel — из того же кэша
    s = netutils.get_computer_specs_summary("PC-0396")
    assert "Windows 10 Pro" in s and "8.0 ГБ" in s


def test_specs_chain_live_disabled(monkeypatch):
    monkeypatch.setattr(netutils.settings, "invent_hardware_dir", "")
    from adk import psrun
    monkeypatch.setattr(psrun, "run", lambda *a, **kw: _PsFail())
    d = netutils.get_computer_specs_dict("PC-X", live=False)
    assert "error" in d


def test_specs_chain_stale_cache_recollected(monkeypatch):
    monkeypatch.setattr(netutils.settings, "invent_hardware_dir", "")
    from adk import psrun
    db.save_specs("PC-OLD", {"os": {"Название": "Windows 7"}})
    # делаем отметку времени старой
    row = db.db_execute_with_retry("SELECT specs FROM pc_inventory WHERE computer_name = 'PC-OLD'", fetch="one")
    stale = json.loads(row[0])
    stale["_ts"] = "2020-01-01T00:00:00"
    db.db_execute_with_retry("UPDATE pc_inventory SET specs = ? WHERE computer_name = 'PC-OLD'", (json.dumps(stale),))
    monkeypatch.setattr(psrun, "run", lambda *a, **kw: _PsOk({"os": {"Название": "Windows 11"}}))
    d = netutils.get_computer_specs_dict("PC-OLD")          # live=True по умолчанию → пересбор
    assert d["os"]["Название"] == "Windows 11"


def test_specs_chain_live_fail_keeps_old_cache(monkeypatch):
    monkeypatch.setattr(netutils.settings, "invent_hardware_dir", "")
    from adk import psrun
    db.save_specs("PC-OFF", {"os": {"Название": "Windows 10"}})
    monkeypatch.setattr(psrun, "run", lambda *a, **kw: _PsFail())
    d = netutils.get_computer_specs_dict("PC-OFF")          # ПК выключен → показываем старое, а не ошибку
    assert d.get("os", {}).get("Название") == "Windows 10"


def test_save_specs_creates_row_and_roundtrip():
    db.save_specs("PC-0001", {"os": {"Название": "Windows 10"}, "cpu": {"Название": "i7"}})
    loaded, ts = netutils._load_cached_specs("pc-0001")
    assert loaded["os"]["Название"] == "Windows 10"
    assert ts and ts.startswith("20")
    # повторное сохранение обновляет, а не дублирует
    db.save_specs("PC-0001", {"os": {"Название": "Windows 11"}})
    n = db.db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory WHERE computer_name = 'PC-0001'", fetch="one")
    assert n[0] == 1
    loaded2, _ = netutils._load_cached_specs("PC-0001")
    assert loaded2["os"]["Название"] == "Windows 11"


def test_known_volumes_from_live_specs(monkeypatch):
    """Меню «Диск» получает тома из кэша живого опроса — и без живого опроса (клик в UI не должен виснуть)."""
    monkeypatch.setattr(netutils.settings, "invent_hardware_dir", "")
    from adk import psrun
    called = {"n": 0}

    def fake_run(*a, **kw):
        called["n"] += 1
        return _PsFail()

    monkeypatch.setattr(psrun, "run", fake_run)
    db.save_specs("PC-VOL", {"logdisks": {"0": {"Буква": "C:", "Метка": "System", "Размер": "476 ГБ"},
                                          "1": {"Буква": "D:", "Метка": "Data", "Размер": "1000 ГБ"}}})
    vols = netutils.known_volumes("PC-VOL")
    assert {v["letter"] for v in vols} == {"C", "D"}
    assert called["n"] == 0                                   # live=False — опроса не было


# --------------------------------------------------------------------------- принтеры: IP из «Расположения»
def test_ip_from_text():
    assert netutils.ip_from_text("http://10.0.69.70:3911/") == "10.0.69.70"
    assert netutils.ip_from_text("WSD-abc") == ""
    assert netutils.ip_from_text("999.999.999.999") == ""


def test_printers_from_specs_location_ip():
    d = {"printers": {"0": {"Наименование": "Kyocera ECOSYS", "Порт": "WSD-1a2b.", "По умолчанию": "да",
                            "Расположение": "http://10.0.69.70:3911/"}}}
    prn = netutils.printers_from_specs(d)
    assert prn and prn[0]["kind"] == "network" and prn[0]["ip"] == "10.0.69.70"


def test_parse_live_printers_location():
    payload = [{"name": "Kyocera", "port": "WSD-1a2b", "default": True, "status": 3, "offline": False,
                "driver": "Kyocera", "host": None, "location": "http://10.0.69.70:3911/"}]
    prn = netutils.parse_live_printers_json(json.dumps(payload))
    assert prn[0]["ip"] == "10.0.69.70" and prn[0]["kind"] == "network"


# --------------------------------------------------------------------------- журнал действий: старая база
def test_audit_log_legacy_schema_migrated(tmp_path, monkeypatch):
    """База ранней сборки: audit_log без admin/action/target/details → init_db добавляет столбцы,
    старые записи из event видны в журнале, а не роняют окно «no such column: admin»."""
    legacy = tmp_path / "legacy.db"
    con = sqlite3.connect(legacy)
    con.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, event TEXT)")
    con.execute("INSERT INTO audit_log (ts, event) VALUES ('2026-01-01 10:00:00', 'вход в программу')")
    con.commit()
    con.close()

    monkeypatch.setattr(db.settings, "db_path", str(legacy))
    db.init_db()

    rows = db.audit_entries()
    assert rows and rows[0][4] == "вход в программу"       # details ← event
    db.log_action("admin", "health", "PC-1", "ок")          # запись работает
    rows = db.audit_entries(admin="admin")
    assert any(r[2] == "health" for r in rows)


def test_audit_log_new_schema_untouched():
    db.log_action("admin", "note_add", "user", "текст")
    assert db.audit_entries(action="note_add")


# --------------------------------------------------------------------------- обзор: пустая дата загрузки
def test_parse_health_json_boot_missing():
    assert "error" in health.parse_health_json(json.dumps({"boot": "", "now": "2026-09-19 12:00:00"}))
    assert "error" in health.parse_health_json(json.dumps({"boot": "мусор"}))


def test_parse_health_json_dcom_dates():
    """Ответ по DCOM/WMI: даты уже приведены FmtDate к обычному виду — разбор не падает."""
    d = health.parse_health_json(json.dumps({
        "boot": "2026-09-10 08:00:00", "now": "2026-09-19 12:00:00", "total_mb": 16384, "free_mb": 8192,
        "cpu": 12, "os": "Windows 10 Pro", "disks": [], "phys": []}))
    assert d["uptime_days"] == 9 and d["ram_used_pct"] == 50


def test_is_ipv4():
    assert netutils._is_ipv4("10.0.2.50") and netutils._is_ipv4(" 192.168.1.1 ")
    assert not netutils._is_ipv4("prn-hp01") and not netutils._is_ipv4("10.0.2.50:9100")
    assert not netutils._is_ipv4("999.1.1.1") and not netutils._is_ipv4("")


def test_parse_live_printers_host_address_wins():
    """3.9.5: фактический адрес порта (HostAddress) важнее метки в имени порта —
    порт «IP_10.0.2.50» может называться по старому адресу, а печатать Windows уже на 10.0.9.99."""
    payload = [{"name": "HP LaserJet", "port": "IP_10.0.2.50", "default": True, "status": 3,
                "offline": False, "driver": "HP", "host": "10.0.9.99", "location": ""}]
    prn = netutils.parse_live_printers_json(json.dumps(payload))
    assert prn[0]["ip"] == "10.0.9.99" and prn[0]["kind"] == "network"


def test_parse_live_printers_host_dns_name_fallback():
    """Порт создан по DNS-имени и не резолвился (принтер офлайн): показываем имя порта-адреса,
    а если в имени порта есть IP — его (числовой адрес полезнее имени)."""
    payload = [{"name": "Kyocera", "port": "IP_10.0.2.50", "default": True, "status": 3,
                "offline": False, "driver": "Kyocera", "host": "prn-hp01.corp.local", "location": ""},
               {"name": "Canon", "port": "Standard TCP/IP", "default": False, "status": 3,
                "offline": False, "driver": "Canon", "host": "prn-canon01", "location": ""}]
    ps = netutils.parse_live_printers_json(json.dumps(payload))
    assert ps[0]["ip"] == "10.0.2.50"            # IP из имени порта полезнее нерезолвленного имени
    assert ps[1]["ip"] == "prn-canon01" and ps[1]["kind"] == "network"   # имени порта нет IP — честно имя


def test_printers_from_specs_ip_column_wins():
    """3.9.7: отдельная колонка «IP-адрес» в инвентарном CSV надёжнее IP в имени порта-метки."""
    d = {"printers": {"0": {"Наименование": "HP LaserJet", "Порт": "IP_10.0.2.50", "IP-адрес": "10.0.9.99",
                            "По умолчанию": "да"}}}
    prn = netutils.printers_from_specs(d)
    assert prn and prn[0]["ip"] == "10.0.9.99" and prn[0]["kind"] == "network"


def test_printers_from_specs_no_ip_column_keeps_port_ip():
    """Колонки адреса нет — прежнее поведение: IP из имени порта, «Расположение» — фолбэк для WSD."""
    d = {"printers": {"0": {"Наименование": "HP", "Порт": "IP_10.0.2.50", "Расположение": "2 этаж"}}}
    prn = netutils.printers_from_specs(d)
    assert prn[0]["ip"] == "10.0.2.50"
