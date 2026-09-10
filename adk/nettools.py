"""Сетевые инструменты 3.1: Wake-on-LAN, массовый пинг, сообщение пользователю ПК (msg), MAC из ARP-кэша.

Чистые функции (``parse_mac``, ``magic_packet``, ``parse_arp``, ``msg_command``) — без сети, тестируются напрямую.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import re
import socket
import subprocess
from typing import Callable, Iterable

from . import db
from .config import CREATE_NO_WINDOW
from . import netutils
from .netutils import get_computer_network_info, is_valid_hostname

_ORIG_NET_INFO = get_computer_network_info

log = logging.getLogger(__name__)

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2})[:\-\. ]?([0-9A-Fa-f]{2})[:\-\. ]?([0-9A-Fa-f]{2})[:\-\. ]?"
                     r"([0-9A-Fa-f]{2})[:\-\. ]?([0-9A-Fa-f]{2})[:\-\. ]?([0-9A-Fa-f]{2})$")


# --------------------------------------------------------------------------- Wake-on-LAN
def parse_mac(text: str) -> str:
    """'00-1a-2b-3c-4d-5e' / '001a.2b3c.4d5e' / '00:1A:…' → '00:1A:2B:3C:4D:5E'; '' — если не MAC."""
    m = _MAC_RE.match((text or "").strip())
    if not m:
        return ""
    mac = ":".join(g.upper() for g in m.groups())
    return "" if mac in ("00:00:00:00:00:00", "FF:FF:FF:FF:FF:FF") else mac


def magic_packet(mac: str) -> bytes:
    mac = parse_mac(mac)
    if not mac:
        raise ValueError("Некорректный MAC-адрес")
    raw = bytes.fromhex(mac.replace(":", ""))
    return b"\xff" * 6 + raw * 16


def wake(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bool:
    pkt = magic_packet(mac)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(pkt, (broadcast, port))
            s.sendto(pkt, (broadcast, 7))
        return True
    except OSError as exc:
        log.warning("WoL %s: %s", mac, exc)
        return False


def parse_arp(text: str, ip: str) -> str:
    """Строка `arp -a` с нужным IP → MAC (Windows «00-1a-…» и Linux «00:1a:…»)."""
    for line in (text or "").splitlines():
        parts = line.split()
        if ip in parts or f"({ip})" in parts:
            for tok in parts:
                mac = parse_mac(tok)
                if mac:
                    return mac
    return ""


def learn_mac(computer_name: str, ip: str) -> str:
    """Пингует ПК (чтобы он попал в ARP), читает `arp -a`, сохраняет MAC в БД. Работает только в одном L2-сегменте."""
    if not ip or ip == "Не найден":
        return ""
    try:
        subprocess.run(["arp", "-a", ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, creationflags=CREATE_NO_WINDOW)
        out = subprocess.run(["arp", "-a"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5,
                             creationflags=CREATE_NO_WINDOW, text=True, encoding="cp866" if CREATE_NO_WINDOW else "utf-8", errors="replace").stdout
    except (subprocess.SubprocessError, OSError, LookupError):
        return ""
    mac = parse_arp(out, ip)
    if mac:
        db.save_mac(computer_name, mac)
    return mac


# --------------------------------------------------------------------------- массовый пинг
def probe_host(comp: str) -> tuple[str, bool]:
    """(ip, online) для одного ПК. Сначала DNS + ping по имени; если DNS имя не знает — берём последний адрес
    из инвентаря (``db.known_ip``) и честно пингуем его: ПК может быть в сети, даже когда запись в DNS устарела.
    Возвращаемый ip в этом случае помечается « (по данным сканирования)»."""
    # берём актуальную функцию из netutils (её подменяют в тестах/демо); прямой импорт выше — для обратной совместимости
    fn = netutils.get_computer_network_info if get_computer_network_info is _ORIG_NET_INFO else get_computer_network_info
    ip, online = fn(comp, use_cache=False)
    if ip and ip not in ("Не найден", "Не указан"):
        return ip, online
    known = db.known_ip(comp)
    if not known:
        return "Не найден", False
    try:
        alive = bool(netutils.is_host_alive(known))
    except Exception as exc:  # noqa: BLE001
        log.debug("ping %s (%s): %s", comp, known, exc)
        alive = False
    return f"{known} (по данным сканирования)", alive


def mass_ping(hosts: Iterable[str], progress: Callable[[str, str, bool], None] | None = None, workers: int = 32,
              cancelled: Callable[[], bool] | None = None) -> list[tuple[str, str, bool]]:
    """Параллельно проверяет список ПК → [(comp, ip, online)]. ``progress(comp, ip, online)`` — по мере готовности."""
    hosts = [h for h in dict.fromkeys(hosts) if h]
    out: list[tuple[str, str, bool]] = []
    with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts) or 1))) as ex:
        futs = {ex.submit(probe_host, h): h for h in hosts}
        for fut in cf.as_completed(futs):
            if cancelled and cancelled():
                break
            h = futs[fut]
            try:
                ip, on = fut.result()
            except Exception as exc:  # noqa: BLE001
                log.debug("ping %s: %s", h, exc)
                ip, on = "Не найден", False
            out.append((h, ip, on))
            if progress:
                progress(h, ip, on)
    return out


# --------------------------------------------------------------------------- msg
def msg_command(target: str, text: str, seconds: int = 60) -> list[str]:
    """argv для `msg * /server:PC /time:N текст` — сообщение всем сессиям на ПК."""
    if not is_valid_hostname(target):
        raise ValueError(f"Недопустимое имя узла: {target!r}")
    text = " ".join((text or "").split())
    if not text:
        raise ValueError("Пустое сообщение")
    return ["msg", "*", f"/server:{target}", f"/time:{int(seconds)}", text[:255]]


def send_message(target: str, text: str, seconds: int = 60) -> tuple[bool, str]:
    argv = msg_command(target, text, seconds)
    try:
        res = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, creationflags=CREATE_NO_WINDOW,
                             text=True, errors="replace")
    except FileNotFoundError:
        return False, "Команда msg недоступна (только Windows)"
    except (subprocess.SubprocessError, OSError) as exc:
        return False, str(exc)
    if res.returncode == 0:
        return True, f"Сообщение отправлено на {target}"
    return False, (res.stderr or res.stdout or "ошибка").strip().splitlines()[0][:200]
