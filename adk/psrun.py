"""Единый запуск PowerShell-скриптов для удалённых опросов (3.5.10).

До 3.5.10 каждый модуль (health, software, logons, netutils, dhcp) сам вызывал ``powershell -Command <текст>``
и при любой неудаче показывал «нет ответа». На реальном домене это выглядело так: все окна («Здоровье ПК»,
«Установленное ПО», «Входы за 24 ч») пустые, а причина скрыта. Здесь собраны исправления, которые это лечат:

* **-EncodedCommand** (UTF-16LE → base64) вместо ``-Command``: скрипт любого размера, с кавычками, ``#``
  и переводами строк, доходит до PowerShell байт в байт — никакой обработки командной строки cmd/CRT.
  Очень длинный скрипт (> 30 000 символов base64) уходит через временный ``.ps1`` и ``-File``.
* **stdin = DEVNULL и -NonInteractive** — из приложения без консоли (PyInstaller ``console=False``) дочерний
  powershell.exe иначе получает «битый» дескриптор stdin и может завершиться молча, без вывода.
* **UTF-8 в обе стороны**: прелюдия ставит ``[Console]::OutputEncoding``; вывод читается с ``errors="replace"``,
  чтобы русское сообщение об ошибке в OEM-кодировке не роняло вызов исключением UnicodeDecodeError.
* **Ошибка всегда в JSON на stdout**: скрипт оборачивается в ``try/catch``, и любое исключение приходит как
  ``{"error": "..."}`` — не зависим от stderr, который у PowerShell бывает пустым при ненулевом коде выхода.
* **Понятная причина**: :func:`explain_error` переводит типовые сообщения WinRM/DCOM/RPC/доступа в подсказку,
  что именно включить (``winrm quickconfig``, правило брандмауэра, права администратора на ПК).
* **Отмена и таймаут** без зависших процессов: процесс убивается, частичный вывод не теряется.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from .config import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

_PRELUDE = (
    "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
    "$ProgressPreference = 'SilentlyContinue'\n"
    "try {\n"
)
_EPILOGUE = (
    "\n} catch {\n"
    "  $m = '' + $_.Exception.Message\n"
    "  if ($_.Exception.InnerException) { $m += ' (' + $_.Exception.InnerException.Message + ')' }\n"
    "  [Console]::Out.Write((@{ error = $m } | ConvertTo-Json -Compress))\n"
    "  exit 3\n"
    "}\n"
)
MAX_ENCODED = 30000   # запас до предела командной строки Windows (32 767 символов)


@dataclass
class PsResult:
    ok: bool
    stdout: str = ""
    error: str = ""
    returncode: int | None = None


def wrap(script: str) -> str:
    """Скрипт с прелюдией UTF-8 и try/catch → JSON-ошибка на stdout."""
    return _PRELUDE + script + _EPILOGUE


def encode(script: str) -> str:
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def build_argv(script: str) -> tuple[list[str], str | None]:
    """(argv, путь временного файла или None). Короткий скрипт — -EncodedCommand, длинный — -File."""
    base = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass"]
    enc = encode(script)
    if len(enc) <= MAX_ENCODED:
        return base + ["-EncodedCommand", enc], None
    fd, path = tempfile.mkstemp(suffix=".ps1", prefix="adk_")
    with os.fdopen(fd, "w", encoding="utf-8-sig") as fh:
        fh.write(script)
    return base + ["-File", path], path


# типовые тексты ошибок WinRM/DCOM/RPC (русская и английская Windows) → что делать
_HINTS = (
    (("winrm", "wsman", "ws-management", "не удается подключиться к удаленному серверу", "cannot connect to the remote server",
      "клиент не может подключиться", "the client cannot connect"),
     "WinRM на ПК выключен или закрыт брандмауэром — на ПК выполните «winrm quickconfig» (или включите политикой) "
     "и разрешите правило «Удалённое управление Windows»"),
    (("rpc", "сервер rpc недоступен", "the rpc server is unavailable", "0x800706ba"),
     "нет доступа по RPC/DCOM (порт 135 и правила «Инструментарий управления Windows (WMI)», "
     "«Удалённое управление журналом событий») — ПК выключен, за брандмауэром или имя не разрешается"),
    (("отказано в доступе", "access is denied", "access denied", "0x80070005", "unauthorizedaccess", "не удается получить доступ"),
     "доступ запрещён — нужна учётная запись с правами администратора на этом ПК (ваша текущая учётка их не имеет)"),
    (("kerberos", "проверка подлинности", "authentication", "logon failure", "ошибка входа"),
     "не прошла проверка подлинности — проверьте, что вы вошли доменной учёткой и имя ПК указано полностью"),
    (("не найдено событий", "no events were found"), ""),
    (("no such host", "не удается разрешить", "could not be resolved", "неизвестный узел", "unknown host"),
     "имя ПК не разрешается в DNS — проверьте имя/суффикс домена"),
    (("timed out", "истекло время ожидания", "время ожидания операции"),
     "ПК не ответил вовремя — включён ли он и доступен ли по сети?"),
)


def explain_error(text: str) -> str:
    """Короткая причина + подсказка по типовым сообщениям Windows; неизвестный текст — как есть (обрезан)."""
    t = " ".join((text or "").split())
    low = t.lower()
    for keys, hint in _HINTS:
        if any(k in low for k in keys):
            return f"{t[:220]} — {hint}" if hint else t[:220]
    return t[:300] if t else "PowerShell завершился без вывода"


def run(script: str, timeout: int = 60, cancelled: Callable[[], bool] | None = None,
        on_tick: Callable[[float], None] | None = None) -> PsResult:
    """Выполнить скрипт. ``cancelled()`` опрашивается каждые 0,5 с — при True процесс убивается.

    Возвращает :class:`PsResult`; ``error`` уже пропущен через :func:`explain_error`.
    """
    if os.name != "nt":
        return PsResult(False, error="Опрос доступен только с Windows (PowerShell/CIM)")
    argv, tmp = build_argv(wrap(script))
    started = time.monotonic()
    try:
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        except OSError as exc:
            return PsResult(False, error=f"PowerShell не запускается: {exc}")
        out, err = "", ""
        while True:
            try:
                out, err = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - started
                if on_tick:
                    on_tick(elapsed)
                if cancelled and cancelled():
                    _kill(proc)
                    return PsResult(False, error="Остановлено пользователем", returncode=proc.returncode)
                if elapsed > timeout:
                    _kill(proc)
                    return PsResult(False, error=f"ПК не ответил за {timeout} с", returncode=proc.returncode)
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass
    return interpret(out or "", err or "", proc.returncode)


def _kill(proc: subprocess.Popen) -> None:
    try:
        proc.kill()
        proc.communicate(timeout=5)
    except (subprocess.SubprocessError, OSError):
        pass


def interpret(out: str, err: str, returncode: int | None) -> PsResult:
    """Разобрать вывод процесса: JSON-ошибка из catch, пустой вывод, stderr — в один понятный текст."""
    text = (out or "").strip()
    if text.startswith("{") and '"error"' in text[:40]:
        try:
            d = json.loads(text)
            if isinstance(d, dict) and d.get("error"):
                return PsResult(False, stdout=text, error=explain_error(str(d["error"])), returncode=returncode)
        except ValueError:
            pass
    if returncode not in (0, None) or not text:
        e = " ".join((err or "").strip().splitlines()[:3]).strip()
        if not e:
            e = f"PowerShell завершился без ответа (код {returncode})" if returncode else "ПК не вернул данных"
            e += " — обычно это значит, что WinRM/WMI на ПК недоступны или нет прав администратора"
        return PsResult(False, stdout=text, error=explain_error(e), returncode=returncode)
    return PsResult(True, stdout=text, returncode=returncode)
