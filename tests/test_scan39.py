"""3.9.0: маска парка — главный фильтр; характеристики в полном опросе; миграция config.ini;
массовые операции без фриза; календарь; выход; старт-вопрос до окна."""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from adk import config, db
from adk.workers import PCScannerWorker


class _Attr:
    def __init__(self, values):
        self.values = list(values)
        self.value = self.values[0] if self.values else None


class _E:
    """Минимальная «запись AD» с одним атрибутом name."""

    def __init__(self, name):
        self._a = {"name": _Attr([name])}

    def __getitem__(self, key):
        return self._a[key]


# ---------------------------------------------------------------- маска парка
def test_host_mask_overrides_pattern(monkeypatch):
    """Маска задана — парк определяет она; host_pattern больше не вырезает «чужие» серии (3.9.0)."""
    monkeypatch.setattr(config.settings, "host_pattern", r"^(WS-\d+|PC-.*)$")
    monkeypatch.setattr(config.settings, "host_exclude", "")
    monkeypatch.setattr(config.settings, "host_mask", "PC-???, LT-*")
    names = PCScannerWorker.workstation_names([_E("PC-101"), _E("LT-North"), _E("WS-5"), _E("PC-77"), _E("PC-10")])
    assert names == ["PC-101", "LT-NORTH"]          # pattern не действует, маска решает


def test_host_mask_empty_pattern_rules(monkeypatch):
    """Маска пуста — как раньше: парк определяет host_pattern."""
    monkeypatch.setattr(config.settings, "host_pattern", r"^(WS-\d+|PC-.*)$")
    monkeypatch.setattr(config.settings, "host_exclude", "")
    monkeypatch.setattr(config.settings, "host_mask", "")
    names = PCScannerWorker.workstation_names([_E("PC-101"), _E("WS-5"), _E("PC-77")])
    assert names == ["WS-5", "PC-77"]


def test_host_mask_exclude_still_applies(monkeypatch):
    monkeypatch.setattr(config.settings, "host_pattern", r"^(WS-\d+)$")
    monkeypatch.setattr(config.settings, "host_exclude", "(TEST)")
    monkeypatch.setattr(config.settings, "host_mask", "PC-*")
    names = PCScannerWorker.workstation_names([_E("PC-1"), _E("PC-TEST")])
    assert names == ["PC-1"]


# ---------------------------------------------------------------- свежие характеристики
def test_hosts_with_fresh_specs():
    db.save_specs("WS-201", {"os": {"Название": "Windows 11"}})
    db.save_specs("WS-202", {"os": {"Название": "Windows 10"}})
    try:
        assert "WS-201" in db.hosts_with_fresh_specs()
        # устаревшую пометим прошлым годом — выпадает из «свежих»
        import json
        from datetime import datetime, timedelta
        old = json.dumps({"os": {}, "_ts": (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")})
        with db.get_db_connection() as conn:
            conn.execute("UPDATE pc_inventory SET specs = ? WHERE computer_name = 'WS-201'", (old,))
            conn.commit()
        fresh = db.hosts_with_fresh_specs()
        assert "WS-201" not in fresh and "WS-202" in fresh
    finally:
        with db.get_db_connection() as conn:
            conn.execute("DELETE FROM pc_inventory WHERE computer_name IN ('WS-201','WS-202')")
            conn.commit()


# ---------------------------------------------------------------- миграция config.ini
def test_migrate_config_adds_missing_keys(tmp_path):
    p = str(tmp_path / "config.ini")
    with open(p, "w", encoding="utf-8") as f:
        f.write("[Scanner]\nauto_scan_interval_min = 30\nhost_pattern = ^(WS-\\d+)$\n\n[Design]\nbg_style = x\n")
    config._migrate_config(p)
    text = open(p, encoding="utf-8").read()
    assert "host_mask =" in text and "ГЛАВНЫЙ ФИЛЬТР ПАРКА" in text
    assert "host_pattern = ^(WS-\\d+)$" in text          # своё значение не тронуто
    # повторная миграция ничего не меняет
    before = open(p, encoding="utf-8").read()
    config._migrate_config(p)
    assert open(p, encoding="utf-8").read() == before


def test_migrate_config_ignores_unknown_sections(tmp_path):
    p = str(tmp_path / "config.ini")
    with open(p, "w", encoding="utf-8") as f:
        f.write("[AD]\ndc_host = dc01\n")
    config._migrate_config(p)
    text = open(p, encoding="utf-8").read()
    assert text == "[AD]\ndc_host = dc01\n"               # секций без настроек не добавляем


# ---------------------------------------------------------------- массовые операции: без фриза
def test_bulk_dialog_caps_table_rows(qapp, monkeypatch):
    from adk.tools import BulkOperationsDialog

    class _App:
        def get_conn(self):
            raise RuntimeError("не должен вызываться при открытии")

    users = [{"entry": object(), "login": f"u{i}", "full_fio": f"Фамилия Имя{i} Отчество"} for i in range(1200)]
    monkeypatch.setattr("adk.tools.run_in_background", lambda *a, **k: None)
    dlg = BulkOperationsDialog(users, _App(), None)
    assert dlg.table.rowCount() == 400                    # таблица построена только для первых 400
    assert dlg.users and len(dlg.users) == 1200           # операция — по всему списку
    assert "1200" in dlg.lbl_more.text()
    dlg.deleteLater()


# ---------------------------------------------------------------- контраст текста на акценте
def test_contrast_text_light_accents_get_dark_text():
    from adk.theme import contrast_text
    assert contrast_text("#FFB020") == "#1D1D1F"          # янтарь — тёмный текст
    assert contrast_text("#3DD68C") == "#1D1D1F"          # салатовый — тёмный текст
    assert contrast_text("#0A84FF") == "#ffffff"          # синий — белый, как и было


# ---------------------------------------------------------------- вход по кэшу: подпись и пустые
def test_logons_cached_label():
    from adk.logons import LOGON_TYPES, parse_events_json
    assert LOGON_TYPES["11"] == "Вход по кэшу"
    d = parse_events_json('[{"id": 4624, "ts": "2026-09-19 10:00:00", "user": "ivanov", "domain": "CITY",'
                          ' "type": "11", "ip": "", "status": "0x0"}]')
    assert d["events"][0]["type"] == "Вход по кэшу"


# ---------------------------------------------------------------- входы: сетевые отсеиваются в PowerShell
def test_logons_skip_net_builds_script(monkeypatch):
    from adk import logons
    seen = {}

    class _Res:
        ok = True
        stdout = "[]"

    def fake_run(script, timeout=None, cancelled=None):
        seen["script"] = script
        return _Res()

    import adk.psrun as psrun
    monkeypatch.setattr(psrun, "run", fake_run)
    logons.get_logons("WS-101", 24, include_net=False)
    assert "$skipNet = $true" in seen["script"] and "-ne '3'" in seen["script"]
    logons.get_logons("WS-101", 24, include_net=True)
    assert "$skipNet = $false" in seen["script"]
