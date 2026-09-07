"""Установленное ПО и последние обновления на ПК — через PowerShell (реестр Uninstall + Win32_QuickFixEngineering).

Опрос — только с Windows; разбор JSON вынесен в чистые функции (тестируются без PowerShell).
Результаты кэшируются в таблице ``pc_software`` — поиск «у кого стоит X» идёт по кэшу всего парка.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime

from . import db
from .config import CREATE_NO_WINDOW
from .netutils import is_valid_hostname

log = logging.getLogger(__name__)

_PS = r"""
$ErrorActionPreference = 'Stop'
$c = '__HOST__'
$paths = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
$soft = Invoke-Command -ComputerName $c -ScriptBlock {
  param($paths)
  foreach ($p in $paths) {
    Get-ChildItem "HKLM:\$p" -ErrorAction SilentlyContinue | ForEach-Object {
      $i = Get-ItemProperty $_.PSPath
      if ($i.DisplayName -and -not $i.SystemComponent) {
        [pscustomobject]@{ name = $i.DisplayName; version = [string]$i.DisplayVersion; publisher = [string]$i.Publisher; installed = [string]$i.InstallDate }
      }
    }
  }
} -ArgumentList (,$paths)
$hot = Get-CimInstance Win32_QuickFixEngineering -ComputerName $c | Sort-Object InstalledOn -Descending | Select-Object -First 15 |
  ForEach-Object { [pscustomobject]@{ id = $_.HotFixID; desc = $_.Description; installed = if ($_.InstalledOn) { $_.InstalledOn.ToString('yyyy-MM-dd') } else { '' } } }
[pscustomobject]@{ software = @($soft); hotfixes = @($hot) } | ConvertTo-Json -Depth 4 -Compress
"""


def parse_software_json(text: str) -> dict:
    """JSON → {software: [{name, version, publisher, installed}], hotfixes: [{id, desc, installed}]} — без дублей, отсортировано."""
    d = json.loads(text)
    seen: set[tuple[str, str]] = set()
    soft: list[dict] = []
    for x in d.get("software") or []:
        name = (x.get("name") or "").strip()
        ver = (x.get("version") or "").strip()
        if not name or (name.casefold(), ver) in seen:
            continue
        seen.add((name.casefold(), ver))
        inst = (x.get("installed") or "").strip()
        if len(inst) == 8 and inst.isdigit():
            inst = f"{inst[:4]}-{inst[4:6]}-{inst[6:]}"
        soft.append({"name": name, "version": ver, "publisher": (x.get("publisher") or "").strip(), "installed": inst})
    soft.sort(key=lambda s: s["name"].casefold())
    hot = [{"id": h.get("id", ""), "desc": h.get("desc", ""), "installed": h.get("installed", "")} for h in d.get("hotfixes") or [] if h.get("id")]
    hot.sort(key=lambda h: h["installed"], reverse=True)
    return {"software": soft, "hotfixes": hot}


def get_software(host: str, timeout: int = 60) -> dict:
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    if os.name != "nt":
        return {"error": "Опрос ПО доступен только с Windows (PowerShell Remoting)"}
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS.replace("__HOST__", host)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"error": f"PowerShell: {exc}"}
    if res.returncode != 0 or not res.stdout.strip():
        return {"error": (res.stderr or "нет ответа").strip().splitlines()[0][:200]}
    try:
        data = parse_software_json(res.stdout)
    except (ValueError, KeyError) as exc:
        return {"error": f"Разбор ответа: {exc}"}
    cache_software(host, data["software"])
    return data


# --------------------------------------------------------------------------- кэш
def cache_software(host: str, items: list[dict]) -> None:
    comp = db.clean_computer_name(host)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    import contextlib
    with contextlib.closing(db.get_db_connection()) as conn:
        conn.execute("DELETE FROM pc_software WHERE computer_name = ?", (comp,))
        conn.executemany("INSERT INTO pc_software (computer_name, name, version, publisher, installed, ts) VALUES (?,?,?,?,?,?)",
                         [(comp, s["name"], s["version"], s["publisher"], s["installed"], now) for s in items])
        conn.commit()


def cached_software(host: str) -> tuple[list[dict], str]:
    comp = db.clean_computer_name(host)
    rows = db.db_execute_with_retry("SELECT name, version, publisher, installed, ts FROM pc_software WHERE computer_name = ? ORDER BY name COLLATE NOCASE",
                                    (comp,), fetch="all") or []
    return [{"name": r[0], "version": r[1], "publisher": r[2], "installed": r[3]} for r in rows], (rows[0][4] if rows else "")


def find_software(query: str, limit: int = 500) -> list[dict]:
    """«У кого стоит X»: [{comp, name, version, ts}] по всему кэшу парка; подстрока без учёта регистра."""
    q = (query or "").strip()
    if len(q) < 2:
        return []
    like = f"%{db.like_escape(q)}%"
    rows = db.db_execute_with_retry("SELECT computer_name, name, version, ts FROM pc_software WHERE name LIKE ? ESCAPE '\\' "
                                    "ORDER BY computer_name, name LIMIT ?", (like, int(limit)), fetch="all") or []
    return [{"comp": r[0], "name": r[1], "version": r[2], "ts": r[3]} for r in rows]


def software_summary() -> list[tuple[str, int]]:
    """Топ программ по числу ПК — для вкладки «ПО парка»."""
    rows = db.db_execute_with_retry("SELECT name, COUNT(DISTINCT computer_name) AS n FROM pc_software GROUP BY name ORDER BY n DESC, name LIMIT 300",
                                    fetch="all") or []
    return [(r[0], r[1]) for r in rows]
