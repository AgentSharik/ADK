"""Окно «Пинг»: дизайн, график отклика, единая лента журнала (строки, фильтры, лимит, консольный текст), фильтр по
умолчанию «События», шрифт, офлайн-ПК и маркер ожидания ответа.

Воркер пинга подменяется через monkeypatch — реальная сеть не нужна.
"""

from PyQt6.QtWidgets import QApplication

from adk import netutils


# ------------------------------------------------------------------ 1. окно и график
def test_ping_dialog_new_design(qapp, monkeypatch):
    """Новое окно: график, счётчики, журнал событий; запись в БД только при смене состояния."""
    from adk import pingui
    from adk.dialogs import PingDialog

    class Silent(pingui.PingWorker):
        def run(self):
            return

    monkeypatch.setattr(pingui, "PingWorker", Silent)
    writes = []
    monkeypatch.setattr(pingui.db, "set_pc_online", lambda comp, ok: writes.append((comp, ok)))
    dlg = PingDialog("WS-1", "10.0.2.7", None)
    assert dlg.worker is not None and hasattr(dlg, "graph") and hasattr(dlg, "events") and not hasattr(dlg, "table")
    ev = lambda ms, ok=True: {"ip": "10.0.2.7", "bytes": "32", "time": f"{ms}мс" if ok else "—", "ttl": "128",  # noqa: E731
                              "status": "Ответ" if ok else "Превышен интервал ожидания", "success": ok, "is_info": False}
    dlg.on_event({"is_info": True, "status": "Обмен пакетами…"})
    for ms in (3, 5, 4):
        dlg.on_event(ev(ms))
    dlg.on_event(ev(0, ok=False))
    dlg.on_event(ev(6))
    assert (dlg.sent, dlg.recv) == (5, 4)
    assert dlg.stat["loss"].text() == "20%" and dlg.stat["min"].text() == "3" and dlg.stat["max"].text() == "6"
    assert dlg.graph.samples == [3.0, 5.0, 4.0, None, 6.0]
    assert dlg.lbl_now.text() == "6" and "отвечает" in dlg.lbl_state.text()
    from tests.test_gui import _wait
    _wait(lambda: len(writes) >= 3, qapp, 3000)                             # 3.5.5: запись в БД фоновая
    assert writes == [("WS-1", True), ("WS-1", False), ("WS-1", True)]      # только смены состояния
    assert dlg.events.count() >= 3
    rep = dlg.report_text()
    assert "WS-1" in rep and "потери 20%" in rep
    dlg.toggle_pause()
    dlg.on_event(ev(9))
    assert dlg.sent == 5           # на паузе замеры не считаются
    dlg.reset()
    assert dlg.sent == 0 and dlg.graph.samples == [] and dlg.events.count() == 0
    dlg.reject()


def test_latency_graph_paints(qapp):
    from PyQt6.QtGui import QColor
    from adk.pingui import LatencyGraph

    g = LatencyGraph()
    g.resize(400, 120)
    for s in (2, 4, None, 8):
        g.push(s)
    img = g.grab().toImage()
    colors = {QColor(img.pixel(x, y)).name() for x in range(50, 390, 7) for y in range(12, 108, 6)}
    assert len(colors) > 3       # столбики/линии/таймаут нарисованы, не сплошной фон


# ------------------------------------------------------------------ 2. журнал: лента, фильтры, лимит
def test_ping_journal_has_console_text_and_events(qapp, monkeypatch):
    """Журнал пинга: вкладка «Вывод ping» получает строки как в консоли, «События» — только смену состояния."""
    from adk import pingui
    from adk.pingui import PingDialog

    class NoWorker:  # реальный ping не запускаем
        def __init__(self, *a, **k): pass
        def start(self): pass
        def isRunning(self): return False
        def stop(self): pass
        def wait(self, *_): return True
        ping_event = error = type("S", (), {"connect": lambda *a, **k: None})()

    monkeypatch.setattr(pingui, "PingWorker", NoWorker)
    d = PingDialog("WS-1", "10.0.2.11", None)
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=3мс TTL=128", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.11"))
    raw = [d.raw.item(i).text() for i in range(d.raw.count())]
    assert any("Ответ от 10.0.2.11" in t and "TTL=128" in t for t in raw)
    assert any("Превышен интервал" in t for t in raw)
    assert d.sent == 2 and d.recv == 1
    d.close()


def _ping_dialog(monkeypatch):
    from adk import pingui
    from adk.pingui import PingDialog

    class NoWorker:
        def __init__(self, *a, **k): pass
        def start(self): pass
        def isRunning(self): return False
        def stop(self): pass
        def wait(self, *_): return True
        ping_event = error = type("S", (), {"connect": lambda *a, **k: None})()

    monkeypatch.setattr(pingui, "PingWorker", NoWorker)
    return PingDialog("WS-1", "10.0.2.11", None)


def test_ping_journal_rows_and_filter(qapp, monkeypatch):
    d = _ping_dialog(monkeypatch)
    d.on_event(netutils.parse_ping_line("Обмен пакетами с 10.0.2.11 по с 32 байтами данных:", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=3мс TTL=128", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=5мс TTL=128", "10.0.2.11"))
    kinds = [d.journal.item(r, 0).data(0x0100) for r in range(d.journal.rowCount())]
    # служебная строка, ответ, событие «узел отвечает», таймаут, событие «перестал», ответ, событие «снова»
    assert kinds.count("ok") == 2 and kinds.count("fail") == 1 and kinds.count("info") == 1 and "event" in kinds
    # столбцы: № / отклик / TTL заполнены только у ответов
    ok_row = kinds.index("ok")
    assert d.journal.item(ok_row, 1).text() == "1" and d.journal.item(ok_row, 4).text() == "3мс" and d.journal.item(ok_row, 5).text() == "128"
    fail_row = kinds.index("fail")
    assert "Превышен" in d.journal.item(fail_row, 3).text() and d.journal.item(fail_row, 4).text() == "—"
    # фильтр «Сбои и события» прячет ответы и служебные строки
    d._set_filter("bad")
    hidden = [d.journal.isRowHidden(r) for r in range(d.journal.rowCount())]
    assert all(h for h, k in zip(hidden, kinds) if k in ("ok", "info")) and not any(h for h, k in zip(hidden, kinds) if k in ("fail", "event"))
    assert "показано" in d.lbl_journal.text()
    d._set_filter("all")
    assert not any(d.journal.isRowHidden(r) for r in range(d.journal.rowCount()))
    # консольный текст сохраняется полностью, копирование работает
    raw = d.raw_text()
    assert "Ответ от 10.0.2.11: число байт=32 время=3мс TTL=128" in raw and "Превышен интервал" in raw
    d.copy_raw()
    assert QApplication.clipboard().text() == raw
    d.reset()
    assert d.journal.rowCount() == 0 and d.raw.count() == 0
    d.close()


def test_ping_journal_is_capped(qapp, monkeypatch):
    d = _ping_dialog(monkeypatch)
    for _ in range(650):
        d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=1мс TTL=128", "10.0.2.11"))
    assert d.journal.rowCount() <= 600 and d.raw.count() <= 600 and d.sent == 650
    d.close()


# ------------------------------------------------------------------ 3. фильтр по умолчанию и настройки
def test_ping_journal_always_opens_on_events(qapp, monkeypatch):
    """Окно пинга всегда открывается на «События» — даже если в прошлый раз выбрали «Все». Ответы и таймауты
    при этом скрыты, события видны; переключение на «Все» показывает всё."""
    from adk import netutils
    d = _ping_dialog(monkeypatch)
    assert d._filter == "events" and d.filter_btns["events"].isChecked()
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=3мс TTL=128", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.11"))
    kinds = [d.journal.item(r, 0).data(0x0100) for r in range(d.journal.rowCount())]
    hidden = [d.journal.isRowHidden(r) for r in range(d.journal.rowCount())]
    assert all(h for h, k in zip(hidden, kinds) if k in ("ok", "fail", "info"))
    assert any(not h for h, k in zip(hidden, kinds) if k == "event")
    d._set_filter("all")
    assert not any(d.journal.isRowHidden(r) for r in range(d.journal.rowCount()))
    d.close()
    d2 = _ping_dialog(monkeypatch)                  # новое окно — снова «События», выбор не тянется
    assert d2._filter == "events" and d2.filter_btns["events"].isChecked()
    d2.close()


def test_ping_journal_rows_share_one_font(qapp, monkeypatch):
    """Строка таймаута и строка ответа — одним шрифтом (семейство и размер); у события — только жирность."""
    from adk import netutils
    d = _ping_dialog(monkeypatch)
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.11: число байт=32 время=3мс TTL=128", "10.0.2.11"))
    d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.11"))
    fonts = {}
    for r in range(d.journal.rowCount()):
        it = d.journal.item(r, 3)
        fonts[it.data(0x0100)] = it.font()
    ok, fail, ev = fonts["ok"], fonts["fail"], fonts["event"]
    assert (ok.family(), ok.pointSize()) == (fail.family(), fail.pointSize()) == (ev.family(), ev.pointSize())
    assert ok.bold() == fail.bold() is False and ev.bold() is True
    d.close()


def _ping_dialog_shown(monkeypatch, target="10.0.2.33"):
    from adk import pingui

    class Quiet(pingui.PingWorker):
        def run(self):
            return

    monkeypatch.setattr(pingui, "PingWorker", Quiet)
    d = pingui.PingDialog("WS-133", target, None)
    d.show()
    return d


# ------------------------------------------------------------------ 4. шрифт, офлайн-ПК, маркер ожидания
def test_ping_journal_uses_window_font(qapp, monkeypatch):
    """Раньше журнал был моноширинным (Consolas) и выбивался из окна; теперь семейство шрифта — как у диалога."""
    d = _ping_dialog_shown(monkeypatch)
    assert d.journal.font().family() == QApplication.font().family()
    d.close()


def test_ping_offline_pc_reports_no_answer(qapp, monkeypatch):
    d = _ping_dialog_shown(monkeypatch)
    d.on_event(netutils.parse_ping_line("Обмен пакетами с 10.0.2.33 по с 32 байтами данных:", "10.0.2.33"))
    for _ in range(4):
        d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.33"))
    assert d.lbl_state.text() == "Узел не отвечает" and d.sent == 4 and d.recv == 0
    assert d.stat["loss"].text().startswith("100") and d.stat["avg"].text() == "—" and d.lbl_now.text() == "—"
    # событие «перестал отвечать» есть, «снова отвечает» — нет
    kinds = [(d.journal.item(r, 0).data(0x0100), d.journal.item(r, 3).text()) for r in range(d.journal.rowCount())]
    assert any(k == "event" and "перестал" in t for k, t in kinds) and not any("снова" in t for _, t in kinds)
    # а когда узел поднялся — состояние меняется и появляется «снова отвечает»
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.33: число байт=32 время=2мс TTL=128", "10.0.2.33"))
    assert d.lbl_state.text() == "Узел отвечает" and d.recv == 1
    assert any("снова отвечает" in d.journal.item(r, 3).text() for r in range(d.journal.rowCount()))
    d.close()


def test_ping_graph_shows_waiting_marker_when_no_answer_for_a_while(qapp, monkeypatch):
    """ping.exe на таймауте молчит до 4 с — раньше график просто замирал; теперь через 1 с появляется маркер ожидания."""
    from datetime import datetime, timedelta
    d = _ping_dialog_shown(monkeypatch)
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.33: число байт=32 время=2мс TTL=128", "10.0.2.33"))
    d._check_waiting()
    assert d.graph.waiting is False
    d._last_event = datetime.now() - timedelta(seconds=2)
    d._check_waiting()
    assert d.graph.waiting is True
    d.on_event(netutils.parse_ping_line("Превышен интервал ожидания для запроса.", "10.0.2.33"))
    assert d.graph.waiting is False                     # ответ (пусть и таймаут) пришёл — маркер снят
    d.toggle_pause()
    d._last_event = datetime.now() - timedelta(seconds=2)
    d._check_waiting()
    assert d.graph.waiting is False                     # на паузе не мигаем
    d.close()


def test_ping_parser_ipv6_and_gateway_replies():
    """3.5.5: у живого узла появлялись красные столбцы. Причины: ответ по IPv6 («Ответ от fe80::1%12: время<1мс»)
    без TTL и «байт» не подходил под шаблон успеха → считался потерей; «время<1мс» считалось ровно 1 мс.
    Ответ шлюза «Заданный узел недоступен» при этом по-прежнему НЕ успех."""
    from adk.pingui import PingDialog
    r = netutils.parse_ping_line("Ответ от fe80::a1b2:c3d4:e5f6:1%12: время<1мс", "fe80::1")
    assert r["success"] is True and r["ttl"] == "—" and r["time"] == "<1мс"
    assert PingDialog._ms(r["time"]) == 0.5 and PingDialog._ms("12мс") == 12.0 and PingDialog._ms("0.030ms") == 0.03
    r = netutils.parse_ping_line("Reply from 2001:db8::11: time=3ms", "x")
    assert r["success"] is True and r["time"] == "3ms"
    r = netutils.parse_ping_line("Ответ от 10.0.0.1: Заданный узел недоступен.", "10.0.2.11")
    assert r["success"] is False and r["ip"] == "10.0.0.1"
    for line in ("Статистика Ping для 10.0.2.11:", "    Пакетов: отправлено = 4, получено = 4, потеряно = 0",
                 "Приблизительное время приёма-передачи в мс:", "Approximate round trip times in milli-seconds:"):
        r = netutils.parse_ping_line(line, "x")
        assert r["is_info"] is True and r["success"] is None, line


def test_ping_state_change_writes_db_in_background(qapp, monkeypatch):
    """3.5.5: запись «в сети/не в сети» при смене состояния уходила в БД прямо из потока интерфейса; при занятой
    базе (сканер парка) db_execute_with_retry ждёт до 5 с — окно и график застывали. Теперь запись фоновая."""
    import threading
    from adk import pingui
    d = _ping_dialog_shown(monkeypatch)
    seen = []
    monkeypatch.setattr(pingui.db, "set_pc_online", lambda comp, ok: seen.append((comp, ok, threading.current_thread() is threading.main_thread())))
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.33: число байт=32 время=2мс TTL=128", "10.0.2.33"))
    from tests.test_gui import _wait
    assert _wait(lambda: seen, qapp, 3000)
    assert seen[0][1] is True and seen[0][2] is False       # записано, и не в главном потоке
    d.close()


def test_ping_waiting_marker_threshold_is_above_one_second(qapp, monkeypatch):
    """3.5.5: ответы живого узла идут раз в ~1,0–1,1 с; порог маркера «ждём ответ» был ровно 1,0 с и он мигал
    на каждом замере, будто связь пропадает. Порог поднят выше секунды."""
    from datetime import datetime, timedelta
    from adk.pingui import PingDialog
    assert PingDialog.WAIT_AFTER_S > 1.1
    d = _ping_dialog_shown(monkeypatch)
    d.on_event(netutils.parse_ping_line("Ответ от 10.0.2.33: число байт=32 время=2мс TTL=128", "10.0.2.33"))
    d._last_event = datetime.now() - timedelta(seconds=1.1)
    d._check_waiting()
    assert d.graph.waiting is False
    d.close()
