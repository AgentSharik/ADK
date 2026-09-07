"""Конфигурация приложения.

Все параметры, специфичные для организации (домен, сетевые пути, RMS), вынесены
в ``config.ini`` в профиле пользователя. В коде нет ни одного жёстко прописанного
адреса — репозиторий можно публиковать.
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



def _app_dir() -> str:
    """Каталог приложения: рядом с exe (PyInstaller) или корень репозитория."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_docs_dir() -> str:
    """Portable-режим: если рядом с exe лежит файл ``portable`` (или задан ADK_PORTABLE=1 / ADK_HOME),
    данные (config.ini, БД, лог, плагины) живут в ``<app>/data`` — можно носить на флешке."""
    env_home = os.environ.get("ADK_HOME")
    if env_home:
        return os.path.abspath(env_home)
    app_dir = _app_dir()
    if os.environ.get("ADK_PORTABLE") == "1" or os.path.exists(os.path.join(app_dir, "portable")):
        return os.path.join(app_dir, "data")
    return os.path.join(os.path.expanduser("~"), "Documents", APP_NAME)


DOCS_DIR = _resolve_docs_dir()
IS_PORTABLE = not DOCS_DIR.startswith(os.path.join(os.path.expanduser("~"), "Documents"))
INI_FILE = os.path.join(DOCS_DIR, "config.ini")
LOG_FILE = os.path.join(DOCS_DIR, "adk.log")
PLUGINS_DIR = os.path.join(DOCS_DIR, "plugins")


# Флаги userAccountControl
ACCOUNT_DISABLE_FLAG = 0x0002
NORMAL_ACCOUNT_FLAG = 0x0200
SMARTCARD_REQUIRED_FLAG = 0x40000

DB_RETRY_ATTEMPTS = 5
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
        "use_ssl": "true",
        "tls_validate": "true",
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
        # по умолчанию — тема «Графит и титан» (см. theme.PRESET_THEMES["dark"])
        "bg_style": "background-color: #1C1C1F;",
        "is_dark": "true",
        "font_family": "Segoe UI",
        "font_size": "10",
        "accent_color": "#F59E0B",
        "panel_color": "#26262A",
        "text_color": "#F4F4F5",
        "border_color": "",
        "follow_system": "false",     # тёмная/светлая — как в Windows (AppsUseLightTheme)
    },
    "UI": {
        "language": "ru",             # ru | en
        "minimize_to_tray": "true",   # закрытие окна сворачивает в трей; выход — из меню трея
        "global_hotkey": "Ctrl+Shift+A",  # показать окно и перейти в поиск (Windows, RegisterHotKey); пусто — выключить
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


def _load() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(_DEFAULTS)
    if os.path.exists(INI_FILE):
        try:
            cp.read(INI_FILE, encoding="utf-8")
        except (configparser.Error, OSError) as exc:
            logging.getLogger(__name__).warning("config.ini не прочитан: %s", exc)
    return cp


def write_default_config(path: str = INI_FILE) -> None:
    """Создаёт config.ini со значениями по умолчанию (если его ещё нет)."""
    if os.path.exists(path):
        return
    _ensure_dirs()
    cp = configparser.ConfigParser(interpolation=None)
    cp.read_dict(_DEFAULTS)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("; ADK — настройки организации. Правится вручную.\n")
        cp.write(fh)


class Settings:
    """Типизированный доступ к config.ini. Перечитывается методом reload()."""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        cp = _load()
        ad = cp["AD"]
        self.domain_netbios: str = ad.get("domain_netbios")
        self.dc_host: str = ad.get("dc_host")
        self.search_base: str = ad.get("search_base")
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
        self.db_backend: str = ((cp["Paths"].get("db_backend", "sqlite") if "Paths" in cp else "sqlite") or "sqlite").lower()
        self.db_dsn: str = (cp["Paths"].get("db_dsn", "") if "Paths" in cp else "") or ""

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
