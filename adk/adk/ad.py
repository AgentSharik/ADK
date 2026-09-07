"""Работа с Active Directory через ldap3.

* единая фабрика подключений (LDAPS, таймауты, проверка сертификата);
* постраничный поиск — без него AD молча обрезает выдачу до 1000 записей;
* хелперы чтения атрибутов и типовые операции (создать пользователя, включить/отключить).
"""
from __future__ import annotations

import hashlib
import logging
import re
import secrets
import ssl
import string
from datetime import datetime, timezone
from typing import Any, Iterable

from ldap3 import (
    GSSAPI, MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, NONE, NTLM, SASL, SUBTREE,
    Connection, Server, Tls,
)
from ldap3.utils.conv import escape_filter_chars
from ldap3.utils.dn import escape_rdn

from .config import ACCOUNT_DISABLE_FLAG, LDAP_PAGE_SIZE, NORMAL_ACCOUNT_FLAG, settings

log = logging.getLogger(__name__)

__all__ = [
    "MODIFY_ADD", "MODIFY_DELETE", "MODIFY_REPLACE", "SUBTREE", "escape_filter_chars",
    "make_connection", "paged_search", "get_ad_value", "get_ad_int_value", "get_ad_list_value",
    "get_full_fio", "dn_to_cn", "qualify_user", "create_user", "set_account_disabled",
    "generate_secure_password", "transliterate", "sanitize_sam_account_name",
    "get_all_attribute_values", "USER_ATTRS", "describe_ldap_error",
    "filetime_to_datetime", "get_ad_datetime", "account_status", "reset_password",
]

USER_ATTRS = [
    "sAMAccountName", "displayName", "sn", "givenName", "middleName", "userAccountControl",
    "telephoneNumber", "ipPhone", "mobile", "streetAddress", "physicalDeliveryOfficeName",
    "company", "department", "title", "mail", "description", "memberOf", "msRTCSIP-UserEnabled",
    "lockoutTime", "pwdLastSet", "accountExpires", "lastLogonTimestamp", "badPwdCount", "whenCreated",
]

# FILETIME: 100-нс интервалы с 01.01.1601 (UTC). 0 и 0x7FFFFFFFFFFFFFFF — «никогда».
_FILETIME_NEVER = (0, 0x7FFFFFFFFFFFFFFF)
_FILETIME_EPOCH_DIFF = 116444736000000000  # между 1601 и 1970 годами
DONT_EXPIRE_PASSWORD_FLAG = 0x10000


# --------------------------------------------------------------------------- MD4 для NTLM
def _install_md4_fallback() -> None:
    """OpenSSL 3 отключил MD4; ldap3 нужен он для NT-хэша. Патчим только при отсутствии."""
    try:
        hashlib.new("md4")
        return
    except ValueError:
        pass
    from .md4 import PureMD4

    original = hashlib.new

    def patched(name: str, data: bytes = b"", **kw):
        if name.lower() == "md4":
            return PureMD4(data)
        return original(name, data, **kw)

    hashlib.new = patched  # type: ignore[assignment]


_install_md4_fallback()


# --------------------------------------------------------------------------- подключение
def qualify_user(user: str) -> str:
    """NTLM требует DOMAIN\\user: UPN (user@domain) и голый логин приводятся к этому виду."""
    user = user.strip()
    if "\\" in user:
        return user
    if "@" in user:
        user = user.split("@", 1)[0]
    return f"{settings.domain_netbios}\\{user}"


def _server() -> Server:
    if settings.use_ssl:
        tls = Tls(validate=ssl.CERT_REQUIRED if settings.tls_validate else ssl.CERT_NONE)
        return Server(settings.dc_host, port=636, use_ssl=True, tls=tls, get_info=NONE,
                      connect_timeout=settings.connect_timeout)
    return Server(settings.dc_host, port=389, get_info=NONE, connect_timeout=settings.connect_timeout)


def make_connection(user: str | None = None, password: str | None = None) -> Connection:
    """NTLM при наличии пароля, иначе Kerberos/SSO. ``raise_exceptions=True``."""
    server = _server()
    if user and password:
        return Connection(server, user=qualify_user(user), password=password, authentication=NTLM,
                          auto_bind=True, raise_exceptions=True, receive_timeout=60)
    try:
        return Connection(server, authentication=SASL, sasl_mechanism=GSSAPI, auto_bind=True,
                          raise_exceptions=True, receive_timeout=60)
    except Exception as exc:
        if "gssapi" in str(exc).lower() or "winkerberos" in str(exc).lower():
            raise RuntimeError("Для SSO установите пакет: pip install winkerberos") from exc
        raise


_AUTH_CODES = ("52e", "525", "530", "531", "532", "533", "701", "773", "775")


def is_auth_error(exc: Exception) -> bool:
    """Ошибка именно учётных данных (неверный пароль, блокировка…), а не сети/сертификата.

    3.3.0: раньше сохранённый пароль стирался при *любой* неудаче автологина — в том числе когда просто
    не ответил контроллер домена. Теперь стираем только по этим кодам."""
    text = str(exc)
    return any(f"data {c}" in text for c in _AUTH_CODES) or "invalidCredentials" in text


def describe_ldap_error(exc: Exception) -> str:
    text = str(exc)
    codes = {
        "52e": "Неверный логин или пароль.",
        "525": "Пользователь не найден.",
        "530": "Вход запрещён в это время.",
        "531": "Вход с этого компьютера запрещён.",
        "532": "Срок действия пароля истёк.",
        "533": "Учётная запись отключена.",
        "701": "Срок действия учётной записи истёк.",
        "773": "Требуется смена пароля.",
        "775": "Учётная запись заблокирована (lockout).",
    }
    for code, msg in codes.items():
        if f"data {code}" in text:
            return msg
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "Сертификат контроллера домена не доверен (см. tls_validate в config.ini)."
    if "socket" in text.lower() or "timeout" in text.lower():
        return f"Контроллер домена недоступен: {settings.dc_host}"
    if "insufficientAccessRights" in text:
        return "Недостаточно прав для операции."
    return text


# --------------------------------------------------------------------------- поиск
def paged_search(conn: Connection, search_filter: str, attributes: Iterable[str],
                 base: str | None = None, page_size: int = LDAP_PAGE_SIZE, limit: int = 0) -> list:
    """Полная выборка через Simple Paged Results (обходит MaxPageSize=1000 на стороне AD).

    ``limit`` — мягкий предел строк (0 — без предела). Возвращает список ``Entry``.
    """
    entries: list = []
    cookie = None
    while True:
        conn.search(base or settings.search_base, search_filter, SUBTREE,
                    attributes=list(attributes), paged_size=page_size, paged_cookie=cookie)
        entries.extend(conn.entries)
        if limit and len(entries) >= limit:
            return entries[:limit]
        try:
            cookie = conn.result["controls"]["1.2.840.113556.1.4.319"]["value"]["cookie"]
        except (KeyError, TypeError):
            cookie = None
        if not cookie:
            return entries


def get_all_attribute_values(conn: Connection, attr: str) -> list[str]:
    values: set[str] = set()
    for e in paged_search(conn, f"(&(objectClass=user)({attr}=*))", [attr]):
        v = get_ad_value(e, attr).strip()
        if v:
            values.add(v)
    return sorted(values)


# --------------------------------------------------------------------------- атрибуты
def get_ad_value(entry: Any, attr: str, default: str = "") -> str:
    try:
        a = entry[attr]
    except (KeyError, AttributeError, TypeError):
        return default
    try:
        vals = list(a.values) if a.values is not None else []
    except AttributeError:
        return default
    if not vals:
        return default
    return str(vals[0])


def get_ad_int_value(entry: Any, attr: str, default: int = 0) -> int:
    try:
        return int(get_ad_value(entry, attr, str(default)))
    except (TypeError, ValueError):
        return default


def get_ad_list_value(entry: Any, attr: str) -> list[str]:
    try:
        a = entry[attr]
        return [str(v) for v in (a.values or [])]
    except (KeyError, AttributeError, TypeError):
        return []


def short_fio(full: str) -> str:
    """«Иванов Иван Петрович» → «Иванов И.П.»; одно слово/логин — как есть."""
    parts = [x for x in (full or "").replace("\u00a0", " ").split() if x]
    if len(parts) < 2:
        return full or ""
    return parts[0] + " " + "".join(p[0].upper() + "." for p in parts[1:3])


def get_full_fio(entry: Any, default: str = "") -> str:
    """Полное «Фамилия Имя Отчество». Отчество берём из middleName, а если его нет — из displayName
    («Сидоров Пётр Ильич»), когда тот начинается с той же фамилии и имени."""
    sn = get_ad_value(entry, "sn").strip()
    given = get_ad_value(entry, "givenName").strip()
    middle = get_ad_value(entry, "middleName").strip()
    disp = get_ad_value(entry, "displayName", "").strip()
    if sn and given:
        if not middle and disp:
            parts = disp.split()
            if len(parts) >= 3 and parts[0].casefold() == sn.casefold() and parts[1].casefold() == given.casefold():
                middle = parts[2]
        return " ".join(p for p in (sn, given, middle) if p)
    return disp or default


_DN_SPLIT_RE = re.compile(r"(?<!\\),")


def filetime_to_datetime(value: Any) -> "datetime | None":
    """AD FILETIME (int/str) → aware datetime UTC; ``None`` для 0 / «никогда» / мусора."""
    try:
        ft = int(value)
    except (TypeError, ValueError):
        return None
    if ft in _FILETIME_NEVER or ft < 0:
        return None
    try:
        return datetime.fromtimestamp((ft - _FILETIME_EPOCH_DIFF) / 10_000_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def get_ad_datetime(entry: Any, attr: str) -> "datetime | None":
    """Читает атрибут-дату. ldap3 сам конвертирует FILETIME-атрибуты в datetime; сырое число тоже поддерживаем."""
    try:
        raw = entry[attr].value
    except (KeyError, AttributeError, TypeError):
        return None
    if raw is None or raw == []:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    return filetime_to_datetime(raw)


def account_status(entry: Any, max_pwd_age_days: int, now: "datetime | None" = None) -> dict:
    """Сводка для инспектора: срок пароля, блокировка, истечение УЗ, неудачные входы.

    Ничего не выдумывает — при отсутствии данных даёт «—».
    """
    now = now or datetime.now(timezone.utc)
    uac = get_ad_int_value(entry, "userAccountControl")
    res: dict[str, Any] = {"pwd_text": "—", "pwd_days_left": None, "pwd_warn": False,
                           "locked": False, "locked_text": "", "expires_text": "", "expired": False,
                           "bad_pwd": get_ad_int_value(entry, "badPwdCount"),
                           "last_logon_text": "—", "created_text": "—"}
    pwd_set = get_ad_datetime(entry, "pwdLastSet")
    if uac & DONT_EXPIRE_PASSWORD_FLAG:
        res["pwd_text"] = "не истекает" + (f" (сменён {pwd_set:%d.%m.%Y})" if pwd_set else "")
    elif pwd_set is None:
        raw = get_ad_int_value(entry, "pwdLastSet", -1)
        res["pwd_text"] = "требуется смена при следующем входе" if raw == 0 else "—"
        res["pwd_warn"] = raw == 0
    else:
        age = (now - pwd_set).days
        if max_pwd_age_days > 0:
            left = max_pwd_age_days - age
            res["pwd_days_left"] = left
            res["pwd_warn"] = left <= 7
            res["pwd_text"] = f"сменён {age} дн. назад, " + (
                f"истекает через {left} дн." if left > 0 else f"ИСТЁК {-left} дн. назад")
        else:
            res["pwd_text"] = f"сменён {age} дн. назад ({pwd_set:%d.%m.%Y})"
    lock = get_ad_datetime(entry, "lockoutTime")
    if lock is not None:
        res["locked"] = True
        res["locked_text"] = f"ЗАБЛОКИРОВАНА с {lock.astimezone():%d.%m.%Y %H:%M}"
    exp = get_ad_datetime(entry, "accountExpires")
    if exp is not None:
        res["expired"] = exp < now
        res["expires_text"] = ("истекла " if res["expired"] else "истекает ") + f"{exp.astimezone():%d.%m.%Y}"
    ll = get_ad_datetime(entry, "lastLogonTimestamp")
    if ll is not None:
        res["last_logon_text"] = f"{ll.astimezone():%d.%m.%Y %H:%M} (±14 дн., репликация)"
    created = get_ad_datetime(entry, "whenCreated")
    if created is not None:
        res["created_text"] = f"{created.astimezone():%d.%m.%Y}"
    return res


def reset_password(conn: Connection, dn: str, new_password: str, must_change: bool = True,
                   unlock: bool = True) -> None:
    """Сброс пароля администратором. Требует LDAPS. ``must_change`` → pwdLastSet=0."""
    if not settings.use_ssl:
        raise RuntimeError("Смена пароля возможна только по LDAPS (use_ssl=true в config.ini)")
    conn.extend.microsoft.modify_password(dn, new_password)
    changes: dict[str, Any] = {}
    if must_change:
        changes["pwdLastSet"] = [(MODIFY_REPLACE, [0])]
    if unlock:
        changes["lockoutTime"] = [(MODIFY_REPLACE, [0])]
    if changes:
        conn.modify(dn, changes)


def dn_to_cn(dn: str) -> str:
    """Первый RDN без типа; учитывает экранированные запятые (``CN=Smith\\, John,OU=…``)."""
    try:
        rdn = _DN_SPLIT_RE.split(dn, 1)[0]
        return rdn.split("=", 1)[1].replace("\\,", ",")
    except IndexError:
        return dn


def is_disabled(entry: Any) -> bool:
    return bool(get_ad_int_value(entry, "userAccountControl") & ACCOUNT_DISABLE_FLAG)


def account_inactive_reason(entry: Any, now: "datetime | None" = None) -> str:
    """Почему учётка не активна: «отключена» / «истекла» / «заблокирована»; пустая строка — активна.

    Именно эти признаки читает инспектор в строке «Учётная запись», поэтому бейдж и инспектор всегда согласны.
    """
    if entry is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if is_disabled(entry):
        return "отключена"
    exp = get_ad_datetime(entry, "accountExpires")
    if exp is not None and exp < now:
        return "истекла"
    if get_ad_datetime(entry, "lockoutTime") is not None:
        return "заблокирована"
    return ""


def account_badge(entry: Any, now: "datetime | None" = None) -> tuple[str, str]:
    """Бейдж «Учётка» в таблице — ровно два состояния: («Активна», active) — человек работает;
    («Не активна», disabled) — отключена, уволен/декрет, истёк срок или заблокирована после неверных паролей."""
    return ("Не активна", "disabled") if account_inactive_reason(entry, now) else ("Активна", "active")


# --------------------------------------------------------------------------- операции
def set_account_disabled(conn: Connection, dn: str, current_uac: int, disabled: bool) -> int:
    new_uac = current_uac | ACCOUNT_DISABLE_FLAG if disabled else current_uac & ~ACCOUNT_DISABLE_FLAG
    conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [new_uac])]})
    return new_uac


def unlock_account(conn: Connection, dn: str) -> None:
    """Снимает lockout, НЕ трогая флаг «отключена»."""
    conn.modify(dn, {"lockoutTime": [(MODIFY_REPLACE, [0])]})


def create_user(conn: Connection, *, login: str, password: str, surname: str, name: str,
                patronymic: str = "", ou: str | None = None, extra: dict[str, str] | None = None) -> str:
    """Создаёт УЗ безопасной последовательностью: add(514) → пароль → 512.

    При сбое на любом шаге после add объект удаляется (нет «полусозданных» пользователей).
    Требует LDAPS — AD принимает unicodePwd только по защищённому каналу.
    """
    login = sanitize_sam_account_name(login)
    display = f"{surname} {name[0]}." + (f"{patronymic[0]}." if patronymic else "")
    ou = ou or settings.users_ou
    dn = f"CN={escape_rdn(display)},{ou}"

    if conn.search(settings.search_base,
                   f"(|(sAMAccountName={escape_filter_chars(login)})(distinguishedName={escape_filter_chars(dn)}))",
                   SUBTREE, attributes=["cn"]) and conn.entries:
        raise ValueError(f"Логин «{login}» или CN «{display}» уже существует")

    attributes: dict[str, Any] = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "sAMAccountName": login,
        "userPrincipalName": f"{login}@{settings.upn_suffix}",
        "cn": display, "sn": surname, "givenName": name, "displayName": display,
        "userAccountControl": NORMAL_ACCOUNT_FLAG | ACCOUNT_DISABLE_FLAG,
    }
    if patronymic:
        attributes["middleName"] = patronymic
    for k, v in (extra or {}).items():
        if v and v.strip():
            attributes[k] = v.strip()

    conn.add(dn, attributes=attributes)
    try:
        conn.extend.microsoft.modify_password(dn, password)
        conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [NORMAL_ACCOUNT_FLAG])]})
    except Exception:
        try:
            conn.delete(dn)
        finally:
            pass
        raise
    return dn


# --------------------------------------------------------------------------- утилиты
_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "zh", "з": "z",
    "и": "i", "й": "j", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shh",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def transliterate(text: str) -> str:
    """Упрощённая транслитерация для генерации логинов (не ГОСТ)."""
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


def sanitize_sam_account_name(login: str) -> str:
    """sAMAccountName: ≤ 20 символов, только [a-z0-9_.-]."""
    s = re.sub(r"[^a-z0-9_.\-]", "", transliterate(login))
    if not s:
        raise ValueError("Логин пуст после очистки")
    return s[:20]


def generate_secure_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits + "!_@"
    while True:
        pwd = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!_@" for c in pwd)):
            return pwd
