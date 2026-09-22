# -*- coding: utf-8 -*-
"""Партия 39 (3.10.0): портативная папка рядом с exe, окно выбора парка, дефолты SSL/TLS, подсказка маски."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adk import config
from adk.config import _migrate_old_data, _try_portable_dir, park_pattern


# ------------------------------------------------------------------ park_pattern: человеческий ввод → маска
@pytest.mark.parametrize("src, expected", [
    ("PC-", "PC-*"),
    ("pc-", "PC-*"),            # регистр не важен
    ("PC-0000", "PC-????"),     # 4 цифры → 4 «?»
    ("PC-12", "PC-??"),
    ("pc-0099", "PC-????"),
    ("PC", "PC"),               # без дефиса — точное имя
    ("LT_10", "LT-??".replace("-", "_")),   # каждая цифра — «?»
    ("LT-10 ", "LT-??"),       # пробелы по краям срезаются
    ("", ""),
    ("   ", ""),
    ("FS.", "FS.*"),
])
def test_park_pattern(src, expected):
    assert park_pattern(src) == expected


# ------------------------------------------------------------------ портативная папка
def test_try_portable_dir_writable(tmp_path):
    d = _try_portable_dir(str(tmp_path))
    assert d == str(tmp_path / "ADK")
    assert os.path.isdir(d)
    assert not os.path.exists(os.path.join(d, ".write-test"))    # пробный файл убран


def test_try_portable_dir_readonly(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("под root права не работают")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    assert _try_portable_dir(str(ro)) is None
    ro.chmod(0o700)


def test_migrate_old_data_moves_files(tmp_path):
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("[AD]")
    (old / "pc_mapping.db").write_text("db")
    (old / "backups").mkdir()
    (old / "backups" / "b1.zip").write_text("zip")
    _migrate_old_data(str(old), str(new))
    assert (new / "config.ini").read_text() == "[AD]"
    assert (new / "pc_mapping.db").exists()
    assert (new / "backups" / "b1.zip").exists()
    assert not old.exists()                                       # пустая старая папка удалена


def test_migrate_old_data_keeps_existing_config(tmp_path):
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("old")
    (new / "config.ini").write_text("new")                        # в новой уже есть конфиг — не переносим
    _migrate_old_data(str(old), str(new))
    assert (new / "config.ini").read_text() == "new"
    assert (old / "config.ini").read_text() == "old"


# ------------------------------------------------------------------ дефолты: новый конфиг без LDAPS
def test_default_config_writes_ssl_false(tmp_path):
    path = str(tmp_path / "config.ini")
    config.write_default_config(path)
    cp = config._load() if False else None
    import configparser
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    assert cp["AD"]["use_ssl"] == "false"
    assert cp["AD"]["tls_validate"] == "false"


# ------------------------------------------------------------------ окно выбора парка
def test_park_mask_dialog_counts_and_saves(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit
    from adk.setup_ui import ParkMaskDialog
    names = ["PC-0001", "PC-0002", "PC-100", "LT-0001", "WS-01"]
    saved = {}
    monkeypatch.setattr(config.settings, "host_mask", "")
    monkeypatch.setattr(config.settings, "save_section", lambda s, v: saved.update(v))

    d = ParkMaskDialog(None, names)
    # 3.12.0: кнопка всегда активна; пустой ввод обрабатывается подсказкой, а не серой кнопкой
    d.save()
    assert d.mask_saved is False and "Пропустить" in d.total_lbl.text()
    for i in range(d.rows_lay.count()):                           # в первом поле вводим серию
        lay = d.rows_lay.itemAt(i)
        if lay and lay.count():
            w = lay.itemAt(0).widget()
            if isinstance(w, QLineEdit):
                w.setText("PC-0000")
                break
    assert d._patterns() == ["PC-????"]
    assert "2 из 5" in d.total_lbl.text()                         # PC-0001, PC-0002; PC-100 не подходит

    d.add_row("LT-")
    assert d._patterns() == ["PC-????", "LT-*"]
    assert "3 из 5" in d.total_lbl.text()                         # + LT-0001

    d.save()
    assert d.mask_saved is True
    assert saved == {"host_mask": "PC-????, LT-*"}
    assert config.settings.host_mask == "PC-????, LT-*"
    d.deleteLater()


def test_park_mask_dialog_no_names_still_saves(qapp, monkeypatch):
    from adk.setup_ui import ParkMaskDialog
    saved = {}
    monkeypatch.setattr(config.settings, "host_mask", "")
    monkeypatch.setattr(config.settings, "save_section", lambda s, v: saved.update(v))
    d = ParkMaskDialog(None, [])                                  # домен недоступен — без счётчика
    for i in range(d.rows_lay.count()):
        lay = d.rows_lay.itemAt(i)
        if lay and lay.count() and hasattr(lay.itemAt(0).widget(), "setText"):
            lay.itemAt(0).widget().setText("FS-0000")
            break
    assert d.btn_ok.isEnabled()
    d.save()
    assert saved == {"host_mask": "FS-????"}
    d.deleteLater()


# ------------------------------------------------------------------ подсказка сканера при заданной маске
def test_scan_hint_mentions_mask():
    """Текст «проверьте host_pattern» не должен вводить в заблуждение, когда фильтрует host_mask."""
    from adk.workers import PCScannerWorker
    monkey_mask = "PC-????"
    old = config.settings.host_mask
    config.settings.host_mask = monkey_mask
    try:
        # маска задана → ПК, не подходящий под неё, отбрасывается (это и было причиной «скан не начался»)
        rx = PCScannerWorker._mask_regex(monkey_mask)
        assert rx.match("PC-0001") and rx.match("pc-0099")
        assert not rx.match("PC-001")
        assert not rx.match("WS-0001")
    finally:
        config.settings.host_mask = old


def test_migrate_rewrites_config_paths(tmp_path):
    """После переноса данных db_path в конфиге не должен указывать на исчезнувшую старую папку."""
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("[Paths]\ndb_path = %s\n" % (old / "pc_mapping.db"), encoding="utf-8")
    (old / "pc_mapping.db").write_text("db")
    _migrate_old_data(str(old), str(new))
    txt = (new / "config.ini").read_text(encoding="utf-8")
    assert str(new / "pc_mapping.db") in txt
    assert str(old) not in txt
    assert (new / "pc_mapping.db").exists()


# ------------------------------------------------------------------ 3.11.0: нормализация search_base
@pytest.mark.parametrize("src, expected", [
    ("GC://DC=city,DC=local", "DC=city,DC=local"),
    ("LDAP://DC=city,DC=local", "DC=city,DC=local"),
    ("ldaps://DC=city,DC=local", "DC=city,DC=local"),
    ("  DC=city,DC=local  ", "DC=city,DC=local"),
    ("DC=city,DC=local", "DC=city,DC=local"),
    ("", ""),
])
def test_normalize_search_base(src, expected):
    from adk.config import normalize_search_base
    assert normalize_search_base(src) == expected


# ------------------------------------------------------------------ 3.11.0: ADSI-запасной путь
def test_adsi_computer_names_parses(monkeypatch):
    from adk import netutils

    class R:
        returncode, stdout, stderr = 0, "PC-0001\nPC-0002\nsrv-hidden\n", ""
    monkeypatch.setattr(netutils.subprocess, "run", lambda *a, **k: R())
    assert netutils.adsi_computer_names() == ["PC-0001", "PC-0002", "SRV-HIDDEN"]


def test_adsi_computer_names_error(monkeypatch):
    from adk import netutils

    class R:
        returncode, stdout, stderr = 1, "ADSI_ERROR: нет доступа", ""
    monkeypatch.setattr(netutils.subprocess, "run", lambda *a, **k: R())
    with pytest.raises(RuntimeError):
        netutils.adsi_computer_names()


class _Attr:
    def __init__(self, values):
        self.values = values


class _E:                                            # минимальная «запись AD» с атрибутом name
    def __init__(self, name):
        self._a = {"name": _Attr([name])}

    def __getitem__(self, key):
        return self._a[key]


def _fake_conn():
    class C:
        def unbind(self):
            pass
    return C()


def test_host_list_ldap_wins(monkeypatch):
    from adk import workers
    from adk.workers import host_list_from_ad
    monkeypatch.setattr(config.settings, "host_mask", "PC-*")
    monkeypatch.setattr(workers.ad, "paged_search",
                        lambda conn, flt, attrs: [_E("PC-0001"), _E("LT-0002")])
    called = []
    monkeypatch.setattr(workers.netutils, "adsi_computer_names", lambda: called.append(1) or [])
    hosts, via = host_list_from_ad(_fake_conn)
    assert hosts == ["PC-0001"] and via == "ldap" and not called


def test_host_list_falls_back_to_adsi(monkeypatch):
    """LDAP упал или пуст — список берётся через ADSI, как в старой программе."""
    from adk import workers
    from adk.workers import host_list_from_ad
    monkeypatch.setattr(config.settings, "host_mask", "PC-????")
    monkeypatch.setattr(workers.ad, "paged_search", lambda conn, flt, attrs: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(workers.netutils, "adsi_computer_names",
                        lambda: ["PC-0001", "PC-002", "LT-0001", "PC-0002"])
    hosts, via = host_list_from_ad(_fake_conn)
    assert hosts == ["PC-0001", "PC-0002"] and via == "adsi"   # PC-002 (3 цифры) под PC-???? не подходит, LT-0001 вне маски


# ------------------------------------------------------------------ 3.11.0: сектора диаграммы
def test_donut_segments_math(qapp):
    from adk.scan_ui import DonutWidget
    d = DonutWidget(150)
    d.set_segments([(10, 10, "#111111"), (30, 15, "#222222")])
    assert d._total == 40 and d._done == 25          # общий процент = 25/40 = 63%, из реальных чисел
    d.set_segments([])
    assert d._total == 0 and d._done == 0
    d.deleteLater()


# ------------------------------------------------------------------ 3.12.0: журнал КД (4768) — «кто за ПК»
class _PsOk:
    ok, error = True, ""
    def __init__(self, stdout):
        self.stdout = stdout


def test_dc_logon_events_parses(monkeypatch):
    from adk import psrun, workers
    seen = {}

    def fake_run(script, timeout=240):
        seen["script"] = script
        return _PsOk('[{"u":"Ivanov","ip":"10.1.1.5"},{"u":"Petrova","ip":"10.1.1.6"}]')
    monkeypatch.setattr(psrun, "run", fake_run)
    pairs = workers.dc_logon_events("dc01.corp.local")
    assert pairs == [("Ivanov", "10.1.1.5"), ("Petrova", "10.1.1.6")]
    assert "dc01.corp.local" in seen["script"]            # адрес КД подставлен в Get-WinEvent


def test_dc_logon_events_single_object_and_empty(monkeypatch):
    from adk import psrun, workers
    monkeypatch.setattr(psrun, "run", lambda script, timeout=240: _PsOk('{"u":"Sidorov","ip":"10.2.0.9"}'))
    assert workers.dc_logon_events("dc") == [("Sidorov", "10.2.0.9")]
    monkeypatch.setattr(psrun, "run", lambda script, timeout=240: _PsOk(""))
    assert workers.dc_logon_events("dc") == []


def test_dc_logon_events_error(monkeypatch):
    from adk import psrun, workers

    class _Bad:
        ok, error, stdout = False, "нет доступа к журналу", ""
    monkeypatch.setattr(psrun, "run", lambda script, timeout=240: _Bad())
    with pytest.raises(RuntimeError):
        workers.dc_logon_events("dc")


def test_merge_dc_logons_fills_empty_users(monkeypatch):
    from adk import workers
    results = [{"Hostname": "PC-0001", "User": "", "LastLogon": "Неизвестно"},
               {"Hostname": "PC-0002", "User": "Old", "LastLogon": "x"}]
    pairs = [("Ivanov", "10.1.1.5"), ("Petrova", "10.1.1.7"), ("Ivanov", "10.1.1.5")]
    monkeypatch.setattr("socket.gethostbyaddr",
                        lambda ip: ({"10.1.1.5": "PC-0001.corp.local", "10.1.1.7": "PC-0003.corp.local"}[ip],))
    out = workers.merge_dc_logons(results, pairs)
    assert out[0]["User"] == "Ivanov"                       # пустой пользователь заполнен событием КД
    assert "контроллера" in out[0]["LastLogon"]
    assert out[1]["User"] == "Old"                          # уже известный не перезаписывается


def test_enrich_with_dc_logons_swallows_errors(monkeypatch):
    from adk import workers
    monkeypatch.setattr(workers, "dc_logon_events",
                        lambda dc: (_ for _ in ()).throw(RuntimeError("журнал закрыт")))
    res = workers.enrich_with_dc_logons([{"Hostname": "PC-1", "User": ""}])
    assert res[0]["User"] == ""                             # ошибка чтения — шаг пропущен, не упал скан


# ------------------------------------------------------------------ 3.12.1: быстрый выход по крестику
def test_close_event_total_wait_budget(qapp, monkeypatch):
    """Бюджет на ожидание потоков — общий (~2 с), а не по 3 с на каждый: окно не должно подвисать."""
    import threading
    import adk.main_window as mw
    from adk.main_window import ADApp

    waits: list[int] = []

    class StuckWorker:                                  # «поток», который никогда не заканчивается
        def isRunning(self):
            return True

        def cancel(self):
            pass

        def wait(self, ms):
            waits.append(ms)

    released = threading.Event()

    def fake_release(owner):                            # аренда отпускается в фоне, не блокируя закрытие
        released.set()

    monkeypatch.setattr(mw.db, "scan_lease_release", fake_release)
    w = ADApp("admin", "x")
    w.scanner = StuckWorker()
    w._threads = [StuckWorker(), StuckWorker(), StuckWorker()]
    w._bg_workers = [StuckWorker()]
    w.close()
    assert len(waits) == 5                              # все живые потоки получили wait()
    assert all(0 <= ms <= 2000 for ms in waits), waits  # и каждый — в пределах общего бюджета
    assert released.wait(2)                             # аренда отпущена (в фоне)
    w.deleteLater()


# ------------------------------------------------------------------ 3.9.1: цвета кнопок как в 3.8
def test_contrast_text_white_on_accents():
    """3.9.1: возврат к 3.8 — на акцентных заливках БЕЛЫЙ текст/значки (янтарь, салатовый, синий)."""
    from adk.theme import contrast_text
    for accent in ("#F5B324", "#FFB020", "#3DD68C", "#0A84FF", "#F472B6"):
        assert contrast_text(accent) == "#ffffff", accent
    assert contrast_text("#FFF7E0") == "#1D1D1F"        # очень светлый — тёмный


# ------------------------------------------------------------------ 3.9.1: лёгкий фоновый скан + WMI
def test_ps_scanner_asks_pc_for_user_and_ping_param():
    """Сканер спрашивает у онлайн-ПК «кто за ним» (WMI) и пингует с настраиваемым таймаутом."""
    from adk.workers import _PS_SCANNER
    assert "Win32_ComputerSystem" in _PS_SCANNER          # прямой источник «кто за ПК»
    assert "Send($actualIp, $pingMs)" in _PS_SCANNER      # таймаут пинга — параметром
    # 3.9.3: WMI гейтится флагом $askUser (только полный/первичный опрос), пинг подставляется из Python
    assert "$askUser = __ASK_USER__" in _PS_SCANNER
    assert "$pingMs = __PING_MS__" in _PS_SCANNER
    assert ".AddArgument($pingMs).AddArgument($askUser)" in _PS_SCANNER
    # 3.9.5: два транспорта WMI (WinRM → DCOM), спрашиваем у всех ACTIVE (CSV мог устареть),
    # домен отрезаем — в базу пишется чистый логин
    assert "if ($askUser -and $status -eq 'ACTIVE') {" in _PS_SCANNER
    assert "Get-CimInstance -ClassName Win32_ComputerSystem" in _PS_SCANNER
    assert "Get-WmiObject -Class Win32_ComputerSystem" in _PS_SCANNER
    assert ("$u -split '" + chr(92) + chr(92) + "')[-1]") in _PS_SCANNER   # DOMAIN\login → login


def test_run_powershell_substitutes_ping_and_wmi(monkeypatch):
    """3.9.3: ping_ms и wmi_user реально попадают в скрипт (раньше $pingMs приходил пустым),
    фоновый скан (deep=False) не дёргает WMI, полный/первичный — дёргает."""
    from adk import psrun, workers

    captured = {}

    class _R:
        ok, stdout, error = True, "[]", ""

    monkeypatch.setattr(psrun, "run", lambda script, timeout=None, cancelled=None:
                        (captured.__setitem__("script", script), _R())[1])
    w_light = workers.PCScannerWorker(lambda: None, deep=False)
    w_light._run_powershell(["PC-1"], 100, False)
    s = captured["script"]
    assert "$pingMs = 100" in s and "$askUser = False" in s
    w_light._run_powershell(["PC-1"], 100, True)
    assert "$askUser = True" in captured["script"]

    # probe_hosts берёт wmi_user из self.deep; объект без deep (FullScanWorker/scan_once) — полный опрос
    assert w_light._wmi_user_default() is False
    w_full = workers.PCScannerWorker(lambda: None)
    assert w_full._wmi_user_default() is True
    bare = workers.PCScannerWorker.__new__(workers.PCScannerWorker)
    assert bare._wmi_user_default() is True   # QObject без __init__ — RuntimeError → полный опрос


def test_scanner_deep_flag(monkeypatch):
    from adk.workers import PCScannerWorker
    w = PCScannerWorker(lambda: None, deep=False)          # фоновый — лёгкий
    assert w.deep is False
    w2 = PCScannerWorker(lambda: None)                     # полный/первичный — глубокий
    assert w2.deep is True


# ------------------------------------------------------------------ 3.9.1: сводка вместо сырого JSON
def test_specs_json_shown_as_summary():
    import json
    from adk import netutils
    payload = json.dumps({"os": {"Название": "Windows 11 Pro"}, "cpu": {"Название": "i5-12400"},
                          "rams": {"0": {"Объём": "16384 МБ"}},
                          "disks": {"0": {"Размер": "512105932800 байт"}},
                          "_ts": "2026-09-20T10:00:00"}, ensure_ascii=False)
    d = netutils.parse_specs_json(payload)
    s = netutils.summarize_specs(d)
    assert "Windows 11 Pro" in s and "i5-12400" in s       # человекочитаемая сводка
    assert "{" not in s                                     # сырой JSON не показываем


# ------------------------------------------------------------------ 3.9.1: «Прочитать всё» в Внимании
def test_attention_read_all(qapp, monkeypatch):
    from adk.attention_ui import AttentionDialog
    snoozed, logged = [], []

    class _App:
        admin_name = "admin"
        get_conn = staticmethod(lambda: None)

    monkeypatch.setattr("adk.db.snooze", lambda key, days, who: snoozed.append(key))
    monkeypatch.setattr("adk.db.log_action", lambda *a, **k: logged.append(a))
    items = [{"key": f"k{i}", "severity": "high" if i % 2 else "low", "icon": "⚠️",
              "title": f"t{i}", "subject": f"s{i}", "text": "x"} for i in range(4)]
    d = AttentionDialog(_App(), None, items=items)
    d.read_all()
    assert set(snoozed) == {"k0", "k1", "k2", "k3"}         # все показанные скрыты
    assert d.items == [] and d.table.rowCount() == 0
    assert logged and logged[0][1] == "attention_read_all"  # одна запись в журнал, не построчно
    d.deleteLater()
