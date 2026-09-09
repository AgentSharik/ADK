"""Роли: два независимых права — «ПК» и «AD».

Каждое изменяющее действие относится к одному из двух классов:

* ``pc`` — действия с компьютерами и локальными данными ADK: перезагрузка/выключение, WoL,
  опрос ПО, заметки, «отложить» в сводке «Внимание», привязка ПК, изменяющие плагины.
  Прав на запись в домен не требуют.
* ``ad`` — прямые изменения объектов Active Directory: атрибуты, включение/отключение УЗ, снятие блокировки,
  сброс пароля, смарт-карта, создание пользователя, группы (в т. ч. массово и «Группы как у…»), шаблоны.

Кто что может — по группам AD учётной записи, под которой выполнен вход (``memberOf``):

* ``[Access] pc_admin_groups`` — кому доступны действия класса ``pc``;
* ``[Access] ad_admin_groups`` — кому доступны действия класса ``ad``;
* ``admin_groups`` — совместимость: даёт оба права сразу;
* ``readonly_group`` / ``readonly = true`` — отнимает оба права.

Если списки групп не заданы — у всех полный доступ. Задан только один список — второе право есть у всех.
Пример: группа ``GT_Admins`` имеет в AD только чтение → указываем её в ``pc_admin_groups``; такие администраторы
перезагружают ПК, смотрят ПО, ведут заметки, но кнопки создания/отключения УЗ, сброса пароля, групп для них скрыты.

Результат кэшируется на процесс: :func:`can` дешёвая, её можно звать из любого слота.
"""
from __future__ import annotations

import logging

from .config import settings

log = logging.getLogger(__name__)

PC, AD = "pc", "ad"

ACTION_CLASS: dict[str, str] = {
    # --- ПК и локальные данные
    "restart": PC, "shutdown": PC, "wol": PC, "power": PC, "lock": PC, "logoff": PC, "sleep": PC,
    "software": PC, "note_add": PC, "note_delete": PC,
    "bind_pc": PC, "attention_snooze": PC, "plugin_modifying": PC,
    # --- объекты Active Directory
    "modify_user": AD, "enable_user": AD, "disable_user": AD, "unlock": AD, "reset_password": AD,
    "smartcard": AD, "create_user": AD, "group_add": AD, "group_remove": AD, "groups_sync": AD, "template_apply": AD,
    "bulk_group_add": AD, "bulk_group_remove": AD, "bulk_disable": AD, "bulk_enable": AD,
    "bulk_reset_password": AD, "bulk_unlock": AD,
}
READONLY_ACTIONS = frozenset(ACTION_CLASS)  # совместимость: все изменяющие действия

_state: dict = {"pc": None, "ad": None, "reason": ""}


def reset() -> None:
    _state.update(pc=None, ad=None, reason="")


def set_rights(pc: bool, ad: bool, reason: str = "") -> None:
    _state.update(pc=bool(pc), ad=bool(ad), reason=reason)


def set_readonly(value: bool, reason: str = "") -> None:
    """Совместимость: «только чтение» = нет ни одного права."""
    set_rights(not value, not value, reason)


def _ensure() -> None:
    if _state["pc"] is None:
        ro = bool(settings.readonly)
        set_rights(not ro, not ro, "config.ini: readonly = true" if ro else "")


def can_pc() -> bool:
    _ensure()
    return bool(_state["pc"])


def can_ad() -> bool:
    _ensure()
    return bool(_state["ad"])


def is_readonly() -> bool:
    """Нет ни одного права (полный режим «только чтение»)."""
    return not can_pc() and not can_ad()


def is_limited() -> bool:
    """Хоть какое-то право отсутствует — в шапке показываем бейдж."""
    return not (can_pc() and can_ad())


def reason() -> str:
    _ensure()
    return _state["reason"]


def action_class(action: str) -> str | None:
    if action in ACTION_CLASS:
        return ACTION_CLASS[action]
    if action.startswith("bulk_"):
        return AD
    return None


def can(action: str) -> bool:
    """Разрешено ли действие текущему администратору. Неизвестные (не изменяющие) действия разрешены всегда."""
    cls = action_class(action)
    if cls == PC:
        return can_pc()
    if cls == AD:
        return can_ad()
    return True


def deny_text(action: str) -> str:
    """Текст предупреждения, если действие запрещено."""
    if action_class(action) == AD:
        return "Изменение объектов Active Directory недоступно для вашей роли (нет прав записи в домен)."
    return "Действия с ПК недоступны для вашей роли."


def role_summary() -> tuple[str, str]:
    """(заголовок, пояснение) для окна роли и подсказок — чтобы разница ролей была понятна пользователю."""
    if is_readonly():
        return ("🔒 Роль: только чтение", "Просмотр AD и парка ПК; действия с ПК и изменение объектов AD недоступны.")
    if not can_ad():
        return ("🖥️ Роль «ПК»: администратор компьютеров, AD — только чтение",
                "Доступно: пинг, RMS, диск, управление и питание ПК, здоровье, ПО, входы, заметки, массовый пинг. "
                "Скрыто: смена пароля, блокировка/отключение учёток, группы ±, новый пользователь, массовые операции.")
    if not can_pc():
        return ("🗂️ Роль «AD»: объекты домена, действия с ПК — только чтение",
                "Доступно: учётки, пароли, группы, новый пользователь. Скрыто: питание ПК, ПО, заметки, плагины.")
    return ("🛡️ Роль «AD»: полный доступ", "Доступно всё: объекты AD, пароли, группы и все действия с компьютерами.")


def role_title_short() -> str:
    """Краткое отображение текущей роли в противоположном углу окна."""
    if is_readonly():
        return "🔒 Текущая роль: Только чтение"
    if not can_ad():
        return "🖥️ Текущая роль: ПК, AD: Чтение"
    if not can_pc():
        return "🗂️ Текущая роль: AD, ПК: Чтение"
    return "🛡️ Текущая роль: AD (Полный доступ)"


def badge_text() -> str:
    if is_readonly():
        return "🔒 Только чтение"
    if not can_ad():
        return "🔒 AD: только чтение"
    if not can_pc():
        return "🔒 ПК: только чтение"
    return ""


def _in(groups, cns: set[str]) -> bool:
    return any(g.casefold() in cns for g in groups)


def evaluate_groups(member_cns: list[str]) -> tuple[bool, str]:
    """Совместимость: True — полный режим «только чтение»."""
    pc, ad, why = evaluate_rights(member_cns)
    return (not pc and not ad), why


def evaluate_rights(member_cns: list[str]) -> tuple[bool, bool, str]:
    """Чистая логика: список CN групп администратора → (право ПК, право AD, пояснение)."""
    if settings.readonly:
        return False, False, "config.ini: readonly = true"
    cns = {c.casefold() for c in member_cns}
    if settings.readonly_group and settings.readonly_group.casefold() in cns:
        return False, False, f"член группы {settings.readonly_group}"
    full = _in(settings.admin_groups, cns) if settings.admin_groups else None  # None — список не задан
    pc_groups, ad_groups = settings.pc_admin_groups, settings.ad_admin_groups
    pc = full or _in(pc_groups, cns) or (not pc_groups and full is None)
    ad = full or _in(ad_groups, cns) or (not ad_groups and full is None)
    missing = []
    if not pc:
        missing.append("ПК: " + ", ".join(pc_groups or settings.admin_groups))
    if not ad:
        missing.append("AD: " + ", ".join(ad_groups or settings.admin_groups))
    why = "" if not missing else "нет в группах — " + "; ".join(missing)
    return bool(pc), bool(ad), why


def policy_configured() -> bool:
    return bool(settings.readonly_group or settings.admin_groups or settings.pc_admin_groups or settings.ad_admin_groups)


def resolve(conn_factory, admin_login: str) -> bool:
    """Определяет роль по AD (вызывать в фоне после входа). При ошибке LDAP — считаем «только чтение»,
    если политика групп настроена (безопасный отказ), иначе — полный доступ. Возвращает :func:`is_readonly`."""
    from . import ad
    if not policy_configured():
        ro = bool(settings.readonly)
        set_rights(not ro, not ro, "config.ini: readonly = true" if ro else "")
        return is_readonly()
    try:
        conn = conn_factory()
        try:
            login = ad.escape_filter_chars(admin_login.split("\\")[-1].split("@")[0])
            entries = ad.paged_search(conn, f"(&(objectClass=user)(sAMAccountName={login}))", ["memberOf", "primaryGroupID"], limit=1)
        finally:
            conn.unbind()
        cns = [ad.dn_to_cn(g) for g in (ad.get_ad_list_value(entries[0], "memberOf") if entries else [])]
        pc, ad_ok, why = evaluate_rights(cns)
    except Exception as exc:  # noqa: BLE001
        log.warning("access.resolve: %s", exc)
        pc, ad_ok, why = False, False, f"не удалось проверить группы ({exc.__class__.__name__}) — безопасный режим"
    set_rights(pc, ad_ok, why)
    return is_readonly()
