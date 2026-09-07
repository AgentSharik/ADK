"""Хранение учётных данных.

Пароль никогда не пишется на диск открытым текстом:
* Windows — DPAPI: через ``win32crypt``, а если pywin32 не собран в exe — напрямую через ``ctypes``
  (crypt32.dll есть в любой Windows), расшифровать может только тот же пользователь на той же машине;
* иначе — системное хранилище через ``keyring`` (Credential Manager / Keychain / Secret Service).

3.3.0: раньше при отсутствии ``win32crypt`` в сборке сохранение молча не срабатывало («через раз»),
теперь есть ctypes-ветка, а причина отказа доступна через :func:`last_error` и показывается пользователю.

Файл с именем пользователя и зашифрованным блобом лежит в профиле пользователя,
а не рядом с программой.
"""
from __future__ import annotations

import base64
import json
import logging
import os

from .config import APP_NAME, DOCS_DIR, IS_WINDOWS

log = logging.getLogger(__name__)
CRED_FILE = os.path.join(DOCS_DIR, "cred_cache.json")

try:  # pragma: no cover - платформенно
    import win32crypt  # type: ignore
except ImportError:  # pragma: no cover
    win32crypt = None

try:
    import keyring  # type: ignore
except ImportError:  # pragma: no cover
    keyring = None

_last_error = ""


def last_error() -> str:
    """Почему последнее сохранение/чтение не удалось (пусто, если всё хорошо)."""
    return _last_error


def _dpapi_ctypes(raw: bytes, protect: bool) -> bytes:  # pragma: no cover - только Windows
    """DPAPI без pywin32: CryptProtectData/CryptUnprotectData из crypt32.dll через ctypes."""
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(raw, len(raw))
    src = DATA_BLOB(len(raw), buf)
    dst = DATA_BLOB()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), APP_NAME if protect else None, None, None, None, 0, ctypes.byref(dst))
    if not ok:
        raise OSError(f"DPAPI ошибка {ctypes.GetLastError()}")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _dpapi_available() -> bool:
    return IS_WINDOWS and (win32crypt is not None or hasattr(__import__("ctypes"), "windll"))


def _dpapi_protect(secret: str) -> str:
    raw = secret.encode("utf-16-le")
    if win32crypt is not None:
        blob = win32crypt.CryptProtectData(raw, APP_NAME, None, None, None, 0)
    else:  # pragma: no cover
        blob = _dpapi_ctypes(raw, True)
    return base64.b64encode(blob).decode("ascii")


def _dpapi_unprotect(b64: str) -> str:
    raw = base64.b64decode(b64)
    if win32crypt is not None:
        return win32crypt.CryptUnprotectData(raw, None, None, None, 0)[1].decode("utf-16-le")
    return _dpapi_ctypes(raw, False).decode("utf-16-le")  # pragma: no cover


def _keyring_usable() -> bool:
    if keyring is None:
        return False
    try:
        kr = keyring.get_keyring()
        return "fail" not in type(kr).__name__.lower() and "null" not in type(kr).__name__.lower()
    except Exception:  # noqa: BLE001
        return False


def storage_name() -> str:
    """Какое хранилище будет использовано: для подсказки в окне входа."""
    if _dpapi_available():
        return "Windows DPAPI"
    if _keyring_usable():
        return "системное хранилище (keyring)"
    return ""


def save_credentials(username: str, password: str) -> bool:
    """Возвращает True, если пароль удалось сохранить защищённо; иначе причина — в :func:`last_error`."""
    global _last_error
    _last_error = ""
    try:
        os.makedirs(DOCS_DIR, exist_ok=True)
        if _dpapi_available():
            data = {"username": username, "dpapi": _dpapi_protect(password)}
        elif _keyring_usable():
            keyring.set_password(APP_NAME, username, password)
            if keyring.get_password(APP_NAME, username) != password:
                raise RuntimeError("keyring вернул не то, что записали")
            data = {"username": username, "keyring": True}
        else:
            _last_error = "нет защищённого хранилища: ни DPAPI (Windows), ни рабочего keyring-бэкенда"
            log.warning("Пароль не сохранён: %s", _last_error)
            return False
        with open(CRED_FILE, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return True
    except Exception as exc:  # noqa: BLE001
        _last_error = f"{type(exc).__name__}: {exc}"
        log.warning("Не удалось сохранить учётные данные: %s", exc)
        return False


def load_credentials() -> tuple[str | None, str | None]:
    if not os.path.exists(CRED_FILE):
        return None, None
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        user = data.get("username")
        if not user:
            return None, None
        if "dpapi" in data and _dpapi_available():
            return user, _dpapi_unprotect(data["dpapi"])
        if data.get("keyring") and keyring is not None:
            return user, keyring.get_password(APP_NAME, user)
        if "dpapi" in data or data.get("keyring"):
            return user, None      # логин известен, а расшифровать нечем — хотя бы подставим имя
        if "password" in data:  # старый небезопасный формат — не используем и удаляем
            log.warning("Найден пароль в открытом виде в %s — файл удалён", CRED_FILE)
            clear_credentials()
    except Exception as exc:  # noqa: BLE001
        log.warning("Не удалось прочитать учётные данные: %s", exc)
    return None, None


def clear_credentials() -> None:
    try:
        if os.path.exists(CRED_FILE):
            try:
                with open(CRED_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
            if data.get("keyring") and keyring is not None and data.get("username"):
                try:
                    keyring.delete_password(APP_NAME, data["username"])
                except Exception:  # noqa: BLE001
                    pass
            os.remove(CRED_FILE)
    except Exception as exc:  # noqa: BLE001
        log.debug("clear_credentials: %s", exc)
