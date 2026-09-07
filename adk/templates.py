"""Шаблоны регистрации: «новый сотрудник бухгалтерии» = атрибуты + группы + OU одним нажатием.

Хранятся в ``Documents\\ADK\\templates.json`` (общий для отдела файл можно указать в ``[Paths] templates_file``).
Формат: {"Бухгалтер": {"attrs": {"department": "...", "title": "..."}, "groups": ["CN=...", ...], "ou": "OU=...", "note": "..."}}.
"""
from __future__ import annotations

import json
import logging
import os

from .config import DOCS_DIR, settings

log = logging.getLogger(__name__)

TEMPLATE_KEYS = ("attrs", "groups", "ou", "note")
ATTRS = ("title", "department", "company", "streetAddress", "physicalDeliveryOfficeName")


def templates_path() -> str:
    return getattr(settings, "templates_file", "") or os.path.join(DOCS_DIR, "templates.json")


def normalize(tpl: dict) -> dict:
    """Приводит шаблон к полной форме; лишние ключи и пустые значения отбрасываются."""
    attrs = {k: str(v).strip() for k, v in (tpl.get("attrs") or {}).items() if k in ATTRS and str(v).strip()}
    groups = [str(g).strip() for g in (tpl.get("groups") or []) if str(g).strip()]
    return {"attrs": attrs, "groups": list(dict.fromkeys(groups)), "ou": str(tpl.get("ou") or "").strip(),
            "note": str(tpl.get("note") or "").strip()}


def load(path: str | None = None) -> dict[str, dict]:
    path = path or templates_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        log.warning("templates: %s", exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(name): normalize(t) for name, t in raw.items() if isinstance(t, dict) and str(name).strip()}


def save(templates: dict[str, dict], path: str | None = None) -> None:
    path = path or templates_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({n: normalize(t) for n, t in templates.items()}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def from_entry(entry, get_value, get_list) -> dict:
    """Шаблон из существующего пользователя-образца: его атрибуты и группы (кроме Domain Users)."""
    attrs = {a: get_value(entry, a).strip() for a in ATTRS if get_value(entry, a).strip()}
    groups = [g for g in get_list(entry, "memberOf") if not g.upper().startswith("CN=DOMAIN USERS,")]
    dn = getattr(entry, "entry_dn", "") or ""
    ou = dn.split(",", 1)[1] if "," in dn else ""
    return normalize({"attrs": attrs, "groups": groups, "ou": ou})


def apply_groups(conn, user_dn: str, groups: list[str], modify_add) -> tuple[list[str], list[str]]:
    """Добавляет user_dn в каждую группу; → (успешно, с ошибкой)."""
    ok, bad = [], []
    for g in groups:
        try:
            conn.modify(g, {"member": [(modify_add, [user_dn])]})
            desc = conn.result.get("description") if isinstance(getattr(conn, "result", None), dict) else "success"
            (ok if desc in (None, "success", "entryAlreadyExists", "attributeOrValueExists") else bad).append(g)
        except Exception as exc:  # noqa: BLE001
            log.warning("template group %s: %s", g, exc)
            bad.append(g)
    return ok, bad
