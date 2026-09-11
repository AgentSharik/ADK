"""E2E, раунд 4 (3.5.5): окно «Пинг» под нагрузкой — живой поток, честные столбцы, отзывчивость.

Запуск из корня проекта:
    QT_QPA_PLATFORM=offscreen python tests/e2e_round4.py

Что проверяем и почему (жалобы автора по видео 26):
  * «график застывал на зелёных сегментах» — окно не перерисовывалось, хотя связь была;
  * «у живого ПК появлялся красный столбец» — потеря там, где её не было.
Сценарии: поток ответов из отдельного потока с реальным временем (как ping.exe), медленный ответ 1,2 с,
IPv6-ответ без TTL, ответ шлюза «узел недоступен», строки статистики, занятая база (сканер парка),
пауза/сброс/фильтры под потоком, утечки потоков и убийство процесса.
"""
import sys, os, tempfile, types, time, threading  # noqa: E401
sys.path.insert(0, 'tests'); sys.path.insert(0, '.')
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
for n in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(n, types.ModuleType(n))
_home = tempfile.mkdtemp(prefix="admgr_e2e4_")
os.environ["HOME"] = _home; os.environ["USERPROFILE"] = _home
from adk import config, db, netutils, pingui  # noqa: E402
config.settings.db_path = os.path.join(_home, 'e2e4.db')
db.init_db()
import test_gui as tg  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import QThread  # noqa: E402
from adk.widgets import apply_theme, MessageBox  # noqa: E402
from adk.workers import PingWorker  # noqa: E402
app = QApplication([]); apply_theme(config.settings.design)
MessageBox._show = classmethod(lambda cls, *a, **k: cls.YES)
R = []
def check(name, cond, note=""):
    R.append((name, bool(cond), note)); print(("PASS " if cond else "FAIL ") + name + (f"  — {note}" if note else ""))

def wait(ms):
    tg._wait(lambda: False, app, ms)

db.batch_update_inventory([{"Hostname": "WS-101", "ActualIp": "10.0.2.11", "Status": "ACTIVE", "User": "ivanov"}], "2026-09-10 10:00:00")


class ScriptedPing(PingWorker):
    """Поток, который выдаёт строки ping по расписанию (реальное время, как ping.exe)."""
    script: list[tuple[float, str]] = []      # (пауза перед строкой, строка)

    def run(self):
        for delay, line in self.script:
            t_end = time.monotonic() + delay
            while time.monotonic() < t_end:
                if self.cancelled:
                    return
                time.sleep(0.02)
            if self.cancelled:
                return
            parsed = netutils.parse_ping_line(line, self.target)
            if parsed:
                self.ping_event.emit(parsed)
        while not self.cancelled:                 # как -t: висит, пока не закроют
            time.sleep(0.05)


def ok_line(ip, ms):
    return f"Ответ от {ip}: число байт=32 время={ms}мс TTL=128"


# ---------------------------------------------------------------- A. живой узел: ни одного красного столбца
print("=== A. Живой узел (ответы раз в секунду, один медленный 1,2 с) ===")
ScriptedPing.script = [(0.0, "Обмен пакетами с WS-101 [10.0.2.11] с 32 байтами данных:")] + \
    [(0.25, ok_line("10.0.2.11", 1 + i % 3)) for i in range(6)] + \
    [(1.2, ok_line("10.0.2.11", 1180))] + [(0.25, ok_line("10.0.2.11", 2)) for _ in range(4)]
pingui.PingWorker = ScriptedPing
d = pingui.PingDialog("WS-101", "10.0.2.11", None); d.show()
paints = []
_orig_paint = pingui.LatencyGraph.paintEvent
def _spy_paint(self, e):
    paints.append(time.monotonic()); _orig_paint(self, e)
pingui.LatencyGraph.paintEvent = _spy_paint
tg._wait(lambda: d.sent >= 11, app, 8000)
check("11 ответов — все зелёные (None в samples нет)", d.sent == 11 and d.recv == 11 and None not in d.graph.samples, f"sent={d.sent} recv={d.recv} samples={d.graph.samples}")
check("потери 0 %, состояние «отвечает»", d.stat["loss"].text() == "0%" and "отвечает" in d.lbl_state.text() and "не" not in d.lbl_state.text().lower().split("узел")[-1][:4], d.lbl_state.text())
check("медленный ответ 1,2 с — это замер (1180 мс), а не потеря", 1180.0 in d.graph.samples and d.stat["max"].text() == "1180")
check("график перерисовывался при каждом ответе (paint ≥ 11)", len(paints) >= 11, f"paint={len(paints)}")
check("маркер «ждём ответ» не висит при живом потоке", d.graph.waiting is False)
check("журнал: 11 строк ok + 1 info, событий «перестал отвечать» нет",
      sum(1 for r in range(d.journal.rowCount()) if d.journal.item(r, 0).data(0x0100) == "ok") == 11
      and not any("перестал" in d.events.item(i).text() for i in range(d.events.count())))
d.reject(); tg._wait(lambda: not d.worker.isRunning(), app, 3000)

# ---------------------------------------------------------------- B. отзывчивость: занятая база не держит окно
print("\n=== B. Занятая БД (сканер парка держит блокировку) — окно не застывает ===")
import sqlite3  # noqa: E402
lock_conn = sqlite3.connect(config.settings.db_path, timeout=0.1, isolation_level=None)
lock_conn.execute("BEGIN IMMEDIATE")            # эксклюзивная запись: set_pc_online будет ждать retry
ScriptedPing.script = [(0.0, "Обмен пакетами с WS-101 [10.0.2.11] с 32 байтами данных:")] + \
    [(0.2, ok_line("10.0.2.11", 3)) for _ in range(5)]
d = pingui.PingDialog("WS-101", "10.0.2.11", None); d.show()
t0 = time.monotonic()
tg._wait(lambda: d.sent >= 5, app, 6000)
dt = time.monotonic() - t0
check(f"5 ответов дошли за {dt:.1f} с (< 2,5 с), хотя база заблокирована", d.sent == 5 and dt < 2.5, f"{dt:.2f} c")
gaps = []
for _ in range(6):
    t = time.monotonic(); app.processEvents(); wait(50); gaps.append(time.monotonic() - t)
check("цикл событий не блокируется записью в БД (макс. пауза < 0,4 с)", max(gaps) < 0.4, f"max gap {max(gaps):.2f} c")
lock_conn.execute("ROLLBACK"); lock_conn.close()
d.reject(); tg._wait(lambda: not d.worker.isRunning(), app, 3000)
wait(1500)   # фоновая запись доживает после снятия блокировки
row = db.db_execute_with_retry("SELECT is_online FROM pc_inventory WHERE computer_name='WS-101'", fetch="one")
check("после снятия блокировки статус всё же записан (is_online=1)", row and row[0] == 1, str(row))

# ---------------------------------------------------------------- C. форматы ответов, которые раньше красили в красный
print("\n=== C. Форматы строк ping ===")
cases = {
    "IPv6 без TTL и байт (ru)": ("Ответ от fe80::a1b2:c3d4:e5f6:1%12: время<1мс", True),
    "IPv6 (en)": ("Reply from 2001:db8::11: time=3ms", True),
    "IPv4 «время<1мс»": ("Ответ от 10.0.2.11: число байт=32 время<1мс TTL=128", True),
    "IPv4 en": ("Reply from 10.0.2.11: bytes=32 time=12ms TTL=64", True),
    "linux с именем узла": ("64 bytes from ws-101 (10.0.0.9): icmp_seq=1 ttl=128 time=1.2 ms", True),
    "ответ шлюза «недоступен» — НЕ успех": ("Ответ от 10.0.0.1: Заданный узел недоступен.", False),
    "таймаут ru": ("Превышен интервал ожидания для запроса.", False),
    "таймаут en": ("Request timed out.", False),
    "общий сбой": ("Сбой передачи. General failure.", False),
}
for name, (line, exp) in cases.items():
    r = netutils.parse_ping_line(line, "x")
    check(f"{name}", r is not None and r["success"] is exp and not r["is_info"], str(r))
for line in ("Статистика Ping для 10.0.2.11:", "    Пакетов: отправлено = 4, получено = 4, потеряно = 0",
             "Приблизительное время приёма-передачи в мс:", "    Минимальное = 1мсек, Максимальное = 2 мсек, Среднее = 1 мсек",
             "Ping statistics for 10.0.2.11:", "Approximate round trip times in milli-seconds:"):
    r = netutils.parse_ping_line(line, "x")
    check(f"служебная строка не считается потерей: «{line.strip()[:40]}»", r["is_info"] is True and r["success"] is None, str(r))

# ---------------------------------------------------------------- D. IPv6-узел целиком через окно
print("\n=== D. IPv6-узел в окне: зелёный, TTL «—» ===")
ScriptedPing.script = [(0.0, "Обмен пакетами с WS-101 [fe80::1%12] с 32 байтами данных:")] + \
    [(0.15, "Ответ от fe80::1%12: время<1мс") for _ in range(4)]
d = pingui.PingDialog("WS-101", "fe80::1%12", None); d.show()
tg._wait(lambda: d.sent >= 4, app, 5000)
check("IPv6: 4 ответа, 0 потерь, «узел отвечает»", d.sent == 4 and d.recv == 4 and "не отвечает" not in d.lbl_state.text(), f"{d.sent}/{d.recv} {d.lbl_state.text()}")
check("IPv6: столбцы зелёные (0,5 мс за «<1мс»)", d.graph.samples == [0.5] * 4, str(d.graph.samples))
d.reject(); tg._wait(lambda: not d.worker.isRunning(), app, 3000)

# ---------------------------------------------------------------- E. падение и восстановление связи, маркер ожидания
print("\n=== E. Обрыв и восстановление; маркер ожидания только при тишине ===")
ScriptedPing.script = [(0.0, "Обмен пакетами с WS-101 [10.0.2.11] с 32 байтами данных:")] + \
    [(0.1, ok_line("10.0.2.11", 2)) for _ in range(3)] + [(2.2, "Превышен интервал ожидания для запроса.")] + \
    [(0.1, ok_line("10.0.2.11", 2)) for _ in range(3)]
d = pingui.PingDialog("WS-101", "10.0.2.11", None); d.show()
tg._wait(lambda: d.sent >= 3, app, 4000)
wait(1900)      # тишина 1,9 с > WAIT_AFTER_S — должен появиться маркер, но НЕ красный столбец
check("во время тишины: маркер ожидания есть, красного столбца нет", d.graph.waiting is True and None not in d.graph.samples, f"waiting={d.graph.waiting} samples={d.graph.samples}")
tg._wait(lambda: d.sent >= 7, app, 4000)
check("после таймаута — ровно один красный столбец, затем снова зелёные", d.graph.samples.count(None) == 1 and d.graph.samples[-1] == 2.0, str(d.graph.samples))
ev = [d.events.item(i).text() for i in range(d.events.count())]
check("события: «перестал отвечать» и «снова отвечает» по одному разу", sum("перестал" in e for e in ev) == 1 and sum("снова" in e for e in ev) == 1, str(ev))
check("потери 14 % (1 из 7)", d.stat["loss"].text() == "14%", d.stat["loss"].text())
d.reject(); tg._wait(lambda: not d.worker.isRunning(), app, 3000)

# ---------------------------------------------------------------- F. пауза/сброс/фильтры под живым потоком
print("\n=== F. Пауза, сброс и фильтры под потоком ===")
ScriptedPing.script = [(0.0, "Обмен пакетами с WS-101 [10.0.2.11] с 32 байтами данных:")] + \
    [(0.12, ok_line("10.0.2.11", 4)) for _ in range(30)]
d = pingui.PingDialog("WS-101", "10.0.2.11", None); d.show()
tg._wait(lambda: d.sent >= 4, app, 4000)
d.toggle_pause(); n = d.sent; wait(600)
check("пауза: счётчик не растёт, маркер ожидания не мигает", d.sent == n and d.graph.waiting is False, f"{n}→{d.sent}")
d.toggle_pause(); tg._wait(lambda: d.sent > n, app, 3000)
check("после паузы поток продолжается", d.sent > n)
d.reset(); wait(400)
check("сброс под потоком: статистика с нуля и снова копится", 0 < d.sent < 8 and d.graph.samples and d.events.count() == 0, f"sent={d.sent}")
d._set_filter("all"); shown_all = sum(1 for r in range(d.journal.rowCount()) if not d.journal.isRowHidden(r))
d._set_filter("events"); shown_ev = sum(1 for r in range(d.journal.rowCount()) if not d.journal.isRowHidden(r))
check("фильтр «Все» показывает строки, «События» — только события", shown_all >= 1 and shown_ev <= shown_all)
rep = d.report_text()
check("отчёт содержит узел, отправлено/получено, потери", "WS-101" in rep and "потери" in rep, rep.replace("\n", " | ")[:120])
d.reject(); tg._wait(lambda: not d.worker.isRunning(), app, 3000)

# ---------------------------------------------------------------- G. реальный ping-процесс: аргументы и остановка
print("\n=== G. Реальный PingWorker ===")
pingui.PingWorker = PingWorker
active_before = threading.active_count()
w = PingWorker("127.0.0.1")
lines, errs = [], []
w.ping_event.connect(lines.append); w.error.connect(errs.append)
w.start(); tg._wait(lambda: lines or errs, app, 4000); wait(300)
w.stop()
check("ping-процесс останавливается по stop() (поток завершён)", not w.isRunning())
check("либо строки ping, либо внятная ошибка запуска (в песочнице ping может быть запрещён)", bool(lines) or (errs and "ping" in errs[0].lower()), f"lines={len(lines)} errs={errs[:1]}")
bad = PingWorker("WS-1; calc"); e2 = []; bad.error.connect(e2.append); bad.run()
check("недопустимое имя узла отклоняется до запуска процесса", e2 and "Недопустимое" in e2[0], str(e2))
import inspect  # noqa: E402
src = inspect.getsource(PingWorker.run)
check("Windows: таймаут ping 4000 мс (как в cmd), не 1000", '"-w", "4000"' in src and '"1000"' not in src)
wait(500)
check("потоки не текут", threading.active_count() <= active_before + 1, f"{active_before} → {threading.active_count()}")
check("QThread живых окон пинга нет", not any(isinstance(t, QThread) and t.isRunning() for t in app.findChildren(QThread)))

print("\n=== ИТОГ ===")
passed = sum(1 for _, ok, _ in R if ok)
print(f"{passed}/{len(R)} проверок пройдено")
for name, ok, note in R:
    if not ok: print(f"  FAIL: {name} {note}")
