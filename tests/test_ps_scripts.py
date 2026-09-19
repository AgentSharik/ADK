"""3.7.1: PowerShell-скрипты ADK проверяются по-настоящему.

В 3.7.0 карта диска роняло создание пула потоков (`CreateRunspacePool` с `$null` в роли PSHost), а списки,
созданные `New-Object`, в PowerShell с нестандартной культурой не работали с `@()`. Здесь каждый скрипт
прогоняется через синтаксический парсер PowerShell, а карта диска — целиком на локальном тестовом дереве:
параллельный обход, «пожиратели места», профили, крупные файлы и корневые файлы проверяются по точным числам.

Тесты пропускаются там, где PowerShell не установлен (например, локальная песочница без pwsh);
в CI (ubuntu-latest) pwsh предустановлен.
"""
import os
import shutil
import subprocess

import pytest

from adk import health, netutils, psrun, software

PW = shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)

pytestmark = pytest.mark.skipif(PW is None, reason="PowerShell (pwsh/powershell) недоступен — проверка скриптов невозможна")


def _write(path, text: str) -> str:
    with open(path, "w", encoding="utf-8-sig") as fh:
        fh.write(text)
    return str(path)


def _run_file(path: str, timeout: int = 240):
    return subprocess.run([PW, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", path],
                          capture_output=True, text=True, timeout=timeout)


def test_scripts_parse_without_errors(tmp_path):
    """Все удалённые скрипты синтаксически валидны в настоящем PowerShell (ловит опечатки и битые конструкции)."""
    scripts = {
        "health_main": health._PS.replace("__HOST__", "TESTHOST"),
        "health_usage": health._PS_USAGE.replace("__ROOT__", str(tmp_path)).replace("__TOP__", "5"),
        "health_events": health._PS_EVENTS.replace("__HOST__", "TESTHOST")
            .replace("__LOGS__", "'System'").replace("__LEVELS__", "1, 2")
            .replace("__START__", "2026-09-01 00:00").replace("__END__", "2026-09-19 00:00").replace("__MAX__", "10"),
        "netutils_specs": netutils._PS_SPECS.replace("__HOST__", "TESTHOST"),
        "netutils_liveprinters": netutils._PS_LIVE_PRINTERS.replace("__HOST__", "TESTHOST"),
        "software": software._PS.replace("__HOST__", "TESTHOST"),
    }
    for name, script in scripts.items():
        p = _write(tmp_path / f"{name}.ps1", script)
        # путь встраивается в командную строку: лишний аргумент после -Command PowerShell приклеил бы к команде
        cmd = (f"$t=[IO.File]::ReadAllText('{p}');$e=$null;"
               "$null=[System.Management.Automation.Language.Parser]::ParseInput($t,[ref]$null,[ref]$e);"
               "if($e){$e|ForEach-Object{$_.Extent.StartLineNumber.ToString()+': '+$_.Message};exit 1}else{'OK'}")
        r = subprocess.run([PW, "-NoProfile", "-NonInteractive", "-Command", cmd],
                           capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, f"{name}: ошибки парсера: {r.stdout.strip()}"


# дерево с заранее известными размерами; пути → размер в байтах
_TREE = {
    "Windows/Installer/a.bin": 300_000,
    "Windows/Installer/b.bin": 200_000,
    "Windows/SoftwareDistribution/Download/upd.cab": 500_000,
    "Windows/Temp/tmp1.tmp": 100_000,
    "Windows/System32/x.dll": 400_000,
    "Windows/Minidump/dump.dmp": 50_048,
    "Users/alice/AppData/Local/Temp/junk1.tmp": 700_000,
    "Users/alice/Documents/doc1.docx": 10_240,
    "Users/bob/Desktop/note.txt": 5_120,
    "Program Files/App/app.exe": 250_000,
    "hiberfil.sys": 600_000,
    "pagefile.sys": 400_000,
    "bigfile.dat": 51 * 1024 * 1024,      # единственный файл ≥ 50 МБ
}


def test_disk_map_script_on_local_tree(tmp_path):
    """Карта диска (8 потоков): на тестовом дереве сходятся размеры папок, профилей, «пожирателей» и крупных файлов."""
    root = tmp_path / "root"
    for rel, size in _TREE.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.truncate(size)

    script = health._PS_USAGE.replace("__ROOT__", str(root)).replace("__TOP__", "5")
    if os.name != "nt":
        script = script.replace("\\", "/")      # тестовая копия для Linux-разделителей; на Windows скрипт не меняется
    p = _write(tmp_path / "usage.ps1", psrun.wrap(script))
    r = _run_file(p)
    out = (r.stdout or "").strip()
    assert out.startswith("{") and '"error"' not in out[:60], f"ответ карты диска: {out[:300] or r.stderr[:300]}"

    d = health.parse_usage_json(out)
    dirs = {x["name"]: x["size"] for x in d["dirs"]}
    users = {u["name"]: u["size"] for u in d["users"]}
    hogs = {h["label"]: h["size"] for h in d["hogs"]}
    exp = {
        "Windows": sum(v for k, v in _TREE.items() if k.startswith("Windows/")),
        "Program Files": sum(v for k, v in _TREE.items() if k.startswith("Program Files/")),
        "alice": sum(v for k, v in _TREE.items() if k.startswith("Users/alice/")),
        "bob": sum(v for k, v in _TREE.items() if k.startswith("Users/bob/")),
        "root_files": sum(v for k, v in _TREE.items() if "/" not in k),
        "installer": 500_000, "upd": 500_000, "wtemp": 100_000, "minidump": 50_048, "alice_temp": 700_000,
    }
    assert dirs["Windows"] == exp["Windows"]
    assert dirs["Program Files"] == exp["Program Files"]
    assert dirs["Users"] == exp["alice"] + exp["bob"]
    assert any(x["name"] == "<файлы в корне>" and x["size"] == exp["root_files"] for x in d["dirs"])
    assert users["alice"] == exp["alice"] and users["bob"] == exp["bob"]
    assert hogs["Кэш установщика"] == exp["installer"]
    assert hogs["Обновления Windows"] == exp["upd"]
    assert hogs["Временные файлы Windows"] == exp["wtemp"]
    assert hogs["Дампы памяти"] == exp["minidump"]
    assert hogs["Temp профиля alice"] == exp["alice_temp"]
    assert hogs["Файл гибернации"] == 600_000 and hogs["Файл подкачки"] == 400_000
    assert len(d["files"]) == 1 and d["files"][0]["size"] == 51 * 1024 * 1024
    assert d["errors"] == 0
    assert d["total_files"] == sum(1 for k in _TREE if "/" in k)
