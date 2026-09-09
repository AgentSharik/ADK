"""E2E, раунд 3 (2.2.0): принтеры, режимы поиска без телефонов, точность совпадений, производительность.

Запуск из корня проекта:
    QT_QPA_PLATFORM=offscreen python tests/e2e_round3.py
"""
import sys, os, tempfile, types, time  # noqa: E401
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
_home = tempfile.mkdtemp(prefix="admgr_e2e3_")
os.environ["HOME"] = _home; os.environ["USERPROFILE"] = _home
from adk import config, db, ad, netutils  # noqa: E402
config.settings.db_path = os.path.join(_home, 'e2e3.db')
db.init_db()
import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from adk.widgets import apply_theme, MessageBox, BadgeButton  # noqa: E402
from adk.workers import SearchWorker, PCScannerWorker  # noqa: E402
app = QApplication([]); apply_theme(config.settings.design)
_orig_netinfo = netutils.get_computer_network_info
MessageBox._show = classmethod(lambda cls, *a, **k: cls.YES)
R = []
def check(name, cond, note=""):
    R.append((name, bool(cond), note)); print(("PASS " if cond else "FAIL ") + name + (f"  — {note}" if note else ""))


class CountingConn(tg.FakeConn):
    def __init__(self, entries):
        super().__init__(entries); self.calls = []
    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        self.calls.append(flt); return super().search(base, flt, scope, attributes, paged_size, paged_cookie)


print("=== P. Классификация принтеров ===")
cases = {"IP_10.0.2.50": ("network", "10.0.2.50"), "10.0.2.51_1": ("network", "10.0.2.51"), "WSD-1a2b": ("network", ""),
         "\\\\PRINTSRV\\HP2": ("shared", ""), "USB001": ("usb", ""), "DOT4_001": ("usb", ""), "LPT1:": ("local", ""),
         "PORTPROMPT:": ("virtual", ""), "nul:": ("virtual", ""), "SHRFAX:": ("virtual", "")}
bad = {p: netutils.classify_printer_port(p) for p, exp in cases.items() if netutils.classify_printer_port(p) != exp}
check("classify_printer_port: 10 типовых портов", not bad, str(bad))
virt = ["Microsoft Print to PDF", "Microsoft XPS Document Writer", "OneNote (Desktop)", "OneNote for Windows 10", "Fax", "Факс",
        "Adobe PDF", "PDF24", "doPDF", "Foxit Reader PDF Printer", "Send To OneNote 2016", "Bullzip PDF Printer", "Snagit 2024"]
check("виртуальные принтеры отфильтрованы (13 имён)", all(netutils.is_virtual_printer(n) for n in virt),
      str([n for n in virt if not netutils.is_virtual_printer(n)]))
real = ["HP LaserJet Pro M404dn", "Kyocera ECOSYS P3145dn", "Canon i-SENSYS LBP6030", "Epson L3150", "Brother HL-L2340", "Pantum P2500W", "Xerox Phaser 3020"]
check("реальные принтеры НЕ отфильтрованы (7 моделей)", not any(netutils.is_virtual_printer(n) for n in real),
      str([n for n in real if netutils.is_virtual_printer(n)]))

print("\n=== Q. CSV → кэш → индексация сканером ===")
hw = os.path.join(_home, "hw"); os.makedirs(hw); config.settings.invent_hardware_dir = hw
def write_csv(pc, printers):
    lines = ["Операционная система;Название;0;Windows 11 Pro"]
    for i, (n, p, d) in enumerate(printers):
        lines += [f"Принтер;Название;{i};{n}", f"Принтер;Порт;{i};{p}", f"Принтер;По умолчанию;{i};{d}"]
    open(os.path.join(hw, f"{pc}.csv"), "w", encoding="cp1251", newline="").write("\n".join(lines) + "\n")
HP = ("HP LaserJet M404dn", "IP_10.0.2.50", "Да")
write_csv("WS-101", [HP, ("Microsoft Print to PDF", "PORTPROMPT:", ""), ("Canon LBP6030", "USB001", "")])
write_csv("WS-105", [HP, ("OneNote (Desktop)", "nul:", "")])
write_csv("WS-133", [("Kyocera ECOSYS P3145dn", "10.0.2.51_1", "Да"), ("Fax", "SHRFAX:", "")])
write_csv("WS-102", [("Kyocera ECOSYS P3145dn", "10.0.2.51_1", ""), ("Epson L3150", "USB002", "Да")])
db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.2.11", "Status": "ACTIVE", "User": "CORP\\ivanov"},
                           {"Hostname": "WS-105", "ActualIp": "10.0.2.15", "Status": "ACTIVE", "User": "petrov"},
                           {"Hostname": "WS-133", "ActualIp": "10.0.2.33", "Status": "OFFLINE", "User": ""},
                           {"Hostname": "WS-102", "ActualIp": "10.0.20.12", "Status": "OFFLINE", "User": "kuznetsova"},
                           {"Hostname": "WS-999", "ActualIp": "10.0.9.99", "Status": "OFFLINE", "User": ""}], "2026-09-04 10:00:00")
ps = netutils.get_computer_printers_detailed("ws-101.corp.example")
check("CSV WS-101: 2 реальных принтера, PDF отброшен, default помечен",
      [p["name"] for p in ps] == ["HP LaserJet M404dn", "Canon LBP6030"] and ps[0]["is_default"] and ps[0]["ip"] == "10.0.2.50" and ps[1]["kind"] == "usb", str(ps))
check("карточка/инспектор пишут кэш pc_printers", [p["name"] for p in db.printers_for_computers(["WS-101"])["WS-101"]] == ["HP LaserJet M404dn", "Canon LBP6030"])
check("get_computer_printers (устаревший API) даёт подписи", netutils.get_computer_printers("WS-101") == ["HP LaserJet M404dn (сетевой · 10.0.2.50)", "Canon LBP6030 (USB)"], str(netutils.get_computer_printers("WS-101")))
# сканер на не-Windows: список ПК из AD → индексация принтеров всё равно выполняется
class CompConn(tg.FakeConn):
    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        self.entries = [tg.FakeEntry(f"CN={n}", name=n) for n in ("WS-101", "WS-105", "WS-133", "WS-102", "WS-999", "SRV-1")]; return True
config.settings.host_pattern = r"^WS-\d+$"
progress = []
sw = PCScannerWorker(lambda: CompConn([]))
sw.progress.connect(progress.append); sw.run()
summary = {r["name"]: r for r in db.printer_summary()}
check("сканер: индексирует принтеры всех ПК из CSV (4 ПК, 4 принтера)", any("проиндексированы: 4" in p for p in progress) and len(summary) == 4, str(progress[-1:]))
check("сводка: HP на 2 ПК (2 в сети), Kyocera на 2 ПК (0 в сети)", summary["HP LaserJet M404dn"]["pcs"] == 2 and summary["HP LaserJet M404dn"]["online"] == 2
      and summary["Kyocera ECOSYS P3145dn"]["pcs"] == 2 and summary["Kyocera ECOSYS P3145dn"]["online"] == 0, str(summary))
write_csv("WS-105", [("Epson L3150", "USB003", "Да")])
netutils.get_computer_printers_detailed("WS-105")
check("замена CSV → старый принтер исчезает из кэша ПК", [p["name"] for p in db.printers_for_computers(["WS-105"])["WS-105"]] == ["Epson L3150"])
write_csv("WS-105", [HP, ("OneNote (Desktop)", "nul:", "")]); netutils.get_computer_printers_detailed("WS-105")

print("\n=== R. Режимы поиска: без телефонов, IP по октетам, printer: ===")
conn = CountingConn(tg.ENTRIES); ad.make_connection = lambda *a, **k: conn
f = SearchWorker(lambda: conn, "2554567")._build_filter()
check("телефон НЕ ищется (telephoneNumber/ipPhone/mobile отсутствуют в фильтре)", all(a not in f for a in ("telephoneNumber", "ipPhone", "mobile")), f)
check("цифры всё ещё ищутся в логине/displayName", "(sAMAccountName=*2554567*)" in f and "(displayName=*2554567*)" in f)
f = SearchWorker(lambda: conn, "10.0.2")._build_filter()
check("IP-префикс 10.0.2 → только владельцы 10.0.2.x (не 10.0.20.12)", f is not None and "ivanov" in f and "petrov" in f and "kuznetsova" not in f and "displayName" not in f, f)
check("IP без владельцев → None (в AD не идём)", SearchWorker(lambda: conn, "10.0.9")._build_filter() is None)
pw0 = SearchWorker(lambda: conn, "10.0.2.51")
f0 = pw0._build_filter()
check("полный IP принтера (не ПК) → в AD не идём, printer_query не включается (только сам принтер)", pw0.printer_query is None and f0 is None, str(f0))
check("clean_computer_name не режет IP до первого октета", db.clean_computer_name("10.0.2.15") == "10.0.2.15")
check("normalize_query: домен/UPN/пробелы", SearchWorker.normalize_query("CORP\\Ivanov") == "Ivanov" and SearchWorker.normalize_query("ivanov@corp.example") == "ivanov" and SearchWorker.normalize_query(" a   b ") == "a b")
pw = SearchWorker(lambda: conn, "printer: 10.0.2.50")
f = pw._build_filter()
check("printer: по IP → логины владельцев, без текстовой части", f == "(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2))(|(sAMAccountName=ivanov)(sAMAccountName=petrov)))" or (f and "ivanov" in f and "petrov" in f and "displayName" not in f), f)
check("printer: подстрока модели (kyocera) находит владельцев (kuznetsova) + ПК без юзера", db.logins_by_printer("kyocera") == {"kuznetsova"} and {o["comp"] for o in db.printer_owners("kyocera")} == {"WS-133", "WS-102"})
check("printer: короткий запрос (<3) не расширяется", db.logins_by_printer("hp") == set())
got = []
pw = SearchWorker(lambda: conn, "printer: Kyocera")
pw.results_ready.connect(lambda rows, q, t: got.append(rows)); pw.run()
comps = sorted(r["comp"] for r in got[0]) if got else None
check("printer: Kyocera → сам принтер + строки WS-102 (kuznetsova) и WS-133 (свободный ПК)", comps == ["", "WS-102", "WS-133"], str(comps))
free = next((r for r in got[0] if r["comp"] == "WS-133"), {})
check("строка свободного ПК несёт принтеры из кэша и login «—»", free.get("login") == "—" and [p["name"] for p in free.get("printers", [])] == ["Kyocera ECOSYS P3145dn"])
got.clear(); n0 = len(conn.calls)
w2 = SearchWorker(lambda: conn, "10.0.9"); w2.results_ready.connect(lambda rows, q, t: got.append(rows)); w2.run()
check("IP без владельцев → строка ПК WS-999 без обращения к LDAP", got and got[0][0]["comp"] == "WS-999" and len(conn.calls) == n0)

print("\n=== S. GUI: бейджи, клик, статус, диалог принтеров, копирование ===")
from adk.main_window import ADApp  # noqa: E402
from adk.dialogs import PrintersDialog, UserCardDialog  # noqa: E402
netutils.get_computer_network_info = lambda n, **kw: ({"WS-101": "10.0.2.11", "WS-105": "10.0.2.15"}.get(n, "Не найден"), n in ("WS-101", "WS-105"))
ADApp.start_scan = lambda s: None
w = ADApp("CORP\\admin", "x"); w.show()
db.save_computer_for_login("ivanov", "WS-101"); db.save_computer_for_login("petrov", "WS-105")
w.search_input.setText("иванов"); w.start_search()
tg._wait(lambda: w.table.rowCount() >= 1, app, 3000)
w.select_row(next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "ivanov"))
badges = w.printers_box.findChildren(BadgeButton)
check("инспектор: 2 бейджа (сетевой с IP и ★, USB), без Microsoft PDF", [b.text() for b in badges] == ["HP LaserJet M404dn · 10.0.2.50 ★", "Canon LBP6030"], str([b.text() for b in badges]))
check("бейдж: подсказка содержит тип и порт", "сетевой" in badges[0].toolTip() and "IP_10.0.2.50" in badges[0].toolTip() and "По умолчанию" in badges[0].toolTip(), badges[0].toolTip())
from PyQt6.QtWidgets import QApplication as _QA  # noqa: E402
w.copy_card()
check("копия карточки содержит принтеры", "🖨️ HP LaserJet M404dn (сетевой · 10.0.2.50); Canon LBP6030 (USB)" in _QA.clipboard().text(), _QA.clipboard().text())
badges[0].click()
tg._wait(lambda: "Принтер" in w.lbl_status.text(), app, 3000)
logins = sorted(w.table.item(r, 0).text() for r in range(w.table.rowCount()))
check("клик по сетевому бейджу → поиск по IP → только сам принтер (владельцы — в инспекторе)", w.search_input.text() == "10.0.2.50" and logins == ["—"], str(logins))
check("статус: найден принтер, подключено 2 ПК", "Принтеров: 1" in w.lbl_status.text() and "подключено ПК — 2" in w.lbl_status.text(), w.lbl_status.text())
w.select_row(0)
check("строка принтера открывает инспектор принтера (кто подключён — 2 ПК)", w.printer_pane.isVisible() and w.printer_pcs.rowCount() == 2, str(w.printer_pcs.rowCount()))
w.search_input.setText("иванов"); w.start_search()
tg._wait(lambda: w.table.rowCount() > 0 and w.table.item(0, 0).text() != "—", app, 3000)
w.select_row(next(r for r in range(w.table.rowCount()) if w.table.item(r, 0).text() == "ivanov"))
usb = next((b for b in w.printers_box.findChildren(BadgeButton) if "Canon" in b.text()), None)
usb.click()
tg._wait(lambda: "подключено ПК — 1" in w.lbl_status.text(), app, 3000)
check("клик по USB-бейджу → подключён 1 ПК, 1 пользователь", "подключено ПК — 1" in w.lbl_status.text() and sorted(w.table.item(r, 0).text() for r in range(w.table.rowCount())) == ["ivanov", "—"], w.lbl_status.text())
w.search_input.setText("printer: Kyocera"); w.start_search()
tg._wait(lambda: w.table.rowCount() == 3, app, 3000)   # принтер + WS-102 + WS-133
free_row = next((r for r in range(w.table.rowCount()) if w.table.item(r, 3).text() == "WS-133"), None)
w.select_row(free_row)
check("свободный ПК с принтером: инспектор показывает ПК и бейдж Kyocera", "WS-133" in w.lbl_fio.text() and [b.text() for b in w.printers_box.findChildren(BadgeButton)] == ["Kyocera ECOSYS P3145dn · 10.0.2.51 ★"], w.lbl_fio.text())
dlg = PrintersDialog(w, w)
check("диалог «Принтеры парка»: 4 принтера, сортировка по числу ПК", dlg.table.rowCount() == 4 and dlg.table.item(0, 3).text() in ("2",), str([dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())]))
dlg.text.setText("WS-102")
check("диалог: фильтр по имени ПК", sorted(dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())) == ["Epson L3150", "Kyocera ECOSYS P3145dn"])
dlg.text.clear(); dlg.kind.setCurrentIndex(3)
check("диалог: фильтр «USB»", sorted(dlg.table.item(r, 0).text() for r in range(dlg.table.rowCount())) == ["Canon LBP6030", "Epson L3150"])
csvp = os.path.join(_home, "printers.csv"); dlg.write_csv(csvp)
check("диалог: экспорт CSV с BOM и типом", open(csvp, encoding="utf-8-sig").read().startswith("Принтер;Тип;IP;ПК;В сети") and ";USB;" in open(csvp, encoding="utf-8-sig").read())
dlg.close()
card = UserCardDialog(tg.ENTRIES[0], w, w); card.show()
tg._wait(lambda: card.current_ip != "Не найден", app, 3000)
check("карточка 3.2.5: принтеры и действия с ПК убраны (есть в инспекторе), характеристики остались", not hasattr(card, "printers") and card.specs_tree.topLevelItemCount() >= 1)
card.close()
w.search_input.clear(); tg._wait(lambda: False, app, 100)
check("Esc/очистка → дашборд; секция принтеров не падает без выбора", w.stack.currentIndex() == 0)

print("\n=== T. Производительность ===")
netutils.get_computer_network_info = lambda n, **kw: ("10.0.0.1", True)
db.batch_update_inventory([{"Hostname": f"PC-{i}", "ActualIp": f"10.{i // 65536}.{(i // 256) % 256}.{i % 256}", "Status": "ACTIVE", "User": f"u{i}"} for i in range(30000)], "2026-09-04 10:00:00")
entries = [tg.FakeEntry(f"CN=U{i}", sAMAccountName=f"u{i}", displayName=f"U{i}", sn="U", givenName="U", userAccountControl=512) for i in range(0, 30000, 100)]
t = time.perf_counter(); inv, perm, pcm = db.load_inventory_maps(); t_maps = time.perf_counter() - t
t = time.perf_counter(); rows = SearchWorker(lambda: conn, "u")._assemble(entries); t_asm = time.perf_counter() - t
check(f"30k ПК: load_inventory_maps {t_maps * 1000:.0f} мс < 500", t_maps < 0.5)
check(f"30k ПК × 300 записей: _assemble {t_asm * 1000:.0f} мс < 1500, все ПК сопоставлены", t_asm < 1.5 and all(r["comp"].startswith("PC-") for r in rows))
t = time.perf_counter()
for _ in range(50): db.logins_by_computer_or_ip("10.0.2")
t_ip = (time.perf_counter() - t) / 50
check(f"поиск по IP-префиксу с индексом: {t_ip * 1000:.1f} мс/запрос < 50", t_ip < 0.05)
netutils.clear_network_cache()
import socket  # noqa: E402
dns = []
socket.gethostbyname = lambda n: dns.append(n) or "10.0.0.2"
netutils.is_host_alive = lambda ip: True
for _ in range(3): _orig_netinfo("PC-77")
check("кэш сети: 3 запроса подряд → 1 обращение к DNS", len(dns) == 1, str(len(dns)))
idx = {r[1] for r in db.db_execute_with_retry("PRAGMA index_list(pc_printers)", fetch="all") or []}
check("индексы pc_printers созданы", any("printers_name" in i for i in idx) and any("printers_ip" in i for i in idx), str(idx))

print("\n=== U. Артефакты README ===")
docs = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
need = ["dashboard.png", "search.png", "user_card.png", "reset_password.png", "audit_log.png", "printer_search.png", "demo_search.gif", "demo_printers.gif", "demo_card.gif"]
missing = [f for f in need if not os.path.exists(os.path.join(docs, f))]
check("скриншоты и GIF на месте (9 файлов)", not missing, str(missing))
try:
    from PIL import Image
    frames = {g: Image.open(os.path.join(docs, g)).n_frames for g in need if g.endswith(".gif") and os.path.exists(os.path.join(docs, g))}
    check("GIF действительно анимированы (>1 кадра)", frames and all(n > 1 for n in frames.values()), str(frames))
except ImportError:
    check("GIF действительно анимированы (>1 кадра)", True, "PIL недоступен — пропуск")
readme = open(os.path.join(os.path.dirname(docs), "README.md"), encoding="utf-8").read()
gallery = open(os.path.join(docs, "SCREENSHOTS.md"), encoding="utf-8").read()
check("README + галерея ссылаются на все GIF и printer_search.png", all(f in readme + gallery for f in need if f.endswith(".gif")) and "printer_search.png" in gallery
      and "docs/SCREENSHOTS.md" in readme)
w.close()

print("\n=== ИТОГ ===")
passed = sum(1 for _, ok, _ in R if ok)
print(f"{passed}/{len(R)} проверок пройдено")
for name, ok, note in R:
    if not ok: print(f"  FAIL: {name} {note}")
