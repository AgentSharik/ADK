"""3.5.11 — партия 30: мастер «где база?» при первом запуске и первичное наполнение новой базы.

Сценарии как в жизни: (1) чистый ПК — базы нет, ADK спрашивает и создаёт; (2) второй ПК подключается к уже
заполненной базе; (3) старой установке (база есть и полна) вопрос не задаётся; (4) наполнение = сканер + принтеры,
без ПО; (5) следов portable-режима в проекте не осталось.
"""
from __future__ import annotations

import contextlib
import os
import re

import pytest


def _fill_db(path: str, n: int = 3) -> None:
    """Заполненная база: инициализируем схему ADK по указанному пути и кладём n ПК."""
    from adk import config, db
    old = config.settings.db_path
    config.settings.db_path = path
    try:
        db.init_db()
        with contextlib.closing(db.get_db_connection()) as conn:
            for i in range(n):
                conn.execute("INSERT INTO pc_inventory (computer_name, ip_address, is_online, last_checked) VALUES (?, ?, ?, ?)",
                             (f"PC-{i:03d}", f"10.0.0.{i + 1}", 1, "2026-09-11 09:00:00"))
            conn.commit()
    finally:
        config.settings.db_path = old


# --------------------------------------------------------------------------- когда спрашивать
def test_needs_setup_on_clean_pc(tmp_path, monkeypatch):
    from adk import config, setup_ui
    monkeypatch.setattr(config.settings, "db_ready", False)
    monkeypatch.setattr(config.settings, "db_path", str(tmp_path / "nowhere" / "pc_mapping.db"))
    monkeypatch.setattr(config.settings, "save_section", lambda *a, **k: None)
    assert setup_ui.needs_db_setup() is True


def test_no_setup_when_already_answered(monkeypatch):
    from adk import config, setup_ui
    monkeypatch.setattr(config.settings, "db_ready", True)
    assert setup_ui.needs_db_setup() is False


def test_old_install_with_filled_db_is_marked_ready_silently(tmp_path, monkeypatch):
    """Обновление с 3.5.10: база уже есть и заполнена → вопрос не задаём, а флаг записываем сами."""
    from adk import config, setup_ui
    path = str(tmp_path / "old.db")
    _fill_db(path)
    saved = {}
    monkeypatch.setattr(config.settings, "db_ready", False)
    monkeypatch.setattr(config.settings, "db_path", path)
    monkeypatch.setattr(config.settings, "save_section", lambda sec, vals: saved.update({sec: vals}))
    monkeypatch.setattr(config.settings, "reload", lambda: None)
    assert setup_ui.needs_db_setup() is False
    assert saved["Paths"]["db_ready"] == "true" and saved["Paths"]["db_path"] == path


def test_db_has_inventory_and_describe(tmp_path):
    from adk import setup_ui
    path = str(tmp_path / "x.db")
    assert setup_ui.db_has_inventory(path) is False            # файла нет
    open(path, "wb").close()
    assert setup_ui.db_has_inventory(path) is False            # пустой файл
    _fill_db(path, 5)
    assert setup_ui.db_has_inventory(path) is True
    d = setup_ui.describe_db(path)
    assert "5 ПК" in d and "2026-09-11" in d


# --------------------------------------------------------------------------- диалог
def test_dialog_new_db_creates_and_marks_new(qapp, tmp_path, monkeypatch):
    from adk import config, setup_ui
    saved = {}
    monkeypatch.setattr(config.settings, "save_section", lambda sec, vals: saved.update({sec: vals}))
    monkeypatch.setattr(config.settings, "reload", lambda: None)
    dlg = setup_ui.DbSetupDialog()
    dlg.rb_custom.setChecked(True)
    dlg.path_in.setText(str(tmp_path / "shared"))
    assert dlg.is_new is True
    assert "создана" in dlg.lbl_found.text() and "принтеры" in dlg.lbl_found.text()
    assert dlg.btn_ok.text() == "Создать базу здесь"
    dlg.finish()
    assert dlg.result() == 1
    assert dlg.db_path == str(tmp_path / "shared" / "pc_mapping.db")
    assert saved["Paths"] == {"db_path": dlg.db_path, "db_ready": "true"}
    assert os.path.isdir(tmp_path / "shared")                 # папка создана, а мусорный файл проверки записи убран
    assert not os.listdir(tmp_path / "shared")


def test_dialog_existing_db_is_used_as_is(qapp, tmp_path, monkeypatch):
    from adk import config, setup_ui
    path = str(tmp_path / "dept" / "pc_mapping.db")
    _fill_db(path, 7)
    monkeypatch.setattr(config.settings, "save_section", lambda *a, **k: None)
    monkeypatch.setattr(config.settings, "reload", lambda: None)
    dlg = setup_ui.DbSetupDialog()
    dlg.rb_custom.setChecked(True)
    dlg.path_in.setText(str(tmp_path / "dept"))
    assert dlg.is_new is False
    assert "База найдена" in dlg.lbl_found.text() and "7 ПК" in dlg.lbl_found.text()
    assert dlg.btn_ok.text() == "Использовать эту базу"
    dlg.finish()
    assert dlg.db_path == path and dlg.result() == 1


def test_dialog_accepts_full_db_file_path(qapp, tmp_path):
    from adk import setup_ui
    dlg = setup_ui.DbSetupDialog()
    dlg.rb_custom.setChecked(True)
    dlg.path_in.setText(str(tmp_path / "my.db"))
    assert dlg.chosen_path() == str(tmp_path / "my.db")
    dlg.path_in.setText("")
    assert dlg.btn_ok.isEnabled() is False


def test_dialog_default_is_documents_and_shows_one_pc_advice(qapp):
    from adk import setup_ui
    from adk.config import DOCS_DIR
    dlg = setup_ui.DbSetupDialog()
    assert dlg.rb_default.isChecked()
    assert dlg.chosen_path() == os.path.join(DOCS_DIR, "pc_mapping.db")
    texts = " ".join(w.text() for w in dlg.findChildren(type(dlg.lbl_found)))
    assert "одном" in texts and "ярлык" in texts           # совет: один ADK на отдел, остальным ярлык


def test_dialog_refuses_unwritable_folder(qapp, tmp_path, monkeypatch):
    from adk import config, setup_ui
    monkeypatch.setattr(config.settings, "save_section", lambda *a, **k: None)
    dlg = setup_ui.DbSetupDialog()
    dlg.rb_custom.setChecked(True)
    dlg.path_in.setText(str(tmp_path / "ro"))
    monkeypatch.setattr(os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("denied")))
    dlg.finish()
    assert dlg.result() == 0 and "нельзя писать" in dlg.lbl_found.text()


# --------------------------------------------------------------------------- первичное наполнение
def test_initial_fill_runs_scanner_then_printers_not_software(qapp, monkeypatch):
    from PyQt6.QtCore import QEventLoop, QTimer
    from adk import db, fleetpoll, setup_ui
    from adk.workers import PCScannerWorker
    calls = []
    monkeypatch.setattr(PCScannerWorker, "scan_once", classmethod(lambda cls, cf, progress=None: calls.append("scan") or 4))
    monkeypatch.setattr(fleetpoll, "fleet_hosts", lambda cf=None, online_only=True: ["PC-A", "PC-B"])
    monkeypatch.setattr(fleetpoll, "printers_live", lambda h: {"printers": [{"name": f"HP {h}", "port": "IP_10.0.0.9", "driver": "HP"}]})
    monkeypatch.setattr(fleetpoll, "software_live", lambda h: calls.append("software") or {})
    w = setup_ui.InitialFillWorker(lambda: None)
    msgs, out = [], {}
    w.progress.connect(msgs.append)
    w.finished_fill.connect(out.update)
    loop = QEventLoop()
    w.finished_fill.connect(lambda *_: loop.quit())
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(2000)
    assert calls == ["scan"]                                   # ПО не собираем — только сканер и принтеры
    assert out == {"pcs": 4, "printers_pcs": 2, "printers": 2, "error": ""}
    assert any("шаг 1 из 2" in m for m in msgs) and any("Шаг 2 из 2" in m for m in msgs)
    assert {r["name"] for r in db.printers_for_computers(["PC-A"])["PC-A"]} == {"HP PC-A"}
    txt = setup_ui.fill_summary_text(out)
    assert "ПК из домена — 4" in txt and "принтеры — 2" in txt


def test_initial_fill_reports_error_without_crash(qapp, monkeypatch):
    from PyQt6.QtCore import QEventLoop, QTimer
    from adk import setup_ui
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(PCScannerWorker, "scan_once",
                        classmethod(lambda cls, cf, progress=None: (_ for _ in ()).throw(RuntimeError("нет DC"))))
    w = setup_ui.InitialFillWorker(lambda: None)
    out = {}
    w.finished_fill.connect(out.update)
    loop = QEventLoop()
    w.finished_fill.connect(lambda *_: loop.quit())
    QTimer.singleShot(5000, loop.quit)
    w.start()
    loop.exec()
    w.wait(2000)
    assert out["error"] == "нет DC" and out["pcs"] == 0
    assert "прервано" in setup_ui.fill_summary_text(out)


def test_main_window_initial_fill_shows_progress_and_stop(qapp, monkeypatch):
    from adk import setup_ui
    from adk.main_window import ADApp
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(PCScannerWorker, "scan_once", classmethod(lambda cls, cf, progress=None: 0))
    win = ADApp("admin", "x", initial_fill=True)
    try:
        assert win.btn_fill_stop.isHidden()
        win.start_initial_fill()
        assert isinstance(win.fill_worker, setup_ui.InitialFillWorker)
        assert not win.btn_fill_stop.isHidden() and not win.btn_scan.isEnabled()
        win.fill_worker.wait(5000)
        qapp.processEvents()
        win.on_initial_fill_done({"pcs": 0, "printers_pcs": 0, "printers": 0, "error": ""})
        assert win.btn_fill_stop.isHidden() and win.btn_scan.isEnabled() and win.initial_fill is False
        assert "заполнена" in win.lbl_status.text()
    finally:
        win.close()


def test_stop_initial_fill_cancels_worker(qapp, monkeypatch):
    from adk.main_window import ADApp
    from adk.workers import PCScannerWorker
    import time
    monkeypatch.setattr(PCScannerWorker, "scan_once", classmethod(lambda cls, cf, progress=None: time.sleep(0.3) or 0))
    win = ADApp("admin", "x", initial_fill=True)
    try:
        win.start_initial_fill()
        win.stop_initial_fill()
        assert win.fill_worker.cancelled is True and not win.btn_fill_stop.isEnabled()
        win.fill_worker.wait(5000)
    finally:
        win.close()


def test_fleet_hosts_falls_back_to_ad_when_inventory_empty(monkeypatch):
    """Пустая база (новая) → список ПК берётся из AD через PCScannerWorker.workstation_names (в 3.5.10 тут был
    импорт несуществующего класса — «Опросить парк» на пустой базе падал)."""
    from adk import ad, fleetpoll
    monkeypatch.setattr(ad, "paged_search", lambda conn, flt, attrs: [{"attributes": {"name": "WS-01"}}, {"attributes": {"name": "WS-02$"}}])
    monkeypatch.setattr(ad, "get_ad_value", lambda e, k: e["attributes"][k])

    class _C:
        def unbind(self): pass
    assert fleetpoll.fleet_hosts(lambda: _C()) == ["WS-01", "WS-02"]


# --------------------------------------------------------------------------- config + ключ db_ready
def test_settings_db_ready_flag_parsed(tmp_path, monkeypatch):
    from adk import config
    ini = tmp_path / "config.ini"
    monkeypatch.setattr(config, "INI_FILE", str(ini))
    monkeypatch.setattr(config, "_ensure_dirs", lambda: None)
    s = config.Settings()
    assert s.db_ready is False
    s.save_section("Paths", {"db_path": str(tmp_path / "a.db"), "db_ready": "true"})
    s.reload()
    assert s.db_ready is True and s.db_path == str(tmp_path / "a.db")


# --------------------------------------------- старой реализации portable-режима больше нет (см. docstring)
@pytest.mark.parametrize("rel", ["adk", "docs", "README.md", "config.example.ini", "tests"])
def test_no_portable_mode_traces(rel):
    """Партия 30 зачищала остатки старой экспериментальной реализации портативного режима.
    С 3.10.0 папка ADK рядом с exe — официальная функция (config.PORTABLE_DIR), слово «портативно»
    в текстах допустимо; запрещены только имена старых глобалов, чтобы не смешать две реализации."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, rel)
    files = [path] if os.path.isfile(path) else [os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs
                                                 if f.endswith((".py", ".md", ".ini"))]
    bad = []
    for f in files:
        if f.endswith("test_batch30.py"):
            continue
        with open(f, encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
        if re.search(r"IS_PORTABLE|ADK_PORTABLE|ADK_HOME", txt):
            bad.append(os.path.relpath(f, root))
    assert not bad, bad


# =========================================================================== 3.6.0: база без WAL, сторож, бэкапы, аренда сканера
def test_no_wal_anywhere_and_existing_wal_file_is_converted(tmp_path, monkeypatch):
    """Файл, созданный 3.5.x в режиме WAL, при старте переводится в обычный журнал; новые базы WAL не получают."""
    import sqlite3
    from adk import config, db
    path = str(tmp_path / "old_wal.db")
    with contextlib.closing(sqlite3.connect(path)) as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("CREATE TABLE t (x)")
        c.commit()
    assert os.path.exists(path + "-wal") or True
    monkeypatch.setattr(config.settings, "db_path", path)
    db.init_db()
    with contextlib.closing(sqlite3.connect(path)) as c:
        assert str(c.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "delete"
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "adk", "db.py"), encoding="utf-8").read()
    assert "journal_mode=WAL" not in src


def test_connection_has_guard_and_short_busy_timeout():
    from adk import db
    with contextlib.closing(db.get_db_connection()) as c:
        assert c.guard is not None and c.guard.tripped is False
        assert int(c.execute("PRAGMA busy_timeout").fetchone()[0]) == db.BUSY_TIMEOUT_SEC * 1000
    assert db.HANG_LIMIT_SEC == 60 and db.BUSY_TIMEOUT_SEC <= 15


def test_hang_guard_interrupts_long_query_and_calls_hook(monkeypatch):
    """Запрос дольше лимита прерывается сторожем, поднимается DbHangError, хук (окно + выход) вызван один раз."""
    from adk import db
    monkeypatch.setattr(db, "HANG_LIMIT_SEC", 0.3)
    calls = []
    db.set_hang_hook(calls.append)
    try:
        # рекурсивный CTE без конца — «зависший» запрос, который не ждёт блокировку, а просто молотит
        with pytest.raises(db.DbHangError) as ei:
            db.db_execute_with_retry("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c) SELECT COUNT(*) FROM c", fetch="one")
    finally:
        db.set_hang_hook(None)
    assert "дольше" in str(ei.value) and len(calls) == 1 and isinstance(calls[0], db.DbHangError)


def test_locked_db_gives_up_within_limit_not_minutes(monkeypatch):
    """База занята другим процессом: ждём busy_timeout × попытки, но не 2,5 минуты как раньше."""
    import sqlite3
    import time
    from adk import config, db
    monkeypatch.setattr(db, "BUSY_TIMEOUT_SEC", 0.2)
    monkeypatch.setattr(db, "DB_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(db, "DB_RETRY_DELAY_SEC", 0.05)
    holder = sqlite3.connect(config.settings.db_path, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    t0 = time.monotonic()
    try:
        with pytest.raises(sqlite3.OperationalError) as ei:
            db.db_execute_with_retry("INSERT INTO meta (key, value) VALUES ('a', 'b')")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
    assert "locked" in str(ei.value).lower() and time.monotonic() - t0 < 5


def test_is_network_path():
    from adk import db
    assert db.is_network_path(r"\\srv\share\adk\pc_mapping.db") is True
    assert db.is_network_path("//srv/share/adk/pc_mapping.db") is True
    assert db.is_network_path(r"C:\Users\x\Documents\ADK\pc_mapping.db") is False
    assert db.is_network_path("/home/x/pc_mapping.db") is False


def test_backup_periodic_creates_rotates_and_respects_interval(tmp_path, monkeypatch):
    import sqlite3
    import time
    from adk import config, db
    monkeypatch.setattr(config.settings, "backup_every_hours", 6.0)
    monkeypatch.setattr(config.settings, "backup_keep", 2)
    path = config.settings.db_path
    first = db.backup_periodic()
    assert first and os.path.dirname(first) == db.backup_dir(path) and os.path.basename(first).startswith("pc_mapping-")
    with contextlib.closing(sqlite3.connect(first)) as c:              # копия — настоящая база со схемой
        assert c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='pc_inventory'").fetchone()[0] == 1
    assert db.backup_periodic() is None                                 # свежая — второй раз не делаем
    old = time.time() - 7 * 3600
    os.utime(first, (old, old))
    time.sleep(1.1)                                                     # имя копии — с точностью до секунды
    second = db.backup_periodic()
    assert second and second != first
    os.utime(second, (old, old))
    time.sleep(1.1)
    third = db.backup_periodic()
    assert third and len(db.list_backups(path)) == 2                    # keep = 2 → старейшая удалена
    assert first not in db.list_backups(path)
    monkeypatch.setattr(config.settings, "backup_every_hours", 0)
    assert db.backup_periodic() is None                                 # выключено


def test_scan_lease_single_scanner_per_shared_db():
    from adk import db
    ok, holder, until = db.scan_lease_acquire("PC-A\\ivanov")
    assert ok and holder == "PC-A\\ivanov" and len(until) == 19
    ok2, holder2, _ = db.scan_lease_acquire("PC-B\\petrov")
    assert not ok2 and holder2 == "PC-A\\ivanov"                        # второй ADK ждёт, а не сканирует параллельно
    ok3, _, _ = db.scan_lease_acquire("PC-A\\ivanov")                   # свой же — продлевает
    assert ok3
    db.scan_lease_release("PC-A\\ivanov")
    ok4, _, _ = db.scan_lease_acquire("PC-B\\petrov")
    assert ok4


def test_scan_lease_expires(monkeypatch):
    from adk import db
    db.scan_lease_acquire("PC-A\\x", minutes=0)                          # срок вышел сразу
    ok, _, _ = db.scan_lease_acquire("PC-B\\y")
    assert ok


def test_main_window_skips_scan_when_other_pc_holds_lease(qapp, monkeypatch):
    from adk import db
    from adk.main_window import ADApp
    from adk.workers import PCScannerWorker
    started = []
    monkeypatch.setattr(PCScannerWorker, "start", lambda self: started.append(1))
    w = ADApp("CORP\\admin", "x")
    w.scan_timer.stop()
    try:
        db.scan_lease_acquire("SRV-ADK\\kolya")
        w.start_scan()
        assert not started and "сканирует SRV-ADK\\kolya" in w.lbl_status.text() and w.btn_scan.isEnabled()
        db.scan_lease_release("SRV-ADK\\kolya")
        w.start_scan()
        assert started == [1]
        w.on_scan_done(0)
        ok, _, _ = db.scan_lease_acquire("SRV-ADK\\kolya")
        assert ok                                                       # после сканера аренда снята
    finally:
        w.close()


def test_hang_exit_shows_message_and_quits(qapp, monkeypatch):
    from adk import __main__ as m, db
    from adk.widgets import MessageBox
    shown, exits = [], []
    monkeypatch.setattr(MessageBox, "critical", classmethod(lambda cls, p, t, text: shown.append((t, text))))
    monkeypatch.setattr(type(qapp), "exit", lambda self, code=0: exits.append(code))
    qapp._adk_hang_shown = False
    m._db_hang_exit(db.DbHangError("тест"))
    qapp.processEvents()
    assert exits == [3] and shown and "признано зависшим" in shown[0][0]
    txt = shown[0][1]
    for phrase in ("дольше минуты", "отрезаны", "системным администраторам", "переименуйте файл базы", "администратору ПО"):
        assert phrase in txt
    m._db_hang_exit(db.DbHangError("ещё раз"))                          # второй раз окно не дублируется
    assert len(shown) == 1


def test_setup_dialog_warns_about_network_folder(qapp, monkeypatch):
    from adk import setup_ui
    dlg = setup_ui.DbSetupDialog()
    dlg.rb_custom.setChecked(True)
    dlg.path_in.setText(r"\\srv\share\ADK")
    assert "Сетевая папка" in dlg.lbl_found.text()


# =========================================================================== 3.6.1: сканер парка честно сообщает причину и не молчит
def test_scan_once_explains_empty_host_pattern(monkeypatch):
    """В AD 2 компьютера, но ни один не подходит под host_pattern → понятная ошибка, а не «0 ПК» молча."""
    from adk import ad, config
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(ad, "paged_search", lambda c, f, a: [{"attributes": {"name": "LAPTOP-7"}}, {"attributes": {"name": "ACC-01"}}])
    monkeypatch.setattr(ad, "get_ad_value", lambda e, k: e["attributes"][k])
    monkeypatch.setattr(config.settings, "host_pattern", r"^WS-\d+$")

    class _C:
        def unbind(self): pass
    with pytest.raises(RuntimeError) as ei:
        PCScannerWorker.scan_once(lambda: _C())
    assert "host_pattern" in str(ei.value) and "ADSI" in str(ei.value)   # 3.11.0: показывает и фильтр, и какой путь списка пробовался


def test_scan_once_fills_inventory_without_powershell(monkeypatch):
    """Не Windows / PowerShell недоступен → запасной сканер на Python: DNS + доступность, инвентарь заполняется."""
    import socket
    from adk import ad, config, db, netutils
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(ad, "paged_search", lambda c, f, a: [{"attributes": {"name": "WS-001"}}, {"attributes": {"name": "WS-002$"}}])
    monkeypatch.setattr(ad, "get_ad_value", lambda e, k: e["attributes"][k])
    monkeypatch.setattr(config.settings, "host_pattern", r"^WS-\d+$")
    monkeypatch.setattr(config.settings, "valid_subnets", ["10."])
    ips = {"WS-001": "10.0.0.11", "WS-002": "10.0.0.12"}
    monkeypatch.setattr(socket, "getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", (ips[h], 0))] if h in ips else (_ for _ in ()).throw(OSError("no dns")))
    monkeypatch.setattr(netutils, "is_host_alive", lambda ip, timeout=1.0: ip == "10.0.0.11")

    class _C:
        def unbind(self): pass
    msgs = []
    n = PCScannerWorker.scan_once(lambda: _C(), progress=msgs.append)
    assert n == 2
    rows = {r[0]: r for r in db.db_execute_with_retry("SELECT computer_name, ip_address, is_online FROM pc_inventory", fetch="all")}
    assert rows["WS-001"][1:] == ("10.0.0.11", 1) and rows["WS-002"][1:] == ("10.0.0.12", 0)
    assert any("Опрос 2 ПК" in m for m in msgs)
    assert db.get_inventory_summary()["total"] == 2                      # дашборд увидит наполнение


def test_run_powershell_raises_readable_error(monkeypatch):
    """Ошибка PowerShell (или не-Windows) → RuntimeError с текстом причины, а не пустой список как «успех»."""
    from adk import psrun
    from adk.workers import PCScannerWorker
    w = PCScannerWorker.__new__(PCScannerWorker)
    w._cancelled = False
    monkeypatch.setattr(psrun, "run", lambda script, timeout=60, cancelled=None, on_tick=None: psrun.PsResult(False, error="доступ запрещён — нужна учётная запись с правами администратора"))
    with pytest.raises(RuntimeError) as ei:
        w._run_powershell(["WS-001"])
    assert "доступ запрещён" in str(ei.value)
    monkeypatch.setattr(psrun, "run", lambda script, timeout=60, cancelled=None, on_tick=None: psrun.PsResult(True, stdout='{"Hostname":"WS-001","ActualIp":"10.0.0.1","Status":"ACTIVE","User":"ivanov","LastLogon":"01.09.2026 09:00"}'))
    assert w._run_powershell(["WS-001"])[0]["User"] == "ivanov"


def test_probe_hosts_falls_back_to_python_when_powershell_fails(monkeypatch):
    """Windows: PowerShell упал → сообщение в строку состояния и запасной опрос, результат всё равно есть."""
    import os
    from adk import netutils
    from adk.workers import PCScannerWorker, _Emitter
    w = PCScannerWorker.__new__(PCScannerWorker)
    w._cancelled = False
    msgs = []
    w.progress = _Emitter(msgs.append)
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setattr(PCScannerWorker, "_run_powershell", lambda self, hosts: (_ for _ in ()).throw(RuntimeError("PowerShell не запускается")))
    monkeypatch.setattr(PCScannerWorker, "_probe_python", lambda self, hosts: [{"Hostname": h, "ActualIp": "10.0.0.5", "Status": "ACTIVE", "User": "", "LastLogon": "Неизвестно"} for h in hosts])
    res = w.probe_hosts(["WS-005"])
    assert res[0]["ActualIp"] == "10.0.0.5" and any("средствами Python" in m for m in msgs)
    _ = netutils
