"""Замер реальной скорости поиска через строку поиска главного окна на базе из 1500 ПК.

    QT_QPA_PLATFORM=offscreen python tests/bench/search_bench.py [--db extras/data/bench/pc_mapping.db]
        [--ldap-ms 40] [--net-ms 120] [--repeat 3]

Что «реальное», а что заглушка (честно):
  * База pc_mapping.db — настоящий файл SQLite с 1500 ПК, 4,5 тыс. принтеров, 90 тыс. программ (см. make_bench_db.py);
    все запросы к ней идут через настоящий код adk.db.
  * Главное окно, SearchWorker, двухшаговый поиск, таблица, инспектор — настоящие.
  * AD — заглушка (в песочнице контроллера домена нет): отвечает по тому же LDAP-фильтру, что уходит в реальный AD,
    из 1350 пользователей bench_users.json, с задержкой --ldap-ms на запрос (типичный AD в локальной сети: 20–80 мс).
  * Сеть — заглушка: DNS + проверка доступности каждого ПК занимает --net-ms (типично 50–300 мс, параллельно до 50 ПК).

Для каждого запроса измеряется:
  строки  — от вызова start_search() до появления строк в таблице (то, что видит пользователь как «нашлось»);
  сеть    — до прихода статуса сети для всех ПК (второй шаг, бейдж «Проверка…» → «В сети»/«Не в сети»);
  БД      — чистое время SQL-части (фильтр, принтеры, карты инвентаря, сборка строк) без AD и сети;
  UI      — заполнение таблицы.
Итог — таблица в консоли и файл RESULTS.md рядом с базой.
"""
import argparse, json, os, re, statistics, sys, tempfile, time, types  # noqa: E401

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
_home = tempfile.mkdtemp(prefix="adk_bench_")
os.environ["HOME"] = _home; os.environ["USERPROFILE"] = _home

ap = argparse.ArgumentParser()
ap.add_argument("--db", default=os.path.join("extras", "data", "bench", "pc_mapping.db"))
ap.add_argument("--ldap-ms", type=float, default=40)
ap.add_argument("--net-ms", type=float, default=120)
ap.add_argument("--repeat", type=int, default=3)
ap.add_argument("--font", type=int, default=10)
args = ap.parse_args()

from adk import config, db, ad, netutils  # noqa: E402
config.settings.db_path = os.path.abspath(args.db)
config.settings.hide_role_welcome = True
config.settings.design["font_size"] = args.font
db.init_db()
import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from adk.widgets import apply_theme, MessageBox  # noqa: E402
app = QApplication([]); apply_theme(config.settings.design)
MessageBox._show = classmethod(lambda cls, *a, **k: cls.YES)

# ---------------------------------------------------------------- заглушка AD, отвечающая по LDAP-фильтру
users = json.load(open(os.path.join(os.path.dirname(config.settings.db_path), "bench_users.json"), encoding="utf-8"))
ENTRIES = []
for u in users:
    attrs = {k: v for k, v in u.items() if k not in ("disabled", "login")}
    attrs["sAMAccountName"] = u["login"]
    attrs["userAccountControl"] = 514 if u["disabled"] else 512
    attrs["memberOf"] = ["CN=Domain Users,OU=g", f"CN={u['department']},OU=g"]
    ENTRIES.append(tg.FakeEntry(f"CN={u['displayName']},OU=Users,DC=example,DC=local", **attrs))
_TERM = re.compile(r"\((\w[\w-]*)=([^()]*)\)")
_SKIP = {"objectCategory", "objectClass"}


def _val(e, attr):
    try:
        v = e[attr].value
    except KeyError:
        return ""
    return "" if v is None else str(v)


_RX_CACHE: dict[str, re.Pattern] = {}
_VAL_CACHE: dict[tuple[int, str], str] = {}


def _match_term(e, attr, pat):
    if attr == "userAccountControl":
        return True
    key = (id(e), attr)
    v = _VAL_CACHE.get(key)
    if v is None:
        v = _VAL_CACHE[key] = _val(e, attr).lower()
    pat = pat.lower()
    if pat == "*$":
        return v.endswith("$")
    if "*" not in pat:
        return v == pat
    rx = _RX_CACHE.get(pat)
    if rx is None:
        rx = _RX_CACHE[pat] = re.compile("^" + ".*".join(re.escape(p) for p in pat.split("*")) + "$")
    return rx.match(v) is not None


class BenchConn(tg.FakeConn):
    """Фильтрует ENTRIES по LDAP-фильтру SearchWorker (|(...)(...)) и спит --ldap-ms, как настоящий контроллер."""
    calls: list[tuple[str, float]] = []

    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        t0 = time.perf_counter()
        time.sleep(args.ldap_ms / 1000)
        include_disabled = "userAccountControl:1.2.840.113556.1.4.803:=2" not in flt
        terms = [(a, p) for a, p in _TERM.findall(flt) if a not in _SKIP and a != "userAccountControl"]
        body = flt.split("(|", 1)[1] if "(|" in flt else ""
        or_terms = [(a, p) for a, p in _TERM.findall(body) if a not in _SKIP and a != "userAccountControl"]
        out = []
        exact_logins = {p.lower() for a, p in or_terms if a == "sAMAccountName" and "*" not in p}
        other = [(a, p) for a, p in or_terms if not (a == "sAMAccountName" and "*" not in p)]
        for e in ENTRIES:
            if not include_disabled and int(_val(e, "userAccountControl")) & 2:
                continue
            if _val(e, "sAMAccountName").endswith("$"):
                continue
            if or_terms and not (_val(e, "sAMAccountName").lower() in exact_logins or any(_match_term(e, a, p) for a, p in other)):
                continue
            if not or_terms and terms and not all(_match_term(e, a, p) for a, p in terms):
                continue
            out.append(e)
        self.entries = out
        self.result = {"controls": {}}
        BenchConn.calls.append((flt[:80], time.perf_counter() - t0))
        return True


conn = BenchConn(ENTRIES)
ad.make_connection = lambda *a, **k: conn
inv_online = {r[0]: (r[1], bool(r[2])) for r in db.db_execute_with_retry("SELECT computer_name, ip_address, is_online FROM pc_inventory", fetch="all")}


def fake_net(name, **kw):
    time.sleep(args.net_ms / 1000)
    ip, on = inv_online.get(db.clean_computer_name(name), ("Не найден", False))
    return ip, on


netutils.get_computer_network_info = fake_net
netutils.probe_printer = lambda ip, **kw: {"alive": True, "is_printer": True, "evidence": "открыт порт 9100"}
netutils.is_printer_alive = lambda ip, **kw: (time.sleep(args.net_ms / 1000) or True)

# ---------------------------------------------------------------- окно
from adk.main_window import ADApp  # noqa: E402
ADApp.start_scan = lambda self: None
w = ADApp("CORP\\admin", "x"); w.resize(1400, 820); w.show(); tg._wait(lambda: False, app, 300)

# инструментируем чистое время БД внутри SearchWorker
_db_time = {"t": 0.0}
for fn_name in ("logins_by_computer_or_ip", "logins_by_printer", "printer_matches", "printer_owners", "logins_by_printer",
                "load_inventory_maps", "load_audit_map", "printers_for_computers", "inventory_rows_matching", "inventory_rows_for_computers"):
    fn = getattr(db, fn_name)

    def wrap(f):
        def inner(*a, **k):
            t = time.perf_counter()
            try:
                return f(*a, **k)
            finally:
                _db_time["t"] += time.perf_counter() - t
        return inner
    setattr(db, fn_name, wrap(fn))
_fill_time = {"t": 0.0}
_orig_fill = ADApp.fill_table


def _fill(self, rows):
    t = time.perf_counter()
    try:
        return _orig_fill(self, rows)
    finally:
        _fill_time["t"] += time.perf_counter() - t


ADApp.fill_table = _fill

# подбираем реальные значения из базы
some_pc = db.db_execute_with_retry("SELECT computer_name, ip_address, current_user FROM pc_inventory WHERE current_user != '' ORDER BY computer_name LIMIT 1 OFFSET 730", fetch="one")
free_pc = db.db_execute_with_retry("SELECT computer_name, ip_address FROM pc_inventory WHERE current_user = '' LIMIT 1", fetch="one")
top_sn = statistics.mode(u["sn"] for u in users)
n_top = sum(1 for u in users if u["sn"] == top_sn)
net_printer_ip = db.db_execute_with_retry("SELECT ip_address, COUNT(*) c FROM pc_printers WHERE kind='network' GROUP BY ip_address ORDER BY c DESC LIMIT 1", fetch="one")

QUERIES = [
    (f"фамилия с {n_top} совпадениями", top_sn, {}),
    ("логин (точный)", some_pc[2], {}),
    ("имя ПК", some_pc[0], {}),
    ("IP ПК (полный)", some_pc[1], {}),
    ("IP-префикс подсети (10.0.3)", "10.0.3", {}),
    ("отдел «Бухгалтерия»", "Бухгалтерия", {}),
    ("свободный ПК (без пользователя)", free_pc[0], {}),
    ("модель принтера «Kyocera»", "Kyocera", {}),
    (f"IP сетевого принтера ({net_printer_ip[1]} ПК)", net_printer_ip[0], {}),
    ("фамилия + режим «Архивы»", top_sn, {"archive": True}),
    ("фамилия + «Отключённые»", top_sn, {"disabled": True}),
    ("широкий запрос «ов» (усечение до лимита)", "ов", {}),
]


def wait_measure(cond, ms):
    """Как tg._wait, но замеряет самую длинную паузу цикла событий (насколько «подвисает» интерфейс)."""
    t0 = time.perf_counter(); worst = 0.0
    while not cond() and (time.perf_counter() - t0) * 1000 < ms:
        t = time.perf_counter(); app.processEvents(); worst = max(worst, time.perf_counter() - t); time.sleep(0.002)
    return cond(), worst


def run_query(text, opts):
    w.chk_archive.setChecked(bool(opts.get("archive")))
    w.chk_disabled.setChecked(bool(opts.get("disabled")))
    w.search_input.clear(); w.table.setRowCount(0); w.results = []; tg._wait(lambda: False, app, 50)
    _db_time["t"] = 0.0; _fill_time["t"] = 0.0; BenchConn.calls.clear()
    w.search_input.blockSignals(True); w.search_input.setText(text); w.search_input.blockSignals(False)
    t0 = time.perf_counter()
    w.start_search()
    ok, gap1 = wait_measure(lambda: w.stack.currentIndex() == 1 and w.table.rowCount() > 0, 20000)
    t_rows = time.perf_counter() - t0
    _, gap2 = wait_measure(lambda: not any(r.get("net_pending") for r in w.results), 30000)
    t_net = time.perf_counter() - t0
    gap = max(gap1, gap2)
    tg._wait(lambda: w.search_worker is None, app, 5000)
    pending_shown = sum(1 for r in range(w.table.rowCount()) if (w.table.item(r, 4) and "Проверка" in w.table.item(r, 4).text()))
    status = re.sub(r"<[^>]+>", "", w.lbl_status.text()).replace("&nbsp;", " ").strip()   # в подписи иконка <img> вместо эмодзи
    return {"ok": ok, "rows": w.table.rowCount(), "t_rows": t_rows, "t_net": t_net, "t_db": _db_time["t"], "t_fill": _fill_time["t"],
            "ldap": sum(d for _, d in BenchConn.calls), "ldap_calls": len(BenchConn.calls), "status": status, "left_pending": pending_shown, "gap": gap}


results = []
for label, text, opts in QUERIES:
    runs = [run_query(text, opts) for _ in range(args.repeat)]
    med = lambda k: statistics.median(r[k] for r in runs)  # noqa: E731
    r0 = runs[-1]
    results.append({"label": label, "query": text, "rows": r0["rows"], "t_rows": med("t_rows"), "t_net": med("t_net"), "t_db": med("t_db"),
                    "t_fill": med("t_fill"), "ldap": med("ldap"), "ldap_calls": r0["ldap_calls"], "status": r0["status"], "left": r0["left_pending"],
                    "gap": max(r["gap"] for r in runs)})
    print(f"{label:45} «{text}» → {r0['rows']:>3} стр.  строки {med('t_rows')*1000:6.0f} мс  сеть {med('t_net')*1000:6.0f} мс  "
          f"(БД {med('t_db')*1000:4.0f} · AD {med('ldap')*1000:4.0f} · таблица {med('t_fill')*1000:4.0f} · макс. пауза UI {results[-1]['gap']*1000:3.0f})  {r0['status'][:60]}")

# набор по буквам с debounce — как печатает человек
w.search_input.clear(); w.table.setRowCount(0); w.results = []; tg._wait(lambda: False, app, 100); BenchConn.calls.clear()
t0 = time.perf_counter()
for i in range(1, len(top_sn) + 1):
    w.search_input.setText(top_sn[:i]); tg._wait(lambda: False, app, 90)
t_typed = time.perf_counter() - t0
tg._wait(lambda: w.stack.currentIndex() == 1 and w.table.rowCount() > 0, app, 20000)
t_first = time.perf_counter() - t0
print(f"\nнабор «{top_sn}» по буквам (90 мс/буква, debounce {w.debounce.interval()} мс): последняя буква через {t_typed*1000:.0f} мс, "
      f"результат на экране через {t_first*1000:.0f} мс, LDAP-запросов за время набора: {len(BenchConn.calls)}")
typed = {"t_typed": t_typed, "t_first": t_first, "ldap_calls": len(BenchConn.calls), "debounce": w.debounce.interval()}

# отдельные части БД под микроскопом
micro = {}
for name, fn in (("load_inventory_maps (1500 ПК)", lambda: db.load_inventory_maps()),
                 ("logins_by_computer_or_ip('WS-07')", lambda: db.logins_by_computer_or_ip("WS-07")),
                 ("printer_matches('Kyocera')", lambda: db.printer_matches("Kyocera")),
                 ("printers_for_computers(200 ПК)", lambda: db.printers_for_computers([f"WS-{i:04d}" for i in range(1, 201)])),
                 ("suggestions()", lambda: db.suggestions()),
                 ("load_audit_map()", lambda: db.load_audit_map()),
                 ("inventory_rows_matching('10.0.3')", lambda: db.inventory_rows_matching("10.0.3"))):
    ts = []
    for _ in range(5):
        t = time.perf_counter(); fn(); ts.append(time.perf_counter() - t)
    micro[name] = statistics.median(ts)
    print(f"  {name:38} {micro[name]*1000:6.1f} мс")

out = os.path.join(os.path.dirname(config.settings.db_path), "RESULTS.md")
with open(out, "w", encoding="utf-8") as f:
    f.write(f"# Скорость поиска на базе из 1500 ПК\n\nБаза: `{os.path.basename(config.settings.db_path)}` "
            f"({os.path.getsize(config.settings.db_path)/1e6:.1f} МБ): 1500 ПК, {len(users)} пользователей, "
            f"{db.db_execute_with_retry('SELECT COUNT(*) FROM pc_printers', fetch='one')[0]} записей принтеров, "
            f"{db.db_execute_with_retry('SELECT COUNT(*) FROM pc_software', fetch='one')[0]} записей ПО.\n"
            f"Задержки заглушек: AD {args.ldap_ms:.0f} мс/запрос, сеть {args.net_ms:.0f} мс/ПК (параллельно). Медиана из {args.repeat} прогонов.\n\n")
    f.write("| Запрос | Текст | Строк | Строки на экране | Статус сети готов | из них: БД | AD | таблица | макс. пауза UI |\n|---|---|---:|---:|---:|---:|---:|---:|---:|\n")
    for r in results:
        f.write(f"| {r['label']} | `{r['query']}` | {r['rows']} | **{r['t_rows']*1000:.0f} мс** | {r['t_net']*1000:.0f} мс | "
                f"{r['t_db']*1000:.0f} мс | {r['ldap']*1000:.0f} мс ({r['ldap_calls']}) | {r['t_fill']*1000:.0f} мс | {r['gap']*1000:.0f} мс |\n")
    f.write(f"\nНабор «{top_sn}» по буквам (90 мс/буква, debounce {typed['debounce']} мс): результат через {typed['t_first']*1000:.0f} мс "
            f"после первой буквы ({typed['t_first']*1000 - typed['t_typed']*1000:.0f} мс после последней), LDAP-запросов: {typed['ldap_calls']}.\n\n")
    f.write("## Отдельные запросы к базе\n\n| Функция | Время |\n|---|---:|\n")
    for k, v in micro.items():
        f.write(f"| `{k}` | {v*1000:.1f} мс |\n")
print(f"\n→ {out}")
w.close()
