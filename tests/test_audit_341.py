"""Регрессионные тесты к исправлениям 3.4.1 (полный аудит: логика, наложения, визуальные и функциональные баги).

Каждый тест — «дано → делаем → ожидаем» на примерах из приложения, чтобы найденный баг не вернулся.
"""
from types import SimpleNamespace

from adk import ad, config, netutils, theme
from tests.test_gui import _main, fake_conn

__all__ = ["fake_conn"]   # фикстура из test_gui нужна тестам ниже


def _palette(key: str) -> theme.Palette:
    t = theme.PRESET_THEMES[key]
    return theme.Palette(bool(t["is_dark"]), t["accent"], t.get("panel", ""), t.get("text", ""), "")


# ------------------------------------------------------------------ 1. карта подсети: неизвестный статус
def test_subnet_map_ignores_unknown_status(qapp):
    """Дано: воркер прислал статус, о котором карта не знает («cancelled» или опечатка).
    Раньше paintEvent падал с KeyError и окно «Свободный IP» переставало перерисовываться."""
    from adk.freeip_ui import SubnetMap
    m = SubnetMap()
    m.resize(1100, 320)
    m.mark(5, "free")
    m.mark(6, "какой-то-новый-статус")
    m.show()
    qapp.processEvents()
    m.repaint()                     # не должно бросить исключение
    m.close()


# ------------------------------------------------------------------ 2. «Быстрый доступ» переносится, а не режется
def test_quick_access_uses_flow_layout(qapp, fake_conn, monkeypatch):
    from adk.widgets import FlowLayout
    w = _main(qapp, fake_conn, monkeypatch)
    buttons = [b for b in w.findChildren(type(w.btn_notes)) if b.parentWidget() is not None
               and isinstance(b.parentWidget().layout(), FlowLayout)]
    assert len(buttons) >= 6, "кнопки быстрого доступа лежат в FlowLayout — при узком окне переносятся на новую строку"
    w.close()


# ------------------------------------------------------------------ 3. чек-лист регистрации не «раздувает» окно
def test_register_checklist_keeps_plain_captions(qapp, monkeypatch):
    """Раньше подпись бралась из text() — а туда патч иконок уже подставил HTML <img>. На каждом _refresh
    HTML накапливался, и диалог вырастал до десятков тысяч пикселей в ширину."""
    from adk.dialogs import RegisterUserDialog

    class _NoConn:
        entries = []
        result = {"controls": {}}

        def search(self, *a, **k): return True
        def unbind(self): return True
    app = SimpleNamespace(get_conn=lambda: _NoConn(), admin_name="admin")
    d = RegisterUserDialog(app)
    for _ in range(30):
        d.surname.setText("Иванов"); d.surname.setText("Иванов ")
        d._refresh()
    for key, lb in d.checks.items():
        assert lb.text().count("<img") <= 1, f"подпись «{key}» накапливает иконки: {lb.text()[:80]}"
    d.adjustSize()
    assert d.sizeHint().width() < 1600
    d.close()


# ------------------------------------------------------------------ 4. организация в инвентаре — из данных, не из текста
def test_inventory_company_stored_in_user_role(qapp, monkeypatch, tmp_path):
    from adk import db
    monkeypatch.setattr(config.settings, "db_path", str(tmp_path / "inv.db"))
    db.init_db()
    from adk.inventory_ui import InventoryDialog
    d = InventoryDialog(SimpleNamespace(admin_name="admin"), None)
    d.companies = ["ООО Ромашка", "АО Василёк"]
    d._apply_filter("")
    d.list.setCurrentRow(0)
    assert d.selected_company() == "ООО Ромашка"          # чистое имя, без иконки/HTML
    d.close()


# ------------------------------------------------------------------ 5. длинный текст MessageBox не режется кнопками
def test_messagebox_grows_for_long_text(qapp):
    from adk.widgets import MessageBox
    long = "Строка сообщения об ошибке подключения к контроллеру домена. " * 12
    mb = MessageBox(None, "Ошибка", long, "critical")
    mb.show()
    qapp.processEvents()
    lbl = mb._msg_lbl
    assert lbl.height() >= lbl.heightForWidth(lbl.width()) - 2, "надпись показана целиком"
    assert mb.height() > 170, "диалог вырос под текст"
    mb.close()


# ------------------------------------------------------------------ 6. смена темы перекрашивает уже открытые окна
def test_retheme_remaps_inline_palette_colours(qapp):
    """Дано: подпись покрашена inline в цвет subtext тёмной темы. Делаем retheme на светлую.
    Ожидаем: цвет стал subtext светлой темы, а цвет success (другая роль) — success светлой темы."""
    from PyQt6.QtWidgets import QLabel, QWidget
    from adk.widgets import retheme
    dark, light = _palette("dark"), _palette("light")
    root = QWidget()
    a = QLabel("a", root); a.setStyleSheet(f"color: {dark.subtext}; background: {dark.card};")
    b = QLabel("b", root); b.setStyleSheet(f"color: {dark.success[0]};")
    retheme(root, dark.color_map(), light.color_map())
    assert light.subtext.lower() in a.styleSheet().lower() and light.card.lower() in a.styleSheet().lower()
    assert light.success[0].lower() in b.styleSheet().lower()
    assert dark.subtext.lower() not in a.styleSheet().lower() or dark.subtext.lower() == light.subtext.lower()


def test_palette_color_map_covers_all_roles():
    m = _palette("dark").color_map()
    for k in ("text", "subtext", "card", "border", "accent", "success0", "danger0", "warning0", "info0", "neutral0"):
        assert k in m and m[k].startswith("#")


# ------------------------------------------------------------------ 7. даты AD: 1601/9999 — это «никогда»
class _Attr:
    def __init__(self, v): self.value = v; self.values = [v]


class _Entry:
    def __init__(self, **a): self._a = {k: _Attr(v) for k, v in a.items()}
    def __getitem__(self, k): return self._a[k]


def test_ad_datetime_treats_1601_and_9999_as_never():
    """ldap3 при известной схеме сам превращает lockoutTime=0 в 1601-01-01, а accountExpires=«никогда» — в 9999 год.
    Раньше такой пользователь считался «заблокирован с 01.01.1601» и «истекает 31.12.9999»."""
    from datetime import datetime, timezone
    e = _Entry(lockoutTime=datetime(1601, 1, 1, tzinfo=timezone.utc),
               accountExpires=datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc), userAccountControl=512)
    assert ad.get_ad_datetime(e, "lockoutTime") is None
    assert ad.get_ad_datetime(e, "accountExpires") is None
    assert ad.account_inactive_reason(e) == "" and ad.account_badge(e) == ("Активна", "active")


# ------------------------------------------------------------------ 8. порт принтера с «невозможным» IP
def test_printer_port_with_invalid_octet_is_not_network_ip():
    kind, ip = netutils.classify_printer_port("IP_192.168.1.300")
    assert ip == "", "192.168.1.300 — не адрес; иначе принтер попадал бы в поиск по IP с мусорным значением"
    assert kind == "network"
    assert netutils.classify_printer_port("IP_10.0.2.50") == ("network", "10.0.2.50")


# ------------------------------------------------------------------ 9. дашборд честно объясняет разницу AD/инвентарь
def test_dashboard_explains_unscanned_pcs(qapp, fake_conn, monkeypatch, tmp_path):
    from adk import db
    monkeypatch.setattr(config.settings, "db_path", str(tmp_path / "d.db"))
    db.init_db()
    db.db_execute_with_retry(
        "INSERT OR REPLACE INTO pc_inventory (computer_name, ip_address, is_online, last_checked) VALUES (?,?,?,?)",
        ("WS-1", "10.0.2.11", 1, "2026-09-05 10:00:00"))
    from PyQt6.QtWidgets import QLabel
    w = _main(qapp, fake_conn, monkeypatch)
    w.active_ad_total = 4
    w.refresh_dashboard()
    qapp.processEvents()
    texts = [lb.text() for i in range(w.cards.count()) if w.cards.itemAt(i).widget()
             for lb in w.cards.itemAt(i).widget().findChildren(QLabel)]
    assert w.dashboard_counts == {"online": 1, "offline": 3, "total": 4}
    assert any("не сканирован" in t for t in texts), texts
    w.close()
