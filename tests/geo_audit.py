"""Геометрический обход интерфейса: наложения, вылезание за края, обрезанный текст.

Открывает главное окно во всех режимах и каждое окно/вкладку приложения, и для каждого видимого виджета
проверяет: (1) он целиком внутри родителя; (2) у QLabel/QPushButton/QCheckBox без переноса sizeHint по ширине
не больше фактической ширины (иначе текст обрезан); (3) соседи в одном лэйауте не пересекаются.

Запуск (из корня проекта):
    ADK_THEME=dark ADK_FONT=12 QT_QPA_PLATFORM=offscreen python tests/geo_audit.py
Переменные: ADK_THEME — ключ темы (dark|ember|pine|plum|dusk|light|sand|garden|lavender|dawn),
ADK_FONT — базовый размер шрифта (целое, pt), ADK_W/ADK_H — размер главного окна, ADK_SHOTS=1 — писать png в
/tmp/geo_<theme>/. Печатает по строке на окно и итог «ИТОГО замечаний: N».
"""
import os, sys, tempfile, types, json  # noqa: E401
sys.path.insert(0, "tests"); sys.path.insert(0, ".")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
_home = tempfile.mkdtemp(prefix="adk_geo_")
os.environ["HOME"] = _home; os.environ["USERPROFILE"] = _home

from adk import config, db, ad, netutils, access, theme, attention  # noqa: E402
config.settings.db_path = os.path.join(_home, "geo.db")
db.init_db()
THEME = os.environ.get("ADK_THEME", "dark")
FONT = int(os.environ.get("ADK_FONT", "10"))
W, H = int(os.environ.get("ADK_W", 1400)), int(os.environ.get("ADK_H", 820))
SHOTS = os.environ.get("ADK_SHOTS") == "1"
design = dict(config.settings.design)
design.update(theme.theme_design(theme.PRESET_THEMES[THEME]))
design["font_size"] = FONT
config.settings.design = design
config.settings.hide_role_welcome = True

import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QCheckBox, QRadioButton, QTabWidget,  # noqa: E402
                             QAbstractScrollArea, QLayout, QToolButton)
from PyQt6.QtCore import QRect  # noqa: E402
from adk.widgets import apply_theme, MessageBox  # noqa: E402
app = QApplication([]); apply_theme(design)
MessageBox._show = classmethod(lambda cls, *a, **k: cls.YES)
netutils.get_computer_network_info = lambda n, **kw: ("10.0.2.11" if n == "WS-101" else "10.0.2.33", n == "WS-101")
netutils.probe_printer = lambda ip, **kw: {"alive": True, "is_printer": True, "evidence": "открыт порт 9100"}
netutils.is_printer_alive = lambda ip, **kw: True

# --- данные
long_title = "Главный бухгалтер с очень длинным названием должности для проверки переноса"
entries = [
    tg.FakeEntry("CN=Иванов,OU=x", sAMAccountName="ivanov", displayName="Иванов Иван Петрович", sn="Иванов", givenName="Иван",
                 userAccountControl=512, telephoneNumber="2554567", ipPhone="4001", department="ИТ-отдел", company="ООО «Пример»",
                 title="Системный администратор", mail="ivanov@example.local", physicalDeliveryOfficeName="каб. 214",
                 streetAddress="ул. Примерная, 1", memberOf=["CN=IT,OU=g", "CN=VPN-Users,OU=g", "CN=Remote-Desktop,OU=g"]),
    tg.FakeEntry("CN=Петрова,OU=x", sAMAccountName="petrova", displayName="Петрова Анна Сергеевна", sn="Петрова", givenName="Анна",
                 userAccountControl=512, telephoneNumber="2554568", department="Бухгалтерия", company="ООО «Пример»", title=long_title,
                 mail="petrova@example.local"),
    tg.FakeEntry("CN=Сидоров,OU=x", sAMAccountName="sidorov", displayName="Сидоров Пётр Ильич", sn="Сидоров", givenName="Пётр",
                 userAccountControl=514, telephoneNumber="2554590", department="Отдел продаж", company="ООО «Пример»", title="Менеджер",
                 mail="sidorov@example.local", physicalDeliveryOfficeName="каб. 310"),
] + [tg.FakeEntry(f"CN=user{i:02d},OU=x", sAMAccountName=f"user{i:02d}", displayName=f"Фамилия{i:02d} Имя Отчество", sn=f"Фамилия{i:02d}",
                  givenName="Имя", userAccountControl=512, department="Отдел", mail=f"user{i:02d}@example.local") for i in range(30)]
conn = tg.FakeConn(entries)
ad.make_connection = lambda *a, **k: conn
db.batch_update_inventory([
    {"Hostname": "WS-101", "ActualIp": "10.0.2.11", "Status": "ACTIVE", "User": "ivanov"},
    {"Hostname": "WS-105", "ActualIp": "10.0.2.15", "Status": "ACTIVE", "User": "petrova"},
    {"Hostname": "WS-133", "ActualIp": "10.0.2.33", "Status": "OFFLINE", "User": "sidorov"},
    {"Hostname": "WS-777", "ActualIp": "10.0.2.77", "Status": "ACTIVE", "User": ""}], "2026-09-10 10:00:00")
for login, comp in (("ivanov", "WS-101"), ("petrova", "WS-105"), ("sidorov", "WS-133")):
    db.save_computer_for_login(login, comp)
db.replace_printers("WS-101", [{"name": "HP LaserJet M404dn", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50", "is_default": True},
                               {"name": "Canon LBP6030", "port": "USB001", "kind": "usb", "ip": "", "is_default": False}])
db.replace_printers("WS-105", [{"name": "HP LaserJet M404dn", "port": "IP_10.0.2.50", "kind": "network", "ip": "10.0.2.50", "is_default": True}])
db.add_note("ivanov", "Просил второй монитор", "CORP\\admin", "user")
db.log_action("CORP\\admin", "reset_password", "ivanov", "длина 12")

# --- проверка геометрии
SKIP = (QAbstractScrollArea,)


def _text_widgets(w):
    return isinstance(w, (QLabel, QPushButton, QCheckBox, QRadioButton, QToolButton))


def check_window(top: QWidget, name: str) -> list[str]:
    issues = []
    top_rect = QRect(0, 0, top.width(), top.height())
    for w in top.findChildren(QWidget):
        if not w.isVisible() or w.width() <= 0 or w.height() <= 0:
            continue
        if isinstance(w.parentWidget(), QAbstractScrollArea) or w.window() is not top:
            continue
        inside_scroll = any(isinstance(p, QAbstractScrollArea) for p in _parents(w))
        if inside_scroll:
            continue
        g = w.geometry()
        tl = w.mapTo(top, g.topLeft() - g.topLeft())
        r = QRect(tl.x(), tl.y(), w.width(), w.height())
        if not top_rect.contains(r):
            issues.append(f"вылезает за окно: {w.__class__.__name__} «{_txt(w)}» {r.getRect()} vs {top_rect.getRect()}")
        p = w.parentWidget()
        if p is not None and p is not top and not isinstance(p, SKIP):
            pr = QRect(0, 0, p.width(), p.height())
            if not pr.contains(g) and not isinstance(p, QTabWidget):
                issues.append(f"вылезает за родителя {p.__class__.__name__}: {w.__class__.__name__} «{_txt(w)}» {g.getRect()} vs {pr.getRect()}")
        if _text_widgets(w) and _txt(w) and not (isinstance(w, QLabel) and w.wordWrap()):
            need = (w.sizeHint().width() if not isinstance(w, QLabel)
                    else max(w.fontMetrics().horizontalAdvance(line) for line in w.text().split("\n")))
            if isinstance(w, QLabel) and ("<" in w.text() and ">" in w.text()):
                continue
            if need > w.width() + 2 and w.maximumWidth() >= need:
                issues.append(f"обрезан текст: {w.__class__.__name__} «{_txt(w)[:40]}» нужно {need}, есть {w.width()}")
    # пересечения соседей в одном лэйауте
    for lay in top.findChildren(QLayout):
        items = [lay.itemAt(i).widget() for i in range(lay.count()) if lay.itemAt(i) and lay.itemAt(i).widget()]
        items = [w for w in items if w.isVisible()]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i].geometry(), items[j].geometry()
                inter = a.intersected(b)
                if inter.width() > 2 and inter.height() > 2:
                    issues.append(f"наложение: «{_txt(items[i])[:30]}» и «{_txt(items[j])[:30]}» в {lay.__class__.__name__}")
    print(f"{name}: {len(issues)}")
    for s in issues:
        print("   -", s)
    if SHOTS:
        os.makedirs(f"/tmp/geo_{THEME}", exist_ok=True)
        top.grab().save(f"/tmp/geo_{THEME}/{name.replace('/', '_')}_{FONT}.png")
    return issues


def _parents(w):
    p = w.parentWidget()
    while p is not None:
        yield p
        p = p.parentWidget()


def _txt(w):
    for a in ("text", "title", "windowTitle"):
        f = getattr(w, a, None)
        if callable(f):
            try:
                t = f()
                if t:
                    return t.replace("\n", " ")
            except Exception:  # noqa: BLE001
                pass
    return w.objectName() or ""


def wait(ms):
    tg._wait(lambda: False, app, ms)


def run_dialog(name, factory):
    try:
        d = factory()
    except Exception as exc:  # noqa: BLE001
        print(f"{name}: не открылось — {exc!r}")
        ALL.append((name, [f"exception {exc!r}"]))
        return None
    d.show(); wait(350)
    ALL.append((name, check_window(d, name)))
    if isinstance(getattr(d, "tabs", None), QTabWidget):
        for i in range(1, d.tabs.count()):
            d.tabs.setCurrentIndex(i); wait(200)
            ALL.append((f"{name}/вкладка {d.tabs.tabText(i)}", check_window(d, f"{name}/вкладка {d.tabs.tabText(i)}")))
    return d


ALL: list[tuple[str, list[str]]] = []
from adk.main_window import ADApp  # noqa: E402
ADApp.start_scan = lambda self: None
w = ADApp("CORP\\admin", "x"); w.resize(W, H); w.show(); wait(400)
ALL.append(("главное/дашборд", check_window(w, "главное/дашборд")))
w.search_input.setText("иванов"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1, app, 5000); wait(500)
ALL.append(("главное/иванов", check_window(w, "главное/иванов")))
w.search_input.setText("user"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 10, app, 5000); wait(500)
ALL.append(("главное/много строк", check_window(w, "главное/много строк")))
w.search_input.setText("10.0.2.50"); w.start_search(); tg._wait(lambda: "Принтер" in w.lbl_status.text(), app, 5000); wait(500)
w.select_row(0); wait(200)
ALL.append(("главное/принтер", check_window(w, "главное/принтер")))
w.search_input.setText("10.99.99.99"); w.start_search(); tg._wait(lambda: "не найдено" in w.lbl_fio.text().lower(), app, 5000); wait(300)
ALL.append(("главное/пусто", check_window(w, "главное/пусто")))
w.search_input.setText("петрова"); w.start_search(); tg._wait(lambda: w.table.rowCount() >= 1 and w.table.item(0, 0).text() == "petrova", app, 5000); wait(400)
w.select_row(0); wait(200)
ALL.append(("главное/петрова", check_window(w, "главное/петрова")))
w.resize(1100, 700); wait(300)
ALL.append(("главное/1100x700", check_window(w, "главное/1100x700")))
w.resize(W, H); wait(200)
access.set_rights(pc=True, ad=False); w.apply_role_ui() if hasattr(w, "apply_role_ui") else None; wait(200)
ALL.append(("главное/роль ПК", check_window(w, "главное/роль ПК")))
access.set_rights(pc=True, ad=True); w.apply_role_ui() if hasattr(w, "apply_role_ui") else None; wait(200)

from adk.dialogs import (LoginDialog, RoleWelcomeDialog, RoleInfoDialog, PluginsDialog, UserCardDialog, ResetPasswordDialog,  # noqa: E402
                         AuditLogDialog, PrintersDialog, GroupMembersDialog, RegisterUserDialog, DesignSettingsDialog)
from adk.fleet import MassPingDialog, SoftwareDialog, LogonsDialog, ComparePCDialog  # noqa: E402
from adk.health_ui import HealthDialog  # noqa: E402
from adk.freeip_ui import FreeIPDialog  # noqa: E402
from adk.inventory_ui import InventoryDialog  # noqa: E402
from adk.attention_ui import AttentionDialog  # noqa: E402
from adk.colorpicker import ColorPickerDialog  # noqa: E402
from adk.tools import BulkOperationsDialog, NotesDialog, GroupCompareDialog, HistoryDialog  # noqa: E402
from adk.pingui import PingDialog  # noqa: E402
import adk.pingui as pingui  # noqa: E402


class QuietPing(pingui.PingWorker):
    def run(self):
        import time
        self.ping_event.emit(netutils.parse_ping_line(f"Обмен пакетами с {self.target} по с 32 байтами данных:", self.target))
        for i in range(12):
            if self.cancelled:
                return
            line = "Превышен интервал ожидания для запроса." if (self.target.endswith("33") or i == 5) else f"Ответ от {self.target}: число байт=32 время={2 + i % 4}мс TTL=128"
            self.ping_event.emit(netutils.parse_ping_line(line, self.target)); time.sleep(0.05)


pingui.PingWorker = QuietPing

dialogs = [
    ("вход", lambda: LoginDialog("", saved_user="CORP\\admin", saved_password="pw")),
    ("роль-приветствие", lambda: RoleWelcomeDialog("CORP\\admin", w)),
    ("роль-инфо", lambda: RoleInfoDialog("CORP\\admin", w)),
    ("плагины", lambda: PluginsDialog(w)),
    ("карточка ivanov", lambda: UserCardDialog(entries[0], w, w)),
    ("карточка petrova (длинная должность)", lambda: UserCardDialog(entries[1], w, w)),
    ("карточка sidorov (отключён)", lambda: UserCardDialog(entries[2], w, w)),
    ("смена пароля", lambda: ResetPasswordDialog(entries[0], w, w)),
    ("журнал действий", lambda: AuditLogDialog(w)),
    ("принтеры парка", lambda: PrintersDialog(w, w)),
    ("участники группы", lambda: GroupMembersDialog("CN=IT,OU=g", "IT", w, w)),
    ("новый пользователь", lambda: RegisterUserDialog(w)),
    ("оформление", lambda: DesignSettingsDialog(w, w)),
    ("массовый пинг", lambda: MassPingDialog(["WS-101", "WS-133"], w, w)),
    ("ПО", lambda: SoftwareDialog("WS-101", w, w)),
    ("входы", lambda: LogonsDialog("WS-101", w, w)),
    ("сравнение ПК", lambda: ComparePCDialog("WS-101", "WS-133", w, w)),
    ("здоровье", lambda: HealthDialog("WS-101", w, w)),
    ("свободный IP", lambda: FreeIPDialog(w)),
    ("опись", lambda: InventoryDialog(w, w)),
    ("внимание", lambda: AttentionDialog(w, w, items=[attention._item("locked", "ivanov", "5 неудачных входов")])),
    ("цвет", lambda: ColorPickerDialog("#0A84FF", w)),
    ("массовые операции", lambda: BulkOperationsDialog([{"login": "ivanov", "entry": entries[0], "fio": "Иванов И.И."}], w, w)),
    ("заметки", lambda: NotesDialog("ivanov", "user", w, w)),
    ("группы как у", lambda: GroupCompareDialog(entries[0], w, w)),
    ("история", lambda: HistoryDialog("ivanov", "WS-101", w)),
    ("пинг WS-101", lambda: PingDialog("WS-101", "10.0.2.11", w, w)),
    ("пинг WS-133 (офлайн)", lambda: PingDialog("WS-133", "10.0.2.33", w, w)),
]
opened = []
for name, f in dialogs:
    d = run_dialog(name, f)
    if d is not None:
        opened.append(d)
for d in opened:
    try:
        d.close()
    except Exception:  # noqa: BLE001
        pass
wait(300)
total = sum(len(i) for _, i in ALL)
print(f"ИТОГО замечаний: {total} (тема {THEME}, {FONT} pt, {W}x{H})")
json.dump({n: i for n, i in ALL if i}, open(f"/tmp/geo_{THEME}_{FONT}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
w.close()
