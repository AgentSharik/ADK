"""Сводка «Внимание»: что требует реакции администратора сегодня.

Источники: AD (пароли, истекающие на неделе; учётки без входа N дней; заблокированные), инвентарь
(ПК без сети N дней), кэш здоровья/заметок. Каждая строка — dict {key, kind, severity, subject, text, when}.
``key`` стабилен (kind:subject) — по нему работает «отложить на N дней» (таблица attention_snooze).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from . import ad, db
from .config import settings

log = logging.getLogger(__name__)

# Пароли сюда намеренно не входят: в организациях со смарт-картами/сертификатами срок пароля ничего не значит,
# а «истекает через N дней» превращалось бы в постоянный шум. Срок пароля виден в инспекторе и карточке.
KINDS = {
    "locked": ("🔒", "Учётка заблокирована"),
    "no_logon": ("😴", "Нет входа давно"),
    "acct_expiring": ("📅", "Учётка истекает"),
    "pc_stale": ("💻", "ПК давно не в сети"),
}
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _item(kind: str, subject: str, text: str, severity: str = "medium", when: str = "", extra: dict | None = None) -> dict:
    return {"key": f"{kind}:{subject}", "kind": kind, "icon": KINDS[kind][0], "title": KINDS[kind][1],
            "subject": subject, "text": text, "severity": severity, "when": when, **(extra or {})}


def from_entries(entries, now: datetime | None = None, acct_days: int = 7, no_logon_days: int = 90) -> list[dict]:
    """Чистая логика по списку записей AD (без сети) — для тестов и для :func:`collect`."""
    now = now or datetime.now(timezone.utc)
    out: list[dict] = []
    max_age = settings.max_password_age_days
    for e in entries:
        login = ad.get_ad_value(e, "sAMAccountName")
        if not login or ad.is_disabled(e):
            continue
        fio = ad.get_full_fio(e, login)
        st = ad.account_status(e, max_age, now)
        if st.get("locked"):
            out.append(_item("locked", login, f"{fio}: {st['locked_text']}", "high", extra={"fio": fio}))
        exp = ad.get_ad_datetime(e, "accountExpires")
        if exp is not None and now < exp <= now + timedelta(days=acct_days):
            out.append(_item("acct_expiring", login, f"{fio}: учётка истекает {exp.astimezone():%d.%m.%Y}", "medium", extra={"fio": fio}))
        ll = ad.get_ad_datetime(e, "lastLogonTimestamp")
        created = ad.get_ad_datetime(e, "whenCreated")
        ref = ll or created
        if ref is not None and (now - ref).days >= no_logon_days:
            out.append(_item("no_logon", login, f"{fio}: {'последний вход' if ll else 'создана'} {(now - ref).days} дн. назад", "low", extra={"fio": fio}))
    return out


def from_inventory(stale_days: int = 30) -> list[dict]:
    out = []
    for comp, user, seen in db.stale_computers(stale_days):
        when = seen[:10] if seen else "никогда"
        out.append(_item("pc_stale", comp, f"{comp}{f' ({user})' if user else ''}: последний раз в сети — {when}", "low", when, {"login": user}))
    return out


def collect(conn_factory, acct_days: int = 7, no_logon_days: int = 90, stale_days: int = 30) -> list[dict]:
    """Полная сводка: AD + инвентарь, без отложенных, отсортирована по важности."""
    items: list[dict] = []
    try:
        c = conn_factory()
        try:
            attrs = ["sAMAccountName", "displayName", "sn", "givenName", "middleName", "userAccountControl", "pwdLastSet",
                     "lockoutTime", "accountExpires", "lastLogonTimestamp", "whenCreated", "badPwdCount"]
            entries = ad.paged_search(c, "(&(objectCategory=person)(objectClass=user)(!(userAccountControl:1.2.840.113556.1.4.803:=2)))", attrs)
        finally:
            c.unbind()
        items += from_entries(entries, acct_days=acct_days, no_logon_days=no_logon_days)
    except Exception as exc:  # noqa: BLE001
        log.warning("attention/AD: %s", exc)
    items += from_inventory(stale_days)
    snoozed = db.snoozed_keys()
    items = [i for i in items if i["key"] not in snoozed]
    items.sort(key=lambda i: (SEVERITY_ORDER.get(i["severity"], 9), i["kind"], i["subject"]))
    return items


def collect_from_settings(conn_factory, cfg: dict) -> list[dict]:
    """Обёртка над :func:`collect` с параметрами из ``settings.attention``."""
    return collect(conn_factory, acct_days=int(cfg.get("acct_days", cfg.get("pwd_days", 7))), no_logon_days=int(cfg.get("no_logon_days", 90)),
                   stale_days=int(cfg.get("stale_pc_days", 30)))


def summary_line(items: list[dict]) -> str:
    if not items:
        return "✅ Всё спокойно"
    high = sum(1 for i in items if i["severity"] == "high")
    return f"⚠️ Требуют внимания: {len(items)}" + (f" (срочных: {high})" if high else "")
