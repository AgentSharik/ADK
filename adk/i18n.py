"""Локализация без Qt Linguist: словарь ru → en, функция ``tr``.

Исходные строки в коде — русские (язык команды); ``tr`` возвращает перевод, если выбран ``en``
и строка есть в словаре, иначе — исходную строку. Не переведённые строки остаются русскими, поэтому
словарь расширяется постепенно и приложение всегда работоспособно.
"""
from __future__ import annotations

_LANG = "ru"

EN: dict[str, str] = {
    # финальный добор по всем модулям
    "Active Directory Kit · вход в домен ": "Active Directory Kit · domain sign-in ",
    "«Всё» опрашивает и выключенные ПК пропускает — окно прогресса можно свернуть, работа продолжится.":
        "“All” also queries powered-off PCs later — the progress view can be minimized, work continues.",
    "База обновляется сканированием — без него поиск видит прошлый снимок.":
        "The database is updated by scanning — without it, search sees the previous snapshot.",
    "Включить учётную запись (UAC −= 2)": "Enable the account (UAC −= 2)", "Выключить": "Turn off",
    "Запомнить меня": "Remember me", "Останавливаю — начатые опросы дорабатывают…":
        "Stopping — started queries finish first…", "Подготовка…": "Preparing…",
    "Показывать низкий приоритет": "Show low priority",
    "Рядом с программой (папка ADK возле ADK.exe — конфиг и база всегда при нём)":
        "Next to the program (the ADK folder beside ADK.exe — config and database always with it)",
    "Собрать данные о парке сейчас?": "Collect fleet data now?",
    "Список программ снят с ПК прямо сейчас; если ПК недоступен — последний сохранённый.   ":
        "Software list taken from the PC right now; if the PC is unreachable — the last saved one.   ",
    "Узел отвечает": "Host responds", "да": "yes", "ожидает своей очереди": "waiting for its turn",
    "⏬ Свернуть — работа продолжится": "⏬ Minimize — work continues",
    "⏳ Собираю сводку (AD + инвентарь)…": "⏳ Building the summary (AD + inventory)…",
    "⏳ Читаю журнал Security на ПК (Get-WinEvent, до 1–2 минут на большом журнале)…":
        "⏳ Reading the PC Security log (Get-WinEvent, up to 1–2 minutes on a large log)…",
    "⏹ Остановить": "⏹ Stop", "▶ Продолжить": "▶ Resume", "● Включён": "● Enabled",
    "✅ Включить учётную запись": "✅ Enable the account", "✅ Горячая клавиша сохранена": "✅ Hotkey saved",
    "✅ Прочитать всё": "✅ Read all", "✔ надёжный": "✔ strong",
    "🆕 Базы здесь нет — она будет создана, и после входа ADK заполнит её: ":
        "🆕 No database here — it will be created, and after sign-in ADK will fill it: ",
    "💤 На 30 дней": "💤 For 30 days", "💤 Отложить на 7 дней": "💤 Snooze for 7 days",
    "🔍 Найти в главном окне": "🔍 Find in the main window", "🟢 в сети": "🟢 online",
    # здоровье, парк, свободный IP, первый запуск, инструменты, инвентарь, пинг, виджеты
    "⏳ Опрашиваю через PowerShell/CIM…": "⏳ Querying via PowerShell/CIM…", "🔄 Обновить": "🔄 Refresh",
    "📋 Копировать отчёт": "📋 Copy report", "<b>💾 Разделы</b>": "<b>💾 Partitions</b>",
    "<b>⚠️ Замечания</b>": "<b>⚠️ Findings</b>", "<b>Физические диски</b>": "<b>Physical drives</b>",
    "Выберите диск слева": "Select a drive on the left", "<b>Диск:</b>": "<b>Drive:</b>",
    "<b>Файлов в топе:</b>": "<b>Top files to show:</b>",
    "Сколько самых крупных файлов показывать в таблице «Файлы»": "How many of the largest files to show in the “Files” table",
    "🗺️ Построить карту": "🗺️ Build the map",
    "Прервать обход диска (процесс PowerShell на вашем ПК будет остановлен)":
        "Abort the drive walk (the PowerShell process on your PC will be stopped)",
    "Обход по \\\\ПК\\C$ — запускается только вручную, ПК при этом не тормозит, но ждать придётся.":
        "Walks \\\\PC\\C$ — manual only; the PC is not slowed down, but the walk takes time.",
    "Двойной клик — открыть папку на ПК в Проводнике (через \\\\ПК\\C$)":
        "Double-click — open the folder on the PC in Explorer (via \\\\PC\\C$)",
    "Клик по строке — путь в буфер обмена; полный путь во всплывающей подсказке":
        "Click a row — path to clipboard; the full path is in the tooltip",
    "Здесь пока пусто. Совсем.": "Nothing here yet. At all.", "<b>Уровни:</b>": "<b>Levels:</b>",
    "<b>Журнал:</b>": "<b>Event log:</b>", "с": "from", "по": "to", "📥 Загрузить": "📥 Load",
    "Счётчики появятся после загрузки — считаются ровно по событиям из таблицы.":
        "Counters appear after loading — computed exactly from the events in the table.",
    "Нажмите «Загрузить» — журнал читается с ПК по запросу, только чтение.":
        "Press “Load” — the log is read from the PC on demand, read-only.",
    "⚠️ Выберите хотя бы один уровень.": "⚠️ Select at least one level.",
    "⚠️ Дата «с» должна быть раньше даты «по».": "⚠️ The “from” date must be before the “to” date.",
    "⏳ Читаю журнал через Get-WinEvent…": "⏳ Reading the log via Get-WinEvent…",
    "Счётчики недоступны — журнал не прочитан.": "Counters unavailable — the log was not read.",
    "Нет данных о физических дисках (WinRM/CIM недоступен или нет прав на root/wmi).":
        "No physical drive data (WinRM/CIM unavailable or no access to root/wmi).",
    "⏹ Останавливаю обход…": "⏹ Stopping the walk…", "📋 Отчёт скопирован в буфер обмена": "📋 Report copied to clipboard",
    "🔄 Повторить": "🔄 Retry", "⚡ Разбудить выключенные (WoL)": "⚡ Wake the powered-off (WoL)",
    "Фильтр по названию/издателю…": "Filter by name/publisher…", "🔄 Опросить ПК": "🔄 Query the PC",
    "Все обновления за последнее время (Win32_QuickFixEngineering). Заполняется при опросе ПК.":
        "All recent updates (Win32_QuickFixEngineering). Filled when the PC is queried.",
    "Только обновления безопасности (по описанию KB). Заполняется при опросе ПК.":
        "Security updates only (by KB description). Filled when the PC is queried.",
    "Название программы (часть), например: 1С, Chrome, KES…": "Program name (part), e.g.: Chrome, KES…",
    "Опросить все ПК в сети (WinRM → WMI → удалённый реестр) и сохранить их ПО в базу":
        "Query all online PCs (WinRM → WMI → remote registry) and save their software to the database",
    "Поиск идёт по сохранённым данным опрошенных ПК. Двойной клик — найти ПК в главном окне.":
        "Search runs over saved data of queried PCs. Double-click — find the PC in the main window.",
    "Сохранённых данных о ПО пока нет — нажмите «Опросить парк» или опросите ПК из его карточки.":
        "No saved software data yet — press “Query the fleet” or query the PC from its card.",
    "Топ программ по числу ПК (по сохранённым данным). Введите название для точного поиска.":
        "Top programs by PC count (from saved data). Type a name for an exact search.",
    "⏳ Опрашиваю ПК (WinRM → WMI → удалённый реестр), обычно 10–60 с…":
        "⏳ Querying the PC (WinRM → WMI → remote registry), usually 10–60 s…",
    "⚠️ ПК для опроса не найдены: инвентарь пуст и AD не вернул рабочих станций":
        "⚠️ No PCs to query: the inventory is empty and AD returned no workstations",
    "Только различия": "Differences only", "⏳ Загрузка…": "⏳ Loading…", "⏳ Опрашиваю оба ПК…": "⏳ Querying both PCs…",
    "Первые три октета подсети /24": "First three octets of the /24 subnet",
    "С какого хоста начинать проверку": "Which host to start checking from", "🔍 Найти": "🔍 Find",
    "<b>Подсеть</b>": "<b>Subnet</b>", "<b>начиная с</b>": "<b>starting from</b>",
    "<b>Карта подсети</b> — клик по ячейке задаёт стартовый хост":
        "<b>Subnet map</b> — click a cell to set the starting host",
    "СВОБОДНЫЙ АДРЕС": "FREE ADDRESS", "➡️ Следующий": "➡️ Next", "<b>Найдено за сеанс</b>": "<b>Found this session</b>",
    "Двойной клик по строке — скопировать адрес": "Double-click a row to copy the address",
    "📋 Скопировать все": "📋 Copy all", "Скопировать все найденные адреса, по одному в строке":
        "Copy all found addresses, one per line",
    "Укажите подсеть и нажмите «Найти».": "Enter a subnet and press “Find”.", "Остановлено.": "Stopped.",
    "⏳ Завершаю предыдущую проверку…": "⏳ Finishing the previous check…",
    "Свободные адреса не найдены — попробуйте другую подсеть или меньший стартовый хост.":
        "No free addresses found — try another subnet or a lower starting host.",
    "В другой папке — общая для отдела, сетевая или уже существующая": "In another folder — shared for the team, network or existing",
    "📁 Обзор…": "📁 Browse…", "Укажите папку.": "Choose a folder.", "Использовать эту базу": "Use this database",
    "Создать базу здесь": "Create the database here", "＋ Добавить серию": "＋ Add a series",
    "Пропустить — все ПК домена": "Skip — all domain PCs", "💾 Сохранить и продолжить": "💾 Save and continue",
    "ℹ️ Список ПК домена недоступен — количество не показываю, маска всё равно сохранится.":
        "ℹ️ Domain PC list unavailable — no counts shown; the mask will still be saved.",
    "например PC- или PC-0000": "e.g. PC- or PC-0000", "Убрать серию": "Remove the series",
    "Фильтр групп…": "Filter groups…", "Потребовать смену пароля при входе": "Require password change at sign-in",
    "📋 Скопировать пароли": "📋 Copy passwords", "▶ Выполнить": "▶ Run", "⏳ Выполняется…": "⏳ Running…",
    "Новая заметка… (Ctrl+Enter — сохранить)": "New note… (Ctrl+Enter — save)",
    "🗑 Удалить выбранную": "🗑 Delete selected", "💾 Сохранить заметку": "💾 Save the note",
    "Логин эталонного пользователя (например, petrov)": "Reference user login (e.g. petrov)",
    "Сравнить": "Compare", "Эталон:": "Reference:", "➕ Добавить выбранные": "➕ Add selected",
    "➖ Удалить выбранные": "➖ Remove selected", "<b>1. Организация</b>": "<b>1. Organization</b>",
    "Фильтр…": "Filter…", "Загрузка списка организаций…": "Loading the organization list…",
    "👁 Предпросмотр": "👁 Preview", "<b>2. Что попадёт в файл</b> — выберите организацию слева":
        "<b>2. What goes into the file</b> — pick an organization on the left",
    "<b>3. Колонки</b>": "<b>3. Columns</b>", "Стандарт": "Standard", "<b>Папка</b>": "<b>Folder</b>",
    "📁 Выбрать…": "📁 Choose…", "Открыть после сохранения": "Open after saving",
    "💾 Сохранить Excel": "💾 Save Excel", "⏳ Собираю данные…": "⏳ Collecting data…",
    "У сотрудника нет привязанного ПК": "The employee has no linked PC", "⏳ Запись Excel…": "⏳ Writing Excel…",
    "Запуск…": "Starting…", "мс\nсейчас": "ms\nnow", "<b>Журнал</b>": "<b>Log</b>", "⏸ Пауза": "⏸ Pause",
    "↺ Сброс": "↺ Reset", "🖥 Копировать вывод ping": "🖥 Copy ping output",
    "Весь вывод как в консоли — для вставки в заявку": "Full console-style output — for pasting into a ticket",
    "Ошибка": "Error", "Развернуть / восстановить (двойной клик по шапке, F11 — во весь экран)":
        "Maximize / restore (double-click the title bar, F11 — full screen)",
    "ОК": "OK", "Покажутся только ПК выбранной организации — как в Excel-описи.":
        "Only PCs of the selected organization will be shown — as in the Excel inventory.",
    "Фильтр по названию…": "Filter by name…", "Загрузка списка организаций из AD…": "Loading the organization list from AD…",
    "Выбрать": "Select",
    # диалоги (dialogs.py)
    "<b>Логин</b>": "<b>Login</b>", "<b>Пароль</b>": "<b>Password</b>",
    "пароль доменной учётной записи": "domain account password",
    "Показать": "Show", "Скрыть": "Hide", "Показать/скрыть пароль": "Show/hide the password",
    "Защищённое хранилище недоступно — пароль сохранить не получится":
        "Secure storage is unavailable — the password cannot be saved",
    "Запомнить способ входа": "Remember the sign-in method",
    "В следующий раз ADK сам выберет тот же способ: вход по паролю или через Windows (SSO)":
        "Next time ADK will pick the same method: password or Windows (SSO)",
    "Войти": "Sign in", "Вход по логину и паролю (NTLM)": "Sign in with login and password (NTLM)",
    "🪟 Войти под текущим пользователем Windows": "🪟 Sign in as the current Windows user",
    "Windows SSO (Kerberos): без ввода пароля, под учёткой, из-под которой запущена программа":
        "Windows SSO (Kerberos): no password, runs under the account that launched ADK",
    "<b>Возможности в текущей сессии:</b>": "<b>Available in this session:</b>",
    "Больше не показывать при входе": "Do not show at sign-in again",
    "Продолжить": "Continue", "Понятно": "Got it",
    "<b>Плагины — свои кнопки в инспекторе и в меню строки</b>": "<b>Plugins — your own buttons in the inspector and the menu</b>",
    "➕ Создать шаблон плагина": "➕ Create a plugin template",
    "Создать файл-заготовку с полной документацией внутри (выключен, пока не переименован)":
        "Creates a starter file with full documentation inside (disabled until renamed)",
    "📁 Папка плагинов": "📁 Plugins folder", "🔄 Перечитать": "🔄 Reload",
    "Включить": "Enable", "✏️ Открыть файл": "✏️ Open file", "🗑️ Удалить": "🗑️ Delete",
    "Двойной клик по строке — включить/выключить. Выключенные файлы начинаются с «_».":
        "Double-click a row to enable/disable. Disabled files start with “_”.",
    "<b>Как сделать свой плагин</b>": "<b>How to make your own plugin</b>",
    "Сравнить группы с эталонным сотрудником и выровнять членство":
        "Compare groups with a reference employee and align membership",
    "🔑 Смена пароля…": "🔑 Change password…",
    "Новый пароль по политике или свой; крупно на экране, карточка для сотрудника, снятие блокировки":
        "A policy-compliant or custom password; shown large on screen, a card for the employee, unlock",
    "🔓 Снять блокировку": "🔓 Unlock",
    "lockoutTime = 0 — снимает блокировку после неверных паролей, учётку не включает":
        "lockoutTime = 0 — clears the lockout after wrong passwords, does not enable the account",
    "Только смарт-карта для входа": "Smart card required for sign-in",
    "Отправляются только изменённые поля; пустое значение очищает атрибут.":
        "Only changed fields are sent; an empty value clears the attribute.",
    "💾 Сохранить изменения в AD": "💾 Save changes to AD",
    "Смена пароля, снятие блокировки и отключение — кнопки в верхней панели карточки.":
        "Password change, unlock and disable — buttons on the card’s top panel.",
    "Только просмотр: изменение объектов AD недоступно для вашей роли":
        "Read-only: editing AD objects is not available for your role",
    "Только просмотр: состояние задаёт администратор с правом «AD»":
        "Read-only: the state is managed by an admin with the “AD” right",
    "Только просмотр: добавление в группы недоступно для вашей роли":
        "Read-only: adding to groups is not available for your role",
    "Только просмотр: двойной клик покажет участников группы":
        "Read-only: double-click shows group members",
    "<b>Состоит в группах</b> · двойной клик — участники": "<b>Member of</b> · double-click — members",
    "➖ Удалить из выбранной": "➖ Remove from selected", "<b>Все группы домена</b>": "<b>All domain groups</b>",
    "Фильтр по имени группы…": "Filter by group name…", "➕ Добавить в выбранную": "➕ Add to selected",
    "<b>Связанный ПК:</b>": "<b>Linked PC:</b>", "Имя ПК, например WS-101": "PC name, e.g. WS-101",
    "💾 Сохранить привязку": "💾 Save the link",
    "Можно ввести свой пароль — минимум 8 символов": "You can type your own password — 8 characters minimum",
    "Длина:": "Length:", "🎲 Другой": "🎲 Another", "Сгенерировать заново": "Generate again",
    "Потребовать смену пароля при следующем входе": "Require password change at next sign-in",
    "Снять блокировку (lockout), если была": "Clear the lockout, if any",
    "Задано сценарием «после снятия блокировки»: смена при входе не требуется":
        "Set by the “after unlock” scenario: no change required at sign-in",
    "Задано сценарием «после снятия блокировки»: блокировка снимается":
        "Set by the “after unlock” scenario: the lockout is cleared",
    "📋 Копировать карточку": "📋 Copy card", "🔑 Сменить пароль": "🔑 Change password",
    "Поиск по логину / ПК / деталям…": "Search by login / PC / details…",
    "Фильтр: модель / IP / имя ПК…": "Filter: model / IP / PC name…",
    "📡 Опросить парк": "📡 Query the fleet", "🏢 Область": "🏢 Organization", "⏹ Стоп": "⏹ Stop",
    "💾 Сохранить в базу": "💾 Save to database",
    "Записать результат живого опроса в кэш принтеров — по нему работает поиск по IP/модели":
        "Saves the live query result to the printer cache — IP/model search uses it",
    "В базе пока нет принтеров — нажмите «Опросить парк», чтобы собрать их с ПК прямо сейчас":
        "No printers in the database yet — press “Query the fleet” to collect them from PCs now",
    "⚠️ ПК для опроса не найдены: инвентарь пуст и AD не вернул рабочих станций (проверьте host_pattern)":
        "⚠️ No PCs to query: the inventory is empty and AD returned no workstations (check host_pattern)",
    "⏹ Останавливаю — начатые опросы доработают…": "⏹ Stopping — started queries finish first…",
    "Загрузка…": "Loading…", "✨ Сгенерировать": "✨ Generate",
    "Логин из фамилии и имени (транслит) + надёжный пароль":
        "Login from surname and first name (translit) + a strong password",
    "минимум 8 символов": "8 characters minimum", "подсказки из AD…": "hints from AD…",
    "Сохранить": "Save", "Удалить": "Delete",
    "Сохранить текущие поля (и группы образца) как шаблон": "Save the current fields (and sample groups) as a template",
    "Удалить выбранный шаблон": "Delete the selected template",
    "👥 Как у сотрудника…": "👥 Like another employee…",
    "Скопировать должность, отдел, группы и OU у существующего сотрудника":
        "Copy title, department, groups and OU from an existing employee",
    "👤 Новый сотрудник": "👤 New employee", "<b>Готовность</b>": "<b>Readiness</b>",
    "➕ Создать учётную запись": "➕ Create the account",
    "Создаётся отключённой → пароль → включается. При сбое учётка удаляется.":
        "Created disabled → password → enabled. On failure the account is removed.",
    "кликните сюда и нажмите сочетание…": "click here and press a combination…",
    "Нажмите сочетание (например Ctrl+Shift+A). Esc — очистить, Enter — сохранить.":
        "Press a combination (e.g. Ctrl+Shift+A). Esc — clear, Enter — save.",
    "Изменения применяются сразу и сохраняются в config.ini": "Changes apply immediately and are saved to config.ini",
    "↺ Тема по умолчанию": "↺ Default theme", "💾 Сохранить и закрыть": "💾 Save and close",
    "↩ Сбросить к теме по умолчанию (Графит)": "↩ Reset to the default theme (Graphite)",
    "Вернуть встроенную тёмную тему «Графит» — как при первой установке":
        "Restore the built-in dark “Graphite” theme — as on first install",
    "<b>Акцентный цвет</b> — кнопки, заголовки, выделение": "<b>Accent color</b> — buttons, headers, highlights",
    "+ Свой…": "+ Custom…", "Выбрать акцент из палитры": "Pick an accent from the palette",
    "<b>Фон окна</b>": "<b>Window background</b>", "Градиент:": "Gradient:",
    "Поменять": "Swap", "Поменять цвета местами": "Swap the two colors",
    "Применить градиент": "Apply the gradient",
    "Тёмная/светлая — как в Windows (следовать системной теме)": "Dark/light — follow the Windows system theme",
    "Иванов Иван Петрович · WS-101 · 10.0.2.11 · Съешь же ещё этих мягких французских булок":
        "John Johnson · WS-101 · 10.0.2.11 · The quick brown fox jumps over the lazy dog",
    "Показывать значок ADK в трее (закрытие крестиком — всегда выход)":
        "Show the ADK tray icon (closing with ✕ always exits)",
    "💾 Сохранить": "💾 Save", "↺ Сбросить сохранённый способ входа": "↺ Reset the saved sign-in method",
    "Вернуть экран входа к обычному виду: ADK снова спросит, как входить (пароль или Windows)":
        "Restore the sign-in screen to its usual look: ADK will ask again how to sign in (password or Windows)",
    "💾 Сохранить настройки интерфейса": "💾 Save interface settings",
    "Язык применяется сразу; трей и горячая клавиша — тоже (без перезапуска).":
        "The language applies immediately; so do the tray icon and the hotkey (no restart).",
    "✅ Настройки интерфейса сохранены": "✅ Interface settings saved",
    "✅ Способ входа сброшен — ADK снова спросит, как входить": "✅ Sign-in method reset — ADK will ask again",
    "✅ Применено и сохранено": "✅ Applied and saved",
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
