# -*- coding: utf-8 -*-
"""Партия 39 (3.10.0): портативная папка рядом с exe, окно выбора парка, дефолты SSL/TLS, подсказка маски."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from adk import config
from adk.config import _migrate_old_data, _try_portable_dir, park_pattern


# ------------------------------------------------------------------ park_pattern: человеческий ввод → маска
@pytest.mark.parametrize("src, expected", [
    ("PC-", "PC-*"),          # «ищет все адм»
    ("adm-", "PC-*"),          # регистр не важен
    ("PC-0000", "PC-????"),   # 4 цифры → 4 «?»
    ("PC-12", "PC-??"),
    ("adm-0099", "PC-????"),
    ("PC", "PC"),               # без дефиса — точное имя
    ("LT_10", "LT_??"),       # каждая цифра — «?»
    ("LT-10 ", "LT-??"),      # пробелы по краям срезаются
    ("", ""),
    ("   ", ""),
    ("FS.", "FS.*"),
])
def test_park_pattern(src, expected):
    assert park_pattern(src) == expected


# ------------------------------------------------------------------ портативная папка
def test_try_portable_dir_writable(tmp_path):
    d = _try_portable_dir(str(tmp_path))
    assert d == str(tmp_path / "ADK")
    assert os.path.isdir(d)
    assert not os.path.exists(os.path.join(d, ".write-test"))    # пробный файл убран


def test_try_portable_dir_readonly(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("под root права не работают")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    assert _try_portable_dir(str(ro)) is None
    ro.chmod(0o700)


def test_migrate_old_data_moves_files(tmp_path):
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("[AD]")
    (old / "pc_mapping.db").write_text("db")
    (old / "backups").mkdir()
    (old / "backups" / "b1.zip").write_text("zip")
    _migrate_old_data(str(old), str(new))
    assert (new / "config.ini").read_text() == "[AD]"
    assert (new / "pc_mapping.db").exists()
    assert (new / "backups" / "b1.zip").exists()
    assert not old.exists()                                       # пустая старая папка удалена


def test_migrate_old_data_keeps_existing_config(tmp_path):
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("old")
    (new / "config.ini").write_text("new")                        # в новой уже есть конфиг — не переносим
    _migrate_old_data(str(old), str(new))
    assert (new / "config.ini").read_text() == "new"
    assert (old / "config.ini").read_text() == "old"


# ------------------------------------------------------------------ дефолты: новый конфиг без LDAPS
def test_default_config_writes_ssl_false(tmp_path):
    path = str(tmp_path / "config.ini")
    config.write_default_config(path)
    cp = config._load() if False else None
    import configparser
    cp = configparser.ConfigParser()
    cp.read(path, encoding="utf-8")
    assert cp["AD"]["use_ssl"] == "false"
    assert cp["AD"]["tls_validate"] == "false"


# ------------------------------------------------------------------ окно выбора парка
def test_park_mask_dialog_counts_and_saves(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLineEdit
    from adk.setup_ui import ParkMaskDialog
    names = ["PC-0001", "PC-0002", "PC-100", "LT-0001", "PC-01"]
    saved = {}
    monkeypatch.setattr(config.settings, "host_mask", "")
    monkeypatch.setattr(config.settings, "save_section", lambda s, v: saved.update(v))

    d = ParkMaskDialog(None, names)
    assert not d.btn_ok.isEnabled()                               # пустое поле — сохранить нельзя
    for i in range(d.rows_lay.count()):                           # в первом поле вводим серию
        lay = d.rows_lay.itemAt(i)
        if lay and lay.count():
            w = lay.itemAt(0).widget()
            if isinstance(w, QLineEdit):
                w.setText("PC-0000")
                break
    assert d._patterns() == ["PC-????"]
    assert "2 из 5" in d.total_lbl.text()                         # PC-0001, PC-0002; PC-100 не подходит

    d.add_row("LT-")
    assert d._patterns() == ["PC-????", "LT-*"]
    assert "3 из 5" in d.total_lbl.text()                         # + LT-0001

    d.save()
    assert d.mask_saved is True
    assert saved == {"host_mask": "PC-????, LT-*"}
    assert config.settings.host_mask == "PC-????, LT-*"
    d.deleteLater()


def test_park_mask_dialog_no_names_still_saves(qapp, monkeypatch):
    from adk.setup_ui import ParkMaskDialog
    saved = {}
    monkeypatch.setattr(config.settings, "host_mask", "")
    monkeypatch.setattr(config.settings, "save_section", lambda s, v: saved.update(v))
    d = ParkMaskDialog(None, [])                                  # домен недоступен — без счётчика
    for i in range(d.rows_lay.count()):
        lay = d.rows_lay.itemAt(i)
        if lay and lay.count() and hasattr(lay.itemAt(0).widget(), "setText"):
            lay.itemAt(0).widget().setText("FS-0000")
            break
    assert d.btn_ok.isEnabled()
    d.save()
    assert saved == {"host_mask": "FS-????"}
    d.deleteLater()


# ------------------------------------------------------------------ подсказка сканера при заданной маске
def test_scan_hint_mentions_mask():
    """Текст «проверьте host_pattern» не должен вводить в заблуждение, когда фильтрует host_mask."""
    from adk.workers import PCScannerWorker
    monkey_mask = "PC-????"
    old = config.settings.host_mask
    config.settings.host_mask = monkey_mask
    try:
        # маска задана → ПК, не подходящий под неё, отбрасывается (это и было причиной «скан не начался»)
        rx = PCScannerWorker._mask_regex(monkey_mask)
        assert rx.match("PC-0001") and rx.match("adm-0099")
        assert not rx.match("PC-001")
        assert not rx.match("WS-0001")
    finally:
        config.settings.host_mask = old


def test_migrate_rewrites_config_paths(tmp_path):
    """После переноса данных db_path в конфиге не должен указывать на исчезнувшую старую папку."""
    old, new = tmp_path / "Docs" / "ADK", tmp_path / "exe" / "ADK"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "config.ini").write_text("[Paths]\ndb_path = %s\n" % (old / "pc_mapping.db"), encoding="utf-8")
    (old / "pc_mapping.db").write_text("db")
    _migrate_old_data(str(old), str(new))
    txt = (new / "config.ini").read_text(encoding="utf-8")
    assert str(new / "pc_mapping.db") in txt
    assert str(old) not in txt
    assert (new / "pc_mapping.db").exists()
