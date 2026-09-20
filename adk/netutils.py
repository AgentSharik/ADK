"""Сеть и инвентарные CSV: ping/DNS, характеристики ПК, удалённые действия."""
from __future__ import annotations

import csv
import json
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Iterable

from .config import CREATE_NO_WINDOW, settings
from .db import clean_computer_name, db_execute_with_retry

log = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,62}$")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# Ответ Windows-ping. IPv4: «Ответ от 10.0.2.11: число байт=32 время=1мс TTL=128».
# IPv6: «Ответ от fe80::1%12: время<1мс» — БЕЗ «байт» и БЕЗ TTL, поэтому оба поля необязательны
# (иначе живой узел по IPv6 считался «не отвечает» — красные столбцы при 100 % связи).
_PING_OK_RE = re.compile(
    r"(?i)(?:ответ от|reply from)\s+(\S+?):\s+(?:.*?(?:байт|bytes)=(\d+))?.*?"
    r"(?:время|time)([=<])\s*([0-9.]+\s*(?:мс|ms))(?:.*?ttl=(\d+))?"
)
# Строки итоговой статистики (печатаются при остановке) и прочий служебный текст — не замеры.
_PING_INFO_KEYS = ("статистика ping", "пакетов:", "приблизительное время", "минимальное", "packets:",
                   "approximate round trip", "minimum =", "control-c", "^c")


def is_valid_hostname(name: str) -> bool:
    return bool(name) and bool(_HOSTNAME_RE.match(name) or _IPV4_RE.match(name))


def is_ip_query(q: str) -> bool:
    """Отличает IP/префикс IP (10.1.2) от телефонного номера (все цифры)."""
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){1,3}\.?$", q))


def ping_args(target: str, timeout_ms: int = 300) -> list[str]:
    if platform.system().lower() == "windows":
        return ["ping", "-n", "1", "-w", str(timeout_ms), target]
    return ["ping", "-c", "1", "-W", str(max(1, timeout_ms // 1000 or 1)), target]


def is_host_alive(ip: str, timeout: float = 1.0) -> bool:
    """TCP/445 → быстрый ответ; иначе ICMP."""
    try:
        with socket.create_connection((ip, 445), timeout=0.25):
            return True
    except OSError:
        pass
    try:
        res = subprocess.run(ping_args(ip), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             timeout=timeout, creationflags=CREATE_NO_WINDOW)
        return res.returncode == 0 and (b"TTL=" in res.stdout.upper() or b"ttl=" in res.stdout)
    except (subprocess.SubprocessError, OSError):
        return False


PRINTER_PORTS = (9100, 631, 80, 443, 515)  # JetDirect, IPP, web-панель, LPD — что-то из этого открыто почти у любого сетевого принтера


def is_printer_alive(ip: str, timeout: float = 0.8) -> bool:
    """Сетевой принтер: сначала TCP на типовые порты (быстро и без ICMP-фильтров), затем ping. С кэшем."""
    if not ip:
        return False
    now = time.monotonic()
    key = f"printer:{ip}"
    with _net_cache_lock:
        hit = _net_cache.get(key)
    if hit and now - hit[0] < NET_CACHE_TTL:
        return hit[1][1]
    alive = False
    for port in PRINTER_PORTS[:3]:
        try:
            with socket.create_connection((ip, port), timeout=0.3):
                alive = True
                break
        except OSError:
            continue
    if not alive:
        try:
            res = subprocess.run(ping_args(ip), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=timeout, creationflags=CREATE_NO_WINDOW)
            alive = res.returncode == 0 and (b"TTL=" in res.stdout.upper() or b"ttl=" in res.stdout)
        except (subprocess.SubprocessError, OSError):
            alive = False
    with _net_cache_lock:
        _net_cache[key] = (now, (ip, alive))
    return alive


def probe_printer(ip: str, timeout: float = 0.6) -> dict:
    """Честная проверка «а принтер ли по этому IP». Возвращает
    ``{"alive": bool, "is_printer": True|False|None, "evidence": str}``.

    * ``is_printer=True`` — открыт порт печати 9100 (JetDirect/RAW) или 631 (IPP), либо веб-панель отдала
      заголовок Server/титул, характерный для принтеров/МФУ (HP, Kyocera, Canon, Xerox, Brother, Epson, Ricoh…).
    * ``is_printer=False`` — узел отвечает, но признаков принтера нет (открыт 445/3389 — это ПК, либо только ping).
    * ``is_printer=None`` — узел не отвечает: проверить нельзя, честно говорим «не проверено».
    Без SNMP и без записи куда-либо; каждое соединение — ≤ ``timeout`` с.
    """
    if not ip:
        return {"alive": False, "is_printer": None, "evidence": "нет IP"}

    def port_open(port: int) -> bool:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return True
        except OSError:
            return False

    alive = False
    # 3.5.10: все порты проверяются одновременно (раньше — по очереди: молчащий адрес ждал 7 × timeout ≈ 4 с)
    with ThreadPoolExecutor(max_workers=7, thread_name_prefix="probe") as ex:
        opened = dict(zip((9100, 631, 445, 3389, 135, 80, 443), ex.map(port_open, (9100, 631, 445, 3389, 135, 80, 443))))
    for port in (9100, 631):
        if opened[port]:
            return {"alive": True, "is_printer": True, "evidence": f"открыт порт печати {port}"}
    for port in (445, 3389, 135):
        if opened[port]:
            return {"alive": True, "is_printer": False, "evidence": f"открыт порт {port} — это компьютер, а не принтер"}
    for port in (80, 443):
        if not opened[port]:
            continue
        alive = True
        try:
            import http.client
            cls = http.client.HTTPSConnection if port == 443 else http.client.HTTPConnection
            conn = cls(ip, port, timeout=timeout) if port == 80 else cls(ip, port, timeout=timeout, context=_insecure_ssl())
            conn.request("GET", "/")
            resp = conn.getresponse()
            head = (resp.getheader("Server") or "") + " " + resp.read(4096).decode("latin-1", "ignore")
            conn.close()
        except Exception:  # noqa: BLE001
            head = ""
        low = head.lower()
        if any(k in low for k in PRINTER_WEB_MARKERS):
            return {"alive": True, "is_printer": True, "evidence": f"веб-панель принтера (порт {port})"}
        return {"alive": True, "is_printer": False, "evidence": f"веб-сервер на порту {port} без признаков принтера"}
    if not alive:
        try:
            res = subprocess.run(ping_args(ip), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=max(1.0, timeout * 2), creationflags=CREATE_NO_WINDOW)
            alive = res.returncode == 0 and (b"TTL=" in res.stdout.upper() or b"ttl=" in res.stdout)
        except (subprocess.SubprocessError, OSError):
            alive = False
    if alive:
        return {"alive": True, "is_printer": False, "evidence": "отвечает на ping, портов печати нет"}
    return {"alive": False, "is_printer": None, "evidence": "узел не отвечает — проверить невозможно"}


PRINTER_WEB_MARKERS = ("hp-chai", "hp http server", "laserjet", "officejet", "designjet", "kyocera", "ecosys", "taskalfa",
                       "canon", "imagerunner", "xerox", "workcentre", "versalink", "brother", "epson", "ricoh", "lexmark",
                       "konica", "bizhub", "pantum", "samsung printer", "printer", "принтер", "мфу", "ipp", "embedded web server",
                       "ews", "cups")


# --------------------------------------------------------------------------- принтер, которого нет в базе (3.5.10)
_SNMP_OIDS = (
    "1.3.6.1.2.1.25.3.2.1.3.1",     # hrDeviceDescr — «HP LaserJet M1536dnf MFP»
    "1.3.6.1.2.1.43.5.1.1.16.1",    # prtGeneralPrinterName
    "1.3.6.1.2.1.1.1.0",            # sysDescr
)


def _ber_len(n: int) -> bytes:
    return bytes([n]) if n < 128 else b"\x82" + n.to_bytes(2, "big")


def _ber(tag: int, body: bytes) -> bytes:
    return bytes([tag]) + _ber_len(len(body)) + body


def _ber_oid(oid: str) -> bytes:
    parts = [int(x) for x in oid.split(".")]
    out = bytearray([40 * parts[0] + parts[1]])
    for v in parts[2:]:
        chunk = bytearray([v & 0x7F])
        v >>= 7
        while v:
            chunk.insert(0, 0x80 | (v & 0x7F))
            v >>= 7
        out += chunk
    return _ber(0x06, bytes(out))


def snmp_get_string(ip: str, oid: str, community: str = "public", timeout: float = 0.7, port: int = 161) -> str:
    """Одно SNMPv1 GET без внешних библиотек: строка или «». Только чтение; community по умолчанию «public»."""
    req_id = int(time.time() * 1000) & 0x7FFFFFFF
    pdu = _ber(0xA0, _ber(0x02, req_id.to_bytes(4, "big")) + _ber(0x02, b"\x00") + _ber(0x02, b"\x00")
               + _ber(0x30, _ber(0x30, _ber_oid(oid) + _ber(0x05, b""))))
    msg = _ber(0x30, _ber(0x02, b"\x00") + _ber(0x04, community.encode()) + pdu)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(msg, (ip, port))
            data, _ = sock.recvfrom(4096)
    except OSError:
        return ""
    # ответ: последняя OCTET STRING (0x04) в varbind — берём её (разбор без полноценного BER-парсера достаточен)
    i = data.rfind(b"\x04")
    while i > 0:
        try:
            ln = data[i + 1]
            start = i + 2
            if ln & 0x80:
                nb = ln & 0x7F
                ln = int.from_bytes(data[start:start + nb], "big")
                start += nb
            val = data[start:start + ln]
            if start + ln == len(data) and val and val != community.encode():
                return val.decode("utf-8", "replace").strip()
        except (IndexError, ValueError):
            pass
        i = data.rfind(b"\x04", 0, i)
    return ""


def _http_title(ip: str, timeout: float = 0.8) -> str:
    """<title> веб-панели устройства (HP/Kyocera/Canon пишут в нём модель), «» если недоступна."""
    import http.client
    import html as _html
    for port in (80, 443):
        try:
            cls = http.client.HTTPSConnection if port == 443 else http.client.HTTPConnection
            conn = cls(ip, port, timeout=timeout) if port == 80 else cls(ip, port, timeout=timeout, context=_insecure_ssl())
            conn.request("GET", "/", headers={"User-Agent": "ADK"})
            resp = conn.getresponse()
            body = resp.read(20000).decode("utf-8", "ignore")
            conn.close()
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.I | re.S)
        if m:
            t = " ".join(_html.unescape(m.group(1)).split())
            if t and len(t) < 120:
                return t
    return ""


def discover_printer(ip: str) -> dict | None:
    """Принтер по IP, которого нет в базе: проверить, что по адресу принтер, и узнать модель (SNMP → веб-панель → PTR).

    Возвращает ``{"name", "ip", "kind": "network", "host", "probe", "source"}`` или None, если по адресу не принтер
    (или узел молчит). Ничего не записывает.
    """
    if not _IPV4_RE.match(ip or ""):
        return None
    probe = probe_printer(ip)
    if probe.get("is_printer") is not True:
        return None
    name, source = "", ""
    for oid in _SNMP_OIDS:
        name = snmp_get_string(ip, oid)
        if name:
            source = "SNMP"
            break
    if not name:
        name = _http_title(ip)
        source = "веб-панель" if name else ""
    host = ""
    try:
        host = socket.gethostbyaddr(ip)[0].split(".")[0]
    except OSError:
        pass
    if not name:
        name, source = (f"Сетевой принтер {host}" if host else f"Сетевой принтер {ip}"), "по портам печати"
    return {"name": name.strip(), "ip": ip, "kind": "network", "host": host, "probe": probe, "source": source}


def _insecure_ssl():
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


NET_CACHE_TTL = 20.0  # с; при наборе «ива» → «иван» → «иванов» одни и те же ПК не пингуются трижды
_net_cache: dict[str, tuple[float, tuple[str, bool]]] = {}
_net_cache_lock = threading.Lock()


def clear_network_cache() -> None:
    with _net_cache_lock:
        _net_cache.clear()


def cached_printer_alive(ip: str) -> bool | None:
    """Свежий ответ ``is_printer_alive`` из кэша без обращения к сети; None — надо проверять (3.5.6)."""
    with _net_cache_lock:
        hit = _net_cache.get(f"printer:{ip}")
    if hit and time.monotonic() - hit[0] < NET_CACHE_TTL:
        return hit[1][1]
    return None


def cached_network_info(computer_name: str) -> tuple[str, bool] | None:
    """Свежий (моложе NET_CACHE_TTL) ответ из кэша без обращения к сети; None — надо проверять.
    3.5.4: поиск сначала показывает строки, а сеть проверяет вторым шагом — кэш позволяет не показывать
    «Проверка…» для ПК, которые пинговались только что."""
    name = clean_computer_name(computer_name)
    with _net_cache_lock:
        hit = _net_cache.get(name)
    if hit and time.monotonic() - hit[0] < NET_CACHE_TTL:
        return hit[1]
    return None


def get_computer_network_info(computer_name: str, use_cache: bool = True) -> tuple[str, bool]:
    """(ip, online). ip == 'Не найден' если DNS не знает имя. Результат кэшируется на NET_CACHE_TTL секунд."""
    name = clean_computer_name(computer_name)
    if not name or not is_valid_hostname(name):
        return "Не указан", False
    now = time.monotonic()
    if use_cache:
        with _net_cache_lock:
            hit = _net_cache.get(name)
        if hit and now - hit[0] < NET_CACHE_TTL:
            return hit[1]
    ip = None
    try:
        ip = socket.gethostbyname(name)
    except OSError:
        pass
    if not ip or ip.startswith("127."):
        domain = getattr(settings, "domain_name", "") or getattr(settings, "upn_suffix", "") or getattr(settings, "dc_host", "")
        if domain and "." in domain:
            try:
                ip = socket.gethostbyname(f"{name}.{domain}")
            except OSError:
                pass
    if not ip:
        try:
            from . import db as _db
            known = _db.known_ip(name)
            if known and known not in ("Не найден", "Не указан"):
                ip = known
        except Exception:
            pass
    if not ip:
        result = ("Не найден", False)
    else:
        result = (ip, is_host_alive(ip))
    with _net_cache_lock:
        if len(_net_cache) > 5000:
            _net_cache.clear()
        _net_cache[name] = (now, result)
    return result


def parse_ping_line(line: str, target: str) -> dict | None:
    """Разбор строки вывода ping (ru/en). Возвращает dict для таблицы или None."""
    line = line.strip()
    if not line:
        return None
    low = line.lower()
    m = _PING_OK_RE.search(line)
    if m:
        # «время<1мс» — это «меньше миллисекунды», а не ровно 1 мс: знак сохраняем, окно пинга рисует 0,5
        return {"ip": m.group(1).rstrip(":"), "bytes": m.group(2) or "—",
                "time": ("<" if m.group(3) == "<" else "") + m.group(4).replace(" ", ""),
                "ttl": m.group(5) or "—", "status": "Успешно", "success": True, "is_info": False}
    m = re.search(r"(?i)^(\d+) bytes from (\S+?):?\s.*?ttl=(\d+).*?time=([0-9.]+\s*ms)", line)
    if m:  # формат iputils/BSD (в т.ч. IPv6 с зоной «%eth0») — для запуска вне Windows
        return {"ip": m.group(2), "bytes": m.group(1), "time": m.group(4).replace(" ", ""),
                "ttl": m.group(3), "status": "Успешно", "success": True, "is_info": False}
    m = re.search(r"(?i)(?:ответ от|reply from)\s+(\S+?):\s*(.+)", line)
    if m:   # «Ответ от 10.0.0.1: Заданный узел недоступен.» — ответ шлюза, а не узла
        return {"ip": m.group(1).rstrip(":"), "bytes": "—", "time": "—", "ttl": "—",
                "status": line, "success": False, "is_info": False}
    if any(k in low for k in ("превышен", "timed out", "сбой", "general failure", "unreachable", "недоступен")):
        return {"ip": target, "bytes": "—", "time": "—", "ttl": "—", "status": line,
                "success": False, "is_info": False}
    if "обмен пакетами" in low or "pinging" in low or low.startswith("ping ") or any(k in low for k in _PING_INFO_KEYS):
        return {"ip": "—", "bytes": "—", "time": "—", "ttl": "—", "status": line,
                "success": None, "is_info": True}
    return {"ip": "—", "bytes": "—", "time": "—", "ttl": "—", "status": line,
            "success": False, "is_info": False}


# --------------------------------------------------------------------------- удалённые действия
def remote_command(action: str, target: str) -> list[str] | None:
    """Формирует argv для удалённого действия. Аргументы `/m` и UNC-пути — отдельными токенами."""
    if not is_valid_hostname(target):
        raise ValueError(f"Недопустимое имя узла: {target!r}")
    unc = f"\\\\{target}"
    m = _DISK_ACTION_RE.fullmatch(action)
    if m:   # disk_c, disk_d, … disk_z → административная шара тома
        return ["explorer.exe", f"{unc}\\{m.group(1)}$"]
    return {
        "restart": ["shutdown", "/r", "/m", unc, "/t", "0", "/f"],
        "shutdown": ["shutdown", "/s", "/m", unc, "/t", "0", "/f"],
        "compmgmt": ["mmc.exe", "compmgmt.msc", f"/computer={unc}"],
    }.get(action)


_DISK_ACTION_RE = re.compile(r"disk_([a-z])")

POWER_ACTIONS: dict[str, tuple[str, str]] = {
    # ключ → (подпись, что делает) — порядок = порядок пунктов в меню «Питание ПК»
    "wol": ("⚡ Разбудить (Wake-on-LAN)", "magic-пакет на MAC из базы; ПК поднимается за 30–60 с"),
    "lock": ("🔒 Заблокировать экран", "как Win+L у пользователя — сеанс остаётся, программы не закрываются"),
    "logoff": ("🚪 Выйти из пользователя", "принудительный выход из сеанса (несохранённое пропадёт)"),
    "sleep": ("💤 Спящий режим", "SetSuspendState: если включена гибернация — уйдёт в неё"),
    "restart": ("🔄 Перезагрузить", "shutdown /r /f /t 0"),
    "shutdown": ("⏻ Выключить", "shutdown /s /f /t 0"),
}
POWER_CONFIRM = frozenset({"logoff", "restart", "shutdown"})   # эти — с подтверждением


def power_commands(action: str, target: str) -> list[list[str]]:
    """Шаги (argv) для действия меню «Питание ПК». Несколько шагов выполняются по очереди.

    * restart / shutdown — ``shutdown /m``;
    * logoff — WMI ``Win32Shutdown(4)`` (принудительный выход) через PowerShell/CIM;
    * lock — на удалённом ПК нельзя просто вызвать LockWorkStation из сессии 0, поэтому создаём одноразовую
      задачу планировщика от имени группы «Users» (она выполняется в интерактивном сеансе), запускаем и удаляем;
    * sleep — ``rundll32 powrprof.dll,SetSuspendState`` через WMI Win32_Process.
    """
    if not is_valid_hostname(target):
        raise ValueError(f"Недопустимое имя узла: {target!r}")
    unc = f"\\\\{target}"
    ps = ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command"]
    if action in ("restart", "shutdown"):
        return [remote_command(action, target) or []]
    if action == "logoff":
        return [ps + [f"Invoke-CimMethod -ClassName Win32_OperatingSystem -MethodName Win32Shutdown "
                      f"-Arguments @{{Flags=4}} -ComputerName '{target}' | Out-Null"]]
    if action == "sleep":
        return [ps + [f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create -ComputerName '{target}' "
                      f"-Arguments @{{CommandLine='rundll32.exe powrprof.dll,SetSuspendState 0,1,0'}} | Out-Null"]]
    if action == "lock":
        task = "ADK_LockWorkstation"
        return [["schtasks", "/create", "/s", unc, "/tn", task, "/tr", "rundll32.exe user32.dll,LockWorkStation",
                 "/sc", "once", "/st", "00:00", "/ru", "Users", "/f"],
                ["schtasks", "/run", "/s", unc, "/tn", task],
                ["schtasks", "/delete", "/s", unc, "/tn", task, "/f"]]
    raise ValueError(f"Неизвестное действие питания: {action!r}")
_DRIVE_LETTER_RE = re.compile(r"^([A-Za-z]):?\\?$")


def known_volumes(computer_name: str) -> list[dict]:
    """Тома ПК по CSV инвентаризации: [{'letter': 'C', 'label': 'System', 'size': '476 ГБ'}, …].
    Пустой список — данных нет (тогда кнопка «Диск» открывает C$ без вопросов)."""
    spec = get_computer_specs_dict(computer_name, live=False)
    out: list[dict] = []
    for _idx, sub in sorted(spec.get("logdisks", {}).items(), key=lambda kv: str(kv[0])):
        letter, label, size = "", "", ""
        for k, v in sub.items():
            kl = k.lower()
            m = _DRIVE_LETTER_RE.match(v.strip())
            if m and ("буква" in kl or "имя" in kl or "устройство" in kl or "letter" in kl or "name" in kl or "caption" in kl):
                letter = m.group(1).upper()
            elif "метка" in kl or "label" in kl or "том" in kl:
                label = v.strip()
            elif "размер" in kl or "size" in kl:
                size = v.strip()
                if size.isdigit():
                    size = f"{int(size) / 1024 ** 3:.0f} ГБ"
        if letter and letter not in {d["letter"] for d in out}:
            out.append({"letter": letter, "label": label, "size": size})
    return out


def rms_command(computer_name: str, ip: str) -> list[str]:
    host = ip if ip and ip != "Не найден" else computer_name
    if not is_valid_hostname(host):
        raise ValueError(f"Недопустимое имя узла: {host!r}")
    return [settings.rms_viewer_path, "-create", f"-name:{computer_name}", f"-host:{host}", "-fullcontrol"]


# --------------------------------------------------------------------------- CSV инвентаря
def _hardware_csv_path(computer_name: str) -> str | None:
    name = clean_computer_name(computer_name)
    if not name or not settings.invent_hardware_dir:
        return None
    for candidate in (f"{name}.csv", f"{name.lower()}.csv"):
        path = os.path.join(settings.invent_hardware_dir, candidate)
        if os.path.exists(path):
            return path
    return None


def parse_hardware_csv(file_path: str) -> dict:
    rows: list[list[str]] = []
    for enc in ("cp1251", "utf-8-sig"):
        try:
            with open(file_path, encoding=enc, errors="strict", newline="") as fh:
                rows = [r for r in csv.reader(fh, delimiter=";") if len(r) >= 4]
            if rows:
                break
        except (UnicodeDecodeError, OSError):
            continue
    return _structure_rows(rows)


def _structure_rows(rows: Iterable[list[str]]) -> dict:
    s: dict[str, dict] = {k: {} for k in
                          ("os", "board", "bios", "cpu", "rams", "disks", "logdisks", "gpu", "adapters", "printers")}
    for r in rows:
        cat, param, idx, val = (x.strip() for x in r[:4])
        low = cat.lower()
        if "операционная система" in low:
            s["os"][param] = val
        elif "материнская плата" in low:
            s["board"][param] = val
        elif "bios" in low:
            s["bios"][param] = val
        elif low == "процессор":
            s["cpu"][param] = val
        elif "память" in low:
            s["rams"].setdefault(idx, {})[param] = val
        elif low == "диск":
            s["disks"].setdefault(idx, {})[param] = val
        elif "локальный диск" in low or "логический диск" in low:
            s["logdisks"].setdefault(idx, {})[param] = val
        elif "видео" in low:
            s["gpu"][param] = val
        elif "сетевой адаптер" in low or "network adapter" in low:
            s["adapters"].setdefault(idx, {})[param] = val
        elif "принтер" in low or "printer" in low:
            s["printers"].setdefault(idx, {})[param] = val
    return s


# Живой сбор характеристик по CIM/WMI (WinRM → DCOM). Структура ответа — та же, что у инвентарного CSV
# (разделы os/board/bios/cpu/rams/disks/logdisks/gpu/adapters/printers, параметры по-русски), поэтому карточка
# человека, сравнение ПК, опись Excel и поиск принтеров работают одинаково и с CSV, и без него.
_PS_SPECS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$c = '__HOST__'

function FmtDate($v) {
    if ($null -eq $v) { return '' }
    if ($v -is [datetime]) { return $v.ToString('yyyy-MM-dd HH:mm:ss') }
    $s = [string]$v
    if ($s -match '^\d{14}') {
        return $s.Substring(0,4) + '-' + $s.Substring(4,2) + '-' + $s.Substring(6,2) + ' ' +
               $s.Substring(8,2) + ':' + $s.Substring(10,2) + ':' + $s.Substring(12,2)
    }
    return $s
}

function Q([string]$class, [string]$filter = '') {
    try {
        if ($filter) { return Get-CimInstance -ClassName $class -ComputerName $c -Filter $filter -ErrorAction Stop }
        return Get-CimInstance -ClassName $class -ComputerName $c -ErrorAction Stop
    } catch {
        try {
            if ($filter) { return Get-WmiObject -Class $class -ComputerName $c -Filter $filter -ErrorAction Stop }
            return Get-WmiObject -Class $class -ComputerName $c -ErrorAction Stop
        } catch { return $null }
    }
}

function FmtGB($bytes) {
    if ($null -eq $bytes) { return '' }
    try {
        $n = [double]$bytes
        if ($n -ge 1TB) { return ('{0:N1} ТБ' -f ($n / 1TB)) }
        if ($n -ge 1GB) { return ('{0:N0} ГБ' -f ($n / 1GB)) }
        return ('{0:N0} МБ' -f ($n / 1MB))
    } catch { return '' }
}

$os = Q 'Win32_OperatingSystem'
$cs = Q 'Win32_ComputerSystem'
$bb = Q 'Win32_BaseBoard'
$bios = Q 'Win32_BIOS'
$cpu = Q 'Win32_Processor'
$rams = Q 'Win32_PhysicalMemory'
$ld = Q 'Win32_LogicalDisk' 'DriveType=3'
$gpu = Q 'Win32_VideoController'
$nic = Q 'Win32_NetworkAdapterConfiguration' 'IPEnabled=true'
$prn = Q 'Win32_Printer'
$dd = Q 'Win32_DiskDrive'

# физические диски: сначала Storage-классы (модель/SSD/NVMe), при недоступности — Win32_DiskDrive
$pd = $null
try { $pd = Get-CimInstance -Namespace 'root/Microsoft/Windows/Storage' -ClassName MSFT_PhysicalDisk -ComputerName $c -ErrorAction Stop } catch { $pd = $null }
$busNames = @{ 1 = 'SCSI'; 3 = 'ATA'; 7 = 'USB'; 8 = 'RAID'; 10 = 'SAS'; 11 = 'SATA'; 17 = 'NVMe' }
$mediaNames = @{ 3 = 'HDD'; 4 = 'SSD'; 5 = 'SCM' }

$diskList = @()
if ($pd) {
    foreach ($p in @($pd)) {
        $diskList += @{ 'Наименование' = [string]$p.FriendlyName
                        'Тип носителя' = [string]$mediaNames[[int]$p.MediaType]
                        'Интерфейс' = [string]$busNames[[int]$p.BusType]
                        'Размер' = FmtGB $p.Size
                        'Серийный номер' = [string]$p.SerialNumber }
    }
}
foreach ($d0 in @($dd)) {
    if (-not $d0) { continue }
    $m = [string]$d0.Model
    $media = ''
    if ($m -match 'SSD|NVMe|Solid State') { $media = 'SSD' }
    $iface = [string]$d0.InterfaceType
    $diskList += @{ 'Наименование' = if ($m) { $m } else { [string]$d0.Caption }
                    'Тип носителя' = $media
                    'Интерфейс' = $iface
                    'Размер' = FmtGB $d0.Size
                    'Серийный номер' = [string]$d0.SerialNumber }
}

# принтеры: порт по возможности заменяем адресом TCP-порта, плюс «Расположение» (у WSD-портов там http://IP)
$portMap = @{}
try { Get-CimInstance Win32_TCPIPPrinterPort -ComputerName $c -ErrorAction Stop | ForEach-Object { $portMap[$_.Name] = [string]$_.HostAddress } } catch {
    try { Get-WmiObject Win32_TCPIPPrinterPort -ComputerName $c -ErrorAction Stop | ForEach-Object { $portMap[$_.Name] = [string]$_.HostAddress } } catch {}
}

$ramsList = @(foreach ($r in @($rams)) {
    if (-not $r) { continue }
    $maker = [string]$r.Manufacturer
    if ($maker -match '^[0-9A-F]{2}([0-9A-F]{2})+$') { $maker = '' }   # у части планок «производитель» — hex-мусор
    @{ 'Объём' = FmtGB $r.Capacity; 'Частота' = ('' + $r.Speed + ' МГц'); 'Производитель' = $maker;
       'Форм-фактор' = [string]$r.FormFactor; 'Серийный номер' = [string]$r.SerialNumber }
})

$logList = @(foreach ($l in @($ld)) {
    if (-not $l) { continue }
    @{ 'Буква' = [string]$l.DeviceID; 'Метка' = [string]$l.VolumeName; 'Файловая система' = [string]$l.FileSystem
       'Размер' = FmtGB $l.Size; 'Свободно' = FmtGB $l.FreeSpace }
})

$nicList = @(foreach ($n in @($nic)) {
    if (-not $n) { continue }
    @{ 'Название' = [string]$n.Description; 'MAC' = [string]$n.MACAddress
       'IP' = (@($n.IPAddress) -join ', '); 'DHCP' = $(if ($n.DHCPEnabled) { 'да' } else { 'нет' })
       'Шлюз' = (@($n.DefaultIPGateway) -join ', ') }
})

$prnList = @(foreach ($p in @($prn)) {
    if (-not $p) { continue }
    $port = [string]$p.PortName
    if ($portMap[$port]) { $port = [string]$portMap[$port] }
    @{ 'Наименование' = [string]$p.Name; 'Порт' = $port
       'По умолчанию' = $(if ($p.Default) { 'да' } else { 'нет' })
       'Расположение' = [string]$p.Location; 'Драйвер' = [string]$p.DriverName }
})

$g = @($gpu) | Select-Object -First 1

[pscustomobject]@{
    os = [ordered]@{ 'Название' = [string]$os.Caption; 'Версия' = [string]$os.Version
                     'Сборка' = [string]$os.BuildNumber; 'Установлена' = FmtDate $os.InstallDate }
    board = @{ 'Производитель' = [string]$bb.Manufacturer; 'Модель' = [string]$bb.Product
               'Серийный номер' = [string]$bb.SerialNumber }
    bios = @{ 'Производитель' = [string]$bios.Manufacturer; 'Версия' = [string]$bios.SMBIOSBIOSVersion
              'Дата' = FmtDate $bios.ReleaseDate }
    cpu = [ordered]@{ 'Название' = ([string]$cpu.Name).Trim(); 'Частота' = ('' + $cpu.MaxClockSpeed + ' МГц')
                      'Ядра' = '' + $cpu.NumberOfCores; 'Потоки' = '' + $cpu.NumberOfLogicalProcessors
                      'Разъём' = [string]$cpu.SocketDesignation }
    rams = $ramsList
    disks = $diskList
    logdisks = $logList
    gpu = @{ 'Название' = [string]$g.Name; 'Объём памяти' = FmtGB $g.AdapterRAM
             'Версия драйвера' = [string]$g.DriverVersion }
    adapters = $nicList
    printers = $prnList
    system = [ordered]@{ 'Производитель' = [string]$cs.Manufacturer; 'Модель' = [string]$cs.Model
                         'Тип' = [string]$cs.PCSystemType; 'Пользователь' = [string]$cs.UserName }
} | ConvertTo-Json -Compress -Depth 5
"""


def parse_specs_json(text: str) -> dict:
    """JSON живого опроса → та же структура, что у :func:`parse_hardware_csv` (все значения — строки)."""
    d = json.loads(text)

    def one(name: str) -> dict:
        v = d.get(name)
        return {str(k): ("" if vv is None else str(vv)) for k, vv in (v or {}).items()} if isinstance(v, dict) else {}

    def many(name: str) -> dict:
        v = d.get(name)
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, list):
            v = []
        return {str(i): {str(k): ("" if vv is None else str(vv)) for k, vv in x.items()}
                for i, x in enumerate(v) if isinstance(x, dict)}

    out = {"os": one("os"), "board": one("board"), "bios": one("bios"), "cpu": one("cpu"), "gpu": one("gpu"),
           "system": one("system"), "rams": many("rams"), "disks": many("disks"), "logdisks": many("logdisks"),
           "adapters": many("adapters"), "printers": many("printers")}
    return {k: v for k, v in out.items() if v} or {"error": "ПК не вернул характеристик"}


def collect_specs_live(computer_name: str, timeout: int = 90, cancelled=None) -> dict:
    """Живой сбор характеристик (CIM/WMI: WinRM → DCOM). Ничего не пишет — сохранением занимается вызывающий.

    ``cancelled`` (3.9.0) — проверка «пользователь нажал Стоп» для долгого полного опроса парка."""
    name = clean_computer_name(computer_name)
    if not name or not is_valid_hostname(name):
        return {"error": f"Недопустимое имя узла: {computer_name!r}"}
    from . import psrun
    res = psrun.run(_PS_SPECS.replace("__HOST__", name), timeout=timeout, cancelled=cancelled)
    if not res.ok:
        return {"error": res.error}
    try:
        return parse_specs_json(res.stdout)
    except ValueError as exc:
        return {"error": f"Разбор ответа: {exc}"}


# сколько дней собранные характеристики считаются свежими; потом при очередном просмотре пересобираются
SPECS_TTL_DAYS = 14


def _specs_stale(ts: str) -> bool:
    if not ts:
        return True
    try:
        return (datetime.now() - datetime.fromisoformat(ts)).days >= SPECS_TTL_DAYS
    except ValueError:
        return True


def _load_cached_specs(computer_name: str) -> tuple[dict | None, str]:
    """(характеристики из pc_inventory.specs, отметка времени сбора) или (None, '')."""
    name = clean_computer_name(computer_name)
    if not name:
        return None, ""
    try:
        row = db_execute_with_retry("SELECT specs FROM pc_inventory WHERE computer_name = ?", (name,), fetch="one")
    except Exception:  # noqa: BLE001 — кэш не критичен, база может быть недоступна
        return None, ""
    raw = (row[0] if row else "") or ""
    if not raw.strip().startswith("{"):
        return None, ""
    try:
        d = json.loads(raw)
    except ValueError:
        return None, ""
    if not isinstance(d, dict):
        return None, ""
    ts = str(d.pop("_ts", "") or "")
    return (d or None), ts


def get_computer_specs_dict(computer_name: str, live: bool = True, timeout: int = 90) -> dict:
    """Характеристики ПК: инвентарный CSV → кэш в базе → живой опрос (CIM/WMI, WinRM → DCOM).

    Живой опрос автоматически сохраняется в ``pc_inventory.specs`` — карточка «Характеристики ПК», опись Excel
    и поиск принтеров работают и там, где CSV-инвентаризации нет вообще. ``live=False`` — только CSV и кэш
    (для мест, где ждать опроса нельзя, например меню выбора диска).
    """
    name = clean_computer_name(computer_name)
    path = _hardware_csv_path(name)
    if path:
        return parse_hardware_csv(path)
    cached, ts = _load_cached_specs(name)
    if cached is not None and not (live and _specs_stale(ts)):
        return cached
    if not live:
        if cached is not None:
            return cached
        return {"error": f"Характеристики {name or '—'} не собраны: CSV не найден, в базе данных тоже ничего нет"}
    specs = collect_specs_live(name, timeout=timeout)
    if "error" not in specs:
        try:
            from . import db
            db.save_specs(name, specs)
        except Exception as exc:  # noqa: BLE001
            log.debug("save specs: %s", exc)
        return specs
    return specs if cached is None else cached


# Виртуальные/стандартные принтеры Windows — шум, пользователю они не нужны.
_VIRTUAL_PRINTER_RE = re.compile(
    r"(?i)(microsoft (print to pdf|xps)|onenote|fax|факс|send to|adobe pdf|foxit|pdf-?creator|pdf24|"
    r"bullzip|dopdf|cutepdf|nitro|snagit|evernote|journal|generic ?/ ?text|root print queue|"
    r"\bpdf\b.*(printer|writer)|xps document)")
_IP_IN_PORT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3})")


def classify_printer_port(port: str) -> tuple[str, str]:
    """Порт → (тип, ip). Типы: 'network' (IP_/TCP-порт), 'shared' (\\\\server\\queue), 'usb', 'local', 'virtual'."""
    p = (port or "").strip()
    low = p.lower()
    m = _IP_IN_PORT_RE.search(p)
    if m and all(int(o) <= 255 for o in m.group(1).split(".")):     # «192.168.1.300» — не адрес
        return "network", m.group(1)
    if low.startswith(("ip_", "wsd", "tcp", "lan")) or "://" in low:
        return "network", ""
    if p.startswith("\\\\"):
        return "shared", ""
    if low.startswith(("usb", "dot4", "hp_")) or "usb" in low:
        return "usb", ""
    if low.startswith(("lpt", "com")):
        return "local", ""
    if low in ("nul:", "nul", "portprompt:", "shrfax:", "file:", "xpsport:", "pdf", "pdfcreator", "print2pdf") or "pdf" in low or "file" in low:
        return "virtual", ""
    return "local", ""


def is_virtual_printer(name: str, port: str = "") -> bool:
    return bool(_VIRTUAL_PRINTER_RE.search(name or "")) or classify_printer_port(port)[0] == "virtual"


def printers_from_specs(d: dict) -> list[dict]:
    """[{name, port, kind, ip, is_default}] без виртуальных/стандартных принтеров."""
    out: list[dict] = []
    for info in d.get("printers", {}).values():
        name = _first_match(info, "наименование", "название", "модель", "name", default="")
        port = _first_match(info, "порт", "port", default="")
        loc = _first_match(info, "расположение", "location", "адрес", "url", default="")
        if not name or is_virtual_printer(name, port):
            continue
        kind, ip = classify_printer_port(port)
        if kind == "network" and not ip and loc:
            ip = ip_from_text(loc)          # WSD-порт без адреса, но в «Расположении» есть http://IP:3911/
        default_raw = _first_match(info, "по умолчанию", "default", default="").lower()
        out.append({"name": name.strip(), "port": port, "kind": kind, "ip": ip,
                    "is_default": default_raw in ("да", "yes", "true", "1", "истина")})
    return out


def get_computer_printers_detailed(computer_name: str, cache: bool = True) -> list[dict]:
    """Принтеры ПК из инвентарного CSV; при наличии CSV обновляет кэш pc_printers (для поиска по принтеру)."""
    d = get_computer_specs_dict(computer_name)
    if "error" in d:
        return []
    printers = printers_from_specs(d)
    if cache:
        try:
            from . import db
            db.replace_printers(computer_name, printers)
        except Exception as exc:  # noqa: BLE001
            log.debug("printers cache: %s", exc)
    return printers


def index_printers(hosts, progress=None, cancelled=None) -> int:
    """Пакетно обновляет кэш принтеров по CSV всех ПК (после сканирования). Возвращает число ПК с CSV."""
    from . import db
    done = 0
    hosts = list(hosts)
    for i, h in enumerate(hosts, 1):
        if cancelled and cancelled():
            break
        d = get_computer_specs_dict(h)
        if "error" not in d:
            db.replace_printers(h, printers_from_specs(d))
            done += 1
        if progress and (i % 50 == 0 or i == len(hosts)):
            progress(i, len(hosts))
    return done


_PS_LIVE_PRINTERS = r"""
$ErrorActionPreference = 'Stop'
$c = '__HOST__'
$ps = $null
try { $ps = Get-CimInstance Win32_Printer -ComputerName $c -ErrorAction Stop } catch {
  $w = $_.Exception.Message
  try { $ps = Get-WmiObject Win32_Printer -ComputerName $c -ErrorAction Stop } catch { throw ("Принтеры $c не прочитаны. WinRM: $w | DCOM: " + $_.Exception.Message) }
}
$ps = @($ps | Select-Object Name, PortName, Default, PrinterStatus, WorkOffline, DriverName, Location)
$ports = @{}
try { Get-CimInstance Win32_TCPIPPrinterPort -ComputerName $c -ErrorAction Stop | ForEach-Object { $ports[$_.Name] = $_.HostAddress } } catch {
  try { Get-WmiObject Win32_TCPIPPrinterPort -ComputerName $c -ErrorAction Stop | ForEach-Object { $ports[$_.Name] = $_.HostAddress } } catch {}
}
ConvertTo-Json -InputObject @($ps | ForEach-Object { @{ name = $_.Name; port = $_.PortName; default = [bool]$_.Default; status = [int]$_.PrinterStatus
                            offline = [bool]$_.WorkOffline; driver = $_.DriverName; host = $ports[$_.PortName]; location = [string]$_.Location } }) -Compress -Depth 3
"""
PRINTER_STATUS = {1: "другое", 2: "неизвестно", 3: "готов", 4: "печатает", 5: "прогрев", 6: "остановлен", 7: "офлайн"}


def parse_live_printers_json(text: str) -> list[dict]:
    """JSON Win32_Printer → [{name, port, kind, ip, is_default, status, status_text, offline, driver}] без виртуальных."""
    import json
    raw = json.loads(text) if text.strip() else []
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for x in raw or []:
        name, port = (x.get("name") or "").strip(), (x.get("port") or "").strip()
        if not name or is_virtual_printer(name, port):
            continue
        kind, ip = classify_printer_port(port)
        if not ip and x.get("host"):
            ip = str(x["host"]).strip()
            kind = "network"
        loc = (x.get("location") or "").strip()
        if not ip and loc:
            ip = ip_from_text(loc)      # WSD-порт, но «Расположение» вида http://10.0.69.70:3911/
        st = int(x.get("status") or 0)
        out.append({"name": name, "port": port, "kind": kind, "ip": ip, "is_default": bool(x.get("default")),
                    "status": st, "status_text": "офлайн (WorkOffline)" if x.get("offline") else PRINTER_STATUS.get(st, "—"),
                    "offline": bool(x.get("offline")), "driver": (x.get("driver") or "").strip(), "location": loc})
    out.sort(key=lambda p: (not p["is_default"], p["name"].lower()))
    return out


def get_live_printers(computer_name: str, timeout: int = 45, cancelled=None) -> dict:
    """Принтеры ПК прямо сейчас (CIM Win32_Printer по WinRM/DCOM). **Ничего не пишет** ни в БД, ни в CSV —
    это разовый взгляд «как на самом деле», инвентарный кэш остаётся снимком сканера."""
    name = clean_computer_name(computer_name)
    if not name or not is_valid_hostname(name):
        return {"error": f"Недопустимое имя узла: {computer_name!r}"}
    from . import psrun
    res = psrun.run(_PS_LIVE_PRINTERS.replace("__HOST__", name), timeout=timeout, cancelled=cancelled)
    if not res.ok:
        return {"error": res.error}
    try:
        return {"printers": parse_live_printers_json(res.stdout)}
    except ValueError as exc:
        return {"error": f"Разбор ответа: {exc}"}


def printer_label(p: dict) -> str:
    kind = {"network": "сетевой", "shared": "общий", "usb": "USB", "local": "локальный"}.get(p.get("kind", ""), "")
    if p.get("ip"):
        extra = p["ip"]
    elif p.get("kind") in ("network", "shared") and p.get("port"):
        extra = p["port"]            # IP не известен — показываем честно порт, а не «не сетевой»
    else:
        extra = ""
    tail = " · ".join(x for x in (kind, extra) if x)
    return f"{p['name']} ({tail})" if tail else p["name"]


def get_computer_printers(computer_name: str) -> list[str]:
    """Строки для списков: «HP LaserJet (сетевой · 10.0.2.50)»."""
    return [printer_label(p) for p in get_computer_printers_detailed(computer_name)]


def _first_match(d: dict, *keys: str, default: str = "Н/Д") -> str:
    for k, v in d.items():
        kl = k.lower()
        if any(key in kl for key in keys) and v:
            return v
    return default


def _to_gb(raw: str) -> float | None:
    """'476,9 ГБ' | '476.9GB' | '512110190592' (байты) → гигабайты. Не разобралось → None."""
    s = str(raw).strip().upper().replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ГБ|GB|ТБ|TB|МБ|MB|КБ|KB)?", s)
    if m:
        n, u = float(m.group(1)), (m.group(2) or "")
        if u in ("ГБ", "GB"):
            return round(n, 1)
        if u in ("ТБ", "TB"):
            return round(n * 1024, 1)
        if u in ("МБ", "MB"):
            return round(n / 1024, 1)
        if u in ("КБ", "KB"):
            return round(n / 1024 ** 2, 1)
        # «голое» число: как и раньше — байты, если очень большое, иначе уже гигабайты
        return round(n / 1024 ** 3, 1) if n > 1024 * 1024 else round(n, 1)
    return None


def _to_mb(raw) -> int:
    """'8 ГБ' | '8192 МБ' | 8589934592 (байты) → мегабайты; «голое» число < 1 ГБ считается МБ. Не разобралось → 0."""
    s = str(raw).strip().upper().replace(" ", "").replace(",", ".")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ГБ|GB|ТБ|TB|МБ|MB|КБ|KB)?", s)
    if not m:
        return 0
    n, u = float(m.group(1)), (m.group(2) or "")
    if u in ("ГБ", "GB"):
        return int(round(n * 1024))
    if u in ("ТБ", "TB"):
        return int(round(n * 1024 * 1024))
    if u in ("КБ", "KB"):
        return int(round(n / 1024))
    if not u:
        return int(round(n / 1024 / 1024)) if n > 1024 * 1024 else int(round(n))
    return int(round(n))


def _freq_mhz(raw) -> int:
    m = re.search(r"\d{3,4}", str(raw))
    return int(m.group(0)) if m else 0


def ip_from_text(text: str) -> str:
    """Первый корректный IPv4 в любой строке: 'http://10.0.69.70:3911/' → '10.0.69.70'."""
    for m in _IP_IN_PORT_RE.finditer(text or ""):
        if all(int(o) <= 255 for o in m.group(1).split(".")):
            return m.group(1)
    return ""


def _pick_named(d: dict, *needles: str) -> str:
    """Значение первого параметра, чьё имя содержит одну из подстрок (по приоритету подстрок);
    если ничего не нашлось — первое непустое значение (как в CSV-сводке раньше)."""
    for n in needles:
        for k, v in d.items():
            if n in k.lower() and v:
                return v
    for v in d.values():
        if v:
            return v
    return "Н/Д"


def summarize_specs(d: dict) -> str:
    """Краткая сводка. Неизвестное — «Н/Д», ничего не додумывается."""
    if not d or "error" in d:
        return d.get("error", "Характеристики не собраны") if d else "Характеристики не собраны"
    os_name = _pick_named(d.get("os", {}), "название", "имя")
    cpu = _pick_named(d.get("cpu", {}), "название", "имя", "модель")

    total_mb = 0
    freqs: set[int] = set()
    for m in d.get("rams", {}).values():
        for k, v in m.items():
            kl = k.lower()
            # «Размер»/«Объём»: байты, «8192 МБ» или «8 ГБ» — и живой опрос, и CSV понимаются одинаково
            if ("размер" in kl or "объём" in kl or "объем" in kl) and v:
                total_mb += _to_mb(v)
            elif ("частота" in kl or "speed" in kl) and v:
                f = _freq_mhz(v)
                if f:
                    freqs.add(f)
    ram = "Н/Д"
    if total_mb:
        ram = f"{round(total_mb / 1024, 1)} ГБ"
        if freqs:
            ram += f" ({max(freqs)} МГц)"

    disks = []
    for info in d.get("disks", {}).values():
        model = _first_match(info, "наименование", "модель", "name", default="Диск")
        size = None
        for k, v in info.items():
            if any(x in k.lower() for x in ("размер", "емкость", "ёмкость", "size")):
                size = _to_gb(v)
                break
        media = _first_match(info, "тип носителя", "mediatype", "media", default="")
        parts = [f"{size} ГБ" if size else "Н/Д", media, f"({model})"]
        disks.append(" ".join(p for p in parts if p))
    return (f"ОС: {os_name}\nCPU: {cpu}\nОЗУ: {ram}\n"
            f"Диски: {' + '.join(disks) if disks else 'Н/Д'}")


def get_computer_specs_summary(computer_name: str) -> str:
    """Сводка «ОС/CPU/ОЗУ/Диски» для описи Excel: CSV → кэш БД. Живой опрос здесь НЕ запускается
    (опись строится по всем ПК организации — каждый ПК опрашивать недопустимо долго)."""
    path = _hardware_csv_path(computer_name)
    if path:
        return summarize_specs(parse_hardware_csv(path))
    cached, _ = _load_cached_specs(clean_computer_name(computer_name))
    if cached is not None:
        return summarize_specs(cached)
    return "Характеристики не собраны"


def get_pc_info_from_csv(pc_name: str, login: str, fio: str) -> tuple[str, str]:
    """(ip, дата) последнего входа пользователя на ПК по журналам comp/compexit."""
    name = clean_computer_name(pc_name)
    if not name:
        return "Не найден", "Нет данных"
    ip, date = "Не найден", "Нет данных"
    needles = [x.lower() for x in (login, fio) if x]
    for base in (settings.invent_comp_dir, settings.invent_compexit_dir):
        if not base:
            continue
        path = os.path.join(base, f"{name}.csv")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="cp1251", errors="ignore") as fh:
                for line in fh:
                    low = line.lower()
                    if not any(n in low for n in needles):
                        continue
                    parts = line.split(";" if ";" in line else ",")
                    if len(parts) >= 4 and parts[3].strip():
                        ip = parts[3].strip().split(",")[0].strip()
                    if len(parts) >= 5 and parts[4].strip():
                        date = parts[4].strip()
        except OSError:
            continue
    return ip, date


# ---------------------------------------------------------------- список ПК домена через ADSI (запасной путь)
_ADSI_COMPUTERS_PS = r"""
$ErrorActionPreference = 'Stop'
try {
  $s = [adsisearcher]'(&(objectCategory=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))'
  $s.PageSize = 1000
  $s.PropertiesToLoad.AddRange(@('name','operatingSystem')) | Out-Null
  $out = New-Object System.Collections.Generic.List[string]
  foreach ($r in $s.FindAll()) {
    $os = [string]$r.Properties['operatingsystem']
    if ($os -like '*Server*') { continue }
    $n = ([string]$r.Properties['name']).Trim()
    if ($n) { $out.Add($n.ToUpper()) }
  }
  $out -join "`n"
} catch { Write-Output ("ADSI_ERROR: " + $_.Exception.Message); exit 1 }
"""


def adsi_computer_names(timeout: float = 120.0) -> list[str]:
    """Имена рабочих станций домена через ADSI (PowerShell ``[adsisearcher]``), без LDAP-библиотек.

    Запасной путь, если ldap3 не смог (3.11.0): именно так получала список ПК старая программа
    (ADODB + GC://), т.е. способ проверен в тех же сетях. Работает от учётки вошедшего пользователя,
    фильтры те же: компьютеры, не отключённые, не серверы.
    """
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", _ADSI_COMPUTERS_PS]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ADSI-запрос не выполнился: {exc}") from exc
    if cp.returncode != 0 or cp.stdout.startswith("ADSI_ERROR"):
        raise RuntimeError(f"ADSI-запрос вернул ошибку: {(cp.stdout or cp.stderr or '').strip()[:300]}")
    return [n for n in (x.strip().upper() for x in cp.stdout.splitlines()) if n]
