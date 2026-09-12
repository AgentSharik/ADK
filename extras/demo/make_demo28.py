"""Видео-демонстрация ADK (offscreen, заглушка LDAP) → /home/user/extras/videos/ADK_demo_28.mp4.

Ролик 27: программа работает на стендовой базе из 1500 ПК (копия extras/data/bench/pc_mapping.db + 1350 пользователей
из bench_users.json). В кадре — секундомер: время от нажатия до появления строк и до статуса сети измеряется
по настоящим часам (time.perf_counter) и выводится в правом верхнем углу. Одна версия в начале и в конце.

Не входит в проект: лежит вне пакета проекта (папка demo/), в README/CHANGELOG/zip не упоминается.
Запуск: QT_QPA_PLATFORM=offscreen python /home/user/extras/demo/make_demo28.py
"""
import base64
import json
import os
import random
import re
import shutil
import socket as _socket
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

ROOT = "/home/user"
OUT_DIR = "/home/user/extras/demo/frames28"
OUT_MP4 = "/home/user/extras/videos/ADK_demo_28.mp4"
FPS = 25
W, H = 1400, 820           # размер главного окна = размер кадра
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(lambda t, c, m: None if "This plugin does not support" in m or "propagateSizeHints" in m else sys.stderr.write(f"{m}\n"))
except Exception:
    pass

from adk import access, ad, attention, config, db, netutils, software  # noqa: E402

BENCH_DIR = os.path.join(ROOT, "extras", "data", "bench")
config.settings.db_path = os.path.join(tempfile.mkdtemp(), "pc_mapping.db")
shutil.copy(os.path.join(BENCH_DIR, "pc_mapping.db"), config.settings.db_path)   # стенд: 1500 ПК, принтеры, ПО, архив
config.settings.dhcp_servers = ("dhcp01",)     # в демо сверка с DHCP настроена — бейдж и легенда DHCP показываются
db.init_db()
import test_gui as tg  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QApplication, QPushButton, QTableWidgetItem  # noqa: E402
from adk.theme import PRESET_THEMES, theme_design  # noqa: E402
from adk.widgets import BadgeButton, InputDialog, apply_theme, MessageBox  # noqa: E402
from adk import dhcp as _dhcp, health, pingui  # noqa: E402
from adk.attention_ui import AttentionDialog  # noqa: E402
from adk.colorpicker import ColorPickerDialog  # noqa: E402
from adk.dialogs import (AuditLogDialog, DesignSettingsDialog, FreeIPDialog,  # noqa: E402
                         InventoryDialog, LoginDialog, PingDialog, PluginsDialog, PrintersDialog,
                         GroupMembersDialog, RegisterUserDialog, ResetPasswordDialog, RoleInfoDialog, UserCardDialog)
from adk.fleet import ComparePCDialog, LogonsDialog, MassPingDialog, SoftwareDialog  # noqa: E402
from adk import logons as _logons  # noqa: E402
from adk.health_ui import HealthDialog  # noqa: E402
from adk.main_window import ADApp, COLUMNS  # noqa: E402
from adk.tools import BulkOperationsDialog, GroupCompareDialog, HistoryDialog, NotesDialog  # noqa: E402
from adk import main_window as _mw, workers as _wk  # noqa: E402
from adk.workers import FreeIPWorker, PingWorker, build_inventory_rows, write_inventory_xlsx  # noqa: E402

_mw.SEARCH_RESULT_LIMIT = _wk.SEARCH_RESULT_LIMIT = 200

app = QApplication([])
apply_theme({**config.settings.design, **theme_design(PRESET_THEMES["dark"])})   # «Графит»
now = datetime.now(timezone.utc)

# ----------------------------------------------------------------------------- демо-данные
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

random.seed(6)
_SURNAMES = ["Волков", "Зайцев", "Морозов", "Соколов", "Лебедев", "Козлов", "Новиков", "Егоров", "Павлов", "Фёдоров",
             "Орлов", "Макаров", "Никитин", "Захаров", "Борисов", "Киселёв", "Тихонов", "Белов", "Комаров", "Медведев",
             "Гусев", "Титов", "Кузьмин", "Степанов", "Ковалёв", "Ильин", "Крылов", "Максимов", "Поляков", "Сорокин",
             "Виноградов", "Данилов", "Жуков", "Фролов", "Романов", "Осипов", "Семёнов", "Мельников", "Щербаков", "Блинов"]
_SURNAMES_F = ["Громова", "Ефимова", "Терентьева", "Абрамова", "Лазарева", "Игнатьева", "Дмитриева", "Баранова", "Алексеева",
               "Афанасьева", "Калинина", "Мартынова", "Тарасова", "Филиппова", "Маркова", "Большакова", "Суханова",
               "Миронова", "Ширяева", "Александрова", "Коновалова", "Шестакова", "Казакова", "Ефремова", "Исаева"]
_NAMES_M = [("Андрей", "Сергеевич"), ("Дмитрий", "Олегович"), ("Максим", "Игоревич"), ("Сергей", "Владимирович"),
            ("Николай", "Петрович"), ("Артём", "Андреевич"), ("Кирилл", "Дмитриевич"), ("Евгений", "Александрович")]
_NAMES_F = [("Елена", "Викторовна"), ("Ольга", "Николаевна"), ("Наталья", "Андреевна"), ("Татьяна", "Сергеевна"),
            ("Ирина", "Павловна"), ("Светлана", "Юрьевна"), ("Юлия", "Алексеевна"), ("Дарья", "Игоревна")]
_DEPTS = [("Бухгалтерия", ("Бухгалтер", "Экономист"), "каб. 105"), ("Отдел продаж", ("Менеджер", "Старший менеджер"), "каб. 310"),
          ("ИТ-отдел", ("Инженер", "Техник"), "каб. 214"), ("Отдел кадров", ("Специалист", "Инспектор"), "каб. 102"),
          ("Юридический отдел", ("Юрист",), "каб. 208"), ("Склад", ("Кладовщик", "Логист"), "каб. 12"),
          ("Отдел закупок", ("Специалист по закупкам",), "каб. 305"), ("Приёмная", ("Секретарь",), "каб. 201")]
_TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m",
                     "n", "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


def _tr(word):
    return "".join(_TRANSLIT.get(ch, ch) for ch in word.lower())


EXTRA = []
_pool = [(sn, True) for sn in _SURNAMES] + [(sn, False) for sn in _SURNAMES_F]
_ip_no = 60
for i in range(115):
    sn, male = _pool[i % len(_pool)]
    gn, pat = (_NAMES_M if male else _NAMES_F)[(i * 3 + i // len(_pool)) % 8]
    login = _tr(sn) + (str(i // len(_pool) + 1) if i >= len(_pool) else "")
    dept, titles, office = _DEPTS[i % len(_DEPTS)]
    state = "archive" if i >= 100 else ("disabled" if i % 9 == 4 else ("offline" if i % 3 == 1 else "online"))
    uac = 514 if state == "disabled" else 512
    pc = f"WS-{300 + i}"
    _ip_no += 1
    attrs = dict(sAMAccountName=login, displayName=f"{sn} {gn} {pat}", sn=sn, givenName=gn, userAccountControl=uac,
                 telephoneNumber=f"25546{i:02d}"[:7], ipPhone=str(4000 + i) if i % 2 else "", department=dept,
                 title=titles[i % len(titles)], physicalDeliveryOfficeName=office, company="АО «Логистика Плюс»" if i % 4 == 2 else "ООО «Пример»",
                 mail=f"{login}@example.local", pwdLastSet=now - timedelta(days=(i * 7) % 120), badPwdCount=0,
                 memberOf=["CN=Domain Users,CN=Users,DC=example,DC=local", f"CN={dept},OU=Groups,DC=example,DC=local"],
                 lastLogonTimestamp=now - timedelta(days=(i * 5) % 60 + (200 if state == "archive" else 0)))
    entries.append(tg.FakeEntry(f"CN={sn} {gn} {pat},OU=Employees,DC=example,DC=local", **attrs))
    EXTRA.append((login, pc, state, f"10.0.13.{(_ip_no % 250) + 1}" if _ip_no < 250 else f"10.0.14.{_ip_no - 249}"))

# стендовые пользователи (bench_users.json): 1350 записей → AD-заглушка отвечает как настоящий домен среднего размера
_HERO_LOGINS = {p[0] for p in people}
_HERO_SN = tuple(p[2] for p in people)          # однофамильцы героев не нужны: их фамилии — «якоря» сценария
for u in json.load(open(os.path.join(BENCH_DIR, "bench_users.json"), encoding="utf-8")):
    if u["login"] in _HERO_LOGINS or u["sn"].startswith(_HERO_SN):
        continue
    attrs = dict(sAMAccountName=u["login"], displayName=u["displayName"], sn=u["sn"], givenName=u["givenName"],
                 userAccountControl=514 if u["disabled"] else 512, telephoneNumber=u["telephoneNumber"], ipPhone=u["ipPhone"],
                 department=u["department"], title=u["title"], physicalDeliveryOfficeName=u["physicalDeliveryOfficeName"],
                 company=u["company"], mail=u["mail"], pwdLastSet=now - timedelta(days=random.randint(1, 100)), badPwdCount=0,
                 memberOf=["CN=Domain Users,CN=Users,DC=example,DC=local", f"CN={u['department']},OU=Groups,DC=example,DC=local"],
                 lastLogonTimestamp=now - timedelta(days=random.randint(0, 45)))
    entries.append(tg.FakeEntry(f"CN={u['displayName']},OU=Employees,DC=example,DC=local", **attrs))

entries[4]._a["memberOf"] = tg.FakeAttr(["CN=Domain Users,CN=Users,DC=example,DC=local", "CN=ИТ-отдел,OU=Groups,DC=example,DC=local",
                                         "CN=VPN-Users,OU=Groups,DC=example,DC=local", "CN=Remote-Desktop,OU=Groups,DC=example,DC=local"])
entries[3]._a["lastLogonTimestamp"] = tg.FakeAttr([now - timedelta(days=140)])
GROUPS = [tg.FakeEntry(f"CN={g},OU=Groups,DC=example,DC=local", cn=g)
          for g in ("VPN-Users", "Remote-Desktop", "ИТ-отдел", "Бухгалтерия", "Printer-2F", "WiFi-Staff")]


_LDAP_MS = 40   # задержка «контроллера домена» на запрос — как у настоящего AD в локальной сети (20–80 мс)


class DemoConn(tg.FakeConn):
    """Заглушка AD, разбирающая LDAP-фильтр SearchWorker: (|(sAMAccountName=x)(displayName=*q*)(sn=q*)…)."""
    calls: list = []

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        import time as _t
        _t.sleep(_LDAP_MS / 1000)
        DemoConn.calls.append(flt)
        if "objectClass=group" in flt:
            self.entries = GROUPS
            return True
        if "objectClass=computer" in flt:
            self.entries = []
            return True
        skip_disabled = "userAccountControl:1.2.840.113556.1.4.803:=2" in flt
        body = flt.split("(|", 1)[1] if "(|" in flt else flt
        terms = [(a, v) for a, v in re.findall(r"\((\w+)=([^()]*)\)", body)
                 if a not in ("objectCategory", "objectClass", "userAccountControl")]
        exact = {v.casefold() for a, v in terms if a == "sAMAccountName" and "*" not in v}
        subs = [(a, v.casefold()) for a, v in terms if not (a == "sAMAccountName" and "*" not in v) and v != "*$"]

        def _hit(e):
            if e["sAMAccountName"].value.casefold() in exact:
                return True
            for a, v in subs:
                if a not in e._a:
                    continue
                for val in (str(x).casefold() for x in e[a].values):
                    if v.startswith("*") and v.endswith("*"):
                        ok = v.strip("*") in val
                    elif v.endswith("*"):
                        ok = val.startswith(v.rstrip("*"))
                    else:
                        ok = val == v
                    if ok:
                        return True
            return False

        self.entries = [e for e in self._all if (not terms or _hit(e))
                        and not (skip_disabled and int(e["userAccountControl"].value or 0) & 2)]
        return True


conn = DemoConn(entries)
ad.make_connection = lambda *a, **k: conn
online = {"WS-101": True, "WS-105": True, "WS-133": False, "WS-102": False, "WS-214": True}
ips = {"WS-101": "10.0.12.11", "WS-105": "10.0.12.15", "WS-133": "10.0.12.33", "WS-102": "10.0.12.12", "WS-214": "10.0.12.44"}
for login, pc, state, ip in EXTRA:
    if state != "archive":
        online[pc] = state == "online"
        ips[pc] = ip
pairs = (("ivanov", "WS-101"), ("petrova", "WS-105"), ("sidorov", "WS-133"), ("kuznetsova", "WS-102"), ("smirnov", "WS-214"))
pairs = pairs + tuple((login, pc) for login, pc, state, _ in EXTRA if state != "archive")
for login, pc in pairs:
    db.save_computer_for_login(login, pc)
_now_s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_heroes = tuple(p[0] for p in people)
_ph = ",".join("?" * len(_heroes))
for _sql in (f"UPDATE pc_inventory SET current_user = '' WHERE current_user IN ({_ph})",    # у героев свои ПК из сценария
             f"DELETE FROM audit_cache WHERE login IN ({_ph})", f"DELETE FROM permanent_mapping WHERE login IN ({_ph})",
             f"DELETE FROM notes WHERE subject IN ({_ph})", f"DELETE FROM audit_log WHERE target IN ({_ph})"):
    db.db_execute_with_retry(_sql, _heroes)
for login, pc in pairs:      # герои ролика добавляются к 1500 стендовым ПК (batch_update_inventory удалил бы остальные)
    db.db_execute_with_retry("INSERT OR REPLACE INTO pc_inventory (computer_name, ip_address, is_online, current_user, last_checked, last_logon, last_seen_online) "
                             "VALUES (?,?,?,?,?,?,?)", (pc, ips[pc], 1 if online[pc] else 0, login, _now_s,
                                                       (datetime.now() - timedelta(days=0 if online[pc] else random.randint(1, 40))).strftime("%d.%m.%Y %H:%M"),
                                                       _now_s if online[pc] else (datetime.now() - timedelta(days=random.randint(1, 40))).strftime("%Y-%m-%d %H:%M:%S")))
# сеть для стендовых ПК: адрес и статус — из инвентаря стенда, с задержкой как у настоящего DNS+ping
_inv_net = {r[0]: (r[1], bool(r[2])) for r in db.db_execute_with_retry("SELECT computer_name, ip_address, is_online FROM pc_inventory", fetch="all")}
_NET_MS = 120


def _demo_net(n, **kw):
    import time as _t
    if n in ips:
        return ips.get(n, "Не найден"), online.get(n, False)
    _t.sleep(_NET_MS / 1000)
    return _inv_net.get(db.clean_computer_name(n), ("Не найден", False))


netutils.get_computer_network_info = _demo_net
netutils.is_printer_alive = lambda ip, **kw: (__import__("time").sleep(_NET_MS / 1000) or (not ip.endswith((".29", ".57"))))
netutils.probe_printer = lambda ip, **kw: {"alive": True, "is_printer": True, "evidence": "открыт порт печати 9100"}

for n, (login, pc, state, ip) in enumerate(x for x in EXTRA if x[2] == "archive"):
    e = next(e for e in entries if e["sAMAccountName"].value == login)
    db.db_execute_with_retry("INSERT OR REPLACE INTO audit_cache (login, computer_name, ip_address, full_name, timestamp, raw_data, file_path, file_mtime) "
                             "VALUES (?,?,?,?,?,?,?,?)", (login, f"OLD-{n + 1:02d}", ip, e["displayName"].value,
                                                         (datetime.now() - timedelta(days=220 + n * 9)).strftime("%d.%m.%Y %H:%M"), "",
                                                         f"archive/{login}.csv", 0.0))

for login, old, ip, days in (("sidorov", "WS-090", "10.0.12.90", 160), ("ivanov", "WS-077", "10.0.12.77", 400), ("petrova", "WS-064", "10.0.12.64", 300)):
    e = next(e for e in entries if e["sAMAccountName"].value == login)
    db.db_execute_with_retry("INSERT OR REPLACE INTO audit_cache (login, computer_name, ip_address, full_name, timestamp, raw_data, file_path, file_mtime) "
                             "VALUES (?,?,?,?,?,?,?,?)", (login, old, ip, e["displayName"].value,
                                                         (datetime.now() - timedelta(days=days)).strftime("%d.%m.%Y %H:%M"), "", f"archive/{login}_{old}.csv", 0.0))

hw_dir = os.path.join(tempfile.mkdtemp(), "hardware")
os.makedirs(hw_dir)
config.settings.invent_hardware_dir = hw_dir
PRINTERS = {
    "WS-101": [("HP LaserJet M404dn", "IP_10.0.12.50", "Да"), ("Microsoft Print to PDF", "PORTPROMPT:", ""), ("Canon LBP6030", "USB001", "")],
    "WS-105": [("HP LaserJet M404dn", "IP_10.0.12.50", "Да"), ("Microsoft XPS Document Writer", "PORTPROMPT:", "")],
    "WS-133": [("Kyocera ECOSYS P3145dn", "10.0.12.51_1", "Да"), ("Fax", "SHRFAX:", "")],
    "WS-102": [("Kyocera ECOSYS P3145dn", "10.0.12.51_1", ""), ("Epson L3150", "USB002", "Да")],
    "WS-214": [("HP LaserJet M404dn", "IP_10.0.12.50", ""), ("Kyocera на PRINTSRV", "\\\\PRINTSRV\\KYO-2F", "Да")],
}
HW = {"WS-133": ("Windows 10 Pro 22H2", "Intel Core i3-10100", 8589934592, 2666, "WDC WD10EZEX", 1000204886016, "HDD"),
      "WS-102": ("Windows 10 Pro 22H2", "Intel Core i3-10100", 8589934592, 2666, "Toshiba DT01ACA100", 1000204886016, "HDD"),
      "WS-105": ("Windows 11 Pro 23H2", "Intel Core i5-12400", 17179869184, 3200, "Kingston NV2 500GB", 500107862016, "SSD")}
for pc, plist in PRINTERS.items():
    os_, cpu, ram, mhz, dsk, dsz, dtype = HW.get(pc, ("Windows 11 Pro 23H2", "Intel Core i5-12400", 17179869184, 3200, "Samsung SSD 980", 500107862016, "SSD"))
    lines = [f"Операционная система;Название;0;{os_}", f"Процессор;Название;0;{cpu}",
             f"Оперативная память;Размер;0;{ram}", f"Оперативная память;Частота;0;{mhz}",
             f"Диск;Наименование;0;{dsk}", f"Диск;Размер;0;{dsz}", f"Диск;Тип носителя;0;{dtype}"]
    for i, (name, port, default) in enumerate(plist):
        lines += [f"Принтер;Название;{i};{name}", f"Принтер;Порт;{i};{port}", f"Принтер;По умолчанию;{i};{default}"]
    open(os.path.join(hw_dir, f"{pc}.csv"), "w", encoding="cp1251", newline="").write("\n".join(lines) + "\n")
netutils.index_printers(PRINTERS)
for q in ("иванов", "10.0.12.5", "WS-133", "бухгалтерия"):
    db.save_search_query(q, "CORP\\admin")
for adm, act, tgt, det, mins in (("CORP\\admin", "reset_password", "sidorov", "смена при входе", 3),
                                 ("CORP\\admin", "unlock", "sidorov", "", 4),
                                 ("CORP\\helpdesk", "restart", "WS-105", "", 25),
                                 ("CORP\\admin", "group_add", "smirnov", "VPN-Users", 70)):
    db.db_execute_with_retry("INSERT INTO audit_log (ts, admin, action, target, details) VALUES (?,?,?,?,?)",
                             ((datetime.now() - timedelta(minutes=mins)).strftime("%Y-%m-%d %H:%M:%S"), adm, act, tgt, det))
db.add_note("sidorov", "Просил второй монитор — заявка №4821, ждём поставку.", "CORP\\admin")
db.add_note("sidorov", "Пароль сбрасывали 03.09 — забыл после отпуска.", "CORP\\helpdesk")
db.db_execute_with_retry("INSERT INTO pc_history (computer_name, login, ip_address, first_seen, last_seen) VALUES (?,?,?,?,?)",
                         ("WS-133", "kuznetsova", "10.0.12.90", "2025-11-03 09:12:00", "2026-03-27 18:40:00"))
db.save_mac("WS-133", "00-1A-2B-3C-4D-5E")
db.save_mac("WS-102", "00-1A-2B-3C-4D-6F")
software.cache_software("WS-101", [{"name": "1С:Предприятие 8.3", "version": "8.3.24.1467", "publisher": "1С", "installed": "2026-02-11"},
                                   {"name": "Google Chrome", "version": "128.0.6613.120", "publisher": "Google LLC", "installed": "2026-08-30"},
                                   {"name": "Kaspersky Endpoint Security", "version": "12.6.0.438", "publisher": "AO Kaspersky Lab", "installed": "2026-01-15"},
                                   {"name": "Microsoft Office LTSC 2021", "version": "16.0.14332", "publisher": "Microsoft", "installed": "2025-12-01"}])
software.cache_software("WS-105", [{"name": "1С:Предприятие 8.3", "version": "8.3.22.1851", "publisher": "1С", "installed": "2025-06-11"},
                                   {"name": "Google Chrome", "version": "128.0.6613.120", "publisher": "Google LLC", "installed": "2026-08-30"},
                                   {"name": "КриптоПро CSP", "version": "5.0.13000", "publisher": "КриптоПро", "installed": "2026-03-02"}])

# ----------------------------------------------------------------------------- Видео-пайплайн
shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)
_frame_no = 0
_cursor = [W // 2, H // 2]
_caption = ["", 0]
_FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
_FONT_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 15)

try:
    import imageio_ffmpeg
    _ffmpeg = shutil.which("ffmpeg") or imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    _ffmpeg = "ffmpeg"

_temp_video = os.path.join(OUT_DIR, "video_stream.mp4")
_encoder = subprocess.Popen([
    _ffmpeg, "-y", "-loglevel", "error",
    "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-framerate", str(FPS),
    "-i", "pipe:0",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "fast",
    _temp_video
], stdin=subprocess.PIPE)


def qimg_to_pil(widget):
    qimg = widget.grab().toImage()
    ptr = qimg.constBits(); ptr.setsize(qimg.sizeInBytes())
    return Image.frombuffer("RGBA", (qimg.width(), qimg.height()), bytes(ptr), "raw", "BGRA", qimg.bytesPerLine(), 1).convert("RGB")


def draw_cursor(img, x, y):
    d = ImageDraw.Draw(img)
    pts = [(x, y), (x, y + 19), (x + 5, y + 15), (x + 8, y + 22), (x + 11, y + 21), (x + 8, y + 14), (x + 14, y + 14)]
    d.polygon(pts, fill="white", outline="black")


_CAP_MAX = 58


def _wrap(text):
    import textwrap
    lines = textwrap.wrap(text, _CAP_MAX) if len(text) > _CAP_MAX else [text]
    return lines[:3]


def draw_caption(img, text, alpha=1.0):
    if not text:
        return
    lines = _wrap(text)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    lh = 30
    tw = max(d.textlength(t, font=_FONT) for t in lines)
    pad = 18
    bh = lh * len(lines) + 18
    x0 = (img.width - tw) / 2 - pad
    y0 = img.height - 28 - bh
    a = int(215 * alpha)
    d.rounded_rectangle((x0, y0, x0 + tw + 2 * pad, y0 + bh), radius=12, fill=(28, 28, 30, a), outline=(10, 132, 255, int(200 * alpha)), width=2)
    for i, t in enumerate(lines):
        d.text((x0 + pad + (tw - d.textlength(t, font=_FONT)) / 2, y0 + 9 + i * lh), t, font=_FONT, fill=(255, 255, 255, int(255 * alpha)))
    img.paste(overlay, (0, 0), overlay)


def _write(img):
    global _frame_no
    _encoder.stdin.write(img.tobytes())
    _frame_no += 1


def render(widgets, caption_alpha=1.0, cursor=True):
    base = qimg_to_pil(widgets[0])
    if base.size != (W, H):
        canvas = Image.new("RGB", (W, H), base.getpixel((5, 5)))
        canvas.paste(base, (0, 0))
        base = canvas
    for dlg in widgets[1:]:
        im = qimg_to_pil(dlg)
        sh = Image.new("RGBA", (im.width + 24, im.height + 24), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((12, 12, im.width + 12, im.height + 12), radius=10, fill=(0, 0, 0, 120))
        ox, oy = (W - im.width) // 2, (H - im.height) // 2
        if im.height <= H - 64:
            oy = max(oy, 64)
        pos = getattr(dlg, "_demo_pos", None)
        if pos:
            ox, oy = pos
        base.paste(sh, (ox - 12, oy - 8), sh)
        base.paste(im, (ox, oy))
    draw_caption(base, _caption[0], caption_alpha)
    if cursor:
        draw_cursor(base, *_cursor)
    return base


def caption(widgets, text):
    _caption[0] = text
    _write(render(widgets))


def cap(text):
    """Сменить субтитр (без записи кадра) — следующий snap покажет новый текст."""
    _caption[0] = text


def snap(widgets, hold=1, cap_text=None):
    if cap_text is not None and cap_text != _caption[0]:
        caption(widgets, cap_text)
    img = render(widgets)
    for _ in range(hold):
        _write(img)


def sec(s):
    return max(1, int(s * FPS))


def wait(ms):
    tg._wait(lambda: False, app, ms)


def dlg_origin(top):
    pos = getattr(top, "_demo_pos", None)
    if pos:
        return pos
    oy = (H - top.height()) // 2
    if top.height() <= H - 64:
        oy = max(oy, 64)
    return (W - top.width()) // 2, oy


def move_to(widgets, widget, frames=16, offset=None):
    top = widget.window()
    if top is widgets[0]:
        p = widget.mapTo(top, QPoint(widget.width() // 2, widget.height() // 2))
        tx, ty = p.x(), p.y()
    else:
        p = widget.mapTo(top, QPoint(widget.width() // 2, widget.height() // 2))
        ox, oy = dlg_origin(top)
        tx, ty = ox + p.x(), oy + p.y()
    if offset:
        tx += offset[0]; ty += offset[1]
    sx, sy = _cursor
    for i in range(1, frames + 1):
        t = i / frames
        t = t * t * (3 - 2 * t)
        _cursor[0], _cursor[1] = int(sx + (tx - sx) * t), int(sy + (ty - sy) * t)
        snap(widgets)


def click_fx(widgets, hold=4):
    snap(widgets, hold)


def type_text(widgets, line_edit, text, per_char=0.09):
    line_edit.setFocus()
    for i in range(1, len(text) + 1):
        line_edit.setText(text[:i])
        wait(30)
        snap(widgets, sec(per_char))


def press(widgets, button, hold=4):
    move_to(widgets, button)
    button.setDown(True)
    snap(widgets, hold)
    button.setDown(False)


HOLD_SCALE = 1.27  # общий темп статичных пауз (ролик не короче предыдущего)


def hold(widgets, seconds, text=None):
    if text is not None and text != _caption[0]:
        _caption[0] = text
    snap(widgets, sec(seconds * HOLD_SCALE))


def show_dialog(widgets, dlg, text=None, settle=400):
    dlg.show()
    wait(settle)
    if text is not None:
        _caption[0] = text
    ws = widgets + [dlg]
    snap(ws, 3)
    return ws


def hide_dialog(widgets, dlg, settle=250):
    dlg.close()
    wait(settle)
    ws = widgets[:-1]
    snap(ws, 3)
    return ws


# --- Конфигурация воркеров и окружения
class _Ext:
    class microsoft:
        @staticmethod
        def modify_password(dn, pwd):
            conn.modified.append((dn, {"unicodePwd": pwd}))
            return True


conn.extend = _Ext()


class FakePingWorker(PingWorker):
    def run(self):
        import time as _t
        self.ping_event.emit(netutils.parse_ping_line(f"Обмен пакетами с {self.target} по с 32 байтами данных:", self.target))
        if self.target in ("10.0.12.33", "WS-133"):
            for _ in range(30):
                if self.cancelled:
                    break
                _t.sleep(2.4)
                self.ping_event.emit(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", self.target))
            return
        for i in range(80):
            if self.cancelled:
                break
            ms = max(1, int(random.gauss(4, 1.5))) + (11 if 14 <= i <= 16 else 0)
            line = "Превышен интервал ожидания для запроса." if i in (8, 21) else f"Ответ от {self.target}: число байт=32 время={ms}мс TTL=128"
            self.ping_event.emit(netutils.parse_ping_line(line, self.target))
            _t.sleep(0.3)


pingui.PingWorker = FakePingWorker

_DHCP = json.dumps({"server": "dhcp01", "scopes": [{
    "scope": "10.0.12.0", "name": "Офис, 2 этаж", "start": "10.0.12.20", "end": "10.0.12.250", "state": "Active",
    "leases": [{"ip": f"10.0.12.{h}", "mac": f"00-1a-2b-3c-4d-{h:02x}", "host": f"ws-{h}", "state": "Active", "expires": "2026-09-07 09:00"}
               for h in (61, 62, 63, 66, 67, 68, 70, 71, 75, 76, 80, 81, 82)],
    "reservations": [{"ip": "10.0.12.64", "mac": "00-1a-2b-3c-4d-40", "name": "printer-2f"}, {"ip": "10.0.12.65", "mac": "00-1a-2b-3c-4d-41", "name": "scanner-2f"}],
    "exclusions": [{"start": "10.0.12.20", "end": "10.0.12.29"}]}]})
_dhcp_data = _dhcp.parse_dhcp_json(_DHCP)
_dhcp.query = lambda prefix, servers=None, timeout=40: {**_dhcp_data, "servers": ["dhcp01"], "errors": []}
netutils.is_host_alive = lambda ip, timeout=1.0: ip.rsplit(".", 1)[1] in ("60", "72", "73", "78")
_socket.gethostbyaddr = lambda ip: ("srv", [], []) if ip.endswith((".77", ".79")) else (_ for _ in ()).throw(OSError())


def _smart(attrs):
    buf = bytearray(512)
    buf[0:2] = b"\x10\x00"
    for i, (aid, (cur, worst, raw)) in enumerate(attrs.items()):
        off = 2 + i * 12
        buf[off] = aid
        buf[off + 3], buf[off + 4] = cur, worst
        buf[off + 5:off + 11] = raw.to_bytes(6, "little")
    return base64.b64encode(bytes(buf)).decode()


_ssd = _smart({0x05: (100, 100, 0), 0x09: (97, 97, 8760), 0x0C: (99, 99, 412), 0xB1: (99, 99, 3), 0xBE: (63, 50, 37), 0xC7: (100, 100, 0)})
_hdd = _smart({0x01: (100, 253, 0), 0x05: (97, 97, 48), 0x09: (41, 41, 52014), 0xC2: (107, 91, 43), 0xC5: (200, 200, 8), 0xC7: (200, 200, 0)})
_health = health.parse_health_json(json.dumps({
    "boot": "2026-08-21 08:12:00", "now": "2026-09-05 11:40:00", "total_mb": 16384, "free_mb": 5100, "cpu": 17, "os": "Windows 11 Pro 23H2",
    "disks": [{"id": "C:", "size": 476 * 1024 ** 3, "free": 31 * 1024 ** 3, "label": "System", "fs": "NTFS"},
              {"id": "D:", "size": 931 * 1024 ** 3, "free": 402 * 1024 ** 3, "label": "Data", "fs": "NTFS"}],
    "phys": [{"id": "0", "model": "Samsung SSD 870 EVO 500GB", "serial": "S5Y2NG0R123456A", "fw": "SVT02B6Q", "media": 4, "bus": 11,
              "size": 500107862016, "health": 0, "temp": 37, "wear": 4, "hours": 8760, "predict": False, "smart": _ssd},
             {"id": "1", "model": "WDC WD10EZEX-08WN4A0", "serial": "WD-WCC6Y4ABC123", "fw": "01.01A01", "media": 3, "bus": 11,
              "size": 1000204886016, "health": 1, "temp": 43, "hours": 52014, "predict": False, "smart": _hdd}]}))
_rd0 = "\\\\WS-101\\D$"
_usage_d = health.parse_usage_json(json.dumps({
    "root": _rd0, "root_files": 2 * 1024 ** 3, "total_files": 61_204, "errors": 0,
    "dirs": [{"path": _rd0 + "\\Архив", "size": 212 * 1024 ** 3, "files": 38000}, {"path": _rd0 + "\\1C_Backup", "size": 146 * 1024 ** 3, "files": 420},
             {"path": _rd0 + "\\Видео", "size": 88 * 1024 ** 3, "files": 610}, {"path": _rd0 + "\\Дистрибутивы", "size": 47 * 1024 ** 3, "files": 1900},
             {"path": _rd0 + "\\Сканы", "size": 19 * 1024 ** 3, "files": 17000}, {"path": _rd0 + "\\$Recycle.Bin", "size": 21 * 1024 ** 3, "files": 1450},
             {"path": _rd0 + "\\Temp", "size": 4 * 1024 ** 3, "files": 1800}],
    "files": [{"path": _rd0 + "\\1C_Backup\\buh_2025-12-31.dt", "size": 41_000_000_000}, {"path": _rd0 + "\\Видео\\Конференция_2026-02.mkv", "size": 12_400_000_000},
              {"path": _rd0 + "\\Дистрибутивы\\Win11_23H2_x64.iso", "size": 6_400_000_000}, {"path": _rd0 + "\\Архив\\Проекты_2019.zip", "size": 5_200_000_000},
              {"path": _rd0 + "\\1C_Backup\\zup_2025-12-31.dt", "size": 3_900_000_000}],
    "hogs": [{"label": "Корзина", "path": _rd0 + "\\$Recycle.Bin", "size": 21 * 1024 ** 3}, {"label": "Временные файлы", "path": _rd0 + "\\Temp", "size": 4 * 1024 ** 3}]}))
_r = "\\\\WS-101\\C$"
_usage = health.parse_usage_json(json.dumps({
    "root": _r, "root_files": 9 * 1024 ** 3, "total_files": 412_318, "errors": 14,
    "dirs": [{"path": _r + "\\Users", "size": 198 * 1024 ** 3, "files": 210000}, {"path": _r + "\\Windows", "size": 41 * 1024 ** 3, "files": 150000},
             {"path": _r + "\\Program Files", "size": 38 * 1024 ** 3, "files": 30000}, {"path": _r + "\\Program Files (x86)", "size": 22 * 1024 ** 3, "files": 18000},
             {"path": _r + "\\ProgramData", "size": 17 * 1024 ** 3, "files": 4000}, {"path": _r + "\\1C_Bases", "size": 91 * 1024 ** 3, "files": 300},
             {"path": _r + "\\$Recycle.Bin", "size": 12 * 1024 ** 3, "files": 80}, {"path": _r + "\\Windows.old", "size": 15 * 1024 ** 3, "files": 90000}],
    "files": [{"path": _r + "\\1C_Bases\\buh\\1Cv8.1CD", "size": 38_000_000_000}, {"path": _r + "\\hiberfil.sys", "size": 6_800_000_000},
              {"path": _r + "\\Users\\ivanov\\Downloads\\Win11_23H2.iso", "size": 6_400_000_000}, {"path": _r + "\\pagefile.sys", "size": 4_900_000_000},
              {"path": _r + "\\Users\\ivanov\\Videos\\Совещание_2026-03.mp4", "size": 3_100_000_000}],
    "hogs": [{"label": "Старая Windows", "path": _r + "\\Windows.old", "size": 15 * 1024 ** 3}, {"label": "Корзина", "path": _r + "\\$Recycle.Bin", "size": 12 * 1024 ** 3},
             {"label": "Файл гибернации", "path": _r + "\\hiberfil.sys", "size": 6_800_000_000}, {"label": "Файл подкачки", "path": _r + "\\pagefile.sys", "size": 4_900_000_000},
             {"label": "Обновления Windows", "path": _r + "\\Windows\\SoftwareDistribution\\Download", "size": 3 * 1024 ** 3},
             {"label": "Временные файлы Windows", "path": _r + "\\Windows\\Temp", "size": 1_300_000_000}],
    "users": [{"path": _r + "\\Users\\ivanov", "size": 141 * 1024 ** 3, "files": 150000}, {"path": _r + "\\Users\\petrova", "size": 39 * 1024 ** 3, "files": 40000},
              {"path": _r + "\\Users\\Public", "size": 2 * 1024 ** 3, "files": 300}]}))
_now = datetime.now()
_t_fn = lambda h, m=0: (_now - timedelta(hours=h, minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
_events = health.parse_events_json(json.dumps([
    {"time": _t_fn(0, 40), "log": "System", "level": 1, "id": 41, "source": "Microsoft-Windows-Kernel-Power", "msg": "Система перезагружена без корректного завершения работы."},
    {"time": _t_fn(1, 5), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой при запуске службы «Служба печати»: превышено время ожидания."},
    {"time": _t_fn(2, 30), "log": "System", "level": 2, "id": 7000, "source": "Service Control Manager", "msg": "Сбой при запуске службы «Служба печати»: превышено время ожидания."},
    {"time": _t_fn(3, 10), "log": "Application", "level": 2, "id": 1000, "source": "Application Error", "msg": "Имя сбойного приложения: EXCEL.EXE — исключение 0xc0000005."},
    {"time": _t_fn(4, 0), "log": "System", "level": 2, "id": 10016, "source": "Microsoft-Windows-DistributedCOM", "msg": "Параметры разрешений не дают разрешения Local Activation для сервера COM."},
    {"time": _t_fn(5, 20), "log": "System", "level": 3, "id": 129, "source": "storahci", "msg": "Сброс на устройстве \\\\Device\\\\RaidPort0."},
    {"time": _t_fn(6, 45), "log": "Application", "level": 3, "id": 1530, "source": "Microsoft-Windows-User Profiles Service", "msg": "Файл реестра всё ещё используется другими приложениями."},
    {"time": _t_fn(9, 0), "log": "System", "level": 3, "id": 1014, "source": "Microsoft-Windows-DNS-Client", "msg": "Истекло время ожидания разрешения имени."},
    {"time": _t_fn(11, 15), "log": "System", "level": 2, "id": 10016, "source": "Microsoft-Windows-DistributedCOM", "msg": "Параметры разрешений не дают разрешения Local Activation для сервера COM."},
    {"time": _t_fn(13, 0), "log": "Application", "level": 4, "id": 1001, "source": "Windows Error Reporting", "msg": "Контейнер ошибки, тип 0. Имя события: APPCRASH."},
    {"time": _t_fn(20, 0), "log": "Application", "level": 3, "id": 8193, "source": "VSS", "msg": "Ошибка службы теневого копирования томов."},
    {"time": _t_fn(30, 0), "log": "System", "level": 2, "id": 7034, "source": "Service Control Manager", "msg": "Служба «Spooler» неожиданно завершена."},
]))
_live = {"printers": netutils.parse_live_printers_json(json.dumps([
    {"name": "HP LaserJet M404dn", "port": "IP_10.0.12.50", "default": True, "status": 3, "offline": False, "driver": "HP", "host": "10.0.12.50"},
    {"name": "Microsoft Print to PDF", "port": "PORTPROMPT:", "default": False, "status": 3},
    {"name": "Canon LBP6030", "port": "USB001", "default": False, "status": 7, "offline": True},
    {"name": "Brother HL-L2340", "port": "IP_10.0.12.57", "default": False, "status": 3, "offline": False, "host": "10.0.12.57"}]))}


_lg_now = datetime.now()
_lg = lambda h, m=0: (_lg_now - timedelta(hours=h, minutes=m)).strftime("%Y-%m-%d %H:%M:%S")
_logons_data = _logons.parse_events_json(json.dumps([
    {"ts": _lg(0, 12), "id": 4624, "user": "ivanov", "domain": "CORP", "type": "7", "ip": ""},
    {"ts": _lg(1, 3), "id": 4624, "user": "ivanov", "domain": "CORP", "type": "2", "ip": ""},
    {"ts": _lg(1, 40), "id": 4625, "user": "ivanov", "domain": "CORP", "type": "2", "ip": "", "status": "0xc000006a"},
    {"ts": _lg(1, 41), "id": 4625, "user": "ivanov", "domain": "CORP", "type": "2", "ip": "", "status": "0xc000006a"},
    {"ts": _lg(3, 15), "id": 4624, "user": "admin", "domain": "CORP", "type": "10", "ip": "10.0.12.5"},
    {"ts": _lg(5, 0), "id": 4624, "user": "helpdesk", "domain": "CORP", "type": "3", "ip": "10.0.12.7"},
    {"ts": _lg(6, 30), "id": 4625, "user": "petrova", "domain": "CORP", "type": "10", "ip": "10.0.12.15", "status": "0xc0000064"},
    {"ts": _lg(9, 10), "id": 4624, "user": "ivanov", "domain": "CORP", "type": "2", "ip": ""},
    {"ts": _lg(22, 45), "id": 4624, "user": "ivanov", "domain": "CORP", "type": "11", "ip": ""},
]))
_logons.get_logons = lambda host, hours=24, timeout=90: _logons_data


def title_card(lines, hold_sec=3.0):
    img = Image.new("RGB", (W, H), (28, 28, 30))
    d = ImageDraw.Draw(img)
    logo = Image.open(os.path.join(ROOT, "assets", "logo.png")).convert("RGBA").resize((150, 150), Image.LANCZOS)
    img.paste(logo, (W // 2 - 75, H // 2 - 200), logo)
    y = H // 2 - 20
    for txt, font, col in lines:
        tw = d.textlength(txt, font=font)
        d.text(((W - tw) / 2, y), txt, font=font, fill=col)
        y += {big: 84, mid: 42, small: 30}[font]
    for _ in range(sec(hold_sec)):
        _write(img)


# ----------------------------------------------------------------------------- секундомер (HUD)
import time as _time  # noqa: E402
_hud = {"query": "", "start": None, "t_rows": None, "t_net": None, "db": 0.0, "ldap": 0, "note": ""}
_HUD_F = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 17)
_HUD_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)


def _fmt_s(v):
    return "…" if v is None else f"{v:.3f} с".replace(".", ",")


def draw_hud(img):
    """Секундомер поиска в верхней части кадра: время по настоящим часам от нажатия до строк и до статуса сети."""
    if not _hud["query"]:
        return
    now = _time.perf_counter()
    run = (now - _hud["start"]) if _hud["start"] else 0.0
    t_rows = _hud["t_rows"] if _hud["t_rows"] is not None else (run if _hud["t_net"] is None else None)
    t_net = _hud["t_net"] if _hud["t_net"] is not None else (run if _hud["t_rows"] is not None else None)
    head = f"СЕКУНДОМЕР · «{_hud['query']}»" + (f" · {_hud['note']}" if _hud["note"] else "")
    parts = [f"строки на экране: {_fmt_s(t_rows)}", f"статус сети: {_fmt_s(t_net)}",
             f"из них SQLite: {_hud['db'] * 1000:.0f} мс", f"запросов к AD: {_hud['ldap']}"]
    body = "     ".join(parts)
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    tw = max(d.textlength(head, font=_HUD_F), d.textlength(body, font=_HUD_S))
    x0, y0, pad = 300, 6, 14
    d.rounded_rectangle((x0, y0, x0 + tw + 2 * pad, y0 + 50), radius=10, fill=(28, 28, 30, 225), outline=(10, 132, 255, 210), width=2)
    done = _hud["t_net"] is not None
    d.text((x0 + pad, y0 + 5), head, font=_HUD_F, fill=(48, 209, 88, 255) if done else (255, 214, 10, 255))
    d.text((x0 + pad, y0 + 28), body, font=_HUD_S, fill=(255, 255, 255, 255))
    img.paste(overlay, (0, 0), overlay)


_render_plain = render


def render(widgets, caption_alpha=1.0, cursor=True):   # noqa: F811 — HUD поверх обычного кадра
    img = _render_plain(widgets, caption_alpha, cursor=False)
    draw_hud(img)
    if cursor:
        draw_cursor(img, *_cursor)
    return img


def _wrap_db_timer():
    """Обёртки над функциями adk.db, которые зовёт поиск: копим чистое время SQLite в _hud['db']."""
    for fn_name in ("logins_by_computer_or_ip", "logins_by_printer", "printer_matches", "printer_owners", "load_inventory_maps",
                    "load_audit_map", "printers_for_computers", "inventory_rows_matching", "inventory_rows_for_computers"):
        fn = getattr(db, fn_name)

        def wrap(f):
            def inner(*a, **k):
                t = _time.perf_counter()
                try:
                    return f(*a, **k)
                finally:
                    _hud["db"] += _time.perf_counter() - t
            return inner
        setattr(db, fn_name, wrap(fn))


_wrap_db_timer()


def realtime(widgets, cond, timeout=12.0):
    """Кадры пишутся по настоящим часам, пока не выполнится cond: видео-время = реальное время."""
    t0 = last = _time.perf_counter()
    while True:
        app.processEvents()
        now = _time.perf_counter()
        n = int((now - last) * FPS)
        if n >= 1:
            img = render(widgets)
            for _ in range(n):
                _write(img)
            last += n / FPS
        if cond() or now - t0 > timeout:
            return cond()
        _time.sleep(0.004)


def hud_reset(query, note=""):
    _hud.update(query=query, start=None, t_rows=None, t_net=None, db=0.0, ldap=0, note=note)
    DemoConn.calls.clear()


def hud_start():
    _hud["start"] = _time.perf_counter()


def _hud_tick():
    """Фиксирует моменты «строки на экране» и «статус сети готов» — по состоянию настоящего окна."""
    if _hud["start"] is None:
        return
    now = _time.perf_counter()
    if _hud["t_rows"] is None and w.stack.currentIndex() == 1 and w.table.rowCount() > 0 and w.results and w._hud_fresh:
        _hud["t_rows"] = now - _hud["start"]
    if _hud["t_rows"] is not None and _hud["t_net"] is None and not any(r.get("net_pending") for r in w.results):
        _hud["t_net"] = now - _hud["start"]
    _hud["ldap"] = len(DemoConn.calls)


def timed_search(widgets, text, note="", settle=1.2):
    """Набрать запрос, нажать «Найти» и замерить по настоящим часам: строки на экране, статус сети."""
    hud_reset(text, note)
    w.search_input.blockSignals(True); w.search_input.setText(text); w.search_input.blockSignals(False)
    w.table.setRowCount(0); w.results = []; w._hud_fresh = False
    snap(widgets, 2)
    _btn = [b for b in w.findChildren(QPushButton) if "Найти" in b.text()][0]
    move_to(widgets, _btn, 10)
    _btn.setDown(True); snap(widgets, 3); _btn.setDown(False)
    hud_start(); w._hud_fresh = True
    w.start_search()
    realtime(widgets, lambda: (_hud_tick(), _hud["t_net"] is not None)[1], timeout=15.0)
    wait(150)
    hold(widgets, settle)
    return _hud["t_rows"], _hud["t_net"]


def select_login(login):
    """Выбрать строку героя по логину (в стенде есть однофамильцы — «первая строка» не гарантирует героя)."""
    tg._wait(lambda: any((w.table.item(r, 0) or QTableWidgetItem()).text() == login for r in range(w.table.rowCount())), app, 5000)
    r = next((r for r in range(w.table.rowCount()) if w.table.item(r, 0) and w.table.item(r, 0).text() == login), 0)
    w.table.scrollToItem(w.table.item(r, 0)); w.select_row(r)


def live(widgets, seconds, step=0.3):
    """Живой отрезок: окно обновляется само (пинг, графики) — кадры снимаются с реальным ходом времени."""
    n = max(1, int(seconds / step))
    for _ in range(n):
        wait(int(step * 1000))
        snap(widgets, sec(step))


big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
mid = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
VERSION = "3.5.7"
TITLE = [("ADK", big, (48, 209, 88)), (f"Active Directory Kit · {VERSION}", mid, (242, 247, 243)),
         ("Демонстрация возможностей · стенд: 1500 ПК, 1350 пользователей", small, (148, 163, 184))]

title_card(TITLE, hold_sec=3.0)

# ============================================================ 0. Вход
class _Bg:
    @staticmethod
    def grab():
        from PyQt6.QtGui import QImage, QPixmap
        qi = QImage(W, H, QImage.Format.Format_RGB32); qi.fill(0xFF1C1C1E)
        return QPixmap.fromImage(qi)


login_dlg = LoginDialog(saved_user="CORP\\admin", saved_password="Qz7!pLm2#vX")
login_dlg.show(); wait(300)
S = [_Bg, login_dlg]
cap("Вход: доменная учётная запись, пароль можно показать и запомнить")
_cursor[:] = [W // 2 + 200, H // 2 + 200]
hold(S, 2.6)
move_to(S, login_dlg.pass_in, 8)
move_to(S, login_dlg.btn_eye, 8)
press(S, login_dlg.btn_eye); login_dlg.btn_eye.setChecked(True); wait(150)
hold(S, 1.2)
press(S, login_dlg.btn_eye); login_dlg.btn_eye.setChecked(False); wait(150)
move_to(S, login_dlg.remember, 8)
login_dlg.remember.setChecked(True); snap(S, sec(0.5))
move_to(S, login_dlg.btn_login, 10)
press(S, login_dlg.btn_login)
login_dlg._busy(True, "Вход в систему…"); snap(S, sec(0.8))
login_dlg.close()

# ============================================================ 1. Главное окно → окно роли (после определения прав)
cap("Стартовый экран: состояние парка, что требует внимания, быстрый доступ")
config.settings.hide_role_welcome = False
ADApp.start_scan = lambda self: None      # сканер парка в песочнице не нужен: инвентарь стенда уже в базе
w = ADApp("CORP\\admin", "x")
w._hud_fresh = False
w.resize(W, H); w.show()
w.active_ad_total = len(_inv_net)
w.refresh_dashboard()
items = attention.from_entries(entries, now=now, acct_days=7, no_logon_days=90) + attention.from_inventory(30)
w.set_attention_items(items)
wait(500)
_cursor[:] = [W // 2, H // 2 + 120]
S = [w]
snap(S, 2)
hold(S, 2.6)
cap("После входа ADK определяет права и показывает, что доступно этой роли")
access.set_rights(pc=True, ad=True); w.apply_access()
w.show_role_welcome(); wait(400)
role_dlg = w.role_welcome
S = S + [role_dlg]
snap(S, 3)
hold(S, 3.2)
move_to(S, role_dlg.chk_dont_show, 12)
hold(S, 0.8)
move_to(S, role_dlg.btn_ok, 10)
press(S, role_dlg.btn_ok)
role_dlg.close(); wait(250); S = [w]; snap(S, 3)
hold(S, 2.4)
cap("Текущая роль всегда видна в строке состояния")
move_to(S, w.lbl_role_status, 14)
hold(S, 1.2)

# ============================================================ 2. Плагины: менеджер, шаблон, «настоящий» плагин «Сообщение»
config.settings.plugins_dir = tempfile.mkdtemp(prefix="adk_plugins_")
_PLUGIN_SRC = '''"""Плагин ADK «Сообщение»: окно в стиле программы + текст на экран ПК сотрудника (msg.exe)."""
from adk.plugins import Action


class Message(Action):
    name = "Сообщение"
    icon = "✉️"
    needs_pc = True
    modifying = True
    place = "header"          # компактная кнопка рядом с ФИО

    def run(self, ctx):
        from adk.widgets import InputDialog, MessageBox
        from adk import nettools
        text, ok = InputDialog.get_text(ctx["window"], f"Сообщение для {ctx['fio']}", f"Текст появится на экране {ctx['comp']}:")
        if not ok or not text.strip():
            return "Отменено"
        sent, info = nettools.send_message(ctx["comp"], text)
        (MessageBox.information if sent else MessageBox.warning)(ctx["window"], "Сообщение", info)
        return info
'''
cap("Плагины: свои кнопки в инспекторе — файл .py в папке плагинов")
b_plugins = [b for b in w.findChildren(QPushButton) if "Плагины" in b.text()][0]
move_to(S, b_plugins, 14)
press(S, b_plugins)
plg = PluginsDialog(w)
_PDIR = "Папка: C:\\Users\\admin\\Documents\\ADK\\plugins"
plg.lbl_dir.setText(_PDIR)
S = show_dialog(S, plg)
hold(S, 2.2)
cap("«Создать шаблон плагина» — заготовка со всей документацией внутри файла")
move_to(S, plg.btn_template, 12)
press(S, plg.btn_template)
plg._create_template(); plg.lbl_dir.setText(_PDIR); wait(300)
hold(S, 2.6)
cap("Из шаблона сделан плагин «Сообщение» — окно в стиле ADK и текст на экран ПК")
with open(os.path.join(config.settings.plugins_dir, "message.py"), "w", encoding="utf-8") as _f:
    _f.write(_PLUGIN_SRC)
move_to(S, plg.btn_reload, 10)
press(S, plg.btn_reload); plg.reload(); plg.lbl_dir.setText(_PDIR); wait(300)
hold(S, 2.6)
_row_msg = next(r for r in range(plg.table.rowCount()) if plg.table.item(r, 0).text() == "message.py")
move_to(S, plg.table, 10, offset=(-plg.table.width() // 2 + 90, -plg.table.height() // 2 + 44 + 28 * _row_msg))
click_fx(S); plg.table.selectRow(_row_msg); wait(200)
hold(S, 1.2)
S = hide_dialog(S, plg)
cap("Кнопка «Сообщение» появилась рядом с ФИО — её добавил плагин")
w.search_input.setText("иванов"); w.start_search(); select_login("ivanov"); wait(300)
_pb = w.action_buttons.get("plugin:Сообщение")
move_to(S, _pb, 14)
hold(S, 1.6)
press(S, _pb)
_msg_dlg = InputDialog(w, "Сообщение для Иванов Иван Петрович", "Текст появится на экране WS-101:")
S = show_dialog(S, _msg_dlg)
hold(S, 1.0)
type_text(S, _msg_dlg.edit, "Через 10 минут перезагрузка сервера печати", 0.06)
hold(S, 1.0)
_ok_b = [b for b in _msg_dlg.findChildren(QPushButton) if b.text() in ("ОК", "OK")]
if _ok_b:
    move_to(S, _ok_b[0], 10); press(S, _ok_b[0])
S = hide_dialog(S, _msg_dlg)
db.log_action("CORP\\admin", "plugin", "ivanov", "Сообщение: Сообщение отправлено на WS-101")
_mb = MessageBox(w, "Сообщение", "Сообщение отправлено на WS-101", "info"); _mb.show(); wait(300); S = S + [_mb]
w.lbl_status.setText("✅ Сообщение отправлено на WS-101")
hold(S, 2.0)
S = hide_dialog(S, _mb)
cap("Выключаем плагин в менеджере — кнопка исчезает сразу, без перезапуска")
move_to(S, b_plugins, 14)
press(S, b_plugins)
plg = PluginsDialog(w); plg.lbl_dir.setText(_PDIR)
S = show_dialog(S, plg)
_row_msg = next(r for r in range(plg.table.rowCount()) if plg.table.item(r, 0).text() == "message.py")
move_to(S, plg.table, 10, offset=(-plg.table.width() // 2 + 90, -plg.table.height() // 2 + 44 + 28 * _row_msg))
click_fx(S); plg.table.selectRow(_row_msg); wait(200)
hold(S, 1.0)
move_to(S, plg.btn_toggle, 10)
press(S, plg.btn_toggle); plg._toggle(); plg.lbl_dir.setText(_PDIR); wait(300)
hold(S, 2.0)
S = hide_dialog(S, plg)
select_login("ivanov"); wait(300)
assert w.action_buttons.get("plugin:Сообщение") is None
move_to(S, w.btn_copy, 12)
hold(S, 2.2)
w.search_input.clear(); w.on_text_changed(""); wait(300)
_cursor[:] = [W // 2, H // 2 + 120]
hold(S, 1.0)

# ============================================================ 3. Поиск, столбцы, инспектор
cap("Поиск по фамилии, логину, имени ПК или IP — результат по мере набора")
move_to(S, w.search_input, 16)
type_text(S, w.search_input, "сидоров", 0.11)
# 3.5.4: поиск в два шага — строки сразу, сеть чуть позже. В демо сеть «думает», пока открыт шлюз _gate
import threading as _thr  # noqa: E402
from adk.main_window import BADGE_ROLE, COL_NET  # noqa: E402
_gate = _thr.Event()
_fast_net = netutils.get_computer_network_info
def _slow_net(n, **kw):
    _gate.wait(6)
    return _fast_net(n, **kw)
netutils.clear_network_cache(); netutils.get_computer_network_info = _slow_net
w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "sidorov"
         and (w.table.item(0, COL_NET) or QTableWidgetItem()).data(BADGE_ROLE) == "checking", app, 5000); wait(300)
hold(S, 2.0, "Строки появляются сразу — статус сети из последнего сканирования, в столбце «Сеть» — «Проверка…»")
_gate.set(); tg._wait(lambda: w.table.item(0, COL_NET) is not None and w.table.item(0, COL_NET).data(BADGE_ROLE) != "checking", app, 5000); wait(300)
netutils.get_computer_network_info = _fast_net
hold(S, 2.2, "…и через мгновение — честный результат проверки, строки остаются на месте")
hdr = w.table.horizontalHeader()
move_to(S, hdr, 14, offset=(120, 0))
hold(S, 0.4)
menu = w.build_column_menu(COLUMNS.index("ФИО"))
menu.popup(w.mapToGlobal(QPoint(220, 250))); wait(300)
S = S + [menu]
hold(S, 2.0)
act_dept = next(a for a in menu.actions() if a.text() == "Отдел")
move_to(S, menu, 10, offset=(-20, -menu.height() // 2 + menu.actionGeometry(act_dept).center().y()))
click_fx(S)
act_dept.setChecked(True); wait(250)
hold(S, 1.6)
menu.close(); S = [w]; wait(200)
hold(S, 1.2)
w.reset_columns(); wait(250)
move_to(S, w.table, 14, offset=(-w.table.width() // 2 + 120, -w.table.height() // 2 + 45))
click_fx(S)
select_login("sidorov"); wait(300)
hold(S, 2.8)

# ============================================================ 3а. Скорость поиска на стенде: 1500 ПК, секундомер по настоящим часам
w.search_input.clear(); w.on_text_changed(""); wait(300)
_cursor[:] = [W // 2, H // 2 + 120]
cap("Стенд: 1500 ПК в инвентаре, 1350 учёток в AD, 4,5 тыс. принтеров, 90 тыс. записей ПО — всё в обычном pc_mapping.db")
hold(S, 3.4)
cap("Секундомер сверху — настоящие часы: от нажатия «Найти» до строк на экране и до готового статуса сети")
hold(S, 2.6)
move_to(S, w.search_input, 14)
cap("Фамилия среди 1350 учёток: строки — за доли секунды; сеть каждого ПК проверяется вторым шагом")
timed_search(S, "Шевченко", settle=3.0)
cap("Имя ПК из 1500: инвентарь → логин → AD → строка")
timed_search(S, "WS-0731", settle=2.6)
cap("IP-адрес ПК — тот же путь через инвентарь")
timed_search(S, "10.0.8.176", settle=2.6)
cap("Подсеть 10.0.3 — почти 200 ПК; в AD уходит один запрос сразу по всем логинам")
timed_search(S, "10.0.3", settle=3.0)
cap("Отдел: больше сотни строк — таблица заполняется без задержки")
timed_search(S, "Бухгалтерия", settle=2.8)
cap("Модель принтера: сами принтеры первыми строками, статус каждого — вторым шагом, как у ПК")
timed_search(S, "Kyocera", settle=3.0)
cap("IP сетевого принтера: только он и кто к нему подключён — 40 ПК")
timed_search(S, "10.0.9.93", settle=2.6)
move_to(S, w.table, 12, offset=(-w.table.width() // 2 + 120, -w.table.height() // 2 + 45))
click_fx(S)
w.select_row(0); wait(400)
hold(S, 2.4)
move_to(S, w.chk_archive, 14)
press(S, w.chk_archive)
w.chk_archive.setChecked(True); wait(200)
cap("Режим «Архивы»: к 1500 ПК добавляются 1,6 тыс. записей архива — время почти не меняется")
timed_search(S, "Шевченко", note="архивы", settle=3.0)
press(S, w.chk_archive)
w.chk_archive.setChecked(False); wait(200)
cap("Набор по буквам: поиск ждёт паузу 450 мс — в AD уходит один запрос, а не семь")
hud_reset("Шевченко", "по буквам, от последней буквы")
w.search_input.clear(); w.table.setRowCount(0); w.results = []; w._hud_fresh = True; wait(120)
move_to(S, w.search_input, 10)
for _i in range(1, len("Шевченко") + 1):
    w.search_input.setText("Шевченко"[:_i])
    realtime(S, lambda: False, timeout=0.09)
hud_start()
realtime(S, lambda: (_hud_tick(), _hud["t_net"] is not None)[1], timeout=15.0)
wait(150)
hold(S, 3.2)
cap("Итог на 1500 ПК: база — не узкое место, основное время — ответ контроллера домена и проверка сети")
hold(S, 3.0)
_hud["query"] = ""     # секундомер дальше не нужен
w.search_input.clear(); w.on_text_changed(""); wait(300)
_cursor[:] = [W // 2, H // 2 + 120]
hold(S, 1.4)

# ============================================================ 4. Фильтры: отдел, отключённые, архивы, мультивыбор
cap("Фильтры: отдел, отключённые учётки, архивы; настраиваемые столбцы")
move_to(S, w.search_input, 14)
w.search_input.clear(); wait(150)
type_text(S, w.search_input, "ИТ-отдел", 0.1)
w.start_search(); tg._wait(lambda: w.table.rowCount() >= 50, app, 8000); wait(400)
_n_active = w.table.rowCount()
hold(S, 2.4)
w.table.verticalScrollBar().setValue(w.table.verticalScrollBar().maximum() // 3); wait(200)
hold(S, 1.8)
w.select_row(w.table.rowAt(10)); wait(200)
hold(S, 1.6)
w.table.verticalScrollBar().setValue(0); wait(150)
# мультивыбор — разделители строк видны
cap("Мультивыбор строк — для массовых операций")
w.table.selectAll(); wait(300)
_cursor[:] = [300, 300]
hold(S, 2.4)
w.table.clearSelection(); w.select_row(0); wait(200)
move_to(S, w.chk_disabled, 14)
press(S, w.chk_disabled)
w.chk_disabled.setChecked(True); tg._wait(lambda: w.table.rowCount() > _n_active, app, 8000); wait(400)
_dis_rows = [r for r in range(w.table.rowCount()) if "Не активна" in (w.table.item(r, 2).text() if w.table.item(r, 2) else "")]
if _dis_rows:
    w.table.scrollToItem(w.table.item(_dis_rows[0], 0)); w.select_row(_dis_rows[0]); wait(300)
hold(S, 2.6)
press(S, w.chk_disabled)
w.chk_disabled.setChecked(False); tg._wait(lambda: w.table.rowCount() == _n_active, app, 8000); wait(300)
move_to(S, w.search_input, 12)
w.search_input.clear(); wait(150)
type_text(S, w.search_input, "Бухгалтерия", 0.1)
w.start_search(); tg._wait(lambda: w.table.rowCount() >= 3 and w.table.rowCount() != _n_active, app, 8000); wait(400)
hold(S, 2.2)
w.search_input.clear(); wait(150)
type_text(S, w.search_input, "сидоров", 0.1)
w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "sidorov", app, 5000); wait(300)
move_to(S, w.chk_archive, 14)
press(S, w.chk_archive)
w.chk_archive.setChecked(True); tg._wait(lambda: w.table.rowCount() >= 2, app, 8000); wait(400)
hold(S, 2.6)
press(S, w.chk_archive)
w.chk_archive.setChecked(False); wait(300)
w.start_search(); select_login("sidorov"); wait(300)

# ============================================================ 5. Пинг: ПК не в сети, затем в сети
cap("Пинг: ПК не в сети — живой график, журнал только событий")
move_to(S, w.btn_ping, 16)
press(S, w.btn_ping)
pd = PingDialog("WS-133", "10.0.12.33", w, w)
S = show_dialog(S, pd)
live(S, 14.0)
move_to(S, pd.filter_btns["all"], 12)
press(S, pd.filter_btns["all"]); pd._set_filter("all"); wait(250)
live(S, 3.0)
S = hide_dialog(S, pd)
w.search_input.clear(); type_text(S, w.search_input, "иванов", 0.08)
w.start_search(); select_login("ivanov"); wait(300)
hold(S, 1.2)
move_to(S, w.btn_ping, 14)
press(S, w.btn_ping)
cap("Пинг ПК в сети: задержка, потери, джиттер; фильтры, пауза, отчёт в буфер")
pd = PingDialog("WS-101", "10.0.12.11", w, w)
S = show_dialog(S, pd)
live(S, 12.0)
move_to(S, pd.filter_btns["all"], 12)
press(S, pd.filter_btns["all"]); pd._set_filter("all"); wait(250)
live(S, 4.0)
move_to(S, pd.filter_btns["bad"], 12)
press(S, pd.filter_btns["bad"]); pd._set_filter("bad"); wait(250)
live(S, 2.5)
move_to(S, pd.filter_btns["events"], 10)
press(S, pd.filter_btns["events"]); pd._set_filter("events"); wait(250)
live(S, 1.5)
move_to(S, pd.btn_pause, 12)
press(S, pd.btn_pause); pd.toggle_pause(); wait(200)
hold(S, 1.2)
press(S, pd.btn_pause); pd.toggle_pause(); wait(200)
move_to(S, pd.btn_copy, 12)
press(S, pd.btn_copy); pd.copy_report(); wait(200)
hold(S, 1.2)
S = hide_dialog(S, pd)

# ============================================================ 6. Принтеры: инспектор, живой опрос, поиск по IP, парк
cap("Принтеры сотрудника: из инвентаря и живым опросом ПК")
snap(S, 2)
badge = w.printers_box.findChildren(BadgeButton)[0]
move_to(S, badge, 12)
hold(S, 1.6)
move_to(S, w.btn_live_printers, 14)
press(S, w.btn_live_printers)
w.lbl_live_printers.setVisible(True); w.lbl_live_printers.setText("⏳ Опрашиваю ПК напрямую…"); wait(200)
snap(S, sec(0.9))
w._show_live_printers(w.selected(), "WS-101", _live); wait(300)
hold(S, 2.8)
move_to(S, w.search_input, 14)
w.search_input.clear(); wait(150)
cap("Поиск по IP принтера: находится сам принтер, проверяется, что по адресу именно он")
type_text(S, w.search_input, "10.0.12.50", 0.1)
w.start_search(); tg._wait(lambda: w.table.rowCount() == 1 and "Принтеров: 1" in w.lbl_status.text(), app, 5000); wait(300)
hold(S, 2.4)
move_to(S, w.table, 12, offset=(-w.table.width() // 2 + 120, -w.table.height() // 2 + 45))
click_fx(S)
w.select_row(0); wait(400)
hold(S, 2.8)
move_to(S, w.btn_printer_web, 12)
hold(S, 1.2)
cap("Принтеры парка: сводка по всем ПК, фильтр по типу и модели")
b_prn = [b for b in w.findChildren(QPushButton) if "Принтеры парка" in b.text()][0]
w.search_input.clear(); w.on_text_changed(""); wait(300)
move_to(S, b_prn, 12)
press(S, b_prn)
prd = PrintersDialog(w, w)
S = show_dialog(S, prd)
tg._wait(lambda: prd.table.rowCount() >= 1, app, 3000); wait(200)
hold(S, 2.6)
move_to(S, prd.kind, 10)
_kx = prd.kind.findData("network")
if _kx > 0:
    prd.kind.setCurrentIndex(_kx); wait(300)
hold(S, 1.8)
prd.kind.setCurrentIndex(0); wait(200)
move_to(S, prd.text, 10)
type_text(S, prd.text, "Kyocera", 0.1); wait(300)
hold(S, 1.8)
prd.text.clear(); wait(200)
S = hide_dialog(S, prd)

# ============================================================ 7. Диск, питание, здоровье ПК
cap("Действия с ПК: диски по томам, управление, питание, здоровье")
w.search_input.setText("иванов"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1, app, 5000); select_login("ivanov"); wait(300)
snap(S, 2)
_popen_calls = []
_real_popen = _mw.subprocess.Popen
_mw.subprocess.Popen = lambda argv, **kw: _popen_calls.append(argv)
netutils.known_volumes = lambda comp: [{"letter": "C", "label": "System", "size": "476 ГБ"}, {"letter": "D", "label": "Data", "size": "932 ГБ"}] if comp == "WS-101" else [{"letter": "C", "label": "System", "size": "238 ГБ"}]
b_disk = w.action_buttons["disk"]
move_to(S, b_disk, 14)
press(S, b_disk)
w.remote_action("disk"); wait(250)
_dmenu = w._disk_menu
_dmenu._demo_pos = (b_disk.mapTo(w, QPoint(0, b_disk.height())).x(), b_disk.mapTo(w, QPoint(0, b_disk.height())).y() + 2)
S = S + [_dmenu]
hold(S, 2.2)
_act = [a for a in _dmenu.actions() if a.isEnabled()][1]
move_to(S, _dmenu, 10, offset=(-_dmenu.width() // 2 + 40, -_dmenu.height() // 2 + _dmenu.actionGeometry(_act).center().y()))
click_fx(S)
_act.trigger(); _dmenu.close(); S = S[:-1]; wait(200)
hold(S, 1.2)
b_power = w.action_buttons["power"]
move_to(S, b_power, 14)
press(S, b_power)
w.remote_action("power"); wait(250)
_pmenu = w._power_menu
# положение как на настоящем экране 1920×1080: под кнопкой, если помещается до низа окна, иначе — над кнопкой
# (offscreen-экран песочницы 800×800 заставляет Qt сдвигать меню влево — это артефакт, не поведение программы)
def _menu_pos(menu, btn):
    top_left = btn.mapTo(w, QPoint(0, 0))
    below = top_left.y() + btn.height() + 2
    return (top_left.x(), below if below + menu.height() <= H - 48 else top_left.y() - menu.height() - 2)
_pmenu._demo_pos = _menu_pos(_pmenu, b_power)
S = S + [_pmenu]
hold(S, 2.6)
_pacts = [a for a in _pmenu.actions() if a.isEnabled() and not a.isSeparator()]
_pa = _pacts[1]
move_to(S, _pmenu, 10, offset=(-_pmenu.width() // 2 + 40, -_pmenu.height() // 2 + _pmenu.actionGeometry(_pa).center().y()))
click_fx(S)
_pmenu.close(); S = S[:-1]
w.remote_action("lock"); wait(250)
hold(S, 1.6)
_mw.subprocess.Popen = _real_popen
# входы за 24 ч
cap("Входы на ПК за 24 часа: кто, когда, отказы — из журнала безопасности")
b_logons = w.action_buttons["logons"]
move_to(S, b_logons, 14)
press(S, b_logons)
lg = LogonsDialog("WS-101", w, w)
lg.show(); wait(200)
tg._wait(lambda: lg.table.rowCount() >= 1, app, 4000); wait(300)
S = S + [lg]; snap(S, 3)
hold(S, 3.0)
move_to(S, lg.only_fail, 12)
press(S, lg.only_fail); lg.only_fail.setChecked(True); wait(250)
hold(S, 2.2)
press(S, lg.only_fail); lg.only_fail.setChecked(False); wait(250)
hold(S, 1.0)
S = hide_dialog(S, lg)
cap("Здоровье ПК: обзор, S.M.A.R.T., карта диска по томам, что можно почистить, ошибки")
b_health = w.action_buttons["health"]
move_to(S, b_health, 14)
press(S, b_health)
hd = HealthDialog("WS-101", w, w)
hd.show(); tg._wait(lambda: hd.btn_refresh.isEnabled(), app, 5000); hd.show_health(_health); wait(300)
S = show_dialog(S, hd)
hold(S, 2.6)
move_to(S, hd.tabs.tabBar(), 14, offset=(-hd.tabs.tabBar().width() // 2 + 190, 0))
click_fx(S)
hd.tabs.setCurrentIndex(1); wait(300)
hold(S, 2.4)
move_to(S, hd.disk_cards[1], 14)
hd._select_disk(_health["phys"][1], hd.disk_cards[1]); wait(200)
hold(S, 2.4)
move_to(S, hd.tabs.tabBar(), 14, offset=(0, 0))
click_fx(S)
hd.tabs.setCurrentIndex(2); wait(300)
hold(S, 1.4)
press(S, hd.btn_usage)
hd.lbl_usage.setText("⏳ Обхожу \\\\WS-101\\C$ — это может занять несколько минут…"); snap(S, sec(1.2))
hd.show_usage(_usage); wait(300)
hold(S, 3.0)
_tile_rect = hd.treemap.rects[1][0]
move_to(S, hd.treemap, 12, offset=(int(_tile_rect.center().x()) - hd.treemap.width() // 2, int(_tile_rect.center().y()) - hd.treemap.height() // 2))
click_fx(S)
hd.treemap._selected = 1; hd.treemap.update(); hd.treemap.on_click(hd.treemap.rects[1][1]); wait(200)
hold(S, 2.2)
move_to(S, hd.cb_drive, 14)
click_fx(S)
_dm = hd.cb_drive.menu
_dox, _doy = dlg_origin(hd)
_dp = hd.cb_drive.mapTo(hd, QPoint(0, hd.cb_drive.height()))
_dm._demo_pos = (_dox + _dp.x(), _doy + _dp.y() + 2)
_dm.popup(QPoint(0, 0)); wait(250)
S = S + [_dm]
hold(S, 2.0)
_act_d = next(a for a in _dm.actions() if "D:" in a.text())
move_to(S, _dm, 10, offset=(-_dm.width() // 2 + 40, -_dm.height() // 2 + _dm.actionGeometry(_act_d).center().y()))
click_fx(S)
_dm.close(); S = S[:-1]
hd.cb_drive.setCurrentText("D:"); wait(200)
press(S, hd.btn_usage)
hd.lbl_usage.setText("⏳ Обхожу \\\\WS-101\\D$ — это может занять несколько минут…"); snap(S, sec(1.2))
hd.show_usage(_usage_d); wait(300)
hold(S, 2.6)
move_to(S, hd.usage_tabs.tabBar(), 14, offset=(60, 0))
click_fx(S)
hd.usage_tabs.setCurrentIndex(2); wait(200)
hold(S, 3.0)
move_to(S, hd.tabs.tabBar(), 14, offset=(hd.tabs.tabBar().width() // 2 - 150, 0))
click_fx(S)
hd.tabs.setCurrentIndex(3); wait(300)
b24 = [b for b in hd.findChildren(QPushButton) if b.text() == "24 ч"][0]
press(S, b24); hd._events_quick(24); wait(200)
move_to(S, hd.btn_events, 12)
press(S, hd.btn_events)
hd.lbl_events.setText("⏳ Читаю журнал через Get-WinEvent…"); snap(S, sec(0.9))
hd.show_events(_events, hd.event_filter()); wait(300)
hold(S, 2.8)
move_to(S, hd.tabs.tabBar(), 12, offset=(hd.tabs.tabBar().width() // 2 - 50, 0))
click_fx(S)
hd.tabs.setCurrentIndex(4); wait(300)
hold(S, 2.2)
S = hide_dialog(S, hd)

# ============================================================ 8. Карточка AD: пароль, блокировка, отключение, группы
cap("Карточка сотрудника: профиль, группы, учётная запись, характеристики ПК")
w.search_input.setText("сидоров"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1, app, 5000); select_login("sidorov"); wait(300)
snap(S, 2)
b_card = [b for b in w.findChildren(QPushButton) if "полную карточку" in b.text()][0]
move_to(S, b_card, 14)
press(S, b_card)
card = UserCardDialog(entries[2], w, w)
S = show_dialog(S, card)
tg._wait(lambda: card.current_ip != "Не найден", app, 3000)
hold(S, 2.6)
move_to(S, card.tabs.tabBar(), 12, offset=(0, 0))
click_fx(S)
card.tabs.setCurrentIndex(2); wait(300)
hold(S, 2.4)
card.tabs.setCurrentIndex(0); wait(200)
move_to(S, card.btn_reset, 14)
press(S, card.btn_reset)
cap("Смена пароля: генерация по политике, карточка для сотрудника")
rp = ResetPasswordDialog("sidorov", card, fio="Сидоров Пётр Ильич")
S = show_dialog(S, rp)
hold(S, 2.0)
move_to(S, rp.len_btns[16], 10)
press(S, rp.len_btns[16]); rp.generate(16); wait(150)
hold(S, 1.0)
move_to(S, rp.btn_gen, 8)
press(S, rp.btn_gen); rp.generate(); wait(150)
hold(S, 0.8)
move_to(S, rp.btn_card, 10)
press(S, rp.btn_card); wait(150)
hold(S, 1.2)
move_to(S, rp.unlock, 8)
click_fx(S)
rp.unlock.setChecked(False); wait(150)
move_to(S, rp.btn_ok, 10)
press(S, rp.btn_ok)
pwd_val, must = rp.password.text(), rp.must_change.isChecked()
S = hide_dialog(S, rp)
_unl = rp.unlock.isChecked()
ad.reset_password(conn, entries[2].entry_dn, pwd_val, must_change=must, unlock=_unl)
db.log_action("CORP\\admin", "reset_password", "sidorov", "смена при входе" if must else "без принудительной смены")
card._forget_password_age(must)
mb = MessageBox(card, "Пароль изменён", f"Новый пароль для sidorov скопирован в буфер обмена.\n{'Пользователь сменит его при следующем входе.' if must else ''}", "info")
mb.show(); wait(300); S = S + [mb]
hold(S, 2.0)
S = hide_dialog(S, mb)
move_to(S, card.tabs.tabBar(), 10, offset=(0, 0))
click_fx(S)
card.tabs.setCurrentIndex(2); wait(250)
hold(S, 1.6)
cap("Учётная запись обновляется сразу: новый пароль, снятие блокировки, отключение")
move_to(S, card.btn_unlock, 12)
press(S, card.btn_unlock)
ad.unlock_account(conn, entries[2].entry_dn); db.log_action("CORP\\admin", "unlock", "sidorov")
card._forget_lockout(); wait(250)
q = MessageBox(card, "Блокировка снята", "Блокировка с sidorov снята.\n\nЗадать пользователю новый пароль?", "question", yes_no=True); q.show(); wait(300); S = S + [q]
hold(S, 2.0)
_no = [b for b in q.findChildren(QPushButton) if b.text() == "Нет"][0]
move_to(S, _no, 10)
press(S, _no)
S = hide_dialog(S, q)
hold(S, 2.0)
card.tabs.setCurrentIndex(0); wait(200)
move_to(S, card.btn_toggle, 12)
press(S, card.btn_toggle)
q = MessageBox(card, "Подтверждение", "Отключить учётную запись sidorov?", "question", yes_no=True); q.show(); wait(300); S = S + [q]
hold(S, 1.2)
yes_b = [b for b in q.findChildren(QPushButton) if b.text() == "Да"][0]
press(S, yes_b)
S = hide_dialog(S, q)
new_uac = ad.set_account_disabled(conn, entries[2].entry_dn, card.original_uac, True)
db.log_action("CORP\\admin", "disable_user", "sidorov")
entries[2]._a["userAccountControl"] = tg.FakeAttr([new_uac])
card.original_uac = new_uac
card.btn_toggle.setText("✅ Включить учётную запись"); card.btn_toggle.setObjectName("btnSuccess")
card.btn_toggle.style().unpolish(card.btn_toggle); card.btn_toggle.style().polish(card.btn_toggle)
card._entry_changed = True; card.refresh_state(); wait(250)
hold(S, 2.0)
press(S, card.btn_toggle)
q = MessageBox(card, "Подтверждение", "Включить учётную запись sidorov?", "question", yes_no=True); q.show(); wait(300); S = S + [q]
hold(S, 0.8)
press(S, [b for b in q.findChildren(QPushButton) if b.text() == "Да"][0])
S = hide_dialog(S, q)
new_uac = ad.set_account_disabled(conn, entries[2].entry_dn, card.original_uac, False)
db.log_action("CORP\\admin", "enable_user", "sidorov")
entries[2]._a["userAccountControl"] = tg.FakeAttr([new_uac]); card.original_uac = new_uac
card.btn_toggle.setText("⛔ Отключить учётную запись"); card.btn_toggle.setObjectName("btnDanger")
card.btn_toggle.style().unpolish(card.btn_toggle); card.btn_toggle.style().polish(card.btn_toggle)
card.refresh_state(); wait(250)
hold(S, 1.6)
move_to(S, card.tabs.tabBar(), 12, offset=(-card.tabs.tabBar().width() // 2 + 150, 0))
click_fx(S)
card.tabs.setCurrentIndex(1); tg._wait(lambda: bool(card.all_groups), app, 3000); wait(300)
hold(S, 2.0)
move_to(S, card.group_filter, 12)
type_text(S, card.group_filter, "vpn", 0.1)
card._filter_groups("vpn"); wait(200)
card.groups_all.setCurrentRow(0); wait(200)
hold(S, 1.0)
add_b = [b for b in card.findChildren(QPushButton) if b.text() == "Добавить в выбранную"][0]
press(S, add_b)
cn = card.groups_all.currentItem().text()
conn.modify(card.all_groups[cn], {"member": [(ad.MODIFY_ADD, [card.dn])]})
card.groups_list.addItem(cn); card.group_dns[cn] = card.all_groups[cn]; card._filter_groups("vpn"); card._update_groups_title()
db.log_action("CORP\\admin", "group_add", "sidorov", cn); wait(300)
hold(S, 2.0)
# участники группы — двойной клик по группе слева
cap("Группы: состав группы по двойному клику, добавление и удаление")
card.groups_list.setCurrentRow(0); wait(150)
move_to(S, card.groups_list, 10, offset=(0, -card.groups_list.height() // 2 + 14))
click_fx(S); wait(60); click_fx(S)
_gn = card.groups_list.currentItem().text()
gm = GroupMembersDialog(card.group_dns.get(_gn, ""), _gn, w, card)
gm.show(); wait(300)
gm.table.setRowCount(0)
for r, row in enumerate((("ivanov", "Иванов Иван Петрович", "ivanov@example.local"), ("smirnov", "Смирнов Алексей", "smirnov@example.local"),
                         ("sidorov", "Сидоров Пётр Ильич", "sidorov@example.local"), ("petrova", "Петрова Анна Сергеевна", "petrova@example.local"))):
    gm.table.insertRow(r)
    for c, v in enumerate(row):
        gm.table.setItem(r, c, QTableWidgetItem(v))
gm.status.setText("Участников: 4"); wait(200)
S = S + [gm]; snap(S, 3)
hold(S, 2.4)
S = hide_dialog(S, gm)
move_to(S, card.tabs.tabBar(), 12, offset=(card.tabs.tabBar().width() // 2 - 80, 0))
click_fx(S)
card.tabs.setCurrentIndex(3); wait(300)
hold(S, 2.4)
S = hide_dialog(S, card)

# ============================================================ 9. Группы как у…, заметки, история
cap("«Группы как у…»: выровнять членство по эталонному сотруднику; заметки, история")
move_to(S, w.btn_compare, 12)
press(S, w.btn_compare)
gc = GroupCompareDialog(entries[2], w, w)
S = show_dialog(S, gc)
hold(S, 1.4)
move_to(S, gc.ref, 10)
type_text(S, gc.ref, "smirnov", 0.1)
gc.load_ref(); tg._wait(lambda: gc.missing.count() >= 1, app, 3000); wait(300)
hold(S, 2.8)
gc.missing.selectAll(); wait(200)
move_to(S, gc.btn_add, 12)
press(S, gc.btn_add)
cns = [gc.missing.item(i).text() for i in range(gc.missing.count())]
q = MessageBox(gc, "Подтверждение", f"Добавить в {len(cns)} групп(ы)?", "question", yes_no=True); q.show(); wait(300); S = S + [q]
hold(S, 0.8)
press(S, [b for b in q.findChildren(QPushButton) if b.text() == "Да"][0])
S = hide_dialog(S, q)
for cn in cns:
    conn.modify(gc.ref_groups[cn], {"member": [(ad.MODIFY_ADD, [entries[2].entry_dn])]})
    gc.target_groups[cn] = gc.ref_groups[cn]
    db.log_action("CORP\\admin", "groups_sync", "sidorov", f"+{cn}")
gc._fill(); gc.status.setText(f"✅ Применено: {len(cns)}"); wait(300)
hold(S, 2.0)
S = hide_dialog(S, gc)
move_to(S, w.btn_notes, 10)
press(S, w.btn_notes)
nd = NotesDialog("sidorov", "user", w, w, title="Сидоров Пётр Ильич"); S = show_dialog(S, nd)
hold(S, 2.2)
S = hide_dialog(S, nd)
move_to(S, w.btn_history, 8)
press(S, w.btn_history)
hist = HistoryDialog("sidorov", "WS-133", w); S = show_dialog(S, hist)
hold(S, 2.4)
S = hide_dialog(S, hist)

# ============================================================ 10. Массовые операции, массовый пинг
cap("Массовые операции над выделенными и массовый пинг с пробуждением по WoL")
w.search_input.setText("ИТ-отдел"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 5, app, 5000)
w.table.selectAll(); wait(300)
_cursor[:] = [300, 300]
hold(S, 2.0)
bd = BulkOperationsDialog(w.selected_users(), w, w)
tg._wait(lambda: bd.groups.count() >= 1, app, 3000)
bd.op.setCurrentIndex(bd.op.findData("group_add")); bd.groups.setCurrentRow(0)
S = show_dialog(S, bd)
hold(S, 2.6)
S = hide_dialog(S, bd)
cap("Массовый пинг: ПК без записи в DNS проверяются по адресу из инвентаря — статус честный")
_orig_net = netutils.get_computer_network_info
_no_dns = {c for i, c in enumerate(sorted(w.selected_computers())) if i % 3 == 0}     # у трети ПК «DNS не знает имя»
netutils.get_computer_network_info = lambda n, **kw: ("Не найден", False) if n in _no_dns else _orig_net(n, **kw)
_orig_alive = netutils.is_host_alive
netutils.is_host_alive = lambda ip, timeout=1.0: any(ips.get(c) == ip and online.get(c) for c in _no_dns) or _orig_alive(ip, timeout)
mp = MassPingDialog(w.selected_computers(), w, w); mp.show()
tg._wait(lambda: "Опрос" not in mp.status.text(), app, 5000); wait(200)
S = S + [mp]; snap(S, 3)
hold(S, 2.6)
mp.table.selectAll(); wait(250)
hold(S, 1.8)
mp.table.clearSelection(); wait(200)
hold(S, 1.2)
S = hide_dialog(S, mp)
netutils.get_computer_network_info, netutils.is_host_alive = _orig_net, _orig_alive

# ============================================================ 11. ПО и сравнение ПК
cap("Установленное ПО: живой опрос ПК, поиск по парку; сравнение двух ПК")
w.search_input.setText("иванов"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1, app, 5000); select_login("ivanov"); wait(300)
snap(S, 2)
b_soft = w.action_buttons["software"]
move_to(S, b_soft, 12)
press(S, b_soft)
sd = SoftwareDialog("WS-101", w, w); S = show_dialog(S, sd)
hold(S, 2.4)
move_to(S, sd.filter, 10)
type_text(S, sd.filter, "1С", 0.12); sd._apply_filter(); wait(200)
hold(S, 1.4)
sd.filter.clear(); sd._apply_filter(); wait(150)
from adk.widgets import fit_columns as _fit  # noqa: E402
_hot = [("KB5043076", "Накопительное обновление Windows 11 (2024-09)", "2026-09-03"), ("KB5042099", "Обновление .NET Framework", "2026-08-20"),
        ("KB5041592", "Накопительное обновление Windows 11 (2024-08)", "2026-08-15"), ("KB5039895", "Обновление безопасности Defender", "2026-07-30")]
sd.hot.setRowCount(0)
for row in _hot:
    r = sd.hot.rowCount(); sd.hot.insertRow(r)
    for c, v in enumerate(row):
        sd.hot.setItem(r, c, QTableWidgetItem(v))
_fit(sd.hot)
move_to(S, sd.tabs.tabBar(), 12, offset=(0, 0))
click_fx(S)
sd.tabs.setCurrentIndex(1); wait(250)
hold(S, 2.2)
move_to(S, sd.tabs.tabBar(), 10, offset=(sd.tabs.tabBar().width() // 2 - 70, 0))
click_fx(S)
sd.tabs.setCurrentIndex(2); wait(200)
move_to(S, sd.q, 10)
type_text(S, sd.q, "1С", 0.12); sd.search_fleet(); wait(200)
hold(S, 2.4)
S = hide_dialog(S, sd)
software.cache_software("WS-133", [{"name": "Google Chrome", "version": "128.0", "publisher": "Google", "installed": "2026-01-15"},
                                   {"name": "Kaspersky Endpoint Security", "version": "12.6", "publisher": "Kaspersky", "installed": "2025-11-02"}])
_live_soft = {"software": [{"name": n, "version": "", "publisher": "", "installed": ""} for n in
              ("1С:Предприятие 8.3", "Google Chrome", "Kaspersky Endpoint Security", "Microsoft Office LTSC 2021", "7-Zip 24.08")], "updates": []}
software.get_software = lambda host, timeout=60: _live_soft if host == "WS-101" else {"error": "узел недоступен"}
cp = ComparePCDialog("WS-101", "WS-133", w, w); cp.show()
tg._wait(lambda: cp._data is not None, app, 3000); wait(200)
S = S + [cp]; snap(S, 3)
hold(S, 3.0)
move_to(S, cp.only_diff, 10)
press(S, cp.only_diff); cp.only_diff.setChecked(False); wait(250)
hold(S, 1.8)
press(S, cp.only_diff); cp.only_diff.setChecked(True); wait(250)
move_to(S, cp.tabs.tabBar(), 12, offset=(0, 0))
click_fx(S)
cp.tabs.setCurrentIndex(1); wait(250)
hold(S, 2.8)
move_to(S, cp.tabs.tabBar(), 10, offset=(cp.tabs.tabBar().width() // 2 - 60, 0))
click_fx(S)
cp.tabs.setCurrentIndex(2); wait(250)
hold(S, 1.8)
S = hide_dialog(S, cp)

# ============================================================ 12. Excel-опись
cap("Excel-опись ПК по организации: выбор столбцов, предпросмотр, сохранение")
w.search_input.clear(); w.on_text_changed(""); wait(300)
snap(S, 2)
b_inv = [b for b in w.findChildren(QPushButton) if "Excel-опись" in b.text()][0]
move_to(S, b_inv, 14)
press(S, b_inv)
inv = InventoryDialog(w, w)
S = show_dialog(S, inv)
tg._wait(lambda: inv.companies, app, 3000); wait(200)
hold(S, 1.8)
move_to(S, inv.list, 12, offset=(0, -inv.list.height() // 2 + 14))
click_fx(S)
inv.list.setCurrentRow(0); wait(200)
move_to(S, inv.btn_preview, 12)
press(S, inv.btn_preview)
inv.company = inv.selected_company(); inv.lbl_title.setText(f"<b>2. Что попадёт в файл</b> — {inv.company}")
inv.status.setText("⏳ Собираю данные…"); snap(S, sec(0.9))
_INV_ROWS = build_inventory_rows([e for e in entries if ad.get_ad_value(e, "company") == inv.company])
inv.show_rows(_INV_ROWS); wait(300)
hold(S, 2.8)
move_to(S, inv.checks["login"], 12)
press(S, inv.checks["login"]); inv.checks["login"].setChecked(True); wait(200)
move_to(S, inv.checks["online"], 8)
press(S, inv.checks["online"]); inv.checks["online"].setChecked(True); wait(200)
hold(S, 2.0)
move_to(S, inv.btn_dir, 10)
inv.out_dir = tempfile.mkdtemp(); inv.btn_dir.setText("📁 Описи"); wait(150)
move_to(S, inv.btn_go, 10)
press(S, inv.btn_go)
inv.status.setText("⏳ Запись Excel…"); snap(S, sec(0.8))
_xlsx = os.path.join(inv.out_dir, "Опись_ПК_ООО_Пример_2026-09-09_11-42-10.xlsx")
write_inventory_xlsx(_xlsx, inv.company, _INV_ROWS, inv.columns())
inv.status.setText(f"✅ Сохранено: {_xlsx}"); wait(200)
mb = MessageBox(inv, "Опись", f"Опись сохранена:\n{_xlsx}", "info"); mb.show(); wait(300); S = S + [mb]
hold(S, 2.2)
S = hide_dialog(S, mb)
S = hide_dialog(S, inv)

# ============================================================ 13. Свободный IP
cap("Свободный IP: карта подсети, сверка с инвентарём, ping, DNS и DHCP")
b_ip = [b for b in w.findChildren(QPushButton) if "Свободный IP" in b.text()][0]
move_to(S, b_ip, 14)
press(S, b_ip)
fd = FreeIPDialog(w)
S = show_dialog(S, fd)
hold(S, 1.8)
move_to(S, fd.prefix, 10)
fd.prefix.setText("10.0.12"); snap(S, 2)
_ox, _oy, _c = fd.map._cell()
move_to(S, fd.map, 12, offset=(int(_ox + 28 * _c + _c / 2) - fd.map.width() // 2, int(_oy + 1 * _c + _c / 2) - fd.map.height() // 2))
click_fx(S)
fd.start.setValue(60); wait(200)
hold(S, 1.2)
move_to(S, fd.btn_start, 12)
press(S, fd.btn_start)
FreeIPWorker._dhcp_cache.clear()
fw = FreeIPWorker("10.0.12", 60)
fd.lbl_ip.setText("…"); fd.status.setText("📡 Читаю DHCP: dhcp01…"); snap(S, sec(1.0))
fw._load_dhcp()
fd.status.setText("⚡ Проверка 10.0.12.60–89…")
found = None
for h in range(60, 90):
    r = fw._reason(f"10.0.12.{h}")
    fd.map.mark(h, r); wait(60)
    snap(S, sec(0.14))
    if r == "free" and found is None:
        found = f"10.0.12.{h}"
        break
fd.on_dhcp(fw.verdict(found)); fd.on_done(found); wait(250)
hold(S, 3.0)
move_to(S, fd.lbl_ip, 12)
hold(S, 1.6)
move_to(S, fd.btn_next, 10)
press(S, fd.btn_next)
fd.start.setValue(int(found.rsplit(".", 1)[1]) + 1)
found2 = None
for h in range(int(found.rsplit(".", 1)[1]) + 1, 100):
    r = fw._reason(f"10.0.12.{h}")
    fd.map.mark(h, r); wait(60)
    snap(S, sec(0.14))
    if r == "free":
        found2 = f"10.0.12.{h}"
        break
fd.on_dhcp(fw.verdict(found2)); fd.on_done(found2); wait(250)
hold(S, 2.4)
S = hide_dialog(S, fd)

# ============================================================ 14. Новый пользователь, Внимание, журнал
cap("Новый пользователь по шаблону; список «Требуют внимания»; журнал действий")
b_new = [b for b in w.findChildren(QPushButton) if "Новый пользователь" in b.text()][0]
move_to(S, b_new, 12)
press(S, b_new)
rd = RegisterUserDialog(w, w)
S = show_dialog(S, rd)
hold(S, 1.6)
move_to(S, rd.surname, 12)
type_text(S, rd.surname, "Николаева", 0.08)
move_to(S, rd.name, 8)
type_text(S, rd.name, "Ольга", 0.08)
gen_btn = [b for b in rd.findChildren(QPushButton) if "Сгенерировать" in b.text()][0]
press(S, gen_btn); rd.generate(); wait(200)
hold(S, 1.6)
move_to(S, rd.cb_template, 12)
if rd.cb_template.count() > 1:
    rd.cb_template.setCurrentIndex(1); wait(300)
hold(S, 2.2)
S = hide_dialog(S, rd)
b_att = [b for b in w.findChildren(QPushButton) if b.objectName() == "historyBtn" and b.text() == "Открыть" and b.parent() is w.attention_card]
if b_att:
    move_to(S, b_att[0], 12)
    press(S, b_att[0])
atd = AttentionDialog(w, w, items=items)
S = show_dialog(S, atd)
hold(S, 2.6)
if atd.table.rowCount() > 1:
    atd.table.selectRow(1); wait(200)
    move_to(S, atd.table, 10, offset=(-atd.table.width() // 2 + 120, -atd.table.height() // 2 + 60))
    click_fx(S)
    hold(S, 1.2)
    move_to(S, atd.btn_snooze, 10)
    press(S, atd.btn_snooze); atd.snooze(7); wait(300)
    hold(S, 1.8)
move_to(S, atd.chk_low, 10)
press(S, atd.chk_low); atd.chk_low.setChecked(False); wait(250)
hold(S, 1.4)
S = hide_dialog(S, atd)
b_log = [b for b in w.findChildren(QPushButton) if "Журнал действий" in b.text()][0]
move_to(S, b_log, 12)
press(S, b_log)
al = AuditLogDialog(w, w)
S = show_dialog(S, al)
tg._wait(lambda: al.table.rowCount() >= 3, app, 3000); wait(200)
hold(S, 2.8)
move_to(S, al.action, 10)
_ix = al.action.findData("reset_password")
if _ix > 0:
    al.action.setCurrentIndex(_ix); wait(300)
hold(S, 2.0)
al.action.setCurrentIndex(0); wait(200)
move_to(S, al.text, 10)
type_text(S, al.text, "sidorov", 0.1); wait(300)
hold(S, 1.8)
S = hide_dialog(S, al)

# ============================================================ 15. Роль «ПК»: вход, окно роли, стартовый экран, карточка только для чтения
cap("Роль «ПК»: всё про компьютеры доступно, изменения в AD скрыты")
w.search_input.clear(); w.on_text_changed(""); wait(300)
_cursor[:] = [W // 2, H // 2 + 60]
hold(S, 1.6)
access.set_rights(pc=True, ad=False, reason="нет в группах — AD: IT-Admins"); w.apply_access(); wait(300)
w._welcome_shown = False
w.show_role_welcome(); wait(400)
role_pc = w.role_welcome
S = S + [role_pc]; snap(S, 3)
hold(S, 3.2)
move_to(S, role_pc.btn_ok, 12)
press(S, role_pc.btn_ok)
role_pc.close(); wait(250); S = [w]; snap(S, 3)
hold(S, 2.8)
move_to(S, w.lbl_role_status, 14)
hold(S, 1.0)
click_fx(S)
ri = RoleInfoDialog(w.admin_name, w)
S = show_dialog(S, ri)
hold(S, 2.6)
S = hide_dialog(S, ri)
move_to(S, w.search_input, 12)
type_text(S, w.search_input, "сидоров", 0.09)
w.start_search(); select_login("sidorov"); wait(300)
hold(S, 2.4)
b_card2 = [b for b in w.findChildren(QPushButton) if "полную карточку" in b.text()][0]
move_to(S, b_card2, 14)
press(S, b_card2)
card_pc = UserCardDialog(entries[2], w, w)
S = show_dialog(S, card_pc)
hold(S, 2.8)
move_to(S, card_pc.tabs.tabBar(), 12, offset=(-card_pc.tabs.tabBar().width() // 2 + 150, 0))
click_fx(S)
card_pc.tabs.setCurrentIndex(1); tg._wait(lambda: bool(card_pc.all_groups), app, 3000); wait(300)
hold(S, 2.4)
move_to(S, card_pc.tabs.tabBar(), 12, offset=(card_pc.tabs.tabBar().width() // 2 - 80, 0))
click_fx(S)
card_pc.tabs.setCurrentIndex(3); wait(300)
hold(S, 2.0)
S = hide_dialog(S, card_pc)
access.set_rights(pc=True, ad=True); w.apply_access(); wait(200)
w.search_input.clear(); w.on_text_changed(""); wait(300)
hold(S, 1.4)

# ============================================================ 16. Оформление: темы, свой цвет
cap("Оформление: десять тем, в том числе градиентные «Сумерки» и «Рассвет»; свой акцент, шрифт")
b_design = [b for b in w.findChildren(QPushButton) if "Дизайн" in b.text()][0]
move_to(S, b_design, 16)
press(S, b_design)
dd = DesignSettingsDialog(w, w)
S = show_dialog(S, dd)
hold(S, 1.8)
for key in ("ember", "pine", "plum", "dusk", "light", "sand", "garden", "lavender", "dawn"):
    move_to(S, dd.tiles[key], 12)
    press(S, dd.tiles[key]); dd.preset(key); wait(400)
    snap(S, 3)
    hold(S, 2.2)
move_to(S, dd.tiles["dark"], 12)
press(S, dd.tiles["dark"]); dd.preset("dark"); wait(400)
snap(S, 3)
hold(S, 1.4)
move_to(S, dd.btn_accent_custom, 12)
press(S, dd.btn_accent_custom)
cd = ColorPickerDialog("#0A84FF", dd, "Акцентный цвет")
cd.show(); wait(400)
S = S + [cd]; snap(S, 3)
hold(S, 2.0)
move_to(S, cd.hue, 10, offset=(0, -cd.hue.height() // 2 + 12))
for hh in (0.55, 0.40, 0.25, 0.12, 0.07):
    cd._from_hue(hh); wait(100)
    snap(S, sec(0.3))
move_to(S, cd.sv, 10, offset=(cd.sv.width() // 2 - 30, -cd.sv.height() // 2 + 30))
for sv in ((0.6, 0.98), (0.75, 0.98), (0.76, 0.98)):
    cd._from_sv(*sv); wait(100)
    snap(S, sec(0.3))
move_to(S, cd.hex, 8)
cd._from_hex("#fb923c"); wait(200)
hold(S, 1.2)
move_to(S, cd.btn_ok, 8)
press(S, cd.btn_ok)
cd.close(); wait(200); S = S[:-1]
dd.set_accent("#fb923c"); wait(400)
snap(S, 3)
hold(S, 1.8)
move_to(S, dd.tiles["dark"], 12)
press(S, dd.tiles["dark"]); dd.preset("dark"); wait(400)
snap(S, 3)
move_to(S, dd.tabs.tabBar(), 12, offset=(0, 0))
click_fx(S)
dd.tabs.setCurrentIndex(1); wait(300)
hold(S, 2.0)
move_to(S, dd.size, 10)
for v in (11, 12, 13, 12, 11, 10):
    dd.size.setValue(v); wait(200)
    snap(S, sec(0.4))
hold(S, 1.2)
move_to(S, dd.tabs.tabBar(), 10, offset=(dd.tabs.tabBar().width() // 2 - 60, 0))
click_fx(S)
dd.tabs.setCurrentIndex(2); wait(300)
hold(S, 2.0)
dd.tabs.setCurrentIndex(0); wait(200)
S = hide_dialog(S, dd)
hold(S, 2.4)
# финальный проход: дашборд, поиск, инспектор — общий вид перед титрами
cap("ADK — Active Directory Kit")
move_to(S, w.search_input, 14)
type_text(S, w.search_input, "смирнов", 0.1)
w.start_search(); select_login("smirnov"); wait(300)
hold(S, 2.6)
w.search_input.clear(); w.on_text_changed(""); wait(300)
_cursor[:] = [W // 2, H // 2 + 120]
hold(S, 2.4)

title_card(TITLE, hold_sec=3.0)

w.quit_app()

_encoder.stdin.close()
_encoder.wait()

_music = os.path.join(OUT_DIR, "music.wav")
subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_music.py"),
                f"{_frame_no / FPS + 0.5:.1f}", _music], check=True)

cmd = [_ffmpeg, "-y", "-loglevel", "error",
       "-i", _temp_video, "-i", _music,
       "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart",
       OUT_MP4]
subprocess.run(cmd, check=True)
shutil.rmtree(OUT_DIR, ignore_errors=True)
print(f"frames={_frame_no} duration={_frame_no / FPS:.1f}s → {OUT_MP4} ({os.path.getsize(OUT_MP4) / 1e6:.1f} MB)")
