import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pywin32 нужен только на Windows — подменяем, чтобы тесты шли везде
for name in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(name, type(sys)(name))


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    from adk import config, db

    monkeypatch.setattr(config.settings, "db_path", str(tmp_path / "test.db"))
    db.init_db()
    yield


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from adk import icons
    icons.install()          # как в приложении: эмодзи в текстах превращаются в иконки
    yield app
