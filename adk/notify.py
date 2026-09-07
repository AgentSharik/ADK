"""Уведомления из журнала действий по почте (SMTP).

Вызывается из ``db.log_action`` через хук: событие уходит в фоне, ошибки доставки не мешают работе.
Чистая логика — :func:`should_notify` и :func:`format_event` — тестируется без сети.
"""
from __future__ import annotations

import logging
import smtplib
import threading
from email.message import EmailMessage

from .config import settings
from .i18n import tr

log = logging.getLogger(__name__)


def should_notify(action: str, target: str, cfg: dict | None = None) -> bool:
    cfg = cfg or settings.notify
    if not (cfg.get("smtp_host") and cfg.get("smtp_to")):
        return False
    acts = cfg.get("actions") or ()
    if (target or "").casefold() in (cfg.get("watch_logins") or ()):
        return True
    return "*" in acts or action in acts


def format_event(admin: str, action: str, target: str, details: str, labels: dict) -> str:
    label = labels.get(action, action)
    text = f"ADK · {label}\nАдминистратор: {admin}\nЦель: {target}"
    if details:
        text += f"\n{details[:300]}"
    return text




def send_mail(text: str, cfg: dict, subject: str = "ADK: событие журнала") -> bool:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["smtp_from"] or cfg["smtp_user"], cfg["smtp_to"], subject
    msg.set_content(text)
    try:
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"]), timeout=10) as s:
            if cfg.get("smtp_tls"):
                s.starttls()
            if cfg.get("smtp_user"):
                s.login(cfg["smtp_user"], cfg["smtp_password"])
            s.send_message(msg)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("smtp: %s", exc)
        return False


def send_all(text: str, cfg: dict, subject: str = "ADK: событие журнала") -> list[tuple[str, bool]]:
    """Во все настроенные каналы; → [(канал, ok)]."""
    out = []
    if cfg.get("smtp_host") and cfg.get("smtp_to"):
        out.append(("SMTP", send_mail(text, cfg, subject=subject)))
    return out


def notify_event(admin: str, action: str, target: str, details: str = "", labels: dict | None = None, background: bool = True) -> None:
    cfg = settings.notify
    if not should_notify(action, target, cfg):
        return
    text = format_event(admin, action, target, details, labels or {})

    def work():
        send_all(text, cfg, subject=tr("ADK: событие журнала"))

    if background:
        threading.Thread(target=work, name="adk-notify", daemon=True).start()
    else:
        work()


def test_channels() -> list[tuple[str, bool]]:
    """Проверка настроек из диалога: → [(канал, ok)]."""
    return send_all("ADK: проверка уведомлений ✅", settings.notify, subject="ADK: проверка")
