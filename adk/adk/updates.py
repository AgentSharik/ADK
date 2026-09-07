"""Проверка новой версии по файлу на сетевом диске (``[Updates] version_file``).

Формат файла: первая строка — версия (``3.1.0``), вторая (необязательно) — путь/URL, где лежит
новая сборка. Никакой автоустановки: показываем баннер, по клику открываем путь.
"""
from __future__ import annotations

import logging
import os
import re

from . import __version__
from .config import settings

log = logging.getLogger(__name__)


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


def read_version_file(path: str) -> tuple[str, str]:
    """→ (версия, путь-к-сборке). Пустые строки — если файла нет/нечитаем."""
    if not path or not os.path.exists(path):
        return "", ""
    try:
        lines = [l.strip() for l in open(path, encoding="utf-8-sig").read().splitlines() if l.strip()]
    except OSError as exc:
        log.debug("version_file: %s", exc)
        return "", ""
    return (lines[0] if lines else ""), (lines[1] if len(lines) > 1 else os.path.dirname(path))


def check() -> dict | None:
    """→ {"version", "location"} если доступна более новая версия, иначе None. Безопасно звать в фоне."""
    remote, where = read_version_file(settings.version_file)
    if remote and is_newer(remote):
        return {"version": remote, "location": where}
    return None
