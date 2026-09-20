"""Конфигурация приложения.

Все параметры, специфичные для организации (домен, сетевые пути, RMS), вынесены
в ``config.ini``. В собранном exe папка ``ADK`` с конфигом, базой и логом создаётся
рядом с ``ADK.exe`` (портативно; если рядом с exe нет прав на запись — «Документы\\ADK»).
В коде нет ни одного жёстко прописанного адреса — репозиторий можно публиковать.
"""
from __future__ import annotations

import configparser
import logging
import os
import sys

APP_NAME = "ADK"
APP_TITLE = "ADK — Active Directory Kit"

IS_WINDOWS = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def _try_portable_dir(exe_dir: str) -> str | None:
    """Проверить, что рядом с exe можно создать папку ADK и писать в неё. None — нельзя."""
    cand = os.path.join(exe_dir, APP_NAME)
    try:
        os.makedirs(cand, exist_ok=True)
        probe = os.path.join(cand, ".write-test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return cand
    except OSError:
        return None


def _migrate_old_data(old: str, new: str) -> None:
    """3.10.0: перенос данных из «Документы\\ADK» в папку рядом с exe (однократно, при первом запуске новой версии)."""
    if not os.path.isdir(old) or os.path.exists(os.path.join(new, "config.ini")):
        return
    import shutil
    try:
        for name in os.listdir(old):
            try:
                if not os.path.exists(os.path.join(new, name)):
                    shutil.move(os.path.join(old, name), os.path.join(new, name))
            except OSError:
                logging.getLogger(__name__).warning("перенос %s в %s не удался — файл оставлен на месте", name, new)
        try:
            os.rmdir(old)
        except OSError:
            pass
    except OSError:
        pass
    # абсолютные пути в конфиге (db_path, папки инвентарей, бэкапов), ведущие в старую папку, —
    # переписать на новую: иначе база «потерялась» бы (файл перенесён, а путь остался прежним)
    cfg = os.path.join(new, "config.ini")
    if os.path.exists(cfg):
        try:
            with open(cfg, encoding="utf-8") as fh:
                txt = fh.read()
            if old in txt:
                with open(cfg, "w", encoding="utf-8") as fh:
                    fh.write(txt.replace(old, new))
        except OSError:
            pass


def _data_dir() -> tuple[str, bool]:
    """Каталог данных ADK. Возвращает (путь, портативный_ли).

    3.10.0: в собранном виде (exe) — папка ``ADK`` **рядом с ADK.exe** (портативный режим: конфиг, база,
    лог, плагины — всё в одном месте). Если рядом с exe писать нельзя (Program Files, сетевой диск
    только для чтения) — прежнее поведение, ``Документы\\ADK``. При первом запуске новой версии данные
    из «Документы\\ADK» переносятся в новую папку. В режиме разработки (python) — по-прежнему «Документы».
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cand = _try_portable_dir(exe_dir)
        if cand:
            _migrate_old_data(os.path.join(os.path.expanduser("~"), "Documents", APP_NAME), cand)
            return cand, True
    return os.path.join(os.path.expanduser("~"), "Documents", APP_NAME), False


DOCS_DIR, _PORTABLE = _data_dir()
PORTABLE_DIR = DOCS_DIR if _PORTABLE else None    # не None — работает портативный режим (папка ADK рядом с exe)
INI_FILE = os.path.join(DOCS_DIR, "config.ini")
LOG_FILE = os.path.join(DOCS_DIR, "adk.log")
PLUGINS_DIR = os.path.join(DOCS_DIR, "plugins")


def normalize_search_base(base: str) -> str:
    """Убрать ADSI-префикс, если он попал в config из старой программы («GC://DC=…», «LDAP://DC=…»).

    ldap3 ждёт чистый DN: с префиксом любой поиск по домену молча возвращает пустоту (3.11.0).
    """
    b = (base or "").strip()
    for p in ("GC://", "LDAP://", "LDAPS://"):
        if b.upper().startswith(p):
            return b[len(p):].strip()
    return b


def park_pattern(text: str) -> str:
    """Превратить то, что ввёл пользователь, в элемент маски парка (host_mask).

    «PC-» → ``PC-*`` (все ПК с таким началом); «PC-0000» → ``PC-????`` (каждая цифра — ровно одна
    позиция); «PC» без дефиса — точное имя. 3.10.0.
    """
    t = text.strip().upper()
    if not t:
        return ""
    out = "".join("?" if (c.isascii() and c.isdigit()) else c for c in t)
    if t[-1] in "-_ ./\\":
        out += "*"
    return out


# Флаги userAccountControl
ACCOUNT_DISABLE_FLAG = 0x0002
NORMAL_ACCOUNT_FLAG = 0x0200
SMARTCARD_REQUIRED_FLAG = 0x40000

DB_RETRY_ATTEMPTS = 3
DB_RETRY_DELAY_SEC = 1.0
LDAP_PAGE_SIZE = 500
SEARCH_RESULT_LIMIT = 40

_DEFAULTS: dict[str, dict[str, str]] = {
    "AD": {
        "domain_netbios": "EXAMPLE",
        "dc_host": "dc01.example.local",
        "search_base": "DC=example,DC=local",
        "users_ou": "OU=Employees,DC=example,DC=local",
        "upn_suffix": "example.local",
        # 3.10.0: по умолчанию без LDAPS — обычный порт 389, смена пароля через net user /domain (SAMR).
        # Включать use_ssl = true нужно только для шифрования LDAPS (порт 636).
        "use_ssl": "false",
        "tls_validate": "false",
        "connect_timeout": "5",
        "max_password_age_days": "90",
    },
    "Paths": {
        "db_path": os.path.join(DOCS_DIR, "pc_mapping.db"),
        "invent_hardware_dir": "",
        "invent_comp_dir": "",
        "invent_compexit_dir": "",
        "pst_backup_base": "",
        "rms_viewer_path": r"C:\Program Files (x86)\Remote Manipulator System - Viewer\rutview.exe",
    },
    "Scanner": {
        "auto_scan_interval_min": "30",
        "host_pattern": r"^(WS-\d+|PC-.*)$",
        "host_exclude": r"(VIRT|VM|VBOX|TEST|SRV|SQL|SERVER)",
        "valid_subnets": "10.,192.168.,172.",
        # DHCP-серверы для сверки «Свободного IP» (через запятую). Пусто — только ICMP + PTR + инвентарь.
        # Нужен модуль DhcpServer (RSAT) на ПК администратора и право чтения на сервере DHCP.
        "dhcp_servers": "",
    },
    "Design": {
        # по умолчанию — тема «Графит» (см. theme.PRESET_THEMES["dark"])
        "bg_style": "background-color: #1C1C1E;",
        "is_dark": "true",
        "font_family": "Inter",       # встроенный шрифт из assets/fonts; свой выбирается в «Оформление → Шрифт»
        "font_size": "10",
        "accent_color": "#0A84FF",
        "panel_color": "#2C2C2E",
        "text_color": "#F5F5F7",
        "border_color": "#48484A",
        "follow_system": "false",     # тёмная/светлая — как в Windows (AppsUseLightTheme)
    },
    "UI": {
        "language": "ru",             # ru | en
        "minimize_to_tray": "true",   # показывать значок в трее при работающем ADK (закрытие крестиком — всегда выход)
        "global_hotkey": "Ctrl+Shift+A",  # показать окно и перейти в поиск (Windows, RegisterHotKey); пусто — выключить
        "login_method": "",           # запомненный способ входа: sso | password | пусто — спрашивать как обычно
        "hide_role_welcome": "false", # скрывать окно со справкой по роли после входа
    },
    "Access": {
        # Два независимых права по группам AD администратора (memberOf):
        #   pc_admin_groups — действия с ПК (перезагрузка, WoL, ПО, заметки, плагины);
        #   ad_admin_groups — изменения объектов AD (атрибуты, УЗ, пароли, группы, создание).
        # admin_groups — оба права сразу (совместимость); readonly_group / readonly = true — отнимают оба.
        # Пустые списки — право есть у всех.
        "readonly": "false",
        "readonly_group": "",
        "admin_groups": "",
        "pc_admin_groups": "",
        "ad_admin_groups": "",
    },
    "Updates": {
        # Файл с номером свежей версии (обычно рядом с exe на сетевом диске): 1-я строка — версия,
        # 2-я — путь/URL, откуда брать. Пусто — проверка отключена.
        "version_file": "",
    },
    "Plugins": {
        "dir": PLUGINS_DIR,           # *.py с классом Action — дополнительные кнопки в инспекторе
    },
    "Notify": {
        # Уведомления по почте о важных событиях журнала (сброс пароля привилегированной УЗ, отключение УЗ, массовые операции…).
        # SMTP-сервер без аутентификации или с логином/паролем.
        "smtp_host": "",
        "smtp_port": "25",
        "smtp_from": "",
        "smtp_to": "",
        "smtp_user": "",
        "smtp_password": "",
        "smtp_tls": "false",
        # какие действия журнала отправлять (через запятую); * — все
        "actions": "reset_password,disable_user,bulk_disable,bulk_reset_password,create_user",
        # для каких целей — всегда (например, привилегированные учётки), через запятую
        "watch_logins": "",
    },
    "Attention": {
        "acct_days": "7",             # учётная запись (accountExpires) истекает в ближайшие N дней
        "no_logon_days": "90",        # учётка без входа N дней
        "stale_pc_days": "30",        # ПК не был в сети N дней
        "refresh_min": "30",          # период обновления сводки в фоне; 0 — только вручную
    },
}


def _ensure_dirs() -> None:
    try:
        os.makedirs(DOCS_DIR, exist_ok=True)
    except OSError:
        pass



def detect_ad_domain_params() -> dict[str, str]:
    """Автоопределение параметров Active Directory при запуске на доменной машине Windows."""
    params: dict[str, str] = {}
    dns_domain = os.environ.get("USERDNSDOMAIN", "").strip().lower()
    netbios = os.environ.get("USERDOMAIN", "").strip().upper()
    logon_srv = os.environ.get("LOGONSERVER", "").strip().lstrip("\\").lower()

    if IS_WINDOWS:
        try:
            import ctypes
            from ctypes import wintypes

            class DOMAIN_CONTROLLER_INFO(ctypes.Structure):
                _fields_ = [
                    ("DomainControllerName", wintypes.LPWSTR),
                    ("DomainControllerAddress", wintypes.LPWSTR),
                    ("DomainControllerAddressType", wintypes.ULONG),
                    ("DomainGuid", ctypes.c_byte * 16),
                    ("DomainName", wintypes.LPWSTR),
                    ("DnsForestName", wintypes.LPWSTR),
                    ("Flags", wintypes.ULONG),
                    ("DcSiteName", wintypes.LPWSTR),
                    ("ClientSiteName", wintypes.LPWSTR),
                ]

            pinfo = ctypes.POINTER(DOMAIN_CONTROLLER_INFO)()
            res = ctypes.windll.netapi32.DsGetDcNameW(None, None, None, None, 0x00000010, ctypes.byref(pinfo))
            if res == 0 and pinfo:
                info = pinfo.contents
                if info.DomainName:
                    dns_domain = str(info.DomainName).strip().lower()
                if info.DomainControllerName:
                    dc_name = str(info.DomainControllerName).strip().lstrip("\\").lower()
                    if dc_name:
                        params["dc_host"] = dc_name
                ctypes.windll.netapi32.NetApiBufferFree(pinfo)
        except Exception:
            pass

    if dns_domain and "." in dns_domain:
        params["upn_suffix"] = dns_domain
        base_dn = ",".join(f"DC={part}" for part in dns_domain.split(".") if part)
        params["search_base"] = base_dn
        params["users_ou"] = f"OU=Users,{base_dn}"
        if "dc_host" not in params:
            if logon_srv:
                params["dc_host"] = f"{logon_srv}.{dns_domain}" if "." not in logon_srv else logon_srv
            else:
                params["dc_host"] = dns_domain
    if netbios:
        params["domain_netbios"] = netbios

    return params

def _load() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    defaults = {k: dict(v) for k, v in _DEFAULTS.items()}
    detected_ad = detect_ad_domain_params()
    if detected_ad:
        defaults["AD"].update(detected_ad)
    cp.read_dict(defaults)
    if os.path.exists(INI_FILE):
        try:
            cp.read(INI_FILE, encoding="utf-8")
        except (configparser.Error, OSError) as exc:
            logging.getLogger(__name__).warning("config.ini не прочитан: %s", exc)
    else:
        # Автоматически создаём config.ini при первом запуске
        try:
            write_default_config(INI_FILE)
        except Exception:
            pass
    return cp


# Ключи, которые миграция дописывает в существующий config.ini (секция → ключ → (комментарий, значение)).
# Это настройки, появившиеся после первых релизов: без миграции их в файле просто нет и о них не узнать.
_MIGRATE_KEYS: dict[str, dict[str, tuple[str, str]]] = {
    "Scanner": {
        "host_mask": ("# ГЛАВНЫЙ ФИЛЬТР ПАРКА: маска имён ПК через запятую (? = одна цифра, * = любые).\n"
                      "# Пример: PC-???, LT-* — только эти серии; host_pattern при этом не действует.\n"
                      "# Маска определяет сканирование и (через базу) поиск/опись/«ПО парка»/принтеры.\n"
                      "# Пусто — парк определяет host_pattern.", ""),
        "valid_subnets": ("# Подсети, которым верим при разрешении DNS (остальные адреса считаем чужими)", "10.,192.168.,172."),
        "dhcp_servers": ("# DHCP-серверы для сверки «Свободного IP» (через запятую); пусто — без сверки", ""),
    },
    "Paths": {
        "db_ready": ("# true = вопрос «где база?» уже задан (удалите строку, чтобы задать снова)", "true"),
        "backup_every_hours": ("# Резервная копия базы: раз в N часов (0 — не делать)", "6"),
        "backup_keep": ("# ...и хранить K последних копий", "12"),
    },
    "UI": {
        "language": ("# ru | en", "ru"),
        "minimize_to_tray": ("# Значок ADK в трее; закрытие крестиком — всегда полный выход", "true"),
        "global_hotkey": ("# Глобальное сочетание «показать ADK и перейти в поиск»; пусто — выключено", "Ctrl+Shift+A"),
    },
    "Attention": {
        "acct_days": ("# Учётная запись истекает в ближайшие N дней", "7"),
        "no_logon_days": ("# Учётка без входа N дней", "90"),
        "stale_pc_days": ("# ПК не был в сети N дней", "30"),
        "refresh_min": ("# Период обновления сводки, минут; 0 — только вручную", "30"),
    },
}


def _migrate_config(path: str) -> None:
    """Дописать в существующий config.ini ключи, которых в нём ещё нет (3.9.0).

    Чисто текстовая вставка в конец каждой секции: свои значения, порядок строк и комментарии
    пользователя не трогаем. Файл перезаписывается атомарно (сначала .tmp, потом замена)."""
    import configparser
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
        had_bom = open(path, "rb").read(3) == b"\xef\xbb\xbf"
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_string(text)
        lines = text.splitlines()
        sec_span: dict[str, tuple[int, int]] = {}       # секция → (строка заголовка, конец секции)
        order: list[str] = []
        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("[") and s.endswith("]"):
                name = s[1:-1].strip()
                if name:
                    if name not in sec_span:
                        sec_span[name] = (i, len(lines))
                        order.append(name)
                    for prev in order[:-1]:
                        b, e = sec_span[prev]
                        if e == len(lines):
                            sec_span[prev] = (b, i)
        inserts: list[tuple[int, list[str]]] = []
        tail_blocks: list[str] = []
        for sec, keys in _MIGRATE_KEYS.items():
            existing = set(cp[sec].keys()) if sec in cp else None
            if existing is None:
                continue                                   # секции нет — настройки ей не нужны, не навязываем
            missing = [(k, vc) for k, vc in keys.items() if k not in existing]
            if not missing:
                continue
            block = []
            for k, (comment, value) in missing:
                if comment:
                    block.extend(comment.splitlines())
                block.append(f"{k} = {value}")
            if sec in sec_span:
                b, e = sec_span[sec]
                # вставляем в конец секции, пропустив пустые строки непосредственно перед следующей секцией
                at = e
                while at > b + 1 and not lines[at - 1].strip():
                    at -= 1
                inserts.append((at, block))
            else:
                tail_blocks.append(f"[{sec}]\n" + "\n".join(block))
        if not inserts and not tail_blocks:
            return
        for at, block in sorted(inserts, key=lambda x: -x[0]):
            lines[at:at] = block + [""]
        out = "\n".join(lines).rstrip("\n") + "\n"
        for tb in tail_blocks:
            out += "\n" + tb + "\n"
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8-sig" if had_bom else "utf-8", newline="") as f:
            f.write(out)
        os.replace(tmp, path)
        logging.getLogger(__name__).info("config.ini: добавлены недостающие ключи (%s)", path)
    except Exception as exc:  # noqa: BLE001 — миграция не должна мешать запуску
        logging.getLogger(__name__).warning("миграция config.ini: %s", exc)


def write_default_config(path: str = INI_FILE) -> None:
    """Создаёт config.ini со значениями по умолчанию, комментариями и автоопределением домена.

    3.9.0: если файл уже есть — дописывает в него недостающие ключи (с комментариями), ничего не меняя
    в настроенном: у пользователей со старыми config.ini новые настройки были просто не видны."""
    if os.path.exists(path):
        _migrate_config(path)
        return
    _ensure_dirs()
    detected_ad = detect_ad_domain_params()
    ad_sec = dict(_DEFAULTS["AD"])
    ad_sec.update(detected_ad)

    content = f"""; ==============================================================================
;                 ADK — Active Directory Kit · Конфигурация
; ==============================================================================

[AD]
domain_netbios = {ad_sec.get('domain_netbios', 'EXAMPLE')}
# FQDN контроллера домена или имя самого домена (для DNS round-robin)
dc_host = {ad_sec.get('dc_host', 'dc01.example.local')}
# Базовый корень поиска объектов каталога
search_base = {ad_sec.get('search_base', 'DC=example,DC=local')}
# Подразделение (OU) по умолчанию для создания новых пользователей
users_ou = {ad_sec.get('users_ou', 'OU=Users,DC=example,DC=local')}
# Суффикс UPN (логин@домен)
upn_suffix = {ad_sec.get('upn_suffix', 'example.local')}
# LDAPS (порт 636); false — порт 389 без SSL. Без LDAPS пароль меняется через net user /domain (SAMR).
use_ssl = {ad_sec.get('use_ssl', 'false')}
# Проверка SSL-сертификата контроллера домена. false по умолчанию: у большинства доменов
# сертификат ДК самоподписанный, и строгая проверка даёт «контроллер домена недоступен».
tls_validate = {ad_sec.get('tls_validate', 'false')}
connect_timeout = 5
# Срок действия пароля по доменной политике
max_password_age_days = 90

[Paths]
# Путь к локальной или сетевой базе соответствий
db_path = {os.path.join(DOCS_DIR, 'pc_mapping.db')}
backup_every_hours = 6
backup_keep = 12
# Сетевые папки инвентаризации рабочих станций
invent_hardware_dir = 
invent_comp_dir = 
invent_compexit_dir = 
pst_backup_base = 
# Путь к клиенту удалённого доступа RMS
rms_viewer_path = C:\\Program Files (x86)\\Remote Manipulator System - Viewer\\rutview.exe
templates_file = 

[Scanner]
auto_scan_interval_min = 30
host_pattern = ^(WS-\\d+|PC-.*)$
host_exclude = (VIRT|VM|VBOX|TEST|SRV|SQL|SERVER)
# ГЛАВНЫЙ ФИЛЬТР ПАРКА: маска имён ПК через запятую. ? = одна цифра, * = любые символы.
# Пример: PC-???, LT-* — только эти серии. Если маска задана, ОНА определяет парк:
# host_pattern выше не действует (не вырезает ваши серии), действует только host_exclude.
# Маска применяется к сканированию и через базу — к поиску, описи, «ПО парка» и принтерам.
# Пусто — как раньше: парк определяет host_pattern.
host_mask = 
valid_subnets = 10.,192.168.,172.
# DHCP-серверы для проверки свободных IP
dhcp_servers = 

[Design]
bg_style = background-color: #1C1C1E;
is_dark = true
font_family = Inter
font_size = 10
accent_color = #0A84FF
panel_color = #2C2C2E
text_color = #F5F5F7
border_color = #48484A
follow_system = false

[UI]
language = ru
minimize_to_tray = true
global_hotkey = Ctrl+Shift+A
login_method = 
hide_role_welcome = false

[Access]
readonly = false
readonly_group = 
admin_groups = 
pc_admin_groups = 
ad_admin_groups = 

[Updates]
version_file = 

[Plugins]
dir = {PLUGINS_DIR}

[Notify]
smtp_host = 
smtp_port = 25
smtp_from = 
smtp_to = 
smtp_user = 
smtp_password = 
smtp_tls = false
actions = reset_password,disable_user,bulk_disable,bulk_reset_password,create_user
watch_logins = 

[Attention]
acct_days = 7
no_logon_days = 90
stale_pc_days = 30
refresh_min = 30
"""
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        logging.getLogger(__name__).warning("Не удалось записать начальный config.ini: %s", exc)


class Settings:
    """Типизированный доступ к config.ini. Перечитывается методом reload()."""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        cp = _load()
        ad = cp["AD"]
        self.domain_netbios: str = ad.get("domain_netbios")
        self.dc_host: str = ad.get("dc_host")
        self.search_base: str = normalize_search_base(ad.get("search_base"))
        self.users_ou: str = ad.get("users_ou")
        self.upn_suffix: str = ad.get("upn_suffix")
        self.use_ssl: bool = ad.getboolean("use_ssl")
        self.tls_validate: bool = ad.getboolean("tls_validate")
        self.connect_timeout: int = ad.getint("connect_timeout")
        self.max_password_age_days: int = ad.getint("max_password_age_days", fallback=90)

        p = cp["Paths"]
        self.db_path: str = p.get("db_path")
        self.invent_hardware_dir: str = p.get("invent_hardware_dir")
        self.invent_comp_dir: str = p.get("invent_comp_dir")
        self.invent_compexit_dir: str = p.get("invent_compexit_dir")
        self.pst_backup_base: str = p.get("pst_backup_base")
        self.rms_viewer_path: str = p.get("rms_viewer_path")

        s = cp["Scanner"]
        self.auto_scan_interval_ms: int = s.getint("auto_scan_interval_min") * 60 * 1000
        self.host_pattern: str = s.get("host_pattern")
        self.host_exclude: str = s.get("host_exclude")
        self.valid_subnets: tuple[str, ...] = tuple(
            x.strip() for x in s.get("valid_subnets").split(",") if x.strip()
        )
        self.dhcp_servers: tuple[str, ...] = tuple(
            x.strip() for x in s.get("dhcp_servers", fallback="").replace(";", ",").split(",") if x.strip()
        )
        self.host_mask: str = (s.get("host_mask", fallback="") or "").strip()

        d = cp["Design"]
        self.design: dict = {
            "bg_style": d.get("bg_style"),
            "is_dark": d.getboolean("is_dark"),
            "font_family": d.get("font_family"),
            "font_size": d.getint("font_size"),
            "accent_color": d.get("accent_color"),
            "panel_color": d.get("panel_color", fallback=""),
            "text_color": d.get("text_color", fallback=""),
            "border_color": d.get("border_color", fallback=""),
            "follow_system": d.getboolean("follow_system", fallback=False),
        }

        ui = cp["UI"] if "UI" in cp else {}
        self.language: str = (ui.get("language", "ru") or "ru").lower()[:2]
        self.minimize_to_tray: bool = str(ui.get("minimize_to_tray", "true")).lower() in ("1", "true", "yes", "да")
        self.global_hotkey: str = ui.get("global_hotkey", "Ctrl+Shift+A") or ""
        self.login_method: str = (ui.get("login_method", "") or "").strip().lower()
        self.hide_role_welcome: bool = str(ui.get("hide_role_welcome", "false")).lower() in ("1", "true", "yes", "да")

        acc = cp["Access"] if "Access" in cp else {}
        self.readonly: bool = str(acc.get("readonly", "false")).lower() in ("1", "true", "yes", "да")
        self.readonly_group: str = acc.get("readonly_group", "") or ""
        def _groups(key: str) -> tuple[str, ...]:
            return tuple(x.strip() for x in (acc.get(key, "") or "").split(",") if x.strip())
        self.admin_groups: tuple[str, ...] = _groups("admin_groups")
        self.pc_admin_groups: tuple[str, ...] = _groups("pc_admin_groups")
        self.ad_admin_groups: tuple[str, ...] = _groups("ad_admin_groups")

        upd = cp["Updates"] if "Updates" in cp else {}
        self.version_file: str = upd.get("version_file", "") or ""

        pl = cp["Plugins"] if "Plugins" in cp else {}
        self.plugins_dir: str = pl.get("dir", PLUGINS_DIR) or PLUGINS_DIR
        self.templates_file: str = (cp["Paths"].get("templates_file", "") if "Paths" in cp else "") or ""
        # 3.5.11: вопрос «где база?» уже задан при первом запуске (см. setup_ui.needs_db_setup)
        self.db_ready: bool = str(cp["Paths"].get("db_ready", "false") if "Paths" in cp else "false").lower() in ("1", "true", "yes")
        # 3.6.0: резервные копии базы — раз в N часов (0 — выключено), хранить K последних
        try:
            self.backup_every_hours: float = float((cp["Paths"].get("backup_every_hours", "6") if "Paths" in cp else "6") or 0)
        except ValueError:
            self.backup_every_hours = 6.0
        try:
            self.backup_keep: int = int((cp["Paths"].get("backup_keep", "12") if "Paths" in cp else "12") or 12)
        except ValueError:
            self.backup_keep = 12

        nt = cp["Notify"] if "Notify" in cp else {}
        self.notify: dict = {
            "smtp_host": nt.get("smtp_host", "") or "", "smtp_port": int(nt.get("smtp_port", "25") or 25),
            "smtp_from": nt.get("smtp_from", "") or "", "smtp_to": nt.get("smtp_to", "") or "",
            "smtp_user": nt.get("smtp_user", "") or "", "smtp_password": nt.get("smtp_password", "") or "",
            "smtp_tls": str(nt.get("smtp_tls", "false")).lower() in ("1", "true", "yes", "да"),
            "actions": tuple(x.strip() for x in (nt.get("actions", "") or "").split(",") if x.strip()),
            "watch_logins": tuple(x.strip().casefold() for x in (nt.get("watch_logins", "") or "").split(",") if x.strip()),
        }
        at = cp["Attention"] if "Attention" in cp else {}
        self.attention: dict = {"acct_days": int(at.get("acct_days", at.get("pwd_days", "7")) or 7), "no_logon_days": int(at.get("no_logon_days", "90") or 90),
                                "stale_pc_days": int(at.get("stale_pc_days", "30") or 30), "refresh_min": int(at.get("refresh_min", "30") or 0)}

    def save_design(self, design: dict) -> None:
        self.save_section("Design", design)
        self.design = dict(design)

    def save_section(self, section: str, values: dict) -> None:
        cp = _load()
        if section not in cp:
            cp[section] = {}
        for k, v in values.items():
            cp[section][str(k)] = str(v)
        _ensure_dirs()
        try:
            with open(INI_FILE, "w", encoding="utf-8") as fh:
                cp.write(fh)
        except OSError as exc:
            logging.getLogger(__name__).warning("Не удалось сохранить настройки: %s", exc)


settings = Settings()


def setup_logging(level: int = logging.INFO) -> None:
    _ensure_dirs()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        from logging.handlers import RotatingFileHandler
        handlers.append(RotatingFileHandler(LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
    )
