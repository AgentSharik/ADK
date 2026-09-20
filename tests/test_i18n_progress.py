# -*- coding: utf-8 -*-
"""Прогресс перевода EN: все интерфейсные модули должны быть полностью обёрнуты в tr() и переведены.

Порог фиксированный — откат (новая непереведённая строка) роняет тест.
Детектор строгий: (1) ловит ВСЕ кириллические литералы в UI-строках (списки заголовков,
addTab(widget, "…"), MessageBox-заголовки/тексты), а не только первый аргумент; (2) учитывает
многострочные tr("A" "B" "C") — сворачивает вызовы с сохранением номеров строк; (3) знает
r-литералы (tr(r"путь\\…")); (4) f-строки с кириллицой в UI-строках запрещены — их переводят
шаблоном tr("…{N}…").format(…). Комментарии и журналы (log.*) не переводятся — осознанно."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adk.i18n import EN  # noqa: E402

MODULES = ["main_window.py", "dialogs.py", "health_ui.py", "fleet.py", "freeip_ui.py", "setup_ui.py",
           "tools.py", "inventory_ui.py", "pingui.py", "widgets.py", "notify.py", "attention_ui.py",
           "scan_ui.py", "colorpicker.py"]
_LIT = r'r?"(?:[^"\\]|\\.)*"'
_TR_PAT = re.compile(r'\btr\(\s*(' + _LIT + r'(?:\s+' + _LIT + r')*)\s*\)', re.S)
# свёрнутый вызов: "tr()" + столько же переводов строк, сколько было внутри
_TR_COLLAPSE = re.compile(r'\btr\(\s*(?:' + _LIT + r'(?:\s+' + _LIT + r')*)\s*\)', re.S)

_UI_MARK = ("QPushButton", "QLabel", "QCheckBox", "QRadioButton", "setToolTip", "setPlaceholderText",
            "setText", "QTableWidgetItem", "addTab", "setHorizontalHeaderLabels", "setWindowTitle",
            "MessageBox.warning", "MessageBox.information", "MessageBox.critical", "MessageBox.question",
            "safe_rich", "QLineEdit", "QComboBox")
_FLIT = re.compile(r'\b[rf](' + r'"(?:[^"\\]|\\.)*"' + r')')
_PLAIN_LIT = re.compile(r'(?<![A-Za-z0-9_")\]])"(?:[^"\\]|\\.)*"')
_CYR = re.compile(r"[А-Яа-яЁё]")


def _src(path: str) -> str:
    return open(path, encoding="utf-8").read()


def _collapsed_lines(path: str) -> list[str]:
    """Исходник с tr(...) → 'tr()': построчный скан не видит содержимое обёрнутых строк."""
    def repl(m):
        return "tr()" + "\n" * m.group(0)[3:].count("\n")
    return _TR_COLLAPSE.sub(repl, _src(path)).split("\n")


def _unwrapped(path: str) -> set[str]:
    """Голые кириллические литералы (включая r-префикс) в строках с UI-вызовами."""
    out: set[str] = set()
    for line in _collapsed_lines(path):
        if line.lstrip().startswith("#") or not any(m in line for m in _UI_MARK):
            continue
        for m in _FLIT.finditer(line):
            try:
                v = eval("r" + m.group(1))
            except Exception:  # noqa: BLE001
                continue
            if _CYR.search(v):
                out.add(v)
        for m in _PLAIN_LIT.finditer(_FLIT.sub("", line)):
            try:
                v = eval(m.group(0))
            except Exception:  # noqa: BLE001
                continue
            if isinstance(v, str) and _CYR.search(v):
                out.add(v)
    return out


def _tr_literals(path: str) -> set[str]:
    out: set[str] = set()
    for m in _TR_PAT.finditer(_src(path)):
        parts = re.findall(_LIT, m.group(1))
        try:
            out.add("".join(eval(p) for p in parts))  # noqa: S307
        except Exception:  # noqa: BLE001
            continue
    return out


def test_all_modules_fully_wrapped():
    bad = {f: sorted(_unwrapped(f"adk/{f}")) for f in MODULES if _unwrapped(f"adk/{f}")}
    assert not bad, bad


def test_all_tr_translated():
    missing = {f: sorted(v for v in _tr_literals(f"adk/{f}") if v not in EN)[:5] for f in MODULES}
    missing = {f: v for f, v in missing.items() if v}
    assert not missing, missing


def test_translate_smoke_en():
    import adk.i18n as i18n
    old = i18n._LANG
    i18n.set_language("en")
    try:
        assert i18n.tr("⏹ Остановить") == "⏹ Stop"
        assert i18n.tr("✅ Прочитать всё") == "✅ Read all"
        assert i18n.tr("Внимание") == "Attention"
        assert i18n.tr("такой строки нет — останется русской") == "такой строки нет — останется русской"
    finally:
        i18n.set_language(old)


def test_placeholders_match():
    """У каждого шаблона {N} перевод содержит те же плейсхолдеры — иначе .format() упадёт в EN."""

    def ph(s: str) -> list[str]:
        return sorted(re.findall(r"\{\d+(?::[^}]*)?\}", s))

    bad = [(k[:50], v[:50]) for k, v in EN.items() if ph(k) != ph(v)]
    assert not bad, bad


def test_fstring_templates_translated():
    """Каждый шаблонный ключ реально форматируется в EN (спеки {0:.1f} и т.п. не роняют format)."""
    import adk.i18n as i18n

    old = i18n._LANG
    i18n.set_language("en")
    try:
        for k in EN:
            nums = re.findall(r"\{(\d+)((?::[^}]*)?)\}", k)
            if not nums:
                continue
            args = {int(i): (1 if "d" in spec else 1.5) for i, spec in nums}
            i18n.tr(k).format(*[args[i] for i in range(max(args) + 1)])
    finally:
        i18n.set_language(old)
