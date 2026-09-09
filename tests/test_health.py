"""«Здоровье ПК»: разбор JSON health/S.M.A.R.T., вердикты, журнал ошибок Windows, карта диска (treemap: «Прочее»,
выделение плитки, высота с легендой), «что можно почистить» для любого тома.
"""
import json
import os
import pytest
from datetime import datetime, timedelta
from types import SimpleNamespace

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QSplitter

from adk import db, health


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


def _smart_blob(attrs: dict) -> str:
    """{id: (current, worst, raw)} → base64 512-байтного VendorSpecific, как отдаёт MSStorageDriver_FailurePredictData."""
    import base64
    buf = bytearray(512)
    buf[0:2] = b"\x10\x00"
    for i, (aid, (cur, worst, raw)) in enumerate(attrs.items()):
        off = 2 + i * 12
        buf[off] = aid
        buf[off + 3], buf[off + 4] = cur, worst
        buf[off + 5:off + 11] = raw.to_bytes(6, "little")
    return base64.b64encode(bytes(buf)).decode()


# ------------------------------------------------------------------ 1. разбор health / S.M.A.R.T.
def test_parse_health_json_warnings():
    text = json.dumps({"boot": "2026-08-01 08:00:00", "now": "2026-09-04 12:30:00", "os": "Windows 11 Pro",
                       "total_mb": 16384, "free_mb": 1024, "cpu": 95.4,
                       "disks": [{"id": "C:", "size": 250 * 1024 ** 3, "free": 5 * 1024 ** 3},
                                 {"id": "D:", "size": 1000 * 1024 ** 3, "free": 600 * 1024 ** 3}]})
    h = health.parse_health_json(text)
    assert h["uptime_days"] == 34 and h["ram_used_pct"] == 94 and h["cpu"] == 95
    assert h["disks"][0]["low"] and not h["disks"][1]["low"]
    assert len(h["warnings"]) == 4
    assert "C:" in health.format_health(h) and "⚠️" in health.format_health(h)
    ok = health.parse_health_json(json.dumps({"boot": "2026-09-04 08:00:00", "now": "2026-09-04 09:00:00",
                                              "total_mb": 8192, "free_mb": 4096, "cpu": 3,
                                              "disks": [{"id": "C:", "size": 500 * 1024 ** 3, "free": 300 * 1024 ** 3}]}))
    assert ok["warnings"] == [] and ok["uptime"].startswith("1 ч")


def test_get_health_non_windows_returns_error(monkeypatch):
    if os.name == "nt":
        pytest.skip("на Windows выполняется реальный PowerShell")
    h = health.get_health("PC-01")
    assert "error" in h


def test_parse_smart_vendor_and_verdict():
    blob = _smart_blob({0x05: (100, 100, 0), 0x09: (95, 95, 12345), 0xC2: (35, 40, 38), 0xC5: (100, 100, 0)})
    attrs = health.parse_smart_vendor(blob)
    by_id = {a["id"]: a for a in attrs}
    assert by_id[0x09]["raw"] == 12345 and by_id[0xC2]["raw"] == 38 and by_id[0x05]["critical"]
    assert health.parse_smart_vendor(None) == [] and health.parse_smart_vendor("не base64!!") == []
    good = {"health": 0, "temp": 38, "wear": 5, "attrs": attrs}
    assert health.disk_verdict(good) == ("good", [])
    # переназначенные секторы → «Осторожно», предсказание отказа → «Плохо»
    caution = {"health": 0, "attrs": health.parse_smart_vendor(_smart_blob({0x05: (90, 90, 12)}))}
    v, reasons = health.disk_verdict(caution)
    assert v == "caution" and "Переназначенные" in reasons[0]
    assert health.disk_verdict({"predict": True, "attrs": []})[0] == "bad"
    assert health.disk_verdict({"health": 0, "wear": 97})[0] == "bad"
    assert health.disk_verdict({"health": 0, "temp": 61})[0] == "caution"
    assert health.disk_verdict({})[0] == "unknown"


def test_parse_health_json_with_physical_disks():
    blob = _smart_blob({0xC5: (100, 100, 3), 0x09: (90, 90, 50000)})
    text = json.dumps({"boot": "2026-09-04 08:00:00", "now": "2026-09-04 09:00:00", "total_mb": 8192, "free_mb": 4096, "cpu": 3,
                       "disks": [{"id": "C:", "size": 500 * 1024 ** 3, "free": 300 * 1024 ** 3, "label": "System", "fs": "NTFS"}],
                       "phys": [{"id": "0", "model": "Samsung SSD 870", "media": 4, "bus": 11, "size": 500 * 10 ** 9, "health": 0,
                                 "temp": 40, "wear": 12, "hours": None, "predict": False, "smart": blob},
                                {"id": "1", "model": "WDC WD10EZEX", "media": 3, "bus": 11, "size": 10 ** 12, "health": 2,
                                 "temp": 47, "predict": True, "smart": None}]})
    h = health.parse_health_json(text)
    ssd, hdd = h["phys"]
    assert ssd["media"] == "SSD" and ssd["bus"] == "SATA" and ssd["size_gb"] == 500
    assert ssd["hours"] == 50000                                  # наработку взяли из атрибута 09, раз счётчик пуст
    assert ssd["verdict"] == "caution" and "Секторы-кандидаты" in ssd["reasons"][0]
    assert hdd["verdict"] == "bad" and h["score"] == "bad"
    assert any("ПЛОХО" in w for w in h["warnings"]) and "WDC" in health.format_health(h)
    assert health.fmt_hours(50000).startswith("50000 ч. (~5 г.")


def test_parse_usage_json_and_squarify():
    r = "\\\\PC-01\\C$"
    text = json.dumps({"root": r, "root_files": 1024,
                       "dirs": [{"path": r + "\\Windows", "size": 30 * 1024 ** 3, "files": 120000},
                                {"path": r + "\\Users", "size": 60 * 1024 ** 3, "files": 80000},
                                {"path": r + "\\Program Files", "size": 10 * 1024 ** 3, "files": 20000}],
                       "files": [{"path": r + "\\hiberfil.sys", "size": 6 * 1024 ** 3}],
                       "hogs": [{"label": "Корзина", "path": r + "\\$Recycle.Bin", "size": 2 * 1024 ** 3}],
                       "users": [{"path": r + "\\Users\\ivanov", "size": 50 * 1024 ** 3, "files": 70000}],
                       "total_files": 220000, "errors": 3})
    u = health.parse_usage_json(text)
    assert [d["name"] for d in u["dirs"]] == ["Users", "Windows", "Program Files", "<файлы в корне>"]
    assert u["dirs"][0]["pct"] == 60.0 and u["users"][0]["name"] == "ivanov" and u["errors"] == 3
    assert health.fmt_size(6 * 1024 ** 3) == "6.0 ГБ" and health.fmt_size(512) == "512 Б"
    rects = health.squarify([60, 30, 10], 0, 0, 100, 50)
    assert len(rects) == 3
    assert abs(sum(w * h for _, _, w, h in rects) - 5000) < 1e-6        # площади суммируются в область
    assert all(0 <= x <= 100 and 0 <= y <= 50 for x, y, _, _ in rects)
    assert rects[0][2] * rects[0][3] > rects[1][2] * rects[1][3] > rects[2][2] * rects[2][3]
    assert health.squarify([], 0, 0, 10, 10) == [] and health.squarify([0, 0], 0, 0, 10, 10) == [(0, 0, 0.0, 0.0)] * 2


def test_health_dialog_renders_fake_data(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    from adk.health_ui import HealthDialog
    app = _stub_app()
    hd = HealthDialog("PC-01", app)
    blob = _smart_blob({0x05: (100, 100, 0), 0xC2: (35, 40, 41)})
    h = health.parse_health_json(json.dumps({"boot": "2026-09-01 08:00:00", "now": "2026-09-04 09:00:00", "total_mb": 16384,
                                              "free_mb": 6000, "cpu": 12, "os": "Windows 11 Pro",
                                              "disks": [{"id": "C:", "size": 500 * 1024 ** 3, "free": 40 * 1024 ** 3}],
                                              "phys": [{"id": "0", "model": "KINGSTON SA400", "media": 4, "bus": 11,
                                                        "size": 480 * 10 ** 9, "health": 0, "temp": 41, "smart": blob}]}))
    hd.show_health(h)
    assert hd.lbl_os.text() == "Windows 11 Pro" and len(hd.disk_cards) == 1
    assert hd.attr_table.rowCount() == 2 and "KINGSTON" in hd.lbl_disk_head.text()
    assert hd.cb_drive.currentText() == "C:"
    r = "\\\\PC-01\\C$"
    u = health.parse_usage_json(json.dumps({"root": r, "root_files": 0, "total_files": 10, "errors": 0,
                                            "dirs": [{"path": r + "\\Windows", "size": 3 * 1024 ** 3, "files": 5},
                                                     {"path": r + "\\Users", "size": 1024 ** 3, "files": 5}],
                                            "files": [], "hogs": [], "users": []}))
    hd.show_usage(u)
    assert hd.tbl_dirs.rowCount() == 2 and hd.treemap.items[0]["name"] == "Windows"
    assert hd.tbl_dirs.item(0, 1).data(Qt.ItemDataRole.UserRole) == 3 * 1024 ** 3
    hd.copy_report()
    assert "KINGSTON" in QApplication.clipboard().text() and "Windows" in QApplication.clipboard().text()
    assert db.audit_entries()[0][2] in ("disk_usage", "health")
    hd.close()


def _events_json(now=None):
    now = now or datetime.now()
    t = lambda h: (now - timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    return json.dumps([
        {"time": t(1), "log": "System", "level": 1, "id": 41, "source": "Kernel-Power", "msg": "Система перезагружена без корректного завершения"},
        {"time": t(2), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой службы X"},
        {"time": t(3), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой службы X"},
        {"time": t(5), "log": "Application", "level": 3, "id": 1530, "source": "User Profile Service", "msg": "Профиль"},
        {"time": t(6), "log": "Application", "level": 4, "id": 1, "source": "Info", "msg": "ок"},
        {"time": t(40), "log": "System", "level": 2, "id": 10016, "source": "DistributedCOM", "msg": "старая ошибка"},
        {"error": "Журнал Security: отказано в доступе"},
    ])


# ------------------------------------------------------------------ 2. журнал ошибок Windows
def test_events_parse_and_summary():
    d = health.parse_events_json(_events_json())
    assert len(d["events"]) == 6 and d["errors"] == ["Журнал Security: отказано в доступе"]
    assert d["events"][0]["level_text"] == "Критическая"       # отсортировано: свежие первыми
    sm = d["summary"]
    assert (sm["critical"], sm["error"], sm["warning"], sm["info"], sm["verbose"], sm["total"]) == (1, 3, 1, 1, 0, 6)
    assert sm["by_source"][0] == ("Service Control Manager", 2)
    assert sm["top"][0]["id"] == 7000 and sm["top"][0]["count"] == 2
    # предупреждения и информация в топ не попадают — только критические и ошибки
    assert all(t["source"] not in ("User Profile Service", "Info") for t in sm["top"])
    assert health.LEVELS == {1: "Критическая", 2: "Ошибка", 3: "Предупреждение", 4: "Информация", 5: "Подробно"}


def test_get_events_validates_host():
    r = health.get_events("bad host;", datetime.now() - timedelta(days=1), datetime.now())
    assert "Недопустимое" in r["error"]


def test_health_errors_tab(qapp, monkeypatch):
    """Вкладка «Ошибки»: фильтр по уровням/периоду для таблицы; счётчики считаются по показанным событиям."""
    from adk.health_ui import HealthDialog
    from test_gui import _wait

    hd = HealthDialog("WS-1", None)
    hd.show()
    _wait(lambda: hd.btn_refresh.isEnabled(), qapp, 5000)
    assert hd.tabs.tabText(3).endswith("Ошибки")
    assert [l for l, cb in hd.ev_levels.items() if cb.isChecked()] == [1, 2, 3]   # по умолчанию без Информация/Подробно
    hd.ev_levels[3].setChecked(False)
    hd._events_quick(24)
    f = hd.event_filter()
    assert f["levels"] == (1, 2) and f["logs"] == ("System", "Application")
    d = health.parse_events_json(_events_json())
    hd.show_events(d, f)
    # в таблице только критические и ошибки за 24 ч (3 из 6); счётчики — ровно по этим строкам: 1 критическая + 2 ошибки
    assert hd.tbl_events.rowCount() == 3
    assert hd.tbl_events.item(0, 3).text() == "Kernel-Power"
    tiles = [hd.ev_tiles.itemAt(i).widget() for i in range(hd.ev_tiles.count())]
    texts = [t.findChildren(type(hd.lbl_events))[1].text() for t in tiles]
    assert texts == ["1", "2", "0"]
    assert "По 3 событиям" in hd.lbl_ev_top.text() and "Service Control Manager (2)" in hd.lbl_ev_top.text()
    assert "за сутки" not in hd.lbl_ev_top.text()      # 3.2.9: отдельной сводки «за сутки» больше нет
    assert "1 журнал" in hd.lbl_events.text()
    # ошибка запроса не роняет окно
    hd.show_events({"error": "ПК не ответил"}, f)
    assert "ПК не ответил" in hd.lbl_events.text() and hd.tbl_events.rowCount() == 0
    hd.close()


def test_health_header_has_no_overlay(qapp):
    """Бейдж вердикта не должен рисоваться в размере 640×480 поверх шапки (серая «подложка»)."""
    from PyQt6.QtWidgets import QLabel
    from adk.health_ui import HealthDialog
    from test_gui import _wait

    hd = HealthDialog("WS-1", None)
    hd.show()
    _wait(lambda: hd.btn_refresh.isEnabled(), qapp, 5000)
    qapp.processEvents()
    hd.show_health({"error": "нет доступа"})
    qapp.processEvents()
    big = [l for l in hd.findChildren(QLabel) if l.isVisible() and l.width() >= 600 and l.height() >= 400]
    assert not big
    hd.close()


# ------------------------------------------------------------------ 3. карта диска (treemap)
def test_treemap_groups_small_folders(qapp):
    from adk.health_ui import TreemapWidget
    gb = 1024 ** 3
    dirs = [{"name": "Users", "path": r"\\WS\C$\Users", "size": 200 * gb, "pct": 66.7},
            {"name": "Windows", "path": r"\\WS\C$\Windows", "size": 80 * gb, "pct": 26.7}]
    dirs += [{"name": f"tiny{i}", "path": rf"\\WS\C$\tiny{i}", "size": gb // 2, "pct": 0.2} for i in range(10)]
    total = sum(d["size"] for d in dirs)
    tm = TreemapWidget()
    tm.resize(600, 320)
    tm.show()
    tm.set_items(dirs, total)
    names = [t["name"] for t in tm.tiles]
    assert names[:2] == ["Users", "Windows"] and names[-1].startswith("Прочее · 10 папок")
    assert tm.tiles[-1]["other"] and tm.tiles[-1]["size"] == 10 * (gb // 2)
    tm._layout()
    assert len(tm.rects) == 3 and tm._legend_rows >= 1
    # площадь ∝ размеру и плитки не выходят за область карты
    m = tm._map_rect()
    for r, t in tm.rects:
        assert m.contains(r.adjusted(1, 1, -1, -1))
    r_users = next(r for r, t in tm.rects if t["name"] == "Users")
    r_win = next(r for r, t in tm.rects if t["name"] == "Windows")
    assert r_users.width() * r_users.height() > r_win.width() * r_win.height() * 2
    tm.grab()   # paintEvent не падает
    # клик по «Прочее» не копирует путь; по обычной плитке — вызывает callback
    clicked = []
    tm.on_click = clicked.append
    tm.mousePressEvent(SimpleNamespace(position=lambda: r_users.center()))
    tm.mousePressEvent(SimpleNamespace(position=lambda: tm.rects[-1][0].center()))
    assert clicked == [tm.tiles[0]]
    tm.close()


def test_treemap_click_selects_tile(qapp):
    from adk.health_ui import TreemapWidget
    t = TreemapWidget()
    t.resize(600, 400)
    t.show()
    r = "\\\\WS-1\\C$"
    t.set_items([{"name": "Users", "path": r + "\\Users", "size": 60, "files": 1, "pct": 60.0},
                 {"name": "Windows", "path": r + "\\Windows", "size": 40, "files": 1, "pct": 40.0}], 100)
    clicked = []
    t.on_click = clicked.append
    t._layout()
    rect, item = t.rects[0]
    ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(rect.center()), Qt.MouseButton.LeftButton,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    t.mousePressEvent(ev)
    assert t.selected_item() is item and clicked == [item]
    t.grab()                                                     # отрисовка с выделением не падает
    t.set_items([], 0)
    assert t.selected_item() is None                             # новые данные — выделение сброшено
    t.close()


# ------------------------------------------------------------------ 4. «что можно почистить»
def test_hogs_come_from_the_same_scan_as_the_map():
    """3.2.9: «Почистить» — часть обхода карты: пути с нулевым размером отбрасываются, есть число файлов и общая сумма."""
    r = "\\\\WS-1\\D$"
    u = health.parse_usage_json(json.dumps({"root": r, "root_files": 0, "total_files": 10,
                                            "dirs": [{"path": r + "\\Архив", "size": 100, "files": 10}],
                                            "files": [], "users": [],
                                            "hogs": [{"label": "Корзина", "path": r + "\\$Recycle.Bin", "size": 5, "files": 2},
                                                     {"label": "Файл подкачки", "path": r + "\\pagefile.sys", "size": 0, "files": 0},
                                                     {"label": "Temp профиля ivanov", "path": r + "\\Users\\ivanov\\AppData\\Local\\Temp",
                                                      "size": 9, "files": 30}]}))
    assert [h["label"] for h in u["hogs"]] == ["Temp профиля ivanov", "Корзина"]     # по убыванию, нулевые — вон
    assert u["hogs"][0]["files"] == 30 and u["hogs_total"] == 14


def test_health_dialog_hogs_tab_follows_map_volume(qapp, monkeypatch):
    """Вкладка «Почистить» без своего выбора тома и кнопки: заполняется вместе с картой и говорит, какая это доля карты;
    на томе без известных путей — честное «нет», а не выдуманная корзина."""
    from adk.health_ui import HealthDialog
    monkeypatch.setattr(health, "get_health", lambda host, timeout=40: {"error": "нет сети"})
    app = SimpleNamespace(get_conn=lambda: None, admin_name="admin")
    hd = HealthDialog("WS-1", app)
    hd.show()
    assert not hasattr(hd, "cb_hogs_drive") and not hasattr(hd, "btn_hogs")
    r = "\\\\WS-1\\D$"
    u = health.parse_usage_json(json.dumps({"root": r, "root_files": 0, "total_files": 100,
                                            "dirs": [{"path": r + "\\Архив", "size": 40 * 1024 ** 3, "files": 90},
                                                     {"path": r + "\\$Recycle.Bin", "size": 10 * 1024 ** 3, "files": 10}],
                                            "files": [], "users": [],
                                            "hogs": [{"label": "Корзина", "path": r + "\\$Recycle.Bin", "size": 10 * 1024 ** 3, "files": 10}]}))
    hd.show_usage(u)
    assert hd.tbl_hogs.rowCount() == 1 and hd.tbl_hogs.item(0, 0).toolTip() == r + "\\$Recycle.Bin"   # путь — в подсказке
    assert "20.0%" in hd.lbl_hogs.text() and "10.0 ГБ" in hd.lbl_hogs.text()      # 10 из 50 ГБ карты
    u["hogs"], u["hogs_total"] = [], 0
    hd.show_usage(u)
    assert hd.tbl_hogs.rowCount() == 0 and "не найдено" in hd.lbl_hogs.text() and "На компьютере" in hd.lbl_hogs.text()
    hd.close()


def _usage(n_dirs=16):
    root = "\\\\WS-1\\C$"
    sizes = [198, 91, 41, 38, 22, 17, 15, 12, 9, 4, 3, 2, 1, 1, 0.5, 0.4][:n_dirs]
    dirs = [{"name": f"Folder_{i}", "path": f"{root}\\Folder_{i}", "size": int(s * 1024 ** 3), "files": 10,
             "pct": round(s / 443 * 100, 1)} for i, s in enumerate(sizes)]
    return {"root": root, "total": 443 * 1024 ** 3, "total_files": 1000, "dirs": dirs, "files": [], "hogs": [], "users": [], "errors": 0}


# ------------------------------------------------------------------ 4б. плитка ↔ строка таблицы, двойной клик → Проводник
def test_treemap_click_selects_row_and_double_click_opens_folder(qapp, monkeypatch):
    """3.2.9: клик по плитке выделяет ту же папку в таблице «Папки»; двойной клик по строке открывает её
    на ПК пользователя в Проводнике (в тесте открытие подменено — проверяем, какой путь ушёл)."""
    from adk import health_ui
    from adk.health_ui import HealthDialog
    monkeypatch.setattr(health, "get_health", lambda host, timeout=40: {"error": "нет сети"})
    opened: list[str] = []
    monkeypatch.setattr(health_ui, "open_in_explorer", opened.append)
    hd = HealthDialog("WS-1", SimpleNamespace(get_conn=lambda: None, admin_name="admin"))
    hd.show()
    hd.tabs.setCurrentIndex(2)
    u = _usage(6)
    hd.show_usage(u)
    hd.usage_tabs.setCurrentIndex(1)                            # пользователь смотрел «Файлы»
    hd.treemap.on_click(u["dirs"][2])                            # клик по плитке Folder_2
    assert hd.usage_tabs.currentIndex() == 0                     # переключились на «Папки»
    sel = hd.tbl_dirs.selectedItems()
    assert sel and hd.tbl_dirs.item(sel[0].row(), 0).text() == "Folder_2"
    assert QApplication.clipboard().text() == u["dirs"][2]["path"]     # путь по-прежнему копируется
    hd.tbl_dirs.itemDoubleClicked.emit(hd.tbl_dirs.item(sel[0].row(), 1))   # двойной клик по любой ячейке строки
    assert opened == [u["dirs"][2]["path"]] and "Проводнике" in hd.lbl_status.text()
    # на не-Windows реальный open_in_explorer вежливо отказывает, а не падает
    monkeypatch.undo()
    if os.name != "nt":
        with pytest.raises(OSError):
            health_ui.open_in_explorer(u["dirs"][0]["path"])
    hd.close()


# ------------------------------------------------------------------ 5. высота карты и сплиттер
def test_treemap_min_height_grows_with_legend(qapp, monkeypatch):
    from adk.health_ui import HealthDialog, TreemapWidget
    monkeypatch.setattr(health, "get_health", lambda host, timeout=40: {"error": "нет сети"})
    hd = HealthDialog("WS-1", SimpleNamespace(get_conn=lambda: None, admin_name="admin"))
    hd.show()
    hd.tabs.setCurrentIndex(2)
    base = hd.treemap.minimumHeight()
    assert base == TreemapWidget.MAP_MIN_H            # без данных — только под плитки
    hd.show_usage(_usage())
    qapp.processEvents()
    rows = hd.treemap._legend_rows_for(hd.treemap.width())
    assert rows >= 2                                   # 16 папок в одну строку не влезают
    assert hd.treemap.minimumHeight() == TreemapWidget.MAP_MIN_H + rows * TreemapWidget.LEGEND_ROW + 8
    # плитки и легенда лежат внутри виджета, а вкладки — строго ниже него (сплиттер, а не наложение)
    assert isinstance(hd.usage_split, QSplitter) and hd.usage_split.count() == 2
    assert hd.treemap._map_rect().bottom() + rows * TreemapWidget.LEGEND_ROW + 8 <= hd.treemap.height() + 1
    assert hd.usage_split.orientation() == Qt.Orientation.Horizontal          # таблицы справа от карты, не под ней
    assert hd.usage_tabs.geometry().left() >= hd.treemap.geometry().right()
    hd.close()


# ------------------------------------------------------------------ 6. выбор тома для карты диска
def test_drive_picker_lists_all_volumes_and_map_uses_chosen_one(qapp, monkeypatch):
    """После опроса ПК в выборе тома (стилизованная кнопка-меню) — все диски с подсказкой о месте;
    «Построить карту» идёт по выбранному тому, а не всегда по C:."""
    from adk.health_ui import HealthDialog
    from adk.widgets import DrivePicker
    monkeypatch.setattr(health, "get_health", lambda host, timeout=40: {"error": "нет сети"})
    hd = HealthDialog("WS-1", SimpleNamespace(get_conn=lambda: None, admin_name="admin"))
    hd.show()
    assert isinstance(hd.cb_drive, DrivePicker) and hd.cb_drive.currentText() == "C:"
    hd.show_health(health.parse_health_json(json.dumps({
        "boot": "2026-09-01 08:00:00", "now": "2026-09-05 11:40:00", "total_mb": 16384, "free_mb": 5100, "cpu": 10, "os": "Windows 11",
        "disks": [{"id": "C:", "size": 476 * 1024 ** 3, "free": 31 * 1024 ** 3, "label": "System", "fs": "NTFS"},
                  {"id": "D:", "size": 931 * 1024 ** 3, "free": 402 * 1024 ** 3, "label": "Data", "fs": "NTFS"}], "phys": []})))
    assert hd.cb_drive.count() == 2
    titles = [a.text() for a in hd.cb_drive.menu.actions()]
    assert any("D:" in t and "Data" in t and "402" in t for t in titles)     # подсказка «сколько свободно»
    called = []
    monkeypatch.setattr(health, "get_disk_usage", lambda comp, drive, top: called.append(drive) or {"error": "стоп"})
    hd.cb_drive.setCurrentText("D:")
    assert "D:" in hd.cb_drive.text()
    hd.load_usage()
    qapp.processEvents()
    assert called == ["D:"] and "D$" in hd.lbl_usage.text()
    hd.close()


def test_health_has_diagnostics_placeholder_tab(qapp):
    """3.3.0: пятая вкладка «Диагностика» — заглушка под будущие проверки: картинка + честный текст."""
    from adk.health_ui import HealthDialog, _diagnostics_placeholder
    from adk.widgets import app_palette
    hd = HealthDialog("WS-101", None)
    assert hd.tabs.tabText(hd.tabs.count() - 1) == "Диагностика" and not hd.tabs.tabIcon(hd.tabs.count() - 1).isNull()
    assert "пусто" in hd.lbl_diag.text().lower() or "ничего" in hd.lbl_diag.text().lower() or "выехал" in hd.lbl_diag.text()
    pm = _diagnostics_placeholder(app_palette())
    assert not pm.isNull() and pm.width() == 200
    hd.close()
