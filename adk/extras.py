"""Текст «карточки» с паролем для сотрудника.

Чистая функция :func:`password_card_text` — без Qt, покрыта тестами; используется окном «Смена пароля».
"""
from __future__ import annotations


# ============================================================================ карточка с паролем
def password_card_text(login: str, password: str, must_change: bool, domain: str = "") -> str:
    """Текст «карточки» для сотрудника (в буфер обмена)."""
    who = f"{domain}\\{login}" if domain else login
    lines = [f"Логин: {who}", f"Пароль: {password}"]
    if must_change:
        lines.append("При первом входе система попросит сменить пароль.")
    return "\n".join(lines)
