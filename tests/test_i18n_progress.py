# -*- coding: utf-8 -*-
"""Прогресс перевода EN (партия «полный перевод»): главное окно должно быть полностью обёрнуто в tr()
и переведено. Порог двигаем вперёд по мере перевода остальных модулей — откат ниже порога — ошибка."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adk.i18n import EN  # noqa: E402

_UI_PAT = re.compile(r'(?:QPushButton|QLabel|QCheckBox|QRadioButton|setToolTip|setPlaceholderText|setText'
                     r'|QTableWidgetItem|addTab|setHorizontalHeaderLabels)\(\s*(?!tr\()(f?"(?:[^"\\]|\\.)*")')

# строки, пока не обёрнутые осознанно (многострочная склейка-подсказка и короткий ответ диалога)
ALLOWLIST = {"да", "Принтеры берутся из инвентарных CSV. Доступность — TCP 9100/631/80, затем ping. «Проверка» при "}


def _unwrapped(path: str) -> set[str]:
    src = open(path, encoding="utf-8").read()
    out = set()
    for m in _UI_PAT.finditer(src):
        s = m.group(1)
        if s.startswith('f"'):
            continue
        try:
            v = eval(s)  # noqa: S307
        except Exception:  # noqa: BLE001
            continue
        if re.search(r"[А-Яа-яЁё]", v) and v not in ALLOWLIST:
            out.add(v)
    return out


def _tr_literals(path: str) -> set[str]:
    src = open(path, encoding="utf-8").read()
    out = set()
    for m in re.finditer(r'\btr\(\s*(f?"(?:[^"\\]|\\.)*")\s*\)', src):
        s = m.group(1)
        if s.startswith('f"'):
            continue
        try:
            out.add(eval(s))  # noqa: S307
        except Exception:  # noqa: BLE001
            pass
    return out


def test_main_window_fully_wrapped():
    assert _unwrapped("adk/main_window.py") == set(), _unwrapped("adk/main_window.py")


def test_main_window_tr_translated():
    missing = _tr_literals("adk/main_window.py") - set(EN)
    assert not missing, sorted(missing)[:10]


def test_notify_translated():
    missing = _tr_literals("adk/notify.py") - set(EN)
    assert not missing, sorted(missing)[:10]
