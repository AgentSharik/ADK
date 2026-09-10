"""Сеть и инвентарные CSV: ping/DNS, характеристики ПК, удалённые действия."""
from __future__ import annotations

import csv
import logging
import os
import platform
import re
import socket
import subprocess
import threading
import time
from typing import Iterable

from .config import CREATE_NO_WINDOW, settings
from .db import clean_computer_name, db_execute_with_retry

log = logging.getLogger(__name__)

_HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,62}$")
_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_PING_OK_RE = re.compile(
    r"(?i)(?:ответ от|reply from)\s+([a-f0-9.:]+).*?(?:байт|bytes)=(\d+).*?"
    r"(?:время|time)[=<]\s*([0-9.]+\s*(?:мс|ms)).*?ttl=(\d+)"
)


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
    for port in (9100, 631):
        if port_open(port):
            return {"alive": True, "is_printer": True, "evidence": f"открыт порт печати {port}"}
    for port in (445, 3389, 135):
        if port_open(port):
            return {"alive": True, "is_printer": False, "evidence": f"открыт порт {port} — это компьютер, а не принтер"}
    for port in (80, 443):
        if not port_open(port):
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
    try:
        ip = socket.gethostbyname(name)
    except OSError:
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
        return {"ip": m.group(1).rstrip(":"), "bytes": m.group(2), "time": m.group(3),
                "ttl": m.group(4), "status": "Успешно", "success": True, "is_info": False}
    m = re.search(r"(?i)^(\d+) bytes from ([a-f0-9.:]+)[:\s].*?ttl=(\d+).*?time=([0-9.]+\s*ms)", line)
    if m:  # формат iputils/BSD — для запуска вне Windows
        return {"ip": m.group(2), "bytes": m.group(1), "time": m.group(4).replace(" ", ""),
                "ttl": m.group(3), "status": "Успешно", "success": True, "is_info": False}
    m = re.search(r"(?i)(?:ответ от|reply from)\s+([a-f0-9.:]+):\s*(.+)", line)
    if m:
        return {"ip": m.group(1).rstrip(":"), "bytes": "—", "time": "—", "ttl": "—",
                "status": line, "success": False, "is_info": False}
    if any(k in low for k in ("превышен", "timed out", "сбой", "general failure", "unreachable")):
        return {"ip": target, "bytes": "—", "time": "—", "ttl": "—", "status": line,
                "success": False, "is_info": False}
    if "обмен пакетами" in low or "pinging" in low or low.startswith("ping "):
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
    spec = get_computer_specs_dict(computer_name)
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


def get_computer_specs_dict(computer_name: str) -> dict:
    path = _hardware_csv_path(computer_name)
    if not path:
        return {"error": f"CSV с характеристиками для {clean_computer_name(computer_name) or '—'} не найден"}
    return parse_hardware_csv(path)


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
        if not name or is_virtual_printer(name, port):
            continue
        kind, ip = classify_printer_port(port)
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
$ps = Get-CimInstance Win32_Printer -ComputerName $c | Select-Object Name, PortName, Default, PrinterStatus, WorkOffline, DriverName
$ports = @{}
try { Get-CimInstance Win32_TCPIPPrinterPort -ComputerName $c | ForEach-Object { $ports[$_.Name] = $_.HostAddress } } catch {}
@($ps | ForEach-Object { @{ name = $_.Name; port = $_.PortName; default = [bool]$_.Default; status = [int]$_.PrinterStatus
                            offline = [bool]$_.WorkOffline; driver = $_.DriverName; host = $ports[$_.PortName] } }) | ConvertTo-Json -Compress -Depth 3
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
        st = int(x.get("status") or 0)
        out.append({"name": name, "port": port, "kind": kind, "ip": ip, "is_default": bool(x.get("default")),
                    "status": st, "status_text": "офлайн (WorkOffline)" if x.get("offline") else PRINTER_STATUS.get(st, "—"),
                    "offline": bool(x.get("offline")), "driver": (x.get("driver") or "").strip()})
    out.sort(key=lambda p: (not p["is_default"], p["name"].lower()))
    return out


def get_live_printers(computer_name: str, timeout: int = 30) -> dict:
    """Принтеры ПК прямо сейчас (CIM Win32_Printer по WinRM/DCOM). **Ничего не пишет** ни в БД, ни в CSV —
    это разовый взгляд «как на самом деле», инвентарный кэш остаётся снимком сканера."""
    name = clean_computer_name(computer_name)
    if not name or not is_valid_hostname(name):
        return {"error": f"Недопустимое имя узла: {computer_name!r}"}
    if os.name != "nt":
        return {"error": "Живой опрос доступен только с Windows (PowerShell/CIM)"}
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_LIVE_PRINTERS.replace("__HOST__", name)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                             timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return {"error": f"ПК не ответил за {timeout} с"}
    except (subprocess.SubprocessError, OSError) as exc:
        return {"error": f"PowerShell: {exc}"}
    if res.returncode != 0:
        return {"error": (res.stderr or "нет ответа").strip().splitlines()[0][:200]}
    try:
        return {"printers": parse_live_printers_json(res.stdout)}
    except ValueError as exc:
        return {"error": f"Разбор ответа: {exc}"}


def printer_label(p: dict) -> str:
    kind = {"network": "сетевой", "shared": "общий", "usb": "USB", "local": "локальный"}.get(p.get("kind", ""), "")
    extra = p.get("ip") or (p.get("port") if p.get("kind") == "shared" else "")
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
    try:
        f = float(str(raw).replace(" ", "").replace(",", "."))
    except ValueError:
        return None
    return round(f / 1024 ** 3, 1) if f > 1024 * 1024 else round(f, 1)


def summarize_specs(d: dict) -> str:
    """Краткая сводка. Неизвестное — «Н/Д», ничего не додумывается."""
    if not d or "error" in d:
        return d.get("error", "Характеристики не собраны") if d else "Характеристики не собраны"
    os_name = next(iter(d.get("os", {}).values()), "Н/Д")
    cpu = next(iter(d.get("cpu", {}).values()), "Н/Д")

    total_mb = 0
    freqs: set[int] = set()
    for m in d.get("rams", {}).values():
        for k, v in m.items():
            kl = k.lower()
            if "размер" in kl and v.isdigit():
                n = int(v)
                total_mb += n // (1024 * 1024) if n > 1024 * 1024 else n  # байты или мегабайты
            elif "частота" in kl and v.isdigit():
                freqs.add(int(v))
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
    path = _hardware_csv_path(computer_name)
    if path:
        return summarize_specs(parse_hardware_csv(path))
    row = db_execute_with_retry("SELECT specs FROM pc_inventory WHERE computer_name = ?",
                                (clean_computer_name(computer_name),), fetch="one")
    return row[0] if row and row[0] else "Характеристики не собраны"


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
