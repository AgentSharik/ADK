"""Сверка адресов с DHCP-серверами Windows (модуль PowerShell ``DhcpServer`` из RSAT).

«Свободный IP» по ICMP + PTR + инвентарю — это гипотеза: выключенный ПК не пингуется, а PTR
может не быть. DHCP знает точно: адрес выдан в аренду, зарезервирован, входит в диапазон
исключения или вообще вне области. Здесь только чтение (``Get-Dhcp*``) — ничего на сервере не меняется.

Разбор JSON вынесен в :func:`parse_dhcp_json` — тестируется без PowerShell и без сервера.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import subprocess

from .config import CREATE_NO_WINDOW, settings
from .netutils import is_valid_hostname

log = logging.getLogger(__name__)

_PS = r"""
$ErrorActionPreference = 'Stop'
Import-Module DhcpServer
$srv = '__SERVER__'
$net = '__PREFIX__'
$scopes = @(Get-DhcpServerv4Scope -ComputerName $srv | Where-Object { $_.ScopeId.ToString().StartsWith($net) -or $_.StartRange.ToString().StartsWith($net) })
$out = @()
foreach ($s in $scopes) {
    $leases = @(); $res = @(); $excl = @()
    try { $leases = @(Get-DhcpServerv4Lease -ComputerName $srv -ScopeId $s.ScopeId -AllLeases | ForEach-Object {
        @{ ip = $_.IPAddress.ToString(); mac = [string]$_.ClientId; host = [string]$_.HostName; state = [string]$_.AddressState
           expires = if ($_.LeaseExpiryTime) { $_.LeaseExpiryTime.ToString('yyyy-MM-dd HH:mm') } else { '' } } }) } catch {}
    try { $res = @(Get-DhcpServerv4Reservation -ComputerName $srv -ScopeId $s.ScopeId | ForEach-Object {
        @{ ip = $_.IPAddress.ToString(); mac = [string]$_.ClientId; name = [string]$_.Name } }) } catch {}
    try { $excl = @(Get-DhcpServerv4ExclusionRange -ComputerName $srv -ScopeId $s.ScopeId | ForEach-Object {
        @{ start = $_.StartRange.ToString(); end = $_.EndRange.ToString() } }) } catch {}
    $out += @{ scope = $s.ScopeId.ToString(); name = [string]$s.Name; start = $s.StartRange.ToString(); end = $s.EndRange.ToString()
               mask = $s.SubnetMask.ToString(); state = [string]$s.State; leases = $leases; reservations = $res; exclusions = $excl }
}
@{ server = $srv; scopes = $out } | ConvertTo-Json -Compress -Depth 6
"""


def parse_dhcp_json(text: str) -> dict:
    """JSON → {server, scopes:[{scope,name,start,end,state,leases:{ip:{…}},reservations:{ip:{…}},exclusions:[(start,end)]}]}."""
    d = json.loads(text)
    scopes = []
    for s in d.get("scopes") or []:
        scopes.append({
            "scope": s.get("scope", ""), "name": s.get("name", ""), "start": s.get("start", ""), "end": s.get("end", ""),
            "state": s.get("state", ""),
            "leases": {x["ip"]: {"mac": x.get("mac", ""), "host": x.get("host", ""), "state": x.get("state", ""),
                                 "expires": x.get("expires", "")} for x in (s.get("leases") or []) if x.get("ip")},
            "reservations": {x["ip"]: {"mac": x.get("mac", ""), "name": x.get("name", "")} for x in (s.get("reservations") or []) if x.get("ip")},
            "exclusions": [(x["start"], x["end"]) for x in (s.get("exclusions") or []) if x.get("start") and x.get("end")],
        })
    return {"server": d.get("server", ""), "scopes": scopes}


def _in_range(ip: str, start: str, end: str) -> bool:
    try:
        return ipaddress.ip_address(start) <= ipaddress.ip_address(ip) <= ipaddress.ip_address(end)
    except ValueError:
        return False


def classify(ip: str, data: dict) -> dict:
    """Что DHCP знает про адрес: {status, text, detail}.

    status: ``free`` — в области и не занят; ``lease`` — активная аренда; ``reserved`` — резервирование;
    ``excluded`` — в диапазоне исключения (раздаётся вручную/статикой); ``outside`` — ни одна область не покрывает.
    """
    for s in data.get("scopes") or []:
        if ip in s["reservations"]:
            r = s["reservations"][ip]
            return {"status": "reserved", "text": "зарезервирован в DHCP",
                    "detail": " · ".join(x for x in (r.get("name"), r.get("mac")) if x)}
        if ip in s["leases"]:
            lse = s["leases"][ip]
            state = (lse.get("state") or "").lower()
            if state.startswith(("active", "activereservation")):
                return {"status": "lease", "text": "выдан в аренду DHCP",
                        "detail": " · ".join(x for x in (lse.get("host"), lse.get("mac"), f"до {lse['expires']}" if lse.get("expires") else "") if x)}
            if state in ("offered", "offeredreservation"):
                return {"status": "lease", "text": "предлагается клиенту прямо сейчас", "detail": lse.get("mac", "")}
            return {"status": "free", "text": f"аренда истекла ({lse.get('state') or 'inactive'}) — адрес свободен",
                    "detail": " · ".join(x for x in (lse.get("host"), lse.get("mac")) if x)}
        if _in_range(ip, s["start"], s["end"]):
            for a, b in s["exclusions"]:
                if _in_range(ip, a, b):
                    return {"status": "excluded", "text": "в диапазоне исключения DHCP (статика)", "detail": f"{a}–{b} · {s['name']}"}
            return {"status": "free", "text": "не выдан DHCP", "detail": f"область {s['scope']} · {s['name']}"}
    return {"status": "outside", "text": "вне областей DHCP (адрес назначается вручную)", "detail": ""}


def query(prefix: str, servers: tuple[str, ...] | None = None, timeout: int = 40) -> dict:
    """Читает области/аренды всех настроенных серверов для подсети ``prefix`` (10.0.2). Ошибки — в ``errors``."""
    servers = servers if servers is not None else settings.dhcp_servers
    out: dict = {"scopes": [], "servers": [], "errors": []}
    if not servers:
        return out
    if os.name != "nt":
        out["errors"].append("Сверка с DHCP доступна только с Windows (модуль DhcpServer)")
        return out
    for srv in servers:
        if not is_valid_hostname(srv):
            out["errors"].append(f"Недопустимое имя сервера: {srv!r}")
            continue
        try:
            res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                                  _PS.replace("__SERVER__", srv).replace("__PREFIX__", prefix + ".")],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                 timeout=timeout, creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            out["errors"].append(f"{srv}: не ответил за {timeout} с")
            continue
        except (subprocess.SubprocessError, OSError) as exc:
            out["errors"].append(f"{srv}: PowerShell — {exc}")
            continue
        if res.returncode != 0 or not res.stdout.strip():
            out["errors"].append(f"{srv}: {(res.stderr or 'нет ответа').strip().splitlines()[0][:160]}")
            continue
        try:
            data = parse_dhcp_json(res.stdout)
        except (ValueError, KeyError) as exc:
            out["errors"].append(f"{srv}: разбор ответа — {exc}")
            continue
        out["servers"].append(srv)
        out["scopes"].extend(data["scopes"])
    return out
