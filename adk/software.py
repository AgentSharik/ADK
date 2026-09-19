"""Установленное ПО и последние обновления на ПК — через PowerShell (реестр Uninstall + Win32_QuickFixEngineering).

Опрос — только с Windows; разбор JSON вынесен в чистые функции (тестируются без PowerShell).
Результаты кэшируются в таблице ``pc_software`` — поиск «у кого стоит X» идёт по кэшу всего парка.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

from . import db
from .netutils import is_valid_hostname

log = logging.getLogger(__name__)

# 3.5.10: без Invoke-Command (только WinRM) — читаем реестр удалённо через StdRegProv (DCOM/WMI, работает там же, где
# работает «Здоровье ПК»), а если и WMI закрыт — через удалённый реестр (служба RemoteRegistry). Первый успешный путь побеждает.
_PS = r"""
$ErrorActionPreference = 'Stop'
$c = '__HOST__'
$paths = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
$soft = @()
$how = ''
$errs = @()
$HKLM = [uint32]2147483650

# 1. WinRM (быстрее всего и не требует RemoteRegistry)
try {
  $soft = @(Invoke-Command -ComputerName $c -ErrorAction Stop -ScriptBlock {
    param($paths)
    foreach ($p in $paths) {
      Get-ChildItem "HKLM:\$p" -ErrorAction SilentlyContinue | ForEach-Object {
        $i = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
        if ($i.DisplayName -and -not $i.SystemComponent) {
          [pscustomobject]@{ name = $i.DisplayName; version = [string]$i.DisplayVersion; publisher = [string]$i.Publisher; installed = [string]$i.InstallDate }
        }
      }
    }
  } -ArgumentList (,$paths) | Select-Object name, version, publisher, installed)
  $how = 'WinRM'
} catch { $errs += ('WinRM: ' + $_.Exception.Message) }

# 2. WMI StdRegProv по DCOM
if (-not $how) {
  try {
    $reg = Get-WmiObject -List -Namespace 'root\default' -ComputerName $c -ErrorAction Stop | Where-Object { $_.Name -eq 'StdRegProv' }
    if (-not $reg) { throw 'StdRegProv недоступен' }
    foreach ($p in $paths) {
      $keys = $reg.EnumKey($HKLM, $p)
      if ($keys.ReturnValue -ne 0 -or -not $keys.sNames) { continue }
      foreach ($k in $keys.sNames) {
        $sub = "$p\$k"
        $name = ($reg.GetStringValue($HKLM, $sub, 'DisplayName')).sValue
        if (-not $name) { continue }
        $sys = ($reg.GetDWORDValue($HKLM, $sub, 'SystemComponent')).uValue
        if ($sys -eq 1) { continue }
        $soft += [pscustomobject]@{ name = $name; version = [string]($reg.GetStringValue($HKLM, $sub, 'DisplayVersion')).sValue
                                    publisher = [string]($reg.GetStringValue($HKLM, $sub, 'Publisher')).sValue
                                    installed = [string]($reg.GetStringValue($HKLM, $sub, 'InstallDate')).sValue }
      }
    }
    $how = 'WMI'
  } catch { $errs += ('WMI: ' + $_.Exception.Message) }
}

# 3. Удалённый реестр (служба RemoteRegistry)
if (-not $how) {
  try {
    $base = [Microsoft.Win32.RegistryKey]::OpenRemoteBaseKey([Microsoft.Win32.RegistryHive]::LocalMachine, $c)
    foreach ($p in $paths) {
      $k = $base.OpenSubKey($p)
      if (-not $k) { continue }
      foreach ($n in $k.GetSubKeyNames()) {
        $s = $k.OpenSubKey($n)
        if (-not $s) { continue }
        $name = $s.GetValue('DisplayName')
        if (-not $name -or $s.GetValue('SystemComponent') -eq 1) { continue }
        $soft += [pscustomobject]@{ name = [string]$name; version = [string]$s.GetValue('DisplayVersion'); publisher = [string]$s.GetValue('Publisher'); installed = [string]$s.GetValue('InstallDate') }
      }
    }
    $how = 'RemoteRegistry'
  } catch { $errs += ('RemoteRegistry: ' + $_.Exception.Message) }
}

if (-not $how) { throw ("Список ПО не прочитан. " + ($errs -join ' | ')) }

$hot = @()
try {
  $hot = @(Get-CimInstance Win32_QuickFixEngineering -ComputerName $c -ErrorAction Stop | Sort-Object InstalledOn -Descending | Select-Object -First 15 |
    ForEach-Object { [pscustomobject]@{ id = $_.HotFixID; desc = $_.Description; installed = if ($_.InstalledOn) { $_.InstalledOn.ToString('yyyy-MM-dd') } else { '' } } })
} catch {
  try {
    $hot = @(Get-WmiObject Win32_QuickFixEngineering -ComputerName $c -ErrorAction Stop | Sort-Object InstalledOn -Descending | Select-Object -First 15 |
      ForEach-Object { [pscustomobject]@{ id = $_.HotFixID; desc = $_.Description; installed = if ($_.InstalledOn) { ([datetime]$_.InstalledOn).ToString('yyyy-MM-dd') } else { '' } } })
  } catch {}
}
[pscustomobject]@{ software = @($soft); hotfixes = @($hot); how = $how } | ConvertTo-Json -Depth 4 -Compress
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
    _SEC = ("security", "безопасн", "security update", "критическое", "critical")
    hot = []
    for h in d.get("hotfixes") or []:
        if not h.get("id"):
            continue
        desc = (h.get("desc") or "").strip()
        low = desc.casefold()
        hot.append({"id": h.get("id", ""), "desc": desc, "installed": h.get("installed", ""),
                    "kind": "security" if any(k in low for k in _SEC) else "other"})
    hot.sort(key=lambda h: (h["kind"] != "security", h["installed"]), reverse=False)
    hot.sort(key=lambda h: h["installed"], reverse=True)
    return {"software": soft, "hotfixes": hot, "how": str(d.get("how") or "")}


def get_software(host: str, timeout: int = 90, cancelled=None) -> dict:
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    from . import psrun
    res = psrun.run(_PS.replace("__HOST__", host), timeout=timeout, cancelled=cancelled)
    if not res.ok:
        return {"error": res.error}
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
