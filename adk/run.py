"""Запуск без установки пакета: ``python run.py`` (удобно для PyCharm — правый клик → Run 'run').

Эквивалентно ``python -m adk``. Добавляет корень проекта в sys.path, поэтому работает
из любой рабочей директории и внутри PyInstaller-сборки.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adk.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
