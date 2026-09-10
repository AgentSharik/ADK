"""Сквозной сценарий (E2E) без реального AD: offscreen Qt + заглушки LDAP/сети.

Запуск из корня проекта:
    QT_QPA_PLATFORM=offscreen python tests/e2e_scenario.py

Не является pytest-модулем (имя без префикса test_): печатает PASS/FAIL по 46 проверкам
и итог. Разделы: A — LDAP-слой (paging), B — сценарий пользователя в главном окне,
C — диалоги, D — безопасность, E — устойчивость к сбоям.
"""
import sys, os, threading, json  # noqa: E401
sys.path.insert(0,'tests'); sys.path.insert(0,'.')
for n in ("pythoncom","win32com","win32com.client","win32crypt"): sys.modules.setdefault(n, type(sys)(n))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
_home=tempfile.mkdtemp(prefix="admgr_e2e_"); os.environ["HOME"]=_home; os.environ["USERPROFILE"]=_home
from adk import config, db, ad, netutils, credentials
config.settings.db_path=os.path.join(_home,'e2e.db')
db.init_db()
import test_gui as tg
from PyQt6.QtWidgets import QApplication
from adk.widgets import apply_theme, MessageBox
app=QApplication([]); apply_theme(config.settings.design)
MessageBox._show = classmethod(lambda cls,*a,**k: cls.YES)
R=[]
def check(name, cond, note=""):
    R.append((name, bool(cond), note)); print(("PASS " if cond else "FAIL ")+name+(f"  — {note}" if note else ""))

big=[tg.FakeEntry(f"CN=U{i},OU=x", sAMAccountName=f"user{i}", displayName=f"Пользователь {i}", sn="Тест", givenName="Т",
                  userAccountControl=512, company="Орг", department="Отдел") for i in range(1200)]
class PagedConn(tg.FakeConn):
    def __init__(self,e): super().__init__(e); self.pages=0
    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        start=int(paged_cookie or 0); self.entries=self._all[start:start+paged_size]; self.pages+=1
        nxt=start+paged_size
        self.result={"controls":{"1.2.840.113556.1.4.319":{"value":{"cookie": str(nxt) if nxt<len(self._all) else b""}}}}
        return True
pc=PagedConn(big)
print("=== A. LDAP-слой ===")
res=ad.paged_search(pc,"(x)",["cn"]); check("paged_search обходит лимит 1000", len(res)==1200, f"{len(res)} записей за {pc.pages} страниц")
check("paged_search limit", len(ad.paged_search(pc,"(x)",["cn"],limit=40))==40)
check("get_all_attribute_values (paging)", ad.get_all_attribute_values(pc,"company")==["Орг"])

print("\n=== B. Главное окно (сценарий пользователя) ===")
conn=tg.FakeConn(tg.ENTRIES+big[:30]); ad.make_connection=lambda *a,**k: conn
netutils.get_computer_network_info=lambda n, **kw:("10.0.0.9", n=="WS-101")
db.save_computer_for_login("ivanov","WS-101")
db.batch_update_inventory([{"Hostname":"WS-101","ActualIp":"10.0.0.9","Status":"ACTIVE","User":"ivanov","LastLogon":"01.09.2026"},
                           {"Hostname":"WS-102","ActualIp":"10.0.0.10","Status":"OFFLINE","User":""}],"2026-09-04 10:00:00")
from adk.main_window import ADApp
ADApp.start_scan=lambda s:None
w=ADApp("CORP\\admin","x"); w.show()
check("дашборд при старте", w.stack.currentIndex()==0 and w.cards.count()==3)
conn.calls=0; orig=conn.search
def counting(*a,**k):
    if "objectCategory=person" in a[1]: conn.calls+=1
    return orig(*a,**k)
conn.search=counting
for ch in "ивано": w.search_input.setText(w.search_input.text()+ch); app.processEvents()
tg._wait(lambda: w.table.rowCount()>0, app, 3000); tg._wait(lambda: False, app, 200)
check("debounce: 5 нажатий → 1 LDAP-запрос", conn.calls==1, f"запросов: {conn.calls}")
check("результаты и инспектор", w.table.rowCount()==32 and w.lbl_fio.text()!="", f"строк: {w.table.rowCount()}, инспектор: {w.lbl_fio.text()}")
check("сортировка: онлайн первым", w.table.item(0,0).text()=="ivanov" and w.table.item(0,3).text()=="WS-101")
check("инспектор: ПК и IP", "WS-101 (10.0.0.9)" in w.vals["pc"].text())
check("инспектор: статус В сети", "В сети" in w.vals["status"].text())
w.table.sortItems(1); app.processEvents()
ok=all((w.table.item(r,0).text()=="petrov")==(w.table.item(r,2).text()=="Не активна") for r in range(w.table.rowCount()))
check("бейджи не рассинхронизируются при сортировке", ok)
w.search_input.blockSignals(True); w.search_input.setText("петров"); w.search_input.blockSignals(False)
w.on_results([{"login":"stale","fio":"stale","is_disabled":False,"comp":"—","is_online":False}], "иванов")
check("устаревший ответ поиска отброшен", w.table.item(0,0).text()!="stale")
n_before=conn.calls; w.start_search(); tg._wait(lambda: conn.calls>n_before and w.search_worker is None, app, 3000)
check("повторный поиск после первого (был RuntimeError)", conn.calls==n_before+1 and w.search_worker is None and w.lbl_status.text().startswith("Найдено"), w.lbl_status.text())
check("история поиска пишется", any("петров" in q.lower() for q in db.get_recent_searches()), str(db.get_recent_searches()))
w.show_category("offline"); check("drill-down «не в сети»", w.table.rowCount()==1 and w.table.item(0,3).text()=="WS-102" and "свободный" in w.lbl_fio.text().lower())
w.show_category("all"); check("drill-down «все»", w.table.rowCount()==2)
w.select_row(0); w.copy_card(); clip=app.clipboard().text()
check("копирование карточки в буфер", "💻 ПК:" in clip and "WS-1" in clip, clip.replace("\n"," | "))
w.update_pc_status_in_ui("WS-102", True)
r102=next(r for r in range(w.table.rowCount()) if w.table.item(r,3).text()=="WS-102")
check("update_pc_status_in_ui меняет бейдж", "В сети" in w.table.item(r102,4).text())
w.search_input.clear(); check("очистка поля → дашборд", w.stack.currentIndex()==0)
w.toggle_column(5, False); w.restore_columns(); check("скрытие колонок сохраняется", w.table.isColumnHidden(5)); w.toggle_column(5, True)

print("\n=== C. Диалоги ===")
from adk.dialogs import (UserCardDialog, RegisterUserDialog, FreeIPDialog, InventoryDialog,
                               GroupMembersDialog, DesignSettingsDialog, PingDialog)
active_before=threading.active_count()
d=UserCardDialog(tg.ENTRIES[0], w); d.show(); tg._wait(lambda: d.all_groups, app, 3000)
check("карточка: группы загрузились в фоне", len(d.all_groups)>0, f"{len(d.all_groups)} записей")
check("карточка: memberOf показан", d.groups_list.count()==2)
d.copy_to_clipboard(); check("карточка: копирование не падает (был AttributeError)", "Иванов" in app.clipboard().text())
d.inputs["title"].setText("Инженер"); d.save_changes(); tg._wait(lambda: conn.modified, app, 3000)
check("карточка: сохраняются только изменённые", conn.modified and list(conn.modified[-1][1])==["title"], str(list(conn.modified[-1][1]) if conn.modified else None))
d.close()
d2=RegisterUserDialog(w); d2.show(); tg._wait(lambda: d2.combos["company"].count()>0, app, 3000)
d2.surname.setText("Константинопольский"); d2.name.setText("Константин"); d2.generate()
check("регистрация: логин ≤20 и транслит", d2.login.text()=="konstantinopolskij_k", d2.login.text())
check("регистрация: пароль по политике", len(d2.password.text())==12)
check("регистрация: подсказки в фоне", d2.combos["company"].count()>=1)
config.settings.use_ssl=False; n0=len(conn.modified); d2.create()
check("регистрация без LDAPS блокируется", len(conn.modified)==n0)
config.settings.use_ssl=True; d2.close()
d3=FreeIPDialog(w); d3.show(); d3.prefix.setText("bad"); d3.search(); tg._wait(lambda: "Префикс" in d3.status.text(), app, 3000)
check("свободный IP: валидация префикса", "Префикс" in d3.status.text()); d3.close()
d4=InventoryDialog(w, w); d4.show(); tg._wait(lambda: d4.companies, app, 3000); check("опись: организации загружены", d4.companies==["Орг"]); d4.close()
d5=GroupMembersDialog("CN=IT,OU=g","IT",w,w); d5.show(); tg._wait(lambda: d5.table.rowCount()>0, app, 3000); check("участники группы через LDAP (не COM)", d5.table.rowCount()>0); d5.close()
d6=DesignSettingsDialog(w,w); d6.show(); d6.preset("plum"); check("смена темы применяется", config.settings.design["accent_color"]=="#C084FC" and config.settings.design["panel_color"]=="#2B1F36"); d6.close()
d7=PingDialog("LOCAL","127.0.0.1",w,w); d7.show(); tg._wait(lambda: d7.sent>=2, app, 6000)
got=d7.sent; pid=d7.worker._proc.pid if d7.worker._proc else None
d7.reject(); tg._wait(lambda: not d7.worker.isRunning(), app, 5000)
def alive(p):
    try: return "zombie" not in open(f"/proc/{p}/status").read().lower()
    except Exception: return False
check("пинг: живой вывод парсится", got>=2, f"{got} строк")
check("пинг: процесс убит при Esc/reject", pid and not alive(pid), f"pid {pid}")
tg._wait(lambda: False, app, 500)
check("потоки не текут после диалогов", threading.active_count()<=active_before+1, f"{active_before} → {threading.active_count()}")

print("\n=== D. Безопасность ===")
check("SQL LIKE: '_' буквальный", db.logins_by_computer_or_ip("WS_1")==set())
for bad in ("WS-1; rm -rf /","WS-1 && calc","$(x)","WS-1\\..\\"):
    try: netutils.remote_command("restart",bad); ok=False
    except ValueError: ok=True
    check(f"инъекция в имя узла отклонена: {bad!r}", ok)
from adk.workers import PingWorker
pw=PingWorker("1.1.1.1; id"); errs=[]; pw.error.connect(errs.append); pw.run(); check("PingWorker отклоняет плохой target", bool(errs))
credentials.CRED_FILE=os.path.join(_home,"cred.json"); credentials.win32crypt=None
credentials.save_credentials("CORP\\admin","S3cret!")
raw=open(os.path.join(_home,"cred.json")).read() if os.path.exists(""+_home+"/cred.json") else ""
check("пароль не попадает в файл открытым текстом", "S3cret" not in raw, f"файл: {raw or '(не создан — нет keyring backend в песочнице)'}")
open(os.path.join(_home,"cred.json"),"w").write(json.dumps({"username":"a","password":"plain"}))
u,p=credentials.load_credentials(); check("старый plain-text файл отвергается и удаляется", (u,p)==(None,None) and not os.path.exists(os.path.join(_home,"cred.json")))
e=tg.FakeEntry("CN=x", sAMAccountName="x", displayName="<script>alert(1)</script>")
from adk.widgets import safe_rich; check("html.escape в rich-text", "<script>" not in safe_rich("ФИО:", ad.get_ad_value(e,"displayName")))

print("\n=== E. Устойчивость ===")
config.settings.db_path="/nonexistent/dir/x.db"
try: db.get_inventory_stats(); ok=False
except Exception: ok=True
config.settings.db_path=os.path.join(_home,'e2e.db')
check("ошибка БД — исключение, не молчание", ok)
def boom(*a,**k): raise RuntimeError("LDAP down")
ad.make_connection=boom
w.search_input.setText("кто-то"); w.start_search(); tg._wait(lambda: "exclamationmark" in w.lbl_status.text() or "AD" in w.lbl_status.text(), app, 3000)
check("недоступный AD → сообщение, а не вечный «Поиск…»", "Поиск…" not in w.lbl_status.text() and w.lbl_status.text() != "", w.lbl_status.text()[:80])
ad.make_connection=lambda *a,**k: conn
w.search_input.setText("иван"); w.start_search(); w.close(); tg._wait(lambda: False, app, 800)
check("closeEvent дожидается потоков", not any(t.isRunning() for t in w._threads))

print("\n=== ИТОГ ===")
p=sum(1 for r in R if r[1]); print(f"{p}/{len(R)} проверок пройдено")
for r in R:
    if not r[1]: print("  FAIL:", r[0], r[2])
