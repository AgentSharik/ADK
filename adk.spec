# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller: ``pyinstaller adk.spec`` → dist/ADK/ADK.exe (onedir, без консоли).

Onedir выбран сознательно: стартует быстрее onefile (PyQt6 не распаковывается во временную папку
при каждом запуске) и реже ложно срабатывает у антивирусов.
"""
import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
ROOT = os.path.dirname(os.path.abspath(SPEC))
icon = os.path.join(ROOT, "assets", "icon.ico")

hidden = (
    collect_submodules("ldap3")            # протокол/расширения подключаются динамически
    + collect_submodules("pyasn1")
    + collect_submodules("keyring.backends")
    + ["openpyxl", "openpyxl.styles", "openpyxl.utils", "cryptography"]   # QR-кодов и PIL в ADK нет — лишнее в exe не тянем
    + ["win32crypt", "win32timezone", "winkerberos"]  # на не-Windows отсутствуют — PyInstaller просто предупредит
)

a = Analysis(
    [os.path.join(ROOT, "run.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "config.example.ini"), "."), (os.path.join(ROOT, "assets", "icon.png"), "assets"),
           (os.path.join(ROOT, "assets", "logo.png"), "assets"),
           (os.path.join(ROOT, "assets", "fonts"), os.path.join("assets", "fonts"))],   # встроенный шрифт интерфейса
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PIL", "pytest", "PyQt6.QtWebEngineCore",
              "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick", "PyQt6.Qt3DCore", "PyQt6.QtMultimedia"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ADK",
    debug=False,
    strip=False,
    upx=False,                       # UPX + Qt = ложные срабатывания антивирусов
    console=False,                   # GUI без чёрного окна; логи — в Documents/ADK/adk.log
    icon=icon if os.path.exists(icon) else None,
    version=os.path.join(ROOT, "version_info.txt") if os.path.exists(os.path.join(ROOT, "version_info.txt")) else None,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="ADK")
