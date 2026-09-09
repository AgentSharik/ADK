"""Локализация без Qt Linguist: словарь ru → en, функция ``tr``.

Исходные строки в коде — русские (язык команды); ``tr`` возвращает перевод, если выбран ``en``
и строка есть в словаре, иначе — исходную строку. Не переведённые строки остаются русскими, поэтому
словарь расширяется постепенно и приложение всегда работоспособно.
"""
from __future__ import annotations

_LANG = "ru"

EN: dict[str, str] = {
    # главное окно
    "Найти": "Find", "🎨 Дизайн": "🎨 Theme", "📤 Экспорт": "📤 Export",
    "📦 Архивы (все ПК пользователя)": "📦 Archives (all user PCs)", "🚷 Отключённые учётки": "🚷 Disabled accounts",
    "Фамилия, логин, почта, отдел, кабинет, имя ПК, IP, printer:…": "Surname, login, e-mail, department, room, PC name, IP, printer:…",
    "🔄 Обновить статус сети ПК": "🔄 Refresh PC network status",
    "<b>📊 Состояние компьютеров домена</b> (клик по карточке — список):": "<b>📊 Domain computers</b> (click a tile for the list):",
    "<b>🕒 Недавние поиски:</b>": "<b>🕒 Recent searches:</b>", "<b>⚡ Быстрый доступ:</b>": "<b>⚡ Quick access:</b>",
    "🔍 Свободный IP-адрес": "🔍 Free IP address", "📊 Excel-опись ПК": "📊 Excel PC inventory", "🖨️ Принтеры парка": "🖨️ Fleet printers",
    "➕ Новый пользователь AD": "➕ New AD user", "📜 Журнал действий": "📜 Audit log",
    "Логин": "Login", "ФИО": "Full name", "Учётка": "Account", "Имя ПК": "PC name", "Сеть": "Network", "Телефон": "Phone",
    "IP-тел": "IP phone", "Кабинет": "Room", "Адрес": "Address", "Организация": "Company", "Отдел": "Department",
    "Должность": "Title", "Последний вход": "Last logon",
    "👤 Выберите сотрудника": "👤 Select an employee", "📋 Копировать": "📋 Copy",
    "Логин:": "Login:", "Связанный ПК:": "Linked PC:", "Статус сети:": "Network:", "Телефон:": "Phone:", "Почта:": "E-mail:",
    "Адрес:": "Address:", "Отдел:": "Department:", "Последний вход:": "Last logon:", "Был в сети:": "Last online:",
    "Учётная запись:": "Account:", "📡 Пинг": "📡 Ping", "<b>⚡ Действия с ПК:</b>": "<b>⚡ PC actions:</b>",
    "📁 Диск C$": "📁 Drive C$", "⚙️ Управление ПК": "⚙️ Computer management", "🔄 Перезагрузить": "🔄 Restart",
    "🩺 Здоровье ПК": "🩺 PC health", "<b>🖨️ Принтеры</b> (клик — кто ещё подключён):": "<b>🖨️ Printers</b> (click — who else is connected):",
    "<b>📝 Заметки:</b>": "<b>📝 Notes:</b>", "➕ Добавить заметку…": "➕ Add note…", "<b>💻 Характеристики:</b>": "<b>💻 Specs:</b>",
    "👤 Открыть полную карточку AD…": "👤 Open full AD card…", "Активна": "Active", "Отключена": "Disabled", "Не активна": "Inactive",
    "● В сети": "● Online", "● Не в сети": "● Offline", "🔍 Поиск…": "🔍 Searching…", "Найдено: ": "Found: ",
    "❌ Ничего не найдено": "❌ Nothing found", "Измените запрос": "Change the query",
    "👤 Открыть карточку": "👤 Open card", "🖥️ RMS": "🖥️ RMS", "📤 Экспорт результатов…": "📤 Export results…",
    "🧰 Массовые операции": "🧰 Bulk operations", "📋 Карточка скопирована": "📋 Card copied",
    "Показать ADK": "Show ADK", "Поиск": "Search", "Сканировать парк": "Scan fleet", "Выход": "Quit",
    "ADK свёрнут в трей. Выход — через меню трея.": "ADK minimized to tray. Quit from the tray menu.",
    "Режим «только чтение»": "Read-only mode", "🔒 Только чтение": "🔒 Read-only",
    "🔒 AD: только чтение": "🔒 AD: read-only", "🔒 ПК: только чтение": "🔒 PC: read-only",
    "Недостаточно прав": "Insufficient rights",
    "Изменение объектов Active Directory недоступно для вашей роли (нет прав записи в домен).":
        "Changing Active Directory objects is not available for your role (no write rights in the domain).",
    "Действия с ПК недоступны для вашей роли.": "PC actions are not available for your role.",
    "В режиме «только чтение» изменяющие действия недоступны.": "Modifying actions are unavailable in read-only mode.",
    "Доступна новая версия": "New version available",
    # диалоги (заголовки)
    "🔑 Вход в ADK": "🔑 Sign in to ADK", "Войти (NTLM)": "Sign in (NTLM)", "🔑 Войти через Windows SSO (Kerberos)": "🔑 Windows SSO (Kerberos)",
    "🎨 Оформление": "🎨 Appearance", "📜 Журнал действий администраторов": "📜 Administrators audit log",
    "🖨️ Принтеры парка": "🖨️ Fleet printers", "🧰 Массовые операции": "🧰 Bulk operations", "📝 Заметки": "📝 Notes",
    "🧬 Сравнение групп": "🧬 Group comparison", "🕓 История": "🕓 History", "Как в системе (Windows)": "Follow system (Windows)",
    "Язык / Language": "Language", "Русский": "Russian", "English": "English",
    "Атрибуты AD": "AD attributes", "Группы": "Groups", "ПК / RMS": "PC / RMS", "Характеристики ПК": "PC specs",
}


def set_language(lang: str) -> None:
    """ru | en; неизвестное значение → ru."""
    global _LANG
    lang = (lang or "ru").lower()[:2]
    _LANG = lang if lang in ("ru", "en") else "ru"


def language() -> str:
    return _LANG


def tr(text: str) -> str:
    if _LANG == "en":
        return EN.get(text, text)
    return text
