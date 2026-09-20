"""Локализация без Qt Linguist: словарь ru → en, функция ``tr``.

Исходные строки в коде — русские (язык команды); ``tr`` возвращает перевод, если выбран ``en``
и строка есть в словаре, иначе — исходную строку. Не переведённые строки остаются русскими, поэтому
словарь расширяется постепенно и приложение всегда работоспособно.
"""
from __future__ import annotations

_LANG = "ru"

EN: dict[str, str] = {
    # главное окно — добор 3.9.x
    "Ctrl+F — фокус, Esc — очистить, Enter — искать": "Ctrl+F — focus, Esc — clear, Enter — search",
    "Плагины и модули автоматизации ADK": "ADK plugins and automation modules",
    "🔔 Внимание: сводка собирается…": "🔔 Attention: building the summary…",
    "Открыть": "Open",
    "Разбудить (WoL), заблокировать экран, выйти из пользователя, спящий режим, перезагрузить, выключить":
        "Wake (WoL), lock screen, log off, sleep, restart, shut down",
    "📡 Опросить принтеры сейчас": "📡 Query printers now",
    "Спросить ПК напрямую (CIM Win32_Printer), без записи: инвентарь и БД не меняются.":
        "Ask the PC directly (CIM Win32_Printer), read-only: inventory and DB are not changed.",
    "🧬 Группы как у…": "🧬 Groups like…", "Подключение": "Connection",
    "Пользователь не залогинен": "No user logged on", "● Проверка…": "● Checking…",
    "ПК не привязан": "No PC linked", "🌐 Веб-панель": "🌐 Web panel",
    "<b>💻 Кто подключён</b> (клик — открыть ПК):": "<b>💻 Who is connected</b> (click — open the PC):",
    "— (не сетевой)": "— (not network)", "IP не указан в инвентаре": "No IP in the inventory",
    "не сетевой — проверка по IP не применима": "not a network printer — IP check is not applicable",
    "проверяется…": "checking…",
    "по данным инвентаря (запрос не по IP — устройство по адресу не проверялось)":
        "from inventory data (not queried by IP — the device at the address was not checked)",
    "неизвестно — ПК с этим принтером в инвентаре нет (опросите парк в «Принтеры парка»)":
        "unknown — no PC with this printer in the inventory (run the fleet query in “Fleet printers”)",
    "нет (или CSV не собран)": "none (or CSV not collected)",
    "⏳ Опрашиваю ПК напрямую…": "⏳ Querying the PC directly…",
    "📋 Карточка принтера скопирована": "📋 Printer card copied",
    "● В сети": "● Online", "● Не в сети": "● Offline",
    "⏹ Остановить наполнение": "⏹ Stop filling", "📁 Диск…": "📁 Drive…", "⏻ Питание ПК": "⏻ PC power",
    "🔄 Полный опрос парка: подготовка…": "🔄 Full fleet scan: preparing…", "🔄 Полный опрос: ": "🔄 Full scan: ",
    "🔄 ": "🔄 ", "🗄️ Новая база: первичное наполнение…": "🗄️ New database: initial fill…",
    "⏹ Останавливаю наполнение — начатые ПК дорабатывают…": "⏹ Stopping the fill — started PCs finish first…",
    "⏹ Останавливаю опрос — начатые ПК дорабатывают…": "⏹ Stopping the scan — started PCs finish first…",
    "ADK: событие журнала": "ADK: audit log event",
    # главное окно
    "Найти": "Find", "🎨 Дизайн": "⚙️ Settings", "⚙️ Настройки": "⚙️ Settings", "🧩 Плагины": "🧩 Plugins", "📤 Экспорт": "📤 Export",
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
    # 3.9.0: частые кнопки и подписи — чтобы переключение языка было заметно везде, а не только в главном окне
    "Закрыть": "Close", "Сохранить и закрыть": "Save and close", "Отмена": "Cancel", "Обновить": "Refresh",
    "Операция:": "Operation:", "Выполнить": "Run", "Подтверждение": "Confirmation", "Скопировано": "Copied",
    "Период:": "Period:", "Только отказы": "Failures only", "Сетевые входы (тип 3)": "Network logons (type 3)",
    "Типы входов:": "Logon types:", "Программа": "Program", "Издатель": "Publisher", "Установлена": "Installed",
    "Результат": "Result", "Время": "Time", "Пользователь": "User", "Тип": "Type", "Откуда (IP)": "Source (IP)",
    "Входы на ПК": "PC logons", "Характеристики": "Specifications", "Журнал": "Journal",
    "Отправлено": "Sent", "Получено": "Received", "Потери": "Loss", "Джиттер": "Jitter",
    "События": "Events", "Сбои и события": "Failures & events", "Все": "All",
    "Опрошен": "Polled", "Остановить": "Stop", "Свернуть": "Minimize", "Пауза": "Pause", "Сброс": "Reset",
    "Копировать отчёт": "Copy report", "Копировать вывод ping": "Copy ping output",
    "Готовые темы": "Preset themes", "Акцентный цвет": "Accent colour", "Фон окна": "Window background",
    "Семейство:": "Family:", "Размер:": "Size:", "Настройки": "Settings", "Опросить парк": "Poll the fleet",
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
