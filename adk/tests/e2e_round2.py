"""E2E, раунд 2: действия из карточки, создание УЗ с откатом, сканер, DPAPI, CSV, утечки.

Запуск из корня проекта:
    QT_QPA_PLATFORM=offscreen python tests/e2e_round2.py

Дополняет tests/e2e_scenario.py (раунд 1). Печатает PASS/FAIL и итог.
"""
import sys, os, threading, tempfile, gc, types, base64  # noqa: E401
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')

# --- заглушка pywin32 с РАБОЧИМ DPAPI-путём (обратимое «шифрование»), чтобы проверить Windows-ветку
_w32 = types.ModuleType("win32crypt")
_w32.CryptProtectData = lambda data, *a, **k: b"DPAPI:" + base64.b64encode(data)
_w32.CryptUnprotectData = lambda blob, *a, **k: (None, base64.b64decode(blob[6:]))
sys.modules["win32crypt"] = _w32
for n in ("pythoncom", "win32com", "win32com.client"):
    sys.modules.setdefault(n, types.ModuleType(n))

_home = tempfile.mkdtemp(prefix="admgr_e2e2_")
os.environ["HOME"] = _home; os.environ["USERPROFILE"] = _home
from adk import config, db, ad, netutils, credentials  # noqa: E402
config.settings.db_path = os.path.join(_home, 'e2e2.db')
db.init_db()
import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from adk.widgets import apply_theme, MessageBox  # noqa: E402
app = QApplication([]); apply_theme(config.settings.design)
MessageBox._show = classmethod(lambda cls, *a, **k: cls.YES)
R = []
def check(name, cond, note=""):
    R.append((name, bool(cond), note)); print(("PASS " if cond else "FAIL ") + name + (f"  — {note}" if note else ""))


class RichConn(tg.FakeConn):
    """FakeConn + add/delete/extend + учёт вызовов, чтобы проверить ПОРЯДОК операций."""
    def __init__(self, entries, fail_password=False):
        super().__init__(entries)
        self.calls = []; self.fail_password = fail_password
        me = self
        class _MS:
            def modify_password(self, dn, pwd):
                me.calls.append(("pwd", dn))
                if me.fail_password:
                    raise RuntimeError("constraint violation: password policy")
                return True
        class _Std:
            def who_am_i(self): return "u:CORP\\admin"
        self.extend = types.SimpleNamespace(microsoft=_MS(), standard=_Std())
    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        if "distinguishedName=" in flt:      # проверка уникальности при создании
            import re as _re
            login = _re.search(r"sAMAccountName=([^)]+)", flt).group(1)
            self.entries = [e for e in self._all if e["sAMAccountName"].value == login]; return True
        return super().search(base, flt, scope, attributes, paged_size, paged_cookie)
    def add(self, dn, attributes=None, **k): self.calls.append(("add", dn, dict(attributes or {}))); return True
    def delete(self, dn): self.calls.append(("delete", dn)); return True
    def modify(self, dn, changes): self.calls.append(("modify", dn, changes)); return super().modify(dn, changes)


print("=== F. Утилиты ad.py ===")
check("qualify_user: без домена", ad.qualify_user("admin") == f"{config.settings.domain_netbios}\\admin", ad.qualify_user("admin"))
check("qualify_user: с доменом не трогаем", ad.qualify_user("CORP\\admin") == "CORP\\admin")
check("qualify_user: UPN → DOMAIN\\user", "@" not in ad.qualify_user("admin@corp.example"), ad.qualify_user("admin@corp.example"))
check("dn_to_cn: простой", ad.dn_to_cn("CN=IT Admins,OU=g,DC=x") == "IT Admins")
check("dn_to_cn: экранированная запятая", ad.dn_to_cn("CN=Smith\\, John,OU=g") == "Smith, John", ad.dn_to_cn("CN=Smith\\, John,OU=g"))
check("transliterate: щ/ё/й", ad.transliterate("Щёлков Йод") == "shhyolkov jod", ad.transliterate("Щёлков Йод"))
check("sanitize_sam: ≤20 и мусор", ad.sanitize_sam_account_name("Иванов-Петров Сергей!") == "ivanov-petrovsergej", ad.sanitize_sam_account_name("Иванов-Петров Сергей!"))
try: ad.sanitize_sam_account_name("!!!"); ok = False
except ValueError: ok = True
check("sanitize_sam: пустой → ValueError", ok)
pw_ok = all(len(p) == 12 and any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p)
            and any(c in "!_@" for c in p) for p in (ad.generate_secure_password() for _ in range(300)))
check("generate_secure_password: политика на 300 образцах", pw_ok)
check("describe_ldap_error: 52e", "пароль" in ad.describe_ldap_error(Exception("80090308: LdapErr: DSID-0C09042A, comment: AcceptSecurityContext error, data 52e")))
check("describe_ldap_error: 775 lockout", "заблокирована" in ad.describe_ldap_error(Exception("... data 775, v3839")))
check("describe_ldap_error: socket", config.settings.dc_host in ad.describe_ldap_error(Exception("socket connection error while opening")))
check("describe_ldap_error: cert", "tls_validate" in ad.describe_ldap_error(Exception("[SSL: CERTIFICATE_VERIFY_FAILED]")))
check("escape_filter_chars", ad.escape_filter_chars("a*(b)\\c") == "a\\2a\\28b\\29\\5cc", ad.escape_filter_chars("a*(b)\\c"))

print("\n=== G. Создание пользователя (порядок операций и откат) ===")
config.settings.use_ssl = True
rc = RichConn(tg.ENTRIES)
dn = ad.create_user(rc, login="Сидоров С", password="Pa55w0rd!_x", surname="Сидоров", name="Семён", patronymic="Петрович", extra={"title": "Инженер", "company": " "})
ops = [c[0] for c in rc.calls]
check("create_user: add → password → modify(512)", ops == ["add", "pwd", "modify"], str(ops))
attrs = rc.calls[0][2]
check("create_user: создаётся ОТКЛЮЧЁННОЙ (514)", attrs["userAccountControl"] == 514, str(attrs["userAccountControl"]))
check("create_user: затем включается (512)", rc.calls[2][2]["userAccountControl"][0][1] == [512])
check("create_user: UPN и sAMAccountName", attrs["sAMAccountName"] == "sidorovs" and attrs["userPrincipalName"] == f"sidorovs@{config.settings.upn_suffix}", f"{attrs['sAMAccountName']} / {attrs['userPrincipalName']}")
check("create_user: displayName с инициалами", attrs["displayName"] == "Сидоров С.П." and attrs["middleName"] == "Петрович", attrs["displayName"])
check("create_user: пустые extra отбрасываются", "company" not in attrs and attrs["title"] == "Инженер")
check("create_user: DN в users_ou", dn.endswith(config.settings.users_ou) and dn.startswith("CN=Сидоров С.П."), dn)
rc2 = RichConn(tg.ENTRIES, fail_password=True)
try: ad.create_user(rc2, login="x y", password="p", surname="Икс", name="Игрек"); ok = False
except RuntimeError: ok = True
check("create_user: ошибка пароля → исключение наружу", ok)
check("create_user: ОТКАТ — объект удалён, не осталось «полусозданной» УЗ", [c[0] for c in rc2.calls] == ["add", "pwd", "delete"], str([c[0] for c in rc2.calls]))
rc3 = RichConn(tg.ENTRIES)
try: ad.create_user(rc3, login="ivanov", password="p", surname="Иванов", name="Иван"); ok = False
except ValueError as e: ok = "уже существует" in str(e)
check("create_user: дубликат логина отклонён ДО add", ok and not rc3.calls)
check("create_user: RDN экранируется", "\\," in ad.create_user(RichConn(tg.ENTRIES), login="q", password="p", surname="Ли, Чан", name="У"))

print("\n=== H. Карточка: реальные действия (argv, LDAP-операции, журнал) ===")
import subprocess  # noqa: E402
from adk.main_window import ADApp  # noqa: E402
from adk.dialogs import UserCardDialog  # noqa: E402
conn = RichConn(tg.ENTRIES); ad.make_connection = lambda *a, **k: conn
netutils.get_computer_network_info = lambda n, **kw: ("10.0.0.9", n == "WS-101")
ADApp.start_scan = lambda s: None
w = ADApp("CORP\\admin", "x"); w.show()
db.save_computer_for_login("ivanov", "WS-101")
popen_calls = []
class FakeProc:
    pid = 4242
    def poll(self): return None
    def kill(self): pass
subprocess.Popen = lambda argv, *a, **k: popen_calls.append(argv) or FakeProc()
d = UserCardDialog(tg.ENTRIES[0], w); d.show(); tg._wait(lambda: d.current_ip != "Не найден", app, 3000)
check("карточка: ПК и IP определены в фоне", d.current_comp == "WS-101" and d.current_ip == "10.0.0.9", f"{d.current_comp} {d.current_ip}")
check("карточка 3.2.5: действий с ПК в ней больше нет (они в инспекторе)", not hasattr(d, "remote_action") and not hasattr(d, "printers") and d.tabs.count() == 4, str(d.tabs.count()))
check("карточка: шапка с бейджем состояния и вкладка «Учётная запись»", d.badge_state.text() == "Активна" and "Пароль" in d.account_vals and d.tabs.tabText(2).endswith("Учётная запись"))
# удалённые действия — из инспектора главного окна (строка ivanov / WS-101)
w.search_input.setText("иванов"); w.start_search(); tg._wait(lambda: w.table.rowCount() > 0, app, 3000); w.select_row(0)
w.remote_action("restart")
check("shutdown: /m и UNC отдельными аргументами, цель — IP", popen_calls[-1] == ["shutdown", "/r", "/m", "\\\\10.0.0.9", "/t", "0", "/f"], str(popen_calls[-1]))
w.remote_action("compmgmt")
check("mmc: /computer=\\\\host", popen_calls[-1] == ["mmc.exe", "compmgmt.msc", "/computer=\\\\10.0.0.9"], str(popen_calls[-1]))
w.remote_action("disk_c")
check("explorer: \\\\host\\c$", popen_calls[-1] == ["explorer.exe", "\\\\10.0.0.9\\c$"], str(popen_calls[-1]))
w.remote_action("rms")
check("RMS: -host по IP, -name по имени", popen_calls[-1][1:] == ["-create", "-name:WS-101", "-host:10.0.0.9", "-fullcontrol"], str(popen_calls[-1]))
audit = db.db_execute_with_retry("SELECT action, target FROM audit_log ORDER BY id", fetch="all")
check("журнал действий: restart/compmgmt/disk_c записаны с именем ПК", [a for a, _ in audit][-3:] == ["restart", "compmgmt", "disk_c"] and all(t == "WS-101" for _, t in audit[-3:]), str(audit[-3:]))
n = len(conn.calls); d.toggle_disabled(); tg._wait(lambda: len(conn.calls) > n, app, 3000)
last = conn.calls[-1]
check("отключение УЗ: UAC 512 → 514 одним MODIFY_REPLACE", last[0] == "modify" and last[2]["userAccountControl"][0][1] == [514], str(last[2]))
d2 = UserCardDialog(tg.ENTRIES[1], w)  # petrov: 514
n = len(conn.calls); d2.toggle_disabled(); tg._wait(lambda: len(conn.calls) > n, app, 3000)
check("включение УЗ: 514 → 512", conn.calls[-1][2]["userAccountControl"][0][1] == [512], str(conn.calls[-1][2]))
from adk.dialogs import ResetPasswordDialog  # noqa: E402
_pw_opened = []
ResetPasswordDialog.exec = lambda self: _pw_opened.append(self) or 0     # «задать пароль?» → да → окно, но «Отмена»
n = len(conn.calls); d2.unlock(); tg._wait(lambda: len(conn.calls) > n, app, 3000); tg._wait(lambda: bool(_pw_opened), app, 3000)
check("после снятия блокировки предложено задать пароль; переключатели окна зафиксированы",
      len(_pw_opened) == 1 and not _pw_opened[0].must_change.isChecked() and _pw_opened[0].unlock.isChecked()
      and not _pw_opened[0].must_change.isEnabled(), str(len(_pw_opened)))
check("unlock: только lockoutTime=0, UAC не трогается", conn.calls[-1][2] == {"lockoutTime": [(ad.MODIFY_REPLACE, [0])]}, str(conn.calls[-1][2]))
d2.close()
d.all_groups = {"IT": "CN=IT,OU=g", "VPN": "CN=VPN,OU=g", "Admins": "CN=Admins,OU=g"}; d._filter_groups("")
check("группы: уже состоящие не предлагаются к добавлению", [d.groups_all.item(i).text() for i in range(d.groups_all.count())] == ["Admins"])
d.groups_all.setCurrentRow(0); n = len(conn.calls); d.add_to_group(); tg._wait(lambda: "Admins" in d.group_dns, app, 3000)
check("add_to_group: MODIFY_ADD member на DN группы", conn.calls[-1][1] == "CN=Admins,OU=g" and conn.calls[-1][2] == {"member": [(ad.MODIFY_ADD, [d.dn])]}, str(conn.calls[-1][1:]))
check("add_to_group: список групп обновлён", "Admins" in d.group_dns and d.groups_list.count() == 3)
d.groups_list.setCurrentRow(d.groups_list.count() - 1); n = len(conn.calls); d.remove_from_group(); tg._wait(lambda: d.groups_list.count() == 2, app, 3000)
check("remove_from_group: MODIFY_DELETE", conn.calls[-1][2] == {"member": [(ad.MODIFY_DELETE, [d.dn])]} and d.groups_list.count() == 2)
d.inputs["mail"].setText(""); d.inputs["telephoneNumber"].setText("2554567"); d.inputs["description"].setText("VIP"); n = len(conn.calls); d.save_changes(); tg._wait(lambda: len(conn.calls) > n, app, 3000)
check("save_changes: только изменённое (description), пустое/равное не шлём", conn.calls[-1][0] == "modify" and list(conn.calls[-1][2]) == ["description"], str(conn.calls[-1][2]))
d.pc_input.setText("ws-777.corp.example"); d.save_pc_binding()
check("привязка ПК: имя нормализуется (FQDN → WS-777)", db.get_computer_by_login("ivanov") == "WS-777")
d.close()
check("карточка без ПК: вкладка характеристик говорит «ПК не привязан»", "ПК не привязан" in UserCardDialog(tg.ENTRIES[1], w).lbl_ip.text())

print("\n=== I. Главное окно: остальное ===")
w.search_input.setText("иванов2"); w.start_search(); tg._wait(lambda: False, app, 300)
w.search_input.setText("иванов"); w.start_search(); tg._wait(lambda: w.table.rowCount() > 0 and w.table.item(0, 3).text() == "WS-777", app, 3000)
w.select_row(0); w.remote_action("restart")
check("главное окно: действие из строки → argv и журнал (ПК после перепривязки — WS-777)", popen_calls[-1][0] == "shutdown" and db.db_execute_with_retry("SELECT target FROM audit_log ORDER BY id DESC LIMIT 1", fetch="one")[0] == "WS-777", str(db.db_execute_with_retry("SELECT target FROM audit_log ORDER BY id DESC LIMIT 1", fetch="one")))
w.search_input.clear(); tg._wait(lambda: w.history.count() > 1, app, 2000)
btn = next((w.history.itemAt(i).widget() for i in range(w.history.count()) if w.history.itemAt(i).widget()), None)
btn.click(); tg._wait(lambda: w.stack.currentIndex() == 1, app, 3000)
check("история: клик по чипу запускает поиск", w.stack.currentIndex() == 1 and w.table.rowCount() > 0, btn.text())
big = [tg.FakeEntry(f"CN=U{i},OU=x", sAMAccountName=f"user{i}", displayName=f"Пользователь {i}", sn="Тест", givenName="Т", userAccountControl=512) for i in range(80)]
conn2 = RichConn(big); ad.make_connection = lambda *a, **k: conn2
w.search_input.setText("пользователь"); w.start_search(); tg._wait(lambda: "Найдено" in w.lbl_status.text(), app, 3000)
check("лимит выдачи: пользователю СООБЩАЕТСЯ об усечении", "+" in w.lbl_status.text() or "уточните" in w.lbl_status.text().lower(), w.lbl_status.text())
ad.make_connection = lambda *a, **k: conn
w.results = [{"login": "x", "fio": "x", "is_disabled": False, "comp": "—", "is_online": False, "entry": None}]; w.fill_table(w.results); w.select_row(0)
w.open_card(); check("open_card без Entry: сообщение, а не падение", True)
before = threading.active_count(); gc.collect()
for _ in range(15):
    dd = UserCardDialog(tg.ENTRIES[0], w); dd.show(); dd.close(); dd.deleteLater()
tg._wait(lambda: False, app, 800); gc.collect()
check("15× открыть/закрыть карточку: потоки не копятся", threading.active_count() <= before + 1, f"{before} → {threading.active_count()}")

print("\n=== J. Сканер парка ===")
from adk.workers import PCScannerWorker  # noqa: E402
comps = [tg.FakeEntry(f"CN={n}", name=n) for n in ("WS-101", "ws-102", "PC-BUH1", "SRV-DC1", "WS-103-OLD", "WS-1$")]
config.settings.host_pattern = r"^(WS-\d+|PC-.*)$"; config.settings.host_exclude = "OLD"
names = PCScannerWorker.workstation_names(comps)
check("фильтр рабочих станций: pattern + exclude + регистр + $", names == ["WS-101", "WS-102", "PC-BUH1", "WS-1"], str(names))
sc = PCScannerWorker(lambda: RichConn(comps)); got = []; sc.finished_scan.connect(got.append); sc.progress.connect(got.append); sc.run()
check("сканер вне Windows: честное сообщение и finished_scan(n)", any(isinstance(g, int) and g == 4 for g in got) and any("Windows" in str(g) for g in got), str(got[-2:]))
db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.0.9", "Status": "ACTIVE", "User": "ivanov", "LastLogon": "01.09.2026"},
                           {"Hostname": "WS-102", "ActualIp": "10.0.0.10", "Status": "OFFLINE", "User": "", "LastLogon": "Неизвестно"}], "2026-09-04 10:00:00")
db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.0.9", "Status": "OFFLINE", "User": "", "LastLogon": "Неизвестно"}], "2026-09-04 11:00:00")
row = db.db_execute_with_retry("SELECT current_user, last_logon, last_seen_online, is_online FROM pc_inventory WHERE computer_name='WS-101'", fetch="one")
check("upsert: пустой User/LastLogon не затирают прежние", row[0] == "ivanov" and row[1] == "01.09.2026", str(row))
check("upsert: last_seen_online сохраняется после ухода в offline", row[2] == "2026-09-04 10:00:00" and row[3] == 0, str(row))
check("исчезнувший из AD ПК удаляется из инвентаря", db.db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory WHERE computer_name='WS-102'", fetch="one")[0] == 0)
db.batch_update_inventory([], "2026-09-04 12:00:00")
check("пустой результат сканера НЕ стирает инвентарь", db.db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory", fetch="one")[0] == 1)

print("\n=== K. Учётные данные: DPAPI-ветка (заглушка win32crypt) ===")
import importlib  # noqa: E402
importlib.reload(credentials)
credentials.IS_WINDOWS = True  # в песочнице Linux — включаем Windows-ветку принудительно
credentials.CRED_FILE = os.path.join(_home, "cred_dpapi.json")
ok_save = credentials.save_credentials("CORP\\admin", "S3cret!Пароль")
raw = open(credentials.CRED_FILE, encoding="utf-8").read() if os.path.exists(credentials.CRED_FILE) else ""
check("DPAPI: файл создан, пароля в открытом виде нет", ok_save and raw and "S3cret" not in raw and "Пароль" not in raw, raw[:80])
check("DPAPI: чтение обратно", credentials.load_credentials() == ("CORP\\admin", "S3cret!Пароль"))
open(credentials.CRED_FILE, "w").write("{corrupted json")
check("DPAPI: битый файл → (None, None), без исключения", credentials.load_credentials() == (None, None))
credentials.save_credentials("a", "b"); credentials.clear_credentials()
check("clear_credentials удаляет файл", not os.path.exists(credentials.CRED_FILE))

print("\n=== L. CSV характеристик и сводка ===")
csv_path = os.path.join(_home, "WS-101.csv")
open(csv_path, "w", encoding="cp1251", newline="").write(
    "Операционная система;Название;0;Windows 10 Pro 22H2\n"
    "Процессор;Название;0;Intel Core i5-10400\n"
    "Оперативная память;Размер;0;8192\nОперативная память;Частота;0;2667\n"
    "Оперативная память;Размер;1;8192\nОперативная память;Частота;1;2667\n"
    "Диск;Наименование;0;Samsung SSD 870\nДиск;Размер;0;500107862016\nДиск;Тип носителя;0;SSD\n"
    "Принтер;Название;0;HP LaserJet\n")
config.settings.invent_hardware_dir = _home
s = netutils.get_computer_specs_summary("ws-101.corp.example")
check("CSV cp1251: ОС/CPU", "Windows 10 Pro 22H2" in s and "i5-10400" in s, s.replace("\n", " | "))
check("CSV: ОЗУ суммируется по планкам (16 ГБ, 2667 МГц)", "16.0 ГБ (2667 МГц)" in s, s.split("\n")[2])
check("CSV: диск байты → ГБ (465.8) + тип + модель", "465.8 ГБ SSD (Samsung SSD 870)" in s, s.split("\n")[3])
check("CSV: принтеры", netutils.get_computer_printers("WS-101") == ["HP LaserJet (локальный)"], str(netutils.get_computer_printers("WS-101")))
check("нет CSV и specs → «не собраны», ничего не выдумывается", netutils.get_computer_specs_summary("WS-999") == "Характеристики не собраны")
open(csv_path, "w", encoding="cp1251", newline="").write("Оперативная память;Размер;0;8589934592\n")
check("CSV: ОЗУ в байтах распознаётся как 8 ГБ", "8.0 ГБ" in netutils.get_computer_specs_summary("WS-101"), netutils.get_computer_specs_summary("WS-101").split("\n")[2])
config.settings.invent_hardware_dir = ""

print("\n=== M. Разбор ping / сеть ===")
p = netutils.parse_ping_line("Ответ от 10.0.0.9: число байт=32 время=1мс TTL=128", "10.0.0.9")
check("ping ru: успех", p["success"] and p["ttl"] == "128" and p["time"] == "1мс", str(p))
p = netutils.parse_ping_line("Reply from 10.0.0.9: bytes=32 time<1ms TTL=64", "x")
check("ping en: time<1ms", p["success"] and p["time"] == "1ms", str(p))
p = netutils.parse_ping_line("Reply from 10.0.0.1: Destination host unreachable.", "10.0.0.9")
check("ping: unreachable от шлюза — НЕ успех", p["success"] is False and p["ip"] == "10.0.0.1")
p = netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.0.9")
check("ping: таймаут", p["success"] is False and p["ip"] == "10.0.0.9")
p = netutils.parse_ping_line("64 bytes from 127.0.0.1: icmp_seq=1 ttl=64 time=0.030 ms", "127.0.0.1")
check("ping linux (dev-запуск): успех", p and p["success"] and p["ttl"] == "64", str(p))
check("is_ip_query: 10.0.2 да, 2554567 нет, 255.4567 нет", netutils.is_ip_query("10.0.2") and not netutils.is_ip_query("2554567") and not netutils.is_ip_query("255.4567"))
check("is_valid_hostname: имя/IP да, FQDN и пробел нет", netutils.is_valid_hostname("WS-101") and netutils.is_valid_hostname("10.0.0.9") and not netutils.is_valid_hostname("ws.corp") and not netutils.is_valid_hostname("WS 1"))

print("\n=== N. БД: история, LIKE, дедуп ===")
for q in ("Иванов", "ИВАНОВ", "иванов", "Петров", "  "): db.save_search_query(q, "adm")
hist = db.get_recent_searches()
check("история: Cyrillic-дедуп без учёта регистра (LOWER() SQLite ASCII-only)", hist[:2] == ["Петров", "иванов"] and sum(1 for h in hist if h.lower() == "иванов") == 1, str(hist))
check("история: хранится ПОСЛЕДНИЙ вариант написания", "иванов" in hist and "Иванов" not in hist and "ИВАНОВ" not in hist, str(hist))
db.save_computer_for_login("kim", "WS-50%1"); db.save_computer_for_login("lee", "WS-501")
check("LIKE: '%' в запросе экранируется", db.logins_by_computer_or_ip("50%") == {"kim"}, str(db.logins_by_computer_or_ip("50%")))
db.batch_update_inventory([{"Hostname": "WS-9", "ActualIp": "10.0.7.77", "Status": "ACTIVE", "User": "CORP\\Zoe"}], "2026-09-04 13:00:00")
check("поиск по IP → логин нормализован (без домена, lower)", db.logins_by_computer_or_ip("10.0.7") == {"zoe"})
check("normalize_login/clean_computer_name", db.normalize_login(" CORP\\Ivanov ") == "ivanov" and db.clean_computer_name("ws-1.corp.example") == "WS-1" and db.clean_computer_name("Не найден") == "")

print("\n=== O. Тема / конфиг ===")
from adk import theme  # noqa: E402
check("contrast_text: на светлом тёмный, на тёмном светлый", theme.contrast_text("#ffffff") != theme.contrast_text("#000000"))
check("readable_accent: тёмный акцент на тёмной теме осветляется", theme.luminance(theme.readable_accent("#1e3a8a", True)) > theme.luminance("#1e3a8a"))
css = theme.build_stylesheet("solid", False, "Segoe UI", 10, "#2563eb")
check("stylesheet собирается и содержит акцент", "#2563eb" in css and len(css) > 1000)
ini = os.path.join(_home, "cfg.ini"); config.write_default_config(ini)
check("config: пример без организационных данных", os.path.exists(ini) and "example" in open(ini, encoding="utf-8").read().lower() and ".ru" not in open(ini, encoding="utf-8").read())
config.INI_FILE = ini; config.settings.save_design({**config.settings.design, "accent_color": "#123456"}); config.settings.reload()
check("save_design → reload сохраняет акцент", config.settings.design["accent_color"] == "#123456", config.settings.design["accent_color"])

print("\n=== ИТОГ ===")
passed = sum(1 for _, ok, _ in R if ok)
print(f"{passed}/{len(R)} проверок пройдено")
for name, ok, note in R:
    if not ok: print(f"  FAIL: {name} {note}")
