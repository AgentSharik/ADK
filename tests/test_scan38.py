"""3.8.0: стартовый вопрос сканирования, полный опрос с круговым прогрессом, «Область», порядок свободного IP."""
from __future__ import annotations

import time

import pytest
from PyQt6.QtWidgets import QLabel, QPushButton

from adk import ad, config, db


# ---------------------------------------------------------------- заглушки AD (как в test_gui)
class FakeAttr:
    def __init__(self, values):
        self.values = list(values) if values is not None else []
        self.value = self.values[0] if self.values else None


class FakeEntry:
    def __init__(self, dn, **attrs):
        self.entry_dn = dn
        self._a = {k: FakeAttr(v if isinstance(v, list) else [v]) for k, v in attrs.items()}

    def __getitem__(self, key):
        return self._a[key]


class FakeConn:
    def __init__(self, entries):
        self._all = entries

    def unbind(self):
        return True


PCS = [FakeEntry("CN=WS-101,OU=x", name="WS-101$"), FakeEntry("CN=WS-102,OU=x", name="WS-102$")]
USERS_A = [FakeEntry("CN=Иванов,OU=x", sAMAccountName="ivanov", displayName="Иванов И.И.", company="Филиал А"),
           FakeEntry("CN=Петров,OU=x", sAMAccountName="petrov", displayName="Петров П.П.", company="Филиал А"),
           FakeEntry("CN=Сидоров,OU=x", sAMAccountName="sidorov", displayName="Сидоров С.С.", company="Филиал Б")]


def _wait(cond, app, ms=5000):
    t0 = time.time()
    while not cond() and (time.time() - t0) * 1000 < ms:
        app.processEvents()
        time.sleep(0.01)
    return cond()


# ---------------------------------------------------------------- круговая диаграмма и вопрос
def test_donut_widget_paints_percent(qapp):
    from adk.scan_ui import DonutWidget
    d = DonutWidget(120)
    d.set_progress(3, 4)
    assert not d.grab().isNull()
    d.set_progress(0, 0)          # пустой план — просто кольцо, без цифры
    assert not d.grab().isNull()


def test_startup_dialog_three_choices(qapp):
    from adk.scan_ui import StartupScanDialog
    dlg = StartupScanDialog()
    tiles = [b for b in dlg.findChildren(QPushButton) if b.findChildren(QLabel)]
    assert len(tiles) == 3
    names = " | ".join(l.text() for b in tiles for l in b.findChildren(QLabel))
    assert "Всё" in names and "Только ПК" in names and "Не сейчас" in names
    for b, want in zip(tiles, ("full", "pcs", "skip")):
        b.click()
        assert dlg.choice == want


def test_full_summary_text():
    from adk.scan_ui import full_summary_text
    full = full_summary_text({"pcs": 120, "online": 30, "printers": 34, "printers_pcs": 30,
                              "specs_pcs": 29, "mode": "full"})
    assert "ПК: 120" in full and "принтеры: 34 на 30 ПК" in full and "характеристики: 29 ПК" in full
    assert "программы" not in full          # 3.9.2: шаг «программы» убран из полного опроса
    assert "принтеры" not in full_summary_text({"pcs": 3, "mode": "pcs"})
    assert "прервано" in full_summary_text({"error": "нет связи", "mode": "full"})


# ---------------------------------------------------------------- полный опрос: фазы и итог
def test_full_scan_worker_phases(monkeypatch):
    from adk import fleetpoll
    from adk.scan_ui import FullScanWorker
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(config.settings, "host_pattern", r"^WS-\d+$")
    monkeypatch.setattr(ad, "paged_search", lambda c, f, attrs, **kw: list(c._all))
    monkeypatch.setattr(PCScannerWorker, "probe_hosts",
                        lambda self, hosts: [{"Hostname": h, "ActualIp": "10.0.0.1", "Status": "ACTIVE",
                                              "User": "", "LastLogon": "Неизвестно"} for h in hosts])
    monkeypatch.setattr(PCScannerWorker, "_index_printers", lambda self, hosts: None)
    monkeypatch.setattr(fleetpoll, "fleet_hosts", lambda cf, online_only=True: ["WS-101"])
    monkeypatch.setattr(fleetpoll, "printers_live",
                        lambda h: {"printers": [{"name": "HP LaserJet", "port": "IP_10.0.0.5", "kind": "network", "ip": "10.0.0.5"}]})
    monkeypatch.setattr(fleetpoll, "software_live",
                        lambda h: (_ for _ in ()).throw(AssertionError("3.9.2: ПО не должно опрашиваться полным опросом")))
    monkeypatch.setattr(fleetpoll, "specs_live",
                        lambda h: {"specs": {"os": {"Название": "Windows 11 Pro"}, "cpu": {"Название": "i5"}}})

    def fake_poll(hosts, fn, progress=None, cancelled=None, workers=None):
        out = {}
        for i, h in enumerate(hosts, 1):
            out[h] = fn(h)
            if progress:
                progress(i, len(hosts), h)
        return out

    monkeypatch.setattr(fleetpoll, "poll_fleet", fake_poll)
    w = FullScanWorker(lambda: FakeConn(PCS), "full")
    plans, units, done = [], [], []
    w.plan.connect(lambda p, n: plans.append((p, n)))
    w.unit.connect(lambda p, d, h: units.append((p, d, h)))
    w.finished_full.connect(done.append)
    w.run()
    assert done and not done[0]["error"]
    assert done[0]["pcs"] == 2 and done[0]["online"] == 1
    assert done[0]["printers"] == 1 and done[0]["printers_pcs"] == 1
    assert "sw" not in done[0] and "sw_pcs" not in done[0]   # 3.9.2: фазы «программы» больше нет
    assert done[0]["specs_pcs"] == 1                     # 3.9.0: характеристики собраны и в базе
    # план: шаги объявлены, объём шага принтеров после шага 1 уточнён до фактического списка онлайн-ПК
    assert ("pcs", 2) in plans and ("printers", 1) in plans and ("specs", 1) in plans
    assert all(p != "software" for p, _ in plans)
    assert units[-3][0] == "pcs" and units[-2][0] == "printers" and units[-1][0] == "specs"
    # принтеры живого опроса записаны в базу — они находятся поиском по IP
    assert any(r["ip"] == "10.0.0.5" for r in db.printer_summary())


def test_full_scan_worker_pcs_only_skips_fleet(monkeypatch):
    from adk import fleetpoll
    from adk.scan_ui import FullScanWorker
    from adk.workers import PCScannerWorker
    monkeypatch.setattr(config.settings, "host_pattern", r"^WS-\d+$")
    monkeypatch.setattr(ad, "paged_search", lambda c, f, attrs, **kw: list(c._all))
    monkeypatch.setattr(PCScannerWorker, "probe_hosts",
                        lambda self, hosts: [{"Hostname": h, "ActualIp": "10.0.0.1", "Status": "OFFLINE",
                                              "User": "", "LastLogon": "Неизвестно"} for h in hosts])
    monkeypatch.setattr(PCScannerWorker, "_index_printers", lambda self, hosts: None)

    def boom(*a, **k):
        raise AssertionError("режим «только ПК» не должен трогать живой опрос парка")

    monkeypatch.setattr(fleetpoll, "fleet_hosts", boom)
    w = FullScanWorker(lambda: FakeConn(PCS), "pcs")
    done = []
    w.finished_full.connect(done.append)
    w.run()
    assert done and done[0]["pcs"] == 2 and done[0]["printers_pcs"] == 0


# ---------------------------------------------------------------- «Область»: ПК организации
def test_org_computers_maps_users_to_pcs(monkeypatch):
    from adk import fleetpoll
    monkeypatch.setattr(ad, "paged_search", lambda c, f, attrs, **kw: list(c._all))
    db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.0.9", "Status": "ACTIVE", "User": "ivanov",
                                "LastLogon": "01.09.2026"},
                               {"Hostname": "WS-202", "ActualIp": "10.0.0.10", "Status": "ACTIVE", "User": "sidorov",
                                "LastLogon": "01.09.2026"}], "2026-09-04 10:00:00")
    hosts, by_comp = fleetpoll.org_computers(lambda: FakeConn(USERS_A), "Филиал А")
    assert hosts == ["WS-101"]                          # только организация А: Иванов на WS-101
    assert by_comp == {"WS-101": "Иванов И.И."}


def test_org_picker_dialog_filters_and_picks(qapp, monkeypatch):
    from adk.widgets import OrgPickerDialog
    monkeypatch.setattr(ad, "get_all_attribute_values", lambda c, attr: ["Филиал А", "Филиал Б"])
    d = OrgPickerDialog(lambda: FakeConn([]))
    assert _wait(lambda: d.list.count() == 2, qapp, 3000)
    d.filter.setText("Б")
    assert d.list.count() == 1 and d.list.currentItem().text() == "Филиал Б"   # единственный — выбран сам
    d._pick()
    assert d.choice == "Филиал Б" and d.result() == 1


def test_software_dialog_org_rows_show_user(qapp):
    from adk.fleet import SoftwareDialog
    d = SoftwareDialog("", None)
    d._org_users, d._org_name = {"WS-101": "Иванов И.И."}, "Филиал А"
    d._fleet_done({"WS-101": {"software": [{"name": "1С", "version": "8.3"}]}})
    assert d._org_rows == [("Иванов И.И.", "WS-101", "1С", "8.3")]
    assert d.fleet.rowCount() == 1 and d.fleet.item(0, 0).text() == "Иванов И.И."
    assert d.fleet.item(0, 2).text() == "1С"
    assert "Филиал А" in d.fleet_lbl.text()


def test_printers_dialog_org_rows_show_user_and_kind(qapp):
    from adk.dialogs import PrintersDialog
    d = PrintersDialog(None, None)
    d._org_users, d._org_name = {"WS-101": "Иванов И.И."}, "Филиал А"
    d._live_done({"WS-101": {"printers": [{"name": "HP LaserJet", "port": "IP_10.0.0.5", "kind": "network", "ip": "10.0.0.5"}]}})
    assert d.table.rowCount() == 1
    assert d.table.item(0, 0).text() == "HP LaserJet"
    assert "сетевой" in d.table.item(0, 1).text()          # тип подключения
    assert d.table.item(0, 2).text() == "10.0.0.5"          # адрес
    assert d.table.item(0, 3).text() == "Иванов И.И."       # кто подключён
    assert d.table.item(0, 4).text() == "WS-101"            # на каком ПК


# ---------------------------------------------------------------- иконка: логотип в трее и на панели задач
def test_app_icon_is_logo_with_many_sizes(qapp):
    import os
    from PyQt6.QtCore import QSize
    from adk.tray import app_icon, asset_path
    assert os.path.exists(asset_path("logo.png"))       # логотип проекта на месте
    ic = app_icon()
    sizes = {s.width() for s in ic.availableSizes()}
    assert 16 in sizes and 128 in sizes                  # и трей (16), и панель задач (32–48) получают свой размер
    assert not ic.pixmap(QSize(16, 16)).isNull()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])


def test_full_summary_text_shows_user_sources():
    """3.9.6: в итоге полного опроса видно, откуда взялся «кто за ПК» — машина/CSV/КД."""
    from adk.scan_ui import full_summary_text
    txt = full_summary_text({"pcs": 120, "mode": "full", "printers": 5, "printers_pcs": 4,
                             "user_wmi": 30, "user_csv": 10, "user_other": 5})
    assert "кто за ПК: машина 30 · CSV 10 · КД 5" in txt and txt.startswith("✅")


def test_full_summary_text_warns_when_no_user_source():
    """Ни один источник не ответил, но включённые ПК есть — заметное предупреждение в статусе."""
    from adk.scan_ui import full_summary_text
    txt = full_summary_text({"pcs": 120, "mode": "full", "printers": 5, "printers_pcs": 4,
                             "user_wmi": 0, "user_csv": 0, "user_other": 0, "user_missing_warn": True})
    assert txt.startswith("⚠️") and "не ответил ни на одной" in txt and "5985/135" in txt
