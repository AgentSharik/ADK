"""Журнал входов на ПК за последние N часов: события Security 4624 (успех) / 4625 (отказ).

Опрос — ``Get-WinEvent -ComputerName`` (нужны права на чтение журнала Security удалённо).
Разбор — чистая функция :func:`parse_events_json`, тестируется без Windows.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import Counter

from .config import CREATE_NO_WINDOW
from .netutils import is_valid_hostname

log = logging.getLogger(__name__)

# типы входа (LogonType) — только «человеческие»; 4/5 (batch/service) отбрасываем
LOGON_TYPES = {"2": "Консоль", "7": "Разблокировка", "10": "RDP", "11": "Кэш (офлайн)", "3": "Сеть"}
SKIP_ACCOUNTS_SUFFIX = ("$",)   # компьютерные учётки
SKIP_ACCOUNTS = {"SYSTEM", "ANONYMOUS LOGON", "LOCAL SERVICE", "NETWORK SERVICE", "DWM-1", "DWM-2", "UMFD-0", "UMFD-1", "UMFD-2"}

_PS = r"""
$ErrorActionPreference = 'Stop'
$c = '__HOST__'
$since = (Get-Date).AddHours(-__HOURS__)
$ev = Get-WinEvent -ComputerName $c -FilterHashtable @{LogName='Security'; Id=4624,4625; StartTime=$since} -MaxEvents 2000 |
  ForEach-Object {
    $x = [xml]$_.ToXml()
    $d = @{}
    foreach ($n in $x.Event.EventData.Data) { $d[$n.Name] = $n.'#text' }
    [pscustomobject]@{ id = $_.Id; ts = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); user = $d['TargetUserName'];
                       domain = $d['TargetDomainName']; type = [string]$d['LogonType']; ip = $d['IpAddress']; status = $d['SubStatus'] }
  }
ConvertTo-Json -InputObject @($ev) -Compress -Depth 3
"""

FAIL_REASONS = {"0xc000006a": "неверный пароль", "0xc0000064": "нет такого пользователя", "0xc0000234": "учётка заблокирована",
                "0xc0000072": "учётка отключена", "0xc000006f": "вне разрешённого времени", "0xc0000070": "вход с этого ПК запрещён",
                "0xc0000193": "срок учётки истёк", "0xc0000071": "пароль истёк", "0xc0000224": "требуется смена пароля"}


def parse_events_json(text: str) -> dict:
    """JSON → {events: [{ts, user, domain, kind, type, ip, reason}], by_user: [(user, n)], fails: int, ok: int}."""
    raw = json.loads(text) if text.strip() else []
    if isinstance(raw, dict):
        raw = [raw]
    events: list[dict] = []
    for e in raw:
        user = (e.get("user") or "").strip()
        if not user or user.upper() in SKIP_ACCOUNTS or user.endswith(SKIP_ACCOUNTS_SUFFIX):
            continue
        typ = str(e.get("type") or "")
        if int(e.get("id") or 0) == 4624 and typ not in LOGON_TYPES:
            continue
        ok = int(e.get("id") or 0) == 4624
        ip = (e.get("ip") or "").strip()
        ip = "" if ip in ("-", "::1", "127.0.0.1") else ip
        events.append({"ts": e.get("ts", ""), "user": user, "domain": (e.get("domain") or "").strip(), "kind": "ok" if ok else "fail",
                       "type": LOGON_TYPES.get(typ, typ), "ip": ip,
                       "reason": "" if ok else FAIL_REASONS.get(str(e.get("status") or "").lower(), str(e.get("status") or ""))})
    events.sort(key=lambda x: x["ts"], reverse=True)
    by_user = Counter(x["user"] for x in events if x["kind"] == "ok")
    return {"events": events, "by_user": by_user.most_common(), "ok": sum(1 for x in events if x["kind"] == "ok"),
            "fails": sum(1 for x in events if x["kind"] == "fail")}


def get_logons(host: str, hours: int = 24, timeout: int = 90) -> dict:
    if not is_valid_hostname(host):
        return {"error": f"Недопустимое имя узла: {host!r}"}
    if os.name != "nt":
        return {"error": "Журнал входов доступен только с Windows (Get-WinEvent)"}
    script = _PS.replace("__HOST__", host).replace("__HOURS__", str(int(hours)))
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"error": f"PowerShell: {exc}"}
    if res.returncode != 0:
        err = (res.stderr or "нет ответа").strip().splitlines()[0][:200]
        if "No events were found" in err or "Не найдено событий" in err:
            return parse_events_json("[]")
        return {"error": err}
    try:
        return parse_events_json(res.stdout)
    except (ValueError, KeyError) as exc:
        return {"error": f"Разбор ответа: {exc}"}


def format_summary(d: dict) -> str:
    if "error" in d:
        return f"⚠️ {d['error']}"
    parts = [f"✅ успешных входов: {d['ok']}", f"⛔ отказов: {d['fails']}"]
    if d["by_user"]:
        parts.append("👤 " + ", ".join(f"{u} ({n})" for u, n in d["by_user"][:5]))
    return " · ".join(parts)
