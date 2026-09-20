# -*- coding: utf-8 -*-
"""Прогресс перевода EN: все интерфейсные модули должны быть полностью обёрнуты в tr() и переведены.
Порог фиксированный — откат (новая непереведённая строка) роняет тест. F-строки не считаются:
их переводят разбиением на статичную часть + tr()."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adk.i18n import EN  # noqa: E402

MODULES = ["main_window.py", "dialogs.py", "health_ui.py", "fleet.py", "freeip_ui.py", "setup_ui.py",
           "tools.py", "inventory_ui.py", "pingui.py", "widgets.py", "notify.py", "attention_ui.py",
           "scan_ui.py"]
_LIT = r'"(?:[^"\\]|\\.)*"'
_UI_PAT = re.compile(r'(?:QPushButton|QLabel|QCheckBox|QRadioButton|setToolTip|setPlaceholderText|setText'
                     r'|QTableWidgetItem|addTab|setHorizontalHeaderLabels)\(\s*(?!tr\()(f?' + _LIT + r')')
_TR_PAT = re.compile(r'\btr\(\s*(' + _LIT + r'(?:\s+' + _LIT + r')*)\s*\)')


def _unwrapped(path: str) -> set[str]:
    out = set()
    for m in _UI_PAT.finditer(open(path, encoding="utf-8").read()):
        s = m.group(1)
        if s.startswith('f"'):
            continue
        try:
            v = eval(s)  # noqa: S307
        except Exception:  # noqa: BLE001
            continue
        if re.search(r"[А-Яа-яЁё]", v):
            out.add(v)
    return out


def _tr_literals(path: str) -> set[str]:
    out = set()
    for m in _TR_PAT.finditer(open(path, encoding="utf-8").read()):
        try:
            out.add(eval(m.group(1)))  # noqa: S307
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
        assert i18n.tr("такой строки нет — останется русской") == "такой строки нет — останется русской"
    finally:
        i18n.set_language(old)


def test_placeholders_match():
    """У каждого шаблона {N} перевод содержит те же плейсхолдеры — иначе .format() упадёт в EN."""
    import re as _re

    def ph(s: str) -> list[str]:
        return sorted(_re.findall(r"\{\d+(?::[^}]*)?\}", s))

    bad = [(k[:50], v[:50]) for k, v in EN.items() if ph(k) != ph(v)]
    assert not bad, bad


def test_fstring_templates_translated():
    """Ключевые сообщения прогресса переведены как шаблоны (f-строки стали tr(...).format(...))."""
    must = ["⚡ Опрос {0} ПК (DNS, ping, журналы входов)…", "⚡ Опрос ПК: {0}/{1}",
            "⏳ Парк сканирует {0} (до {1}) — база общая, повторный опрос не нужен",
            "Принтеры: опрашиваю {0} ПК в сети…", "Найдено: {0}", "Готово: {0}/{1} успешно"]
    for m in must:
        assert m in EN, m
