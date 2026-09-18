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


def test_no_setup_for_postgres(monkeypatch):
    from adk import config, setup_ui
    monkeypatch.setattr(config.settings, "db_ready", False)
    monkeypatch.setattr(config.settings, "db_backend", "postgres")
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


# --------------------------------------------------------------------------- portable-режима больше нет
@pytest.mark.parametrize("rel", ["adk", "docs", "README.md", "config.example.ini", "tests"])
def test_no_portable_mode_traces(rel):
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
        if re.search(r"IS_PORTABLE|ADK_PORTABLE|ADK_HOME|portable", txt, re.I):
            bad.append(os.path.relpath(f, root))
    assert not bad, bad
