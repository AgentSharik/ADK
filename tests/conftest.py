import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PyQt6.QtCore import qInstallMessageHandler
    qInstallMessageHandler(lambda t, c, m: None if "This plugin does not support" in m or "propagateSizeHints" in m else sys.stderr.write(f"{m}\n"))
except Exception:
    pass

# pywin32 нужен только на Windows — подменяем, чтобы тесты шли везде
for name in ("pythoncom", "win32com", "win32com.client", "win32crypt"):
    sys.modules.setdefault(name, type(sys)(name))


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    from adk import config, db

    monkeypatch.setattr(config.settings, "db_path", str(tmp_path / "test.db"))
    # окно «Роль и права доступа» после входа в тестах не показываем (иначе всплывает поверх главного окна);
    # его собственные тесты включают показ явно
    monkeypatch.setattr(config.settings, "hide_role_welcome", True)
    db.init_db()
    yield


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from adk import icons
    icons.install()          # как в приложении: эмодзи в текстах превращаются в иконки
    yield app
