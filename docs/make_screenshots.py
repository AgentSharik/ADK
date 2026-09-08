"""Скриншоты для README (offscreen, заглушка LDAP). Запуск: QT_QPA_PLATFORM=offscreen python docs/make_screenshots.py"""
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from adk import ad, config, db, netutils  # noqa: E402

config.settings.db_path = os.path.join(tempfile.mkdtemp(), "shot.db")
db.init_db()
import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from adk.widgets import apply_theme  # noqa: E402

app = QApplication([])
from adk.theme import PRESET_THEMES, theme_design  # noqa: E402
apply_theme({**config.settings.design, **theme_design(PRESET_THEMES["dark"])})   # тема по умолчанию «Графит и титан»
now = datetime.now(timezone.utc)

people = [
    ("ivanov", "Иванов Иван Петрович", "Иванов", "Иван", 512, "2554567", "ИТ-отдел", "Системный администратор", "каб. 214", now - timedelta(days=80), None),
    ("petrova", "Петрова Анна Сергеевна", "Петрова", "Анна", 512, "2554512", "Бухгалтерия", "Главный бухгалтер", "каб. 105", now - timedelta(days=20), None),
    ("sidorov", "Сидоров Пётр Ильич", "Сидоров", "Пётр", 512, "2554590", "Отдел продаж", "Менеджер", "каб. 310", now - timedelta(days=88), now - timedelta(minutes=12)),
    ("kuznetsova", "Кузнецова Мария", "Кузнецова", "Мария", 514, "", "Отдел кадров", "Специалист", "каб. 102", now - timedelta(days=200), None),
    ("smirnov", "Смирнов Алексей", "Смирнов", "Алексей", 512, "2554533", "ИТ-отдел", "Инженер", "каб. 214", now - timedelta(days=5), None),
]
entries = []
for login, disp, sn, gn, uac, phone, dept, title, office, pwd, lock in people:
    attrs = dict(sAMAccountName=login, displayName=disp, sn=sn, givenName=gn, userAccountControl=uac,
                 telephoneNumber=phone, department=dept, title=title, physicalDeliveryOfficeName=office,
                 company="ООО «Пример»", mail=f"{login}@example.local", pwdLastSet=pwd, badPwdCount=3 if lock else 0,
                 memberOf=["CN=Domain Users,CN=Users,DC=example,DC=local", f"CN={dept},OU=Groups,DC=example,DC=local"])
    if lock:
        attrs["lockoutTime"] = lock
    entries.append(tg.FakeEntry(f"CN={disp},OU=Employees,DC=example,DC=local", **attrs))

import re  # noqa: E402


class ShotConn(tg.FakeConn):
    """FakeConn, понимающий точечный фильтр (sAMAccountName=x) — нужен для «Группы как у…»."""

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        if "objectClass=group" in flt:
            self.entries = GROUPS
            return True
        names = re.findall(r"\(sAMAccountName=([^)*]+)\)", flt)
        self.entries = [e for e in self._all if not names or e["sAMAccountName"].value in names]
        return True


GROUPS = [tg.FakeEntry(f"CN={g},OU=Groups,DC=example,DC=local", cn=g)
          for g in ("VPN-Users", "Remote-Desktop", "ИТ-отдел", "Бухгалтерия", "Printer-2F", "WiFi-Staff")]


conn = ShotConn(entries)
ad.make_connection = lambda *a, **k: conn
online = {"WS-101": True, "WS-105": True, "WS-133": False, "WS-102": False, "WS-214": True}
ips = {"WS-101": "10.0.2.11", "WS-105": "10.0.2.15", "WS-133": "10.0.2.33", "WS-102": "10.0.2.12", "WS-214": "10.0.2.44"}
netutils.get_computer_network_info = lambda n, **kw: (ips.get(n, "Не найден"), online.get(n, False))
for login, pc in (("ivanov", "WS-101"), ("petrova", "WS-105"), ("sidorov", "WS-133"), ("kuznetsova", "WS-102"), ("smirnov", "WS-214")):
    db.save_computer_for_login(login, pc)
db.batch_update_inventory([{"Hostname": pc, "ActualIp": ips[pc], "Status": "ACTIVE", "User": login, "LastLogon": "03.09.2026 18:12"}
                           for login, pc in (("ivanov", "WS-101"), ("petrova", "WS-105"), ("sidorov", "WS-133"), ("kuznetsova", "WS-102"), ("smirnov", "WS-214"))],
                          (datetime.now() - timedelta(days=2, hours=3)).strftime("%Y-%m-%d %H:%M:%S"))
db.batch_update_inventory([{"Hostname": pc, "ActualIp": ips[pc], "Status": "ACTIVE" if online[pc] else "OFFLINE", "User": login, "LastLogon": "03.09.2026 18:12"}
                           for login, pc in (("ivanov", "WS-101"), ("petrova", "WS-105"), ("sidorov", "WS-133"), ("kuznetsova", "WS-102"), ("smirnov", "WS-214"))],
                          datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
# инвентарные CSV с принтерами (виртуальные — Microsoft PDF/XPS/OneNote — должны отфильтроваться)
hw_dir = os.path.join(tempfile.mkdtemp(), "hardware")
os.makedirs(hw_dir)
config.settings.invent_hardware_dir = hw_dir
PRINTERS = {
    "WS-101": [("HP LaserJet M404dn", "IP_10.0.2.50", "Да"), ("Microsoft Print to PDF", "PORTPROMPT:", ""), ("Canon LBP6030", "USB001", "")],
    "WS-105": [("HP LaserJet M404dn", "IP_10.0.2.50", "Да"), ("Microsoft XPS Document Writer", "PORTPROMPT:", ""), ("OneNote (Desktop)", "nul:", "")],
    "WS-133": [("Kyocera ECOSYS P3145dn", "10.0.2.51_1", "Да"), ("Fax", "SHRFAX:", "")],
    "WS-102": [("Kyocera ECOSYS P3145dn", "10.0.2.51_1", ""), ("Epson L3150", "USB002", "Да")],
    "WS-214": [("HP LaserJet M404dn", "IP_10.0.2.50", ""), ("Kyocera на PRINTSRV", "\\\\PRINTSRV\\KYO-2F", "Да")],
}
for pc, plist in PRINTERS.items():
    lines = ["Операционная система;Название;0;Windows 11 Pro 23H2", "Процессор;Название;0;Intel Core i5-12400",
             "Оперативная память;Размер;0;17179869184", "Оперативная память;Частота;0;3200",
             "Диск;Наименование;0;Samsung SSD 980", "Диск;Размер;0;500107862016", "Диск;Тип носителя;0;SSD"]
    for i, (name, port, default) in enumerate(plist):
        lines += [f"Принтер;Название;{i};{name}", f"Принтер;Порт;{i};{port}", f"Принтер;По умолчанию;{i};{default}"]
    open(os.path.join(hw_dir, f"{pc}.csv"), "w", encoding="cp1251", newline="").write("\n".join(lines) + "\n")
netutils.index_printers(PRINTERS)   # как это делает сканер после опроса парка

for q in ("иванов", "10.0.2.5", "WS-133", "бухгалтерия"):
    db.save_search_query(q, "CORP\\admin")
for adm, act, tgt, det, mins in (("CORP\\admin", "reset_password", "sidorov", "смена при входе", 3),
                                 ("CORP\\admin", "unlock", "sidorov", "", 4),
                                 ("CORP\\helpdesk", "restart", "WS-105", "", 25),
                                 ("CORP\\admin", "group_add", "smirnov", "VPN-Users", 70),
                                 ("CORP\\helpdesk", "bind_pc", "petrova", "WS-105", 300),
                                 ("CORP\\admin", "create_user", "smirnov", "CN=Смирнов А.,OU=Employees", 2000)):
    db.db_execute_with_retry("INSERT INTO audit_log (ts, admin, action, target, details) VALUES (?,?,?,?,?)",
                             ((datetime.now() - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S"), adm, act, tgt, det))

from adk.main_window import ADApp  # noqa: E402
from adk.dialogs import AuditLogDialog, ResetPasswordDialog, UserCardDialog  # noqa: E402

ADApp.start_scan = lambda s: None
# окно входа (3.2.9 — в языке дизайна приложения)
from adk.dialogs import LoginDialog  # noqa: E402
_ld = LoginDialog("", saved_user="CORP\\admin")
_ld.show(); tg._wait(lambda: False, app, 300)
_ld.grab().save(os.path.join(ROOT, "docs", "login.png"))
_ld.close()

w = ADApp("CORP\\admin", "x")
w.resize(1400, 820)
w.show()
w.active_ad_total = 5
w.refresh_dashboard()
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "dashboard.png"))

w.search_input.setText("отдел")
w.start_search()
tg._wait(lambda: w.table.rowCount() >= 5, app, 5000)
row = next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "sidorov")
w.select_row(row)
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "search.png"))

card = UserCardDialog(entries[2], w, w)
card.resize(960, 640)
card.show()
tg._wait(lambda: card.current_ip != "Не найден", app, 3000)
tg._wait(lambda: False, app, 300)
card.grab().save(os.path.join(ROOT, "docs", "user_card.png"))
card.close()

rp = ResetPasswordDialog("sidorov", w, fio="Сидоров Пётр Ильич")
rp.show()
tg._wait(lambda: False, app, 200)
rp.grab().save(os.path.join(ROOT, "docs", "reset_password.png"))
rp.close()

al = AuditLogDialog(w, w)
al.show()
tg._wait(lambda: False, app, 300)
al.grab().save(os.path.join(ROOT, "docs", "audit_log.png"))
al.close()

# --- поиск по принтеру: клик по бейджу → кто подключён
w.search_input.setText("иванов")
w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1, app, 5000)
w.select_row(0)
tg._wait(lambda: False, app, 300)
from adk.widgets import BadgeButton  # noqa: E402
badge = w.printers_box.findChildren(BadgeButton)[0]
netutils.is_printer_alive = lambda ip, **kw: ip == "10.0.2.50"
badge.click()
tg._wait(lambda: w.table.rowCount() >= 4, app, 5000)
w.select_row(0)                       # первая строка — сам принтер, инспектор принтера
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "printer_search.png"))

# --- здоровье ПК 3.2: обзор, S.M.A.R.T., карта диска (данные подготовлены — offscreen без PowerShell)
import base64  # noqa: E402
import json  # noqa: E402
from adk import health  # noqa: E402
from adk.health_ui import HealthDialog  # noqa: E402


def _smart(attrs):
    buf = bytearray(512)
    buf[0:2] = b"\x10\x00"
    for i, (aid, (cur, worst, raw)) in enumerate(attrs.items()):
        off = 2 + i * 12
        buf[off] = aid
        buf[off + 3], buf[off + 4] = cur, worst
        buf[off + 5:off + 11] = raw.to_bytes(6, "little")
    return base64.b64encode(bytes(buf)).decode()


_ssd = _smart({0x05: (100, 100, 0), 0x09: (97, 97, 8760), 0x0C: (99, 99, 412), 0xB1: (99, 99, 3), 0xB3: (100, 100, 0), 0xB7: (100, 100, 0),
               0xBB: (100, 100, 0), 0xBE: (63, 50, 37), 0xC7: (100, 100, 0), 0xF1: (99, 99, 5_400_000_000), 0xF2: (99, 99, 9_100_000_000)})
_hdd = _smart({0x01: (100, 253, 0), 0x03: (96, 95, 2116), 0x04: (98, 98, 2140), 0x05: (97, 97, 48), 0x07: (100, 253, 0), 0x09: (41, 41, 52014),
               0x0A: (100, 100, 0), 0x0C: (98, 98, 2090), 0xC0: (200, 200, 74), 0xC1: (197, 197, 10520), 0xC2: (107, 91, 43),
               0xC4: (97, 97, 48), 0xC5: (200, 200, 8), 0xC6: (200, 200, 0), 0xC7: (200, 200, 0)})
_h = health.parse_health_json(json.dumps({
    "boot": "2026-08-21 08:12:00", "now": "2026-09-05 11:40:00", "total_mb": 16384, "free_mb": 5100, "cpu": 17, "os": "Windows 11 Pro 23H2",
    "disks": [{"id": "C:", "size": 476 * 1024 ** 3, "free": 31 * 1024 ** 3, "label": "System", "fs": "NTFS"},
              {"id": "D:", "size": 931 * 1024 ** 3, "free": 402 * 1024 ** 3, "label": "Data", "fs": "NTFS"}],
    "phys": [{"id": "0", "model": "Samsung SSD 870 EVO 500GB", "serial": "S5Y2NG0R123456A", "fw": "SVT02B6Q", "media": 4, "bus": 11,
              "size": 500107862016, "health": 0, "temp": 37, "wear": 4, "hours": 8760, "predict": False, "smart": _ssd},
             {"id": "1", "model": "WDC WD10EZEX-08WN4A0", "serial": "WD-WCC6Y4ABC123", "fw": "01.01A01", "media": 3, "bus": 11,
              "size": 1000204886016, "health": 1, "temp": 43, "hours": 52014, "predict": False, "smart": _hdd}]}))
_r = "\\\\WS-101\\C$"
_u = health.parse_usage_json(json.dumps({
    "root": _r, "root_files": 9 * 1024 ** 3, "total_files": 412_318, "errors": 14,
    "dirs": [{"path": _r + "\\Users", "size": 198 * 1024 ** 3, "files": 210000}, {"path": _r + "\\Windows", "size": 41 * 1024 ** 3, "files": 150000},
             {"path": _r + "\\Program Files", "size": 38 * 1024 ** 3, "files": 30000}, {"path": _r + "\\Program Files (x86)", "size": 22 * 1024 ** 3, "files": 18000},
             {"path": _r + "\\ProgramData", "size": 17 * 1024 ** 3, "files": 4000}, {"path": _r + "\\1C_Bases", "size": 91 * 1024 ** 3, "files": 300},
             {"path": _r + "\\$Recycle.Bin", "size": 12 * 1024 ** 3, "files": 80}, {"path": _r + "\\Windows.old", "size": 15 * 1024 ** 3, "files": 90000},
             {"path": _r + "\\Distrib", "size": 3 * 1024 ** 3, "files": 120}, {"path": _r + "\\Intel", "size": 900 * 1024 ** 2, "files": 400},
             {"path": _r + "\\PerfLogs", "size": 120 * 1024 ** 2, "files": 30}, {"path": _r + "\\Temp", "size": 2 * 1024 ** 3, "files": 5000},
             {"path": _r + "\\Recovery", "size": 1 * 1024 ** 3, "files": 12}, {"path": _r + "\\Drivers", "size": 700 * 1024 ** 2, "files": 900}],
    "files": [{"path": _r + "\\1C_Bases\\buh\\1Cv8.1CD", "size": 38_000_000_000}, {"path": _r + "\\hiberfil.sys", "size": 6_800_000_000},
              {"path": _r + "\\Users\\ivanov\\Downloads\\Win11_23H2.iso", "size": 6_400_000_000}, {"path": _r + "\\pagefile.sys", "size": 4_900_000_000}],
    "hogs": [{"label": "Старая Windows", "path": _r + "\\Windows.old", "size": 15 * 1024 ** 3, "files": 90000}, {"label": "Корзина", "path": _r + "\\$Recycle.Bin", "size": 12 * 1024 ** 3, "files": 80},
             {"label": "Файл гибернации", "path": _r + "\\hiberfil.sys", "size": 6_800_000_000, "files": 1}, {"label": "Обновления Windows", "path": _r + "\\Windows\\SoftwareDistribution\\Download", "size": 3 * 1024 ** 3, "files": 2100},
             {"label": "Временные файлы", "path": _r + "\\Temp", "size": 2 * 1024 ** 3, "files": 5000}],
    "users": [{"path": _r + "\\Users\\ivanov", "size": 141 * 1024 ** 3, "files": 150000}, {"path": _r + "\\Users\\petrova", "size": 39 * 1024 ** 3, "files": 40000}]}))
hd = HealthDialog("WS-101", w, w)
hd.show()
tg._wait(lambda: hd.btn_refresh.isEnabled(), app, 5000)
hd.show_health(_h)
tg._wait(lambda: False, app, 400)
hd.grab().save(os.path.join(ROOT, "docs", "health_overview.png"))
hd.tabs.setCurrentIndex(1)
hd._select_disk(_h["phys"][1], hd.disk_cards[1])
tg._wait(lambda: False, app, 300)
hd.grab().save(os.path.join(ROOT, "docs", "health_smart.png"))
hd.tabs.setCurrentIndex(2)
hd.show_usage(_u)
hd.treemap._selected = 0                                   # выбранная плитка — рамка акцентом
tg._wait(lambda: False, app, 300)
hd.grab().save(os.path.join(ROOT, "docs", "health_diskmap.png"))
# «Что можно почистить» — из того же обхода, что и карта: те же цифры, только реально найденные пути
hd.treemap._selected = 6                                  # плитка «$Recycle.Bin» → строка в таблице подсвечена
hd.treemap.on_click(_u["dirs"][6])
hd.usage_tabs.setCurrentIndex(2)
tg._wait(lambda: False, app, 300)
hd.grab().save(os.path.join(ROOT, "docs", "health_cleanup.png"))
hd.usage_tabs.setCurrentIndex(0)
# вкладка «Ошибки»: журнал Windows с фильтром; счётчики — по показанным событиям
from datetime import datetime as _dt, timedelta as _td  # noqa: E402
_now = _dt.now()
_t = lambda h, m=0: (_now - _td(hours=h, minutes=m)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
_ev = health.parse_events_json(json.dumps([
    {"time": _t(0, 40), "log": "System", "level": 1, "id": 41, "source": "Microsoft-Windows-Kernel-Power", "msg": "Система перезагружена без корректного завершения работы. Возможные причины: система перестала отвечать, аварийно завершила работу или неожиданно отключилось питание."},
    {"time": _t(1, 5), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой при запуске службы «Служба печати»: превышено время ожидания."},
    {"time": _t(2, 30), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой при запуске службы «Служба печати»: превышено время ожидания."},
    {"time": _t(3, 10), "log": "Application", "level": 2, "id": 1000, "source": "Application Error", "msg": "Имя сбойного приложения: EXCEL.EXE, версия 16.0.17928.20114 — исключение 0xc0000005."},
    {"time": _t(4, 0), "log": "System", "level": 2, "id": 10016, "source": "Microsoft-Windows-DistributedCOM", "msg": "Параметры разрешений для конкретного приложения не дают разрешения Local Activation для сервера COM."},
    {"time": _t(5, 20), "log": "System", "level": 3, "id": 129, "source": "storahci", "msg": "Сброс на устройстве \\Device\\RaidPort0."},
    {"time": _t(6, 45), "log": "Application", "level": 3, "id": 1530, "source": "Microsoft-Windows-User Profiles Service", "msg": "Windows обнаружила, что файл реестра всё ещё используется другими приложениями или службами."},
    {"time": _t(9, 0), "log": "System", "level": 3, "id": 1014, "source": "Microsoft-Windows-DNS-Client", "msg": "Истекло время ожидания разрешения имени для сервера обновлений."},
    {"time": _t(11, 15), "log": "System", "level": 2, "id": 10016, "source": "Microsoft-Windows-DistributedCOM", "msg": "Параметры разрешений для конкретного приложения не дают разрешения Local Activation для сервера COM."},
    {"time": _t(20, 0), "log": "Application", "level": 3, "id": 8193, "source": "VSS", "msg": "Ошибка службы теневого копирования томов: неожиданная ошибка при вызове процедуры CoCreateInstance."},
]))
hd.tabs.setCurrentIndex(3)
hd._events_quick(24)
hd.show_events(_ev, hd.event_filter())
tg._wait(lambda: False, app, 300)
hd.grab().save(os.path.join(ROOT, "docs", "health_errors.png"))
hd.close()

# --- новое окно пинга (монитор доступности) — замеры имитируются, сеть в песочнице не нужна
from adk import pingui as _pingui  # noqa: E402
from adk.dialogs import PingDialog  # noqa: E402
import random as _rnd  # noqa: E402
class _SilentPing(_pingui.PingWorker):
    def run(self):
        return
_orig_pw = _pingui.PingWorker
_pingui.PingWorker = _SilentPing
pd = PingDialog("WS-101", "10.0.2.11", w, w)
pd.show()
_rnd.seed(7)
for i in range(48):
    ok = i not in (17, 18, 31)
    ms = max(1, int(_rnd.gauss(4, 1.4))) + (12 if 33 <= i <= 36 else 0)
    pd.on_event({"ip": "10.0.2.11", "bytes": "32", "time": f"{ms}мс" if ok else "—", "ttl": "128",
                 "status": "Ответ" if ok else "Превышен интервал ожидания для запроса.", "success": ok, "is_info": False})
tg._wait(lambda: False, app, 300)
pd.grab().save(os.path.join(ROOT, "docs", "ping.png"))
pd.close()
_pingui.PingWorker = _orig_pw

# --- свободный IP: карта подсети + вердикт DHCP (DHCP и сеть подменены)
from adk import dhcp as _dhcp  # noqa: E402
from adk.dialogs import FreeIPDialog  # noqa: E402
from adk.workers import FreeIPWorker  # noqa: E402
_dhcp_data = _dhcp.parse_dhcp_json(json.dumps({"server": "dhcp01", "scopes": [{
    "scope": "10.0.2.0", "name": "Офис, 2 этаж", "start": "10.0.2.20", "end": "10.0.2.250", "state": "Active",
    "leases": [{"ip": f"10.0.2.{h}", "mac": "00-1a-2b-3c-4d-5e", "host": f"ws-{h}", "state": "Active"} for h in (61, 62, 63, 66, 67, 68, 70, 71)],
    "reservations": [{"ip": "10.0.2.64", "mac": "00-1a-2b-3c-4d-40", "name": "printer-2f"}, {"ip": "10.0.2.65", "mac": "00-1a-2b-3c-4d-41", "name": "scanner-2f"}],
    "exclusions": [{"start": "10.0.2.20", "end": "10.0.2.29"}]}]}))
_old_q, _old_alive = _dhcp.query, netutils.is_host_alive
_old_dhcp_servers = config.settings.dhcp_servers
config.settings.dhcp_servers = ("dhcp01", "dhcp02")
_dhcp.query = lambda prefix, servers=None, timeout=40: {**_dhcp_data, "servers": ["dhcp01"], "errors": []}
netutils.is_host_alive = lambda ip, timeout=1.0: ip.rsplit(".", 1)[1] in ("60", "72")
import socket as _socket  # noqa: E402
_old_gha = _socket.gethostbyaddr
_socket.gethostbyaddr = lambda ip: ("x", [], []) if ip.endswith(".73") else (_ for _ in ()).throw(OSError())
FreeIPWorker._dhcp_cache.clear()
fd = FreeIPDialog(w)
fd.prefix.setText("10.0.2"); fd.start.setValue(60)
fd.show(); tg._wait(lambda: False, app, 200)
fw = FreeIPWorker("10.0.2", 60); fw._load_dhcp()
for h in range(60, 76):
    fd.map.mark(h, fw._reason(f"10.0.2.{h}"))
fd.on_dhcp(fw.verdict("10.0.2.69")); fd.on_done("10.0.2.69")
fd.on_dhcp(fw.verdict("10.0.2.74")); fd.on_done("10.0.2.74")
tg._wait(lambda: False, app, 300)
fd.grab().save(os.path.join(ROOT, "docs", "free_ip.png"))
fd.close()
_dhcp.query, netutils.is_host_alive, _socket.gethostbyaddr = _old_q, _old_alive, _old_gha
config.settings.dhcp_servers = _old_dhcp_servers

# --- опись: предпросмотр таблицы и выбор колонок
from adk.dialogs import InventoryDialog  # noqa: E402
inv = InventoryDialog(w, w)
inv.show(); tg._wait(lambda: inv.companies, app, 3000)
inv.list.setCurrentRow(0)
_rows = [
    {"fio": "Иванов Иван Петрович", "login": "ivanov", "title": "Системный администратор", "dept": "ИТ-отдел", "office": "каб. 214",
     "comp": "WS-101", "ip": "10.0.2.11", "online": "да", "os": "Windows 11 Pro 23H2", "cpu": "Intel Core i5-12400", "ram": "16.0 ГБ (3200 МГц)",
     "disks": "Samsung SSD 980 500 ГБ (SSD)", "printers": "HP LaserJet M404dn (сетевой · 10.0.2.50); Canon LBP6030 (USB)", "last_seen": "2026-09-05 11:40"},
    {"fio": "Петрова Анна Сергеевна", "login": "petrova", "title": "Главный бухгалтер", "dept": "Бухгалтерия", "office": "каб. 105",
     "comp": "WS-105", "ip": "10.0.2.15", "online": "да", "os": "Windows 11 Pro 23H2", "cpu": "Intel Core i5-12400", "ram": "16.0 ГБ (3200 МГц)",
     "disks": "Samsung SSD 980 500 ГБ (SSD)", "printers": "HP LaserJet M404dn (сетевой · 10.0.2.50)", "last_seen": "2026-09-05 11:38"},
    {"fio": "Сидоров Пётр Ильич", "login": "sidorov", "title": "Менеджер", "dept": "Отдел продаж", "office": "каб. 310",
     "comp": "WS-133", "ip": "10.0.2.33", "online": "нет", "os": "Windows 10 Pro 22H2", "cpu": "Intel Core i3-10100", "ram": "8.0 ГБ (2666 МГц)",
     "disks": "WDC WD10EZEX 1 ТБ (HDD)", "printers": "Kyocera ECOSYS P3145dn (сетевой · 10.0.2.51)", "last_seen": "2026-08-30 17:02"},
    {"fio": "Кузнецова Мария", "login": "kuznetsova", "title": "Специалист", "dept": "Отдел кадров", "office": "каб. 102",
     "comp": "WS-102", "ip": "10.0.2.12", "online": "нет", "os": "Windows 10 Pro 22H2", "cpu": "Intel Core i3-10100", "ram": "8.0 ГБ (2666 МГц)",
     "disks": "Toshiba DT01ACA100 1 ТБ (HDD)", "printers": "Epson L3150 (USB)", "last_seen": "2026-09-04 18:11"},
    {"fio": "Смирнов Алексей", "login": "smirnov", "title": "Инженер", "dept": "ИТ-отдел", "office": "каб. 214",
     "comp": "Не привязан", "ip": "—", "online": "—", "os": "—", "cpu": "—", "ram": "—", "disks": "—", "printers": "—", "last_seen": "—"},
]
inv.company = "ООО «Пример»"; inv.lbl_title.setText("<b>2. Что попадёт в файл</b> — ООО «Пример»")
inv.show_rows(_rows)
tg._wait(lambda: False, app, 300)
inv.grab().save(os.path.join(ROOT, "docs", "inventory.png"))
inv.close()

# --- GIF-анимации (PIL): кадры снимаем с offscreen-окна
from PIL import Image  # noqa: E402

def frame(widget, scale=0.6):
    qimg = widget.grab().toImage()
    ptr = qimg.constBits(); ptr.setsize(qimg.sizeInBytes())
    img = Image.frombuffer("RGBA", (qimg.width(), qimg.height()), bytes(ptr), "raw", "BGRA", qimg.bytesPerLine(), 1)
    img = img.convert("RGB")
    if scale != 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img

def save_gif(frames, durations, name):
    # у GIF один холст: кадры разного размера (окно / диалог) центрируем на фоне первого кадра
    cw, ch = max(f.width for f in frames), max(f.height for f in frames)
    bg = frames[0].getpixel((frames[0].width // 2, 6))  # цвет заголовка окна
    fixed = []
    for f in frames:
        canvas = Image.new("RGB", (cw, ch), bg)
        canvas.paste(f, ((cw - f.width) // 2, (ch - f.height) // 2))
        fixed.append(canvas)
    frames = fixed
    frames = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE) for f in frames]
    frames[0].save(os.path.join(ROOT, "docs", name), save_all=True, append_images=frames[1:],
                   duration=durations, loop=0, optimize=True)

# GIF 1: живой поиск — набор запроса, результаты, выбор строки, инспектор
frames, durations = [], []
w.search_input.clear(); w.escape() if hasattr(w, "escape") else None
tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(900)
for i in range(1, len("сидоров") + 1):
    w.search_input.setText("сидоров"[:i])
    tg._wait(lambda: False, app, 60)
    frames.append(frame(w)); durations.append(140)
w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "sidorov", app, 5000)
tg._wait(lambda: False, app, 200)
frames.append(frame(w)); durations.append(900)
w.select_row(0)
tg._wait(lambda: False, app, 400)
frames.append(frame(w)); durations.append(2600)
save_gif(frames, durations, "demo_search.gif")

# GIF 2: принтеры — бейдж в инспекторе → клик → кто ещё подключён к сетевому принтеру
frames, durations = [], []
frames.append(frame(w)); durations.append(1200)
badge = w.printers_box.findChildren(BadgeButton)[0]
badge.setDown(True); tg._wait(lambda: False, app, 60)
frames.append(frame(w)); durations.append(350)
badge.setDown(False); badge.click()
tg._wait(lambda: w.table.rowCount() >= 2, app, 5000)
tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1500)
w.select_row(1)
tg._wait(lambda: False, app, 400)
frames.append(frame(w)); durations.append(2500)
save_gif(frames, durations, "demo_printers.gif")

# GIF 3: дашборд → drill-down «в сети» → карточка пользователя
frames, durations = [], []
w.search_input.clear()
tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1200)
w.show_category("online")
tg._wait(lambda: w.table.rowCount() >= 1, app, 3000)
tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1500)
w.select_row(0)
tg._wait(lambda: False, app, 400)
frames.append(frame(w)); durations.append(1500)
card = UserCardDialog(entries[0], w, w)
card.resize(960, 640)
card.show()
tg._wait(lambda: card.current_ip != "Не найден", app, 3000)
tg._wait(lambda: False, app, 300)
frames.append(frame(card, 0.75)); durations.append(1500)
for t in range(1, card.tabs.count()):
    card.tabs.setCurrentIndex(t)
    tg._wait(lambda: False, app, 250)
    frames.append(frame(card, 0.75)); durations.append(1400)
card.close()
save_gif(frames, durations, "demo_card.gif")

# --- 3.0: инструменты набора -------------------------------------------------------------
from adk import access  # noqa: E402
from adk.tools import BulkOperationsDialog, GroupCompareDialog, HistoryDialog, NotesDialog  # noqa: E402

db.add_note("sidorov", "Просил второй монитор — заявка №4821, ждём поставку.", "CORP\\admin")
db.add_note("sidorov", "Пароль сбрасывали 03.09 — забыл после отпуска.", "CORP\\helpdesk")
db.add_note("ivanov", "Ноутбук выдан под подпись, док. в папке ИТ.", "CORP\\admin")
# история: ранее за WS-133 сидел другой пользователь и был другой IP
db.db_execute_with_retry("INSERT INTO pc_history (computer_name, login, ip_address, first_seen, last_seen) VALUES (?,?,?,?,?)",
                         ("WS-133", "kuznetsova", "10.0.2.90", "2025-11-03 09:12:00", "2026-03-27 18:40:00"))
db.db_execute_with_retry("INSERT INTO pc_history (computer_name, login, ip_address, first_seen, last_seen) VALUES (?,?,?,?,?)",
                         ("WS-133", "sidorov", "10.0.2.31", "2026-03-30 08:55:00", "2026-07-14 17:02:00"))

w.search_input.setText("сидоров")
w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "sidorov", app, 5000)
w.select_row(0)
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "inspector_notes.png"))

nd = NotesDialog("sidorov", "user", w, w, title="Сидоров Пётр Ильич")
nd.show(); tg._wait(lambda: False, app, 300)
nd.grab().save(os.path.join(ROOT, "docs", "notes.png"))
nd.close()

hd = HistoryDialog("sidorov", "WS-133", w)
hd.show(); tg._wait(lambda: False, app, 300)
hd.grab().save(os.path.join(ROOT, "docs", "history.png"))
hd.close()

# группы как у…: у Смирнова есть VPN и ИТ-отдел, у Иванова — только ИТ-отдел
entries[4]._a["memberOf"] = tg.FakeAttr(["CN=Domain Users,CN=Users,DC=example,DC=local", "CN=ИТ-отдел,OU=Groups,DC=example,DC=local",
                                         "CN=VPN-Users,OU=Groups,DC=example,DC=local", "CN=Remote-Desktop,OU=Groups,DC=example,DC=local"])
gc = GroupCompareDialog(entries[0], w, w)
gc.ref.setText("smirnov")
gc.load_ref()
tg._wait(lambda: gc.missing.count() >= 1, app, 3000)
gc.show(); tg._wait(lambda: False, app, 300)
gc.grab().save(os.path.join(ROOT, "docs", "group_compare.png"))
gc.close()

# массовые операции: выделяем весь ИТ-отдел
w.search_input.setText("ит-отдел")
w.start_search()
tg._wait(lambda: False, app, 1500)
w.table.selectAll()
tg._wait(lambda: False, app, 200)
bd = BulkOperationsDialog(w.selected_users(), w, w)
tg._wait(lambda: bd.groups.count() >= 1, app, 3000)
bd.op.setCurrentIndex(bd.op.findData("group_add"))
bd.groups.setCurrentRow(0)
bd.show(); tg._wait(lambda: False, app, 300)
bd.grab().save(os.path.join(ROOT, "docs", "bulk_ops.png"))
bd.close()

# роль «ПК-администратор» (GT_Admins): действия с ПК есть, изменения объектов AD скрыты
access.set_rights(pc=True, ad=False, reason="нет в группах — AD: IT-Admins")
w.apply_access()
w.select_row(0)
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "readonly.png"))
access.reset(); w.apply_access()

# GIF 4: набор инструментов — заметки → история → группы как у… → массовые операции
frames, durations = [], []
w.search_input.setText("сидоров"); w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "sidorov", app, 5000)
w.select_row(0); tg._wait(lambda: False, app, 400)
frames.append(frame(w)); durations.append(1500)
for dlg, dur in ((NotesDialog("sidorov", "user", w, w, title="Сидоров Пётр Ильич"), 1800),
                 (HistoryDialog("sidorov", "WS-133", w), 1800)):
    dlg.show(); tg._wait(lambda: False, app, 300)
    frames.append(frame(dlg, 0.8)); durations.append(dur)
    dlg.close()
gc = GroupCompareDialog(entries[0], w, w); gc.ref.setText("smirnov"); gc.load_ref()
tg._wait(lambda: gc.missing.count() >= 1, app, 3000)
gc.show(); tg._wait(lambda: False, app, 300)
frames.append(frame(gc, 0.8)); durations.append(2000)
gc.close()
w.search_input.setText("ит-отдел"); w.start_search()
tg._wait(lambda: False, app, 1500)
w.table.selectAll(); tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1200)
bd = BulkOperationsDialog(w.selected_users(), w, w)
tg._wait(lambda: bd.groups.count() >= 1, app, 3000)
bd.show(); tg._wait(lambda: False, app, 300)
frames.append(frame(bd, 0.8)); durations.append(2400)
bd.close()
save_gif(frames, durations, "demo_kit.gif")

# --- 3.1: сводка «Внимание», парк ПК, шаблоны ---------------------------------------
from adk import attention, software  # noqa: E402
from adk.attention_ui import AttentionDialog  # noqa: E402
from adk.fleet import ComparePCDialog, MassPingDialog, SoftwareDialog  # noqa: E402
from adk.dialogs import RegisterUserDialog  # noqa: E402

# входы за 90+ дней и MAC-адреса для WoL
entries[3]._a["lastLogonTimestamp"] = tg.FakeAttr([now - timedelta(days=140)])
db.save_mac("WS-133", "00-1A-2B-3C-4D-5E")
db.save_mac("WS-102", "00-1A-2B-3C-4D-6F")
items = attention.from_entries(entries, now=now, acct_days=7, no_logon_days=90) + attention.from_inventory(30)
w.set_attention_items(items)
w.search_input.clear(); w.on_text_changed("")
tg._wait(lambda: False, app, 400)
w.grab().save(os.path.join(ROOT, "docs", "dashboard_attention.png"))

atd = AttentionDialog(w, w, items=items)
atd.show(); tg._wait(lambda: False, app, 300)
atd.grab().save(os.path.join(ROOT, "docs", "attention.png"))
atd.close()

w.search_input.setText("отдел"); w.start_search()
tg._wait(lambda: w.table.rowCount() >= 5, app, 5000)
w.table.selectAll(); tg._wait(lambda: False, app, 200)
mp = MassPingDialog(w.selected_computers(), w, w)
mp.show(); tg._wait(lambda: "Опрос" not in mp.status.text(), app, 5000)
mp.grab().save(os.path.join(ROOT, "docs", "mass_ping.png"))
mp.close()

software.cache_software("WS-101", [{"name": "1С:Предприятие 8.3", "version": "8.3.24.1467", "publisher": "1С", "installed": "2026-02-11"},
                                   {"name": "Google Chrome", "version": "128.0.6613.120", "publisher": "Google LLC", "installed": "2026-08-30"},
                                   {"name": "Kaspersky Endpoint Security", "version": "12.6.0.438", "publisher": "AO Kaspersky Lab", "installed": "2026-01-15"},
                                   {"name": "Microsoft Office LTSC 2021", "version": "16.0.14332", "publisher": "Microsoft", "installed": "2025-12-01"},
                                   {"name": "7-Zip 23.01 (x64)", "version": "23.01", "publisher": "Igor Pavlov", "installed": "2025-12-01"}])
software.cache_software("WS-105", [{"name": "1С:Предприятие 8.3", "version": "8.3.22.1851", "publisher": "1С", "installed": "2025-06-11"},
                                   {"name": "Google Chrome", "version": "128.0.6613.120", "publisher": "Google LLC", "installed": "2026-08-30"},
                                   {"name": "КриптоПро CSP", "version": "5.0.13000", "publisher": "КриптоПро", "installed": "2026-03-02"}])
software.cache_software("WS-214", [{"name": "Google Chrome", "version": "127.0.6533.100", "publisher": "Google LLC", "installed": "2026-07-30"}])
sd = SoftwareDialog("WS-101", w, w)
sd.show(); tg._wait(lambda: False, app, 300)
sd.grab().save(os.path.join(ROOT, "docs", "software.png"))
sd.tabs.setCurrentIndex(2); sd.q.setText("1С"); sd.search_fleet()
tg._wait(lambda: False, app, 200)
sd.grab().save(os.path.join(ROOT, "docs", "software_fleet.png"))
sd.close()

cp = ComparePCDialog("WS-101", "WS-105", w, w)
cp.show(); tg._wait(lambda: cp._data is not None, app, 3000); tg._wait(lambda: False, app, 200)
cp.tabs.setCurrentIndex(1); tg._wait(lambda: False, app, 200)
cp.grab().save(os.path.join(ROOT, "docs", "compare_pc.png"))
cp.close()

from adk import templates as _tpl  # noqa: E402
_tpl_path = os.path.join(tempfile.mkdtemp(), "templates.json")
_tpl.save({"Бухгалтер": {"attrs": {"department": "Бухгалтерия", "title": "Бухгалтер", "company": "ООО «Пример»", "physicalDeliveryOfficeName": "каб. 105"},
                         "groups": ["CN=Бухгалтерия,OU=Groups,DC=example,DC=local", "CN=1C-Users,OU=Groups,DC=example,DC=local", "CN=Printer-2F,OU=Groups,DC=example,DC=local"],
                         "ou": "OU=Employees,DC=example,DC=local"},
           "Менеджер продаж": {"attrs": {"department": "Отдел продаж", "title": "Менеджер"}, "groups": ["CN=CRM-Users,OU=Groups,DC=example,DC=local"], "ou": ""}}, _tpl_path)
config.settings.templates_file = _tpl_path
rd = RegisterUserDialog(w, w)
rd.surname.setText("Николаева"); rd.name.setText("Ольга"); rd.patronymic.setText("Викторовна"); rd.generate()
rd.cb_template.setCurrentIndex(1)
rd.show(); tg._wait(lambda: False, app, 400)
rd.grab().save(os.path.join(ROOT, "docs", "register_template.png"))
rd.close()

# GIF 5: 3.1 — сводка «Внимание» → массовый пинг → ПО парка → сравнение ПК
frames, durations = [], []
w.search_input.clear(); w.on_text_changed(""); tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1500)
atd = AttentionDialog(w, w, items=items); atd.show(); tg._wait(lambda: False, app, 300)
frames.append(frame(atd, 0.8)); durations.append(2200); atd.close()
w.search_input.setText("отдел"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 5, app, 5000)
w.table.selectAll(); tg._wait(lambda: False, app, 300)
frames.append(frame(w)); durations.append(1200)
mp = MassPingDialog(w.selected_computers(), w, w); mp.show(); tg._wait(lambda: "Опрос" not in mp.status.text(), app, 5000)
frames.append(frame(mp, 0.8)); durations.append(2200); mp.close()
sd = SoftwareDialog("WS-101", w, w); sd.show(); tg._wait(lambda: False, app, 300)
frames.append(frame(sd, 0.8)); durations.append(1800)
sd.tabs.setCurrentIndex(2); sd.q.setText("1С"); sd.search_fleet(); tg._wait(lambda: False, app, 200)
frames.append(frame(sd, 0.8)); durations.append(2000); sd.close()
cp = ComparePCDialog("WS-101", "WS-105", w, w); cp.show(); tg._wait(lambda: cp._data is not None, app, 3000)
cp.tabs.setCurrentIndex(1); tg._wait(lambda: False, app, 200)
frames.append(frame(cp, 0.8)); durations.append(2400); cp.close()
save_gif(frames, durations, "demo_fleet.gif")

w.quit_app()
print("screenshots + gifs written to docs/")
