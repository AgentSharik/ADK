# ADK — ответы на вопросы по архитектуре (шпаргалка для защиты)

Все ответы — по реальному коду проекта. Где в вопросе цифра или формулировка не совпадает с кодом,
я честно пишу как есть на самом деле: на защите лучше сказать «на самом деле так», чем попасться.

---

## 1. Архитектура и модульность

### 1.1. Как разделён монолит и как избежаны циклические импорты

**Что такое циклический импорт.** `a.py` делает `from b import X`, а `b.py` — `from a import Y`. Python начинает
загружать `a`, на первой строке идёт грузить `b`, а `b` просит `Y` из `a`, который ещё не дочитан → `ImportError`
(или `None` вместо модуля). Лечится одним правилом: **зависимости идут только вниз, снизу вверх — никогда**.

**Как это сделано у нас.** Пакет `adk/` — 30 модулей (не 11 — в вопросе старая цифра, ответьте: «начиналось с
11, после 3.0/3.1 стало 30, принцип тот же»). Они выстроены в слои:

```
уровень 0  config, md4, theme, i18n, pgadapter, plugins, export, tray   — ни от кого не зависят
уровень 1  db, ad, credentials, access, templates, updates               — только от config
уровень 2  netutils, nettools, software, logons, health, attention, notify — от config/db/ad
уровень 3  workers (QThread)                                             — от ad/db/netutils
уровень 4  widgets (общие виджеты)                                       — только от theme
уровень 5  dialogs, tools, fleet, extras, attention_ui                   — от всего выше
уровень 6  main_window                                                   — собирает всё
уровень 7  __main__, cli, run.py                                         — точки входа
```

Проверить можно скриптом за 10 строк (я так и делал): собрать `from . import …` из каждого файла и убедиться,
что `db` не импортирует `dialogs`, `workers` не импортирует `main_window` и т. д. Граф — ациклический.

**Три приёма, которые это обеспечивают:**

1. **Данные не знают про GUI.** `db.py`, `ad.py`, `netutils.py` — обычный Python без единого `import PyQt6`.
   Поэтому их можно вызывать из CLI, из сервера `--serve`, из тестов без Qt.
2. **Воркеры получают зависимости через параметры, а не импортом.** `SearchWorker(self.get_conn, q, …)` —
   окно передаёт функцию-фабрику соединения. Воркеру не нужно импортировать `main_window`, чтобы получить LDAP.
3. **Ленивый импорт там, где связь всё-таки нужна «вверх».** Пример — `widgets.run_in_background`:
   ```python
   def run_in_background(parent, fn, on_done, on_error=None):
       from .workers import FunctionWorker      # импорт внутри функции, а не в шапке файла
   ```
   `widgets` — уровень 4, `workers` — уровень 3, формально это «вниз», но `workers` тянет `ad`/`db`, а `widgets`
   должен оставаться лёгким (его импортирует диалог входа до подключения к AD). Импорт внутри функции выполняется
   только в момент вызова, когда все модули уже загружены — цикла не возникает.
   То же в `access.resolve()`: `from . import ad` внутри функции.

### 1.2. Почему SQLite и как заложен переход на PostgreSQL

**Почему SQLite.** Он встроен в Python (`import sqlite3`, ставить нечего), база — один файл в `Documents\ADK`,
не нужен сервер и учётка БД, для 1–3 админов и 30 000 ПК скорость с индексами достаточная (см. §4).
Инвентарь — это кэш, который пересобирается сканером, потерять его не страшно.

**Как заложен переход.** Весь доступ к базе идёт через две функции в `db.py`:

```python
def get_db_connection():
    if settings.db_backend == "postgres" and settings.db_dsn:
        from .pgadapter import connect
        return connect(settings.db_dsn)          # объект с тем же интерфейсом .execute/.fetchall
    return sqlite3.connect(settings.db_path, timeout=...)

def db_execute_with_retry(query, params=(), fetch=None):
    with contextlib.closing(get_db_connection()) as conn:
        cur = conn.execute(query, params)
        ...
```

Больше нигде в проекте `sqlite3.connect` не встречается. Значит, чтобы сменить БД, меняется одно место.
`pgadapter.py` — «переводчик»: оборачивает соединение psycopg и на лету переписывает SQL, который написан
в диалекте SQLite, в диалект PostgreSQL: `?` → `%s`, `datetime('now','localtime')` → `now()`,
`INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`, `AUTOINCREMENT` → `SERIAL` и т. п. Остальные 700 строк `db.py`
не знают, с какой базой работают. Это классический паттерн «адаптер».

Что ответить на «почему не ORM (SQLAlchemy)?»: ORM — лишняя зависимость и слой магии для 15 таблиц; чистый SQL
проще читать, и его точно так же можно тестировать (см. `tests/test_core.py`).

### 1.3. run.py — одинаковая работа в venv и внутри ADK.exe

`run.py` — 5 строк:

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # корень проекта → первым в путь поиска модулей
from adk.__main__ import main
sys.exit(main())
```

Python ищет пакеты по списку `sys.path`. Когда вы запускаете из PyCharm или из другой папки, «текущая директория»
может быть какой угодно, и `import adk` не найдётся. Первая строка гарантирует: папка, где лежит `run.py`, всегда
в пути, откуда бы ни запустили.

Внутри PyInstaller-сборки `sys.path` тоже свой (временная папка `_MEIPASS`), но `adk` туда упакован целиком,
и та же строка не мешает. Разница между режимами спрятана в `config.py`:

```python
def _app_dir():
    if getattr(sys, "frozen", False):                      # PyInstaller ставит этот флаг
        return os.path.dirname(os.path.abspath(sys.executable))   # папка рядом с ADK.exe
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # корень репозитория
```

Всё, что зависит от «где я лежу» (ищем `portable`, `version.txt`, `assets/logo.png`), спрашивает `_app_dir()`.
Код приложения одинаковый, отличается только ответ этой функции.

### 1.4. Изоляция config.ini, баз и логов в Documents\ADK

`config.py` при импорте один раз вычисляет `DOCS_DIR`:

```python
def _resolve_docs_dir():
    if os.environ.get("ADK_HOME"): return ADK_HOME                       # явно заданный путь
    if ADK_PORTABLE == "1" or os.path.exists(app_dir / "portable"):       # флешка
        return app_dir / "data"
    return ~/Documents/ADK                                               # обычный режим
```

Дальше от него строятся `CONFIG_FILE`, `DB_PATH`, `LOG_FILE`, `CRED_FILE`, `plugins/`, `templates.json`.
Зачем именно так:

- **Профиль пользователя** (`Documents`) доступен на запись без прав администратора и попадает в перемещаемые
  профили/бэкапы. `Program Files` — только чтение.
- **Каждый админ — своя база и свой конфиг.** Две учётки на одном ПК не мешают друг другу.
- **Один exe — много режимов.** Тот же файл работает с флешки (`portable`), на сервере (`ADK_HOME=D:\adk-server`)
  и у пользователя. В тестах `conftest.py` подменяет `settings.db_path` на временный файл — база на диске
  не трогается.
- Логи — `RotatingFileHandler(maxBytes=2 МБ, backupCount=5)`: журнал не растёт бесконечно.
- `config.example.ini` лежит в репозитории, а рабочий `config.ini` — в `Documents\ADK` и в git не попадает.
  При первом запуске программа сама создаёт его из `DEFAULTS` в `config.py`.

---

## 2. PyQt6, асинхронность и UI

### 2.1. Почему сеть в QThread, а не в главном потоке

В Qt один поток рисует окно и обрабатывает клики — **главный (GUI) поток**. Пока в нём выполняется ваша
функция, окно не перерисовывается и не реагирует. LDAP-поиск — 0,3–2 с, пинг 30 ПК — до 30 с при таймауте 1 с
каждый. Если сделать это в слоте кнопки — Windows через 5 секунд напишет «Не отвечает».

Поэтому:

```python
class SearchWorker(BaseWorker):            # BaseWorker(QThread)
    results_ready = pyqtSignal(list, str, bool)
    def run(self):                          # выполняется в отдельном потоке
        rows = ... paged_search ... ping ...
        self.results_ready.emit(rows, self.raw_query, truncated)   # сигнал → главный поток
```

Главное правило, которое мы соблюдаем: **виджеты трогает только главный поток**. Воркер не вызывает
`self.table.setItem`, а *эмитит сигнал*; Qt сам доставит его в главный поток (`QueuedConnection`), и там
уже слот `on_results` наполнит таблицу. В `run()` только чистые функции из `ad`/`db`/`netutils` — вот зачем
нужна была изоляция слоёв из §1.1.

Внутри воркера пинг 30 ПК идёт ещё и параллельно — `ThreadPoolExecutor` (`workers.py`, `futs`), потому что
ожидание ответа ICMP — это простой, GIL не мешает.

### 2.2. Как избегается segfault при закрытии окна с живым потоком

**Откуда segfault.** `QThread` — C++-объект. Если Python удалит его (окно закрылось, родитель уничтожен), пока
C++-поток ещё крутится в `run()`, тот пишет в освобождённую память → падение без traceback.
Классическое сообщение: `QThread: Destroyed while thread is still running`.

**Три меры в коде:**

1. **Кооперативная отмена + ожидание в `closeEvent`:**
   ```python
   for w in [self.scanner, *self._threads, *self._bg_workers]:
       if w is not None and w.isRunning(): w.cancel()        # ставим флаг
   for w in ...:
       if w is not None and w.isRunning(): w.wait(3000)      # ждём до 3 с, пока поток сам выйдет
   event.accept()
   ```
   `cancel()` — просто `self._cancelled = True`; циклы в `run()` проверяют `if self.cancelled: return`.
   Мы **не используем `terminate()`** — он убивает поток посреди записи в SQLite и оставляет базу залоченной.
2. **Ссылки держим списком** `self._threads`, а не локальной переменной. Иначе Python соберёт объект
   сразу после выхода из метода, пока поток ещё бежит (и это самая частая причина падения у новичков).
3. **Фоновые задачи диалогов живут дольше диалога.** `run_in_background` создаёт `FunctionWorker`
   **без Qt-родителя** и кладёт в глобальный `set` `_LIVE_WORKERS`. Если пользователь закрыл диалог раньше, чем
   пришёл LDAP-ответ, поток спокойно дорабатывает, `detach_background_workers` отцепляет его сигналы
   (чтобы не дёрнуть слот уже удалённого виджета), и только по `finished` вызывается `deleteLater()`.
   `deleteLater` — «удали, когда вернёшься в цикл событий», это безопасный момент.

### 2.3. Почему поля воркера нельзя называть start / finished / result / done

Потому что эти имена **уже заняты у `QThread`**:

- `start()` — метод, запускающий поток;
- `finished` — встроенный сигнал «поток завершился»;
- `result`/`done` — не у QThread, но у `QDialog` (`done(int)`, `result()`), и воркеры/диалоги часто путают.

Если написать `self.finished = pyqtSignal(str)` — вы затрёте настоящий сигнал, и `w.finished.connect(_cleanup)`
(на который опирается уборка из §2.2) перестанет срабатывать — поток «утечёт». Поэтому в коде имена с префиксом:
`finished_scan`, `finished_search`, `finished_export`, `results_ready`. Проверить, свободно ли имя:
`hasattr(QThread, "finished")` → `True` — занято.

### 2.4. Debounce 450 мс

```python
self.debounce = QTimer(self); self.debounce.setSingleShot(True)
self.debounce.timeout.connect(self.start_search)
self.search_input.textChanged.connect(lambda: self.debounce.start(450))   # каждый символ ПЕРЕЗАПУСКАЕТ таймер
```

`start()` на уже идущем таймере сбрасывает отсчёт. Пока пользователь печатает быстрее, чем раз в 450 мс,
таймер не дотикивает; поиск уходит один раз — после паузы. Слово «иванов» без debounce = 5 LDAP-запросов
(«ив», «ива», «ива́н», …), каждый — subtree-поиск по всему домену с `*` в фильтре, плюс пинг найденных ПК.
С debounce — один. При 20 администраторах это разница между ~100 и ~20 запросами к контроллеру на каждое
слово. Дополнительно `_cancel_search()` помечает предыдущий воркер отменённым, а `on_results` сверяет
`query` с текущим текстом поля — устаревший ответ отбрасывается (иначе результат «ива» мог прийти позже «иванов»
и затереть его).

### 2.5. Безрамочное окно и ресайз за края

- `setWindowFlags(Qt.WindowType.FramelessWindowHint)` убирает системный заголовок и рамку. Взамен пропадают
  перетаскивание, ресайз, кнопки — их надо вернуть самим.
- **Заголовок** — свой виджет `TitleBar` (`widgets.py`): логотип, «ADK», кнопки min/max/close с иконками
  `QStyle.standardIcon` (символы Unicode рисовались квадратами без нужного шрифта — проверено).
- **Перетаскивание** — не через ручной пересчёт координат (дёргается, ломается на нескольких мониторах), а
  системным способом: `self.window().windowHandle().startSystemMove()` по нажатию на шапку. Windows сам
  двигает окно, как будто вы держите настоящий заголовок.
- **Ресайз** — `EdgeResizeFilter(QObject)` с `eventFilter`: на `MouseMove` вычисляет, попал ли курсор в
  6-пиксельную полосу у края (`_edges(pos)` возвращает `Qt.Edge.LeftEdge | TopEdge` и т. п.) и меняет курсор;
  на `MouseButtonPress` у края вызывает `windowHandle().startSystemResize(edges)`. Это API Qt ≥ 5.15, работает
  на Windows и большинстве Linux. Весь `eventFilter` обёрнут в `try/except`, потому что исключение внутри
  фильтра событий Qt роняет процесс целиком.
- Двойной клик по шапке — `toggle_max`, `F11` — `toggle_fullscreen`; иконка кнопки «развернуть» синхронизируется
  в `changeEvent` окна.

### 2.6. Глобальный хоткей Ctrl+Shift+A: RegisterHotKey + nativeEvent

Обычные `QShortcut` работают только когда окно активно. Чтобы вызвать ADK из трея поверх Outlook, нужен API
Windows:

1. `ctypes.windll.user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL|MOD_SHIFT|MOD_NOREPEAT, VK_A)` —
   регистрируем сочетание в системе. Если оно занято другим приложением — функция вернёт 0, мы пишем warning
   и работаем без хоткея (не падаем).
2. Windows при нажатии кладёт в очередь сообщений нашего процесса `WM_HOTKEY (0x0312)`.
3. Qt отдаёт «сырые» сообщения ОС в метод `nativeEvent(eventType, message)`; `GlobalHotkey` устанавливает
   `QAbstractNativeEventFilter`, читает структуру `MSG` через `ctypes`, и если `msg.message == WM_HOTKEY` и
   `wParam == HOTKEY_ID` — вызывает `window.show_and_focus()`.
4. В `closeEvent` — `UnregisterHotKey`, иначе сочетание останется занятым до перезагрузки.

На Linux/macOS этот код не выполняется (`IS_WINDOWS`), хоткей просто недоступен.

### 2.7. Рассинхронизация бейджей при сортировке

**Проблема.** Соблазн для цветного статуса — `table.setCellWidget(row, col, QLabel(...))`. Но `cellWidget`
привязан к **номеру строки**, а `sortItems()` переставляет **items**. После сортировки виджет «В сети» остаётся
в строке 3, а там уже другой человек.

**Решение.** Статус — обычный `QTableWidgetItem` (`StatusItem`), он сортируется вместе со строкой. А как он
*выглядит* — решает делегат `BadgeDelegate(QStyledItemDelegate)`, назначенный на колонки «Учётка» и «Сеть»:
он читает вид бейджа из `item.data(BADGE_ROLE)` и рисует пилюлю (`drawRoundedRect` + текст). Делегат — это
«как рисовать ячейку», данных он не хранит → нечему рассинхронизироваться. Второй нюанс: сортировку выключаем
на время заполнения (`setSortingEnabled(False)` → заполнить → `True`), иначе таблица пересортировывает после
каждого `setItem` и ставит следующий item не в ту строку. Третий: после сортировки выделение остаётся на том же
номере строки — поэтому инспектор берёт пользователя не по `row`, а по `item.data(UserRole)`, где хранится индекс
в `self.results`.

Тест `test_main_window_search_and_inspector` ровно это и проверяет: сортирует по «Учётка» и смотрит, что у
petrov по-прежнему «Отключена».

---

## 3. Active Directory, безопасность и протоколы

### 3.1. Fallback NTLM → MD4

Вход по NTLM требует NT-хэш пароля = **MD4(пароль в UTF-16LE)**. `ldap3` считает его через `hashlib.new("md4")`.
OpenSSL 3 (Python ≥ 3.9 на новых сборках/системах) вынес MD4 в «legacy provider» и по умолчанию отключил →
`ValueError: unsupported hash type md4`, и NTLM-вход ломается на ровном месте.

`ad.py` при импорте:

```python
def _install_md4_fallback():
    try:
        hashlib.new("md4"); return                 # есть — ничего не трогаем
    except ValueError:
        pass
    from .md4 import PureMD4                       # наша реализация RFC 1320 (~60 строк)
    _orig = hashlib.new
    def _new(name, data=b"", **kw):
        return PureMD4(data) if name.lower() == "md4" else _orig(name, data, **kw)
    hashlib.new = _new
```

Зачем «чистая» реализация, а не библиотека: зависимость `pycryptodome` ради одной функции + проблемы с
PyInstaller. MD4 — это 3 раунда битовых операций над 16 словами, пишется по RFC и проверяется тестовыми
векторами из самого RFC (`test_md4_rfc_vectors`: `MD4("") = 31d6cfe0…`, `MD4("abc") = a448017a…`).
Криптографически MD4 давно сломан, но здесь он не для защиты, а для совместимости с протоколом —
безопасность даёт TLS поверх.

### 3.2. Почему LDAPS обязателен и защита от LDAP-инъекций

**LDAPS (636/TLS).** Без него: (а) NTLM/simple bind передаёт материал пароля админа по сети;
(б) AD **отказывается** менять пароль (`unicodePwd`) по незашифрованному каналу — операция просто вернёт
`unwillingToPerform`. Поэтому `reset_password` и `create` сначала проверяют `settings.use_ssl` и говорят
человеческим языком «включите use_ssl», а не показывают код ошибки LDAP.

**Инъекции.** LDAP-фильтр — строка: `(&(objectClass=user)(sAMAccountName=ivanov))`. Если подставить ввод
пользователя как есть, запрос `*)(userAccountControl=*` превратит его в «все учётки». Правило одно:
**всё, что пришло от человека, проходит `escape_filter_chars()`** (из `ldap3.utils.conv`) — она заменяет
`( ) * \ NUL` на `\28 \29 \2a \5c \00`. Примеры в коде: `access.resolve` (`sAMAccountName={login}`),
`ad.create_user` (проверка занятости логина), `SearchWorker._build_filter`. Единственное место, где `*` нужен
как маска — поиск по подстроке; там звёздочки добавляет сам код *вокруг* уже экранированного текста:
`f"(cn=*{escape_filter_chars(q)}*)"`. Тест `test_ldap_filter_escapes_user_input`: строка `иван*)(cn=*` превращается в `\2a\29\28cn=\2a`, скобки сбалансированы.

Второй уровень — `dn_to_cn()` и составление DN при создании: `cn` тоже экранируется по RFC 4514
(`escape_rdn`), иначе фамилия с запятой создаст объект не в той OU.

### 3.3. Хранение пароля админа: DPAPI и keyring

Пароль нужен между сеансами, чтобы не вводить его 20 раз в день. Хранить в `config.ini` нельзя.

`credentials.py`:

```python
if IS_WINDOWS and win32crypt is not None:
    data = {"username": u, "dpapi": _dpapi_protect(password)}     # CryptProtectData → base64
elif keyring is not None:
    keyring.set_password(APP_NAME, u, password); data = {"username": u, "keyring": True}
else:
    return False                                                  # нет защищённого хранилища — не сохраняем вовсе
json.dump(data, CRED_FILE)
```

- **DPAPI** (`CryptProtectData`) шифрует ключом, производным от **пароля Windows-учётки текущего пользователя
  на этой машине**. Файл `credentials.json` можно скопировать на другой ПК или открыть под другой учёткой —
  расшифровать не получится. Ключом управляет Windows, в программе секретов нет.
- **keyring** на Linux/macOS — системная связка ключей (GNOME Keyring / macOS Keychain).
- В файле лежит только логин и шифртекст; при ошибке расшифровки (сменили пароль Windows) — молча просим
  ввести заново. «Забыть» — `clear_credentials()` удаляет файл и запись keyring.
- Сессия LDAP не хранится: на каждую операцию `get_conn()` открывает соединение и `unbind()` закрывает
  (`try/finally` в каждом воркере). Просто и без «протухших» соединений.

### 3.4. Разграничение прав: интерфейс и бизнес-логика

Два независимых права — **«ПК»** (`pc_admin_groups`) и **«AD»** (`ad_admin_groups`); старые
`readonly`/`readonly_group`/`admin_groups` работают как раньше. Определение роли — после входа, в фоне:
`access.resolve()` читает `memberOf` собственной учётки админа и сравнивает CN групп со списками из конфига
(`evaluate_rights()` — чистая функция, покрыта тестами). Результат кэшируется на процесс.

Каждое изменяющее действие имеет класс в словаре `ACTION_CLASS = {"restart": "pc", "reset_password": "ad", …}`;
`access.can("reset_password")` смотрит класс и соответствующий флаг. Не перечисленные действия (пинг, экспорт)
разрешены всегда.

**Уровень интерфейса** — `apply_access()`: кнопки, зарегистрированные в `_modifying_buttons` как
`(кнопка, действие)`, скрываются, если `can(действие)` ложно; бейдж в шапке показывает, какого права нет;
в карточке AD поля становятся read-only.

**Уровень логики** — каждый слот, который что-то меняет, начинает с `if self._deny("действие"): return`
(окно, карточка, массовые операции, «Группы как у…», WoL, заметки, плагины). Это защита от хоткея, пункта меню,
устаревшего состояния кнопки. Тесты дёргают именно слоты в режиме без прав и проверяют, что LDAP-заглушка
не получила `modify`.

Честный ответ на вопрос «а это безопасность?»: нет, это **UX и защита от случайного клика**. Настоящая граница —
ACL домена: все записи в LDAP идут под учёткой самого администратора, и без прав контроллер вернёт
`insufficientAccessRights`, что бы ни нажали в ADK.

### 3.5. Сброс пароля: TLS и отсутствие паролей в логах

- TLS — см. 3.2: AD не примет `unicodePwd` без шифрования, поэтому проверка `use_ssl` до открытия диалога.
- **Пароль никогда не попадает в аргументы `log_action`.** Журнал пишет
  `log_action(admin, "reset_password", login, "must_change=1")` — только факт и флаги.
- `logging` в `ad.py`/`workers.py` пишет `describe_ldap_error(exc)` — короткое описание кода результата, а не
  `repr` объекта запроса (в котором ldap3 держит атрибуты, включая пароль). `log.exception` стоит только в
  местах, где в стеке нет пароля.
- Временный пароль (QR) генерируется, показывается и **не сохраняется** — ни в БД, ни в файле; окно закрыли —
  его нет (`TempPasswordDialog`, `password_card_text` возвращает строку только для QR).
- Поле ввода — `QLineEdit.EchoMode.Password`; в `credentials.json` — только шифртекст DPAPI (3.3).
- Тест `test_audit_calls_never_receive_password_variables` проходит по исходникам и проверяет, что ни в одном
  вызове `log_action(...)` нет переменной `pwd`/`password` — защита от будущей случайной правки.

### 3.6. Безопасное создание пользователя: add(514) → пароль → 512

Нельзя создать учётку сразу включённой с паролем: AD требует `unicodePwd` отдельной операцией, а объект без
пароля с флагом «включена» — это дырка (пустой пароль до первого входа) и нарушение политики домена
(контроллер вернёт ошибку). Поэтому `ad.create_user`:

```python
attributes["userAccountControl"] = NORMAL_ACCOUNT | ACCOUNT_DISABLE      # 514 — создаём ОТКЛЮЧЁННОЙ
conn.add(dn, attributes=attributes)
try:
    conn.extend.microsoft.modify_password(dn, password)                   # задаём пароль (нужен LDAPS)
    conn.modify(dn, {"userAccountControl": [(MODIFY_REPLACE, [512])]})     # только теперь включаем
except Exception:
    conn.delete(dn)                                                       # откат: полуготовой УЗ не остаётся
    raise
```

Возможные сбои: пароль не прошёл политику сложности, оборвалась сеть, нет прав на `unicodePwd`. В любом из них
в домене либо ничего не появилось, либо осталась **отключённая** учётка, которую тут же удалили. Включённой
без пароля — не бывает. Это «транзакция руками», потому что в LDAP настоящих транзакций нет.

---

## 4. Оптимизация, индексация и алгоритмы

### 4.1. Сопоставление «пользователь → ПК»: от O(N×M) к O(N+M)

В вопросе написано O(1) — точнее сказать: **O(1) на один поиск, O(N+M) на всё**, вместо O(N×M).

**Было:** для каждого из N найденных пользователей — проход по всем M записям инвентаря
`for user in users: for pc in inventory: if pc.user == user.login`. 300 × 30 000 = 9 млн сравнений,
~0,8 с на каждый поиск.

**Стало** (`SearchWorker`, `workers.py:169–195`): один проход по инвентарю строит словарь
`inv_by_user = {login: [pc1, pc2]}` (M операций), потом для каждого пользователя —
`by_user.get(login)` — обращение к хэш-таблице, константа. 30 000 + 300 операций, ~0,1 с.

Это самый частый приём оптимизации в Python: **вложенный цикл → словарь**. Дополнительно:
`normalize_login()` приводит `CORP\Ivanov`, `ivanov@corp.local`, `IVANOV` к одному ключу до построения словаря,
иначе индекс промахивается.

### 4.2. Кэш DNS/ICMP на 20 секунд

```python
NET_CACHE_TTL = 20.0
_net_cache: dict[str, tuple[float, tuple[str, bool]]] = {}   # имя → (когда, (ip, online))
```

`get_computer_network_info(name)` сначала смотрит в словарь; если запись моложе 20 с — возвращает без сети.
Зачем: даже с debounce (§2.4) пользователь уточняет запрос — «иванов» → «иванов сергей», ПК одни и те же,
пинговать их заново бессмысленно; при переключении строк инспектор снова спрашивает статус — тоже из кэша.
20 с — компромисс: меньше — толку нет, больше — «В сети» показывает выключенный 5 минут назад ПК.

**Сброс** `clear_network_cache()`: по `Ctrl+P` / кнопке «Пинг» (человек явно хочет свежие данные, вызов с
`use_cache=False`), после массового пинга/WoL (ПК только что разбудили — старый «не в сети» врёт), после
прохода сканера. Доступ к словарю под `threading.Lock`, потому что читают его сразу несколько потоков пула.

### 4.3. pc_history и защита от дублей

Таблица: `id, computer_name, login, ip_address, first_seen, last_seen` — «кто и с каким IP сидел за ПК и когда».
Сканер идёт каждые N минут, наивный `INSERT` дал бы 288 одинаковых строк в сутки на каждый ПК.

Алгоритм `_record_history()` (`db.py`):

1. Одним запросом берём **последнюю** запись по каждому ПК:
   `JOIN (SELECT computer_name, MAX(id) FROM pc_history GROUP BY computer_name)`.
2. Для каждого результата скана сравниваем `(login, ip)` с последней записью:
   - совпало → **продлеваем** `UPDATE pc_history SET last_seen = now WHERE id = ?`;
   - отличается (сел другой человек или сменился IP) → **новая** строка `first_seen = last_seen = now`.
3. Пустой логин и пустой IP (ПК выключен) — пропускаем, чтобы не рвать интервал.

Получается таймлайн: «WS-101: ivanov 01.03–15.06, petrov с 16.06». Оба запроса — `executemany`, одна
транзакция; индексы `ix_pc_history_login/comp` — чтобы «История» по человеку открывалась мгновенно.

### 4.4. paged_search и почему нельзя «одним запросом»

У контроллера домена есть политика `MaxPageSize` (по умолчанию **1000**). Запрос, который вернул бы больше,
не вернёт ошибку — он молча отдаст первую тысячу и `sizeLimitExceeded`. Вы получите «половину отдела» и не
заметите. Кроме того, гигантский ответ — это память и время на контроллере, а он обслуживает логоны всей
организации.

Simple Paged Results (RFC 2696): контроллер отдаёт порцию и **cookie** — «продолжить отсюда»:

```python
cookie = None
while True:
    conn.search(base, flt, SUBTREE, attributes=attrs, paged_size=500, paged_cookie=cookie)
    entries.extend(conn.entries)
    if limit and len(entries) >= limit: return entries[:limit]      # мягкий предел (поиск в GUI — 300 строк)
    cookie = conn.result["controls"]["1.2.840.113556.1.4.319"]["value"]["cookie"]
    if not cookie: break                                             # пустой cookie = страницы кончились
```

`limit` важен для живого поиска: по «а» не нужно выкачивать 15 000 человек, достаточно 300 и флага
`truncated` («показаны первые 300, уточните запрос»). Полная выгрузка без лимита — только в описи и сканере,
где это осознанно. `FakeConn` в тестах эмулирует cookie, поэтому цикл покрыт.

### 4.5. Классификация принтеров

Источник — `Win32_Printer` (порт, имя, `Network`, `Shared`, `Default`). `netutils.classify_printer_port(port)`
возвращает `(kind, ip)`:

| Порт выглядит как | Класс | Пример |
|---|---|---|
| `IP_10.1.2.3`, `10.1.2.3`, `TCPIP_…`, `WSD-…` | `network` (IP извлекаем regex) | сетевой МФУ |
| `\\server\HP_Buh` | `shared` (принт-сервер) | общий принтер отдела |
| `USB001`, `DOT4_001`, `LPT1`, `COM3` | `local` | принтер на столе |
| `PORTPROMPT:`, `nul:`, `SHRFAX:`, `Microsoft.Office.OneNote…` | `virtual` | не принтер |

Отдельно `_VIRTUAL_PRINTER_RE` по **имени**: `Microsoft Print to PDF`, `XPS Document Writer`, `OneNote`,
`Fax`, `Adobe PDF`, `Send to Kindle`. `is_virtual_printer(name, port)` = имя попало в regex ИЛИ порт `virtual`.
Виртуальные не пишутся в `pc_printers`, поэтому не попадают в поиск «у кого принтер» и в бейджи инспектора
(`test_printer_classification` перечисляет эти случаи). IP сетевого принтера сохраняется отдельной колонкой —
по нему работает поиск «10.1.2.3» → «на каких ПК подключён».

---

## 5. Расширяемость и плагины

### 5.1. Загрузка плагинов и жизненный цикл Action

Плагин — файл `Documents\ADK\plugins\*.py` с классом-наследником `Action`:

```python
class Action:
    name = "Действие"; icon = "🔌"; needs_pc = False; modifying = False; order = 100
    def enabled(self, ctx) -> bool: ...   # показывать ли для этого пользователя/ПК
    def run(self, ctx) -> str | None: ... # ctx = {"login", "fio", "comp", "ip", "entry", "admin"}
```

`load_plugins(directory)` при старте:

1. `for path in sorted(glob("*.py"))`, файлы с `_` в начале пропускаются (там лежит пример).
2. `spec = importlib.util.spec_from_file_location(modname, path)`; `mod = module_from_spec(spec)`;
   `spec.loader.exec_module(mod)` — это «импортировать файл по пути», минуя `sys.path`.
3. В модуле ищем классы, унаследованные от `Action` (`inspect.getmembers`), создаём **один экземпляр** каждого,
   сортируем по `order`.

Жизненный цикл: экземпляр живёт весь сеанс → при выборе строки в таблице окно вызывает `act.enabled(ctx)` и
включает/выключает кнопку → по клику `run_plugin(act)`: проверка `access.can("plugin_modifying")` если
`modifying=True`, затем `act.run(ctx)` **в фоне** через `run_in_background`, возвращённая строка показывается
в статусе, факт — в журнал `plugin:<name>`. Плагин не имеет доступа к виджетам — только к словарю `ctx`
и к модулям `adk.db/ad/netutils`, если сам их импортирует.

### 5.2. Изоляция ошибок плагинов

Три рубежа `try/except Exception`:

1. **При загрузке** — синтаксическая ошибка или падение при импорте одного файла логируется
   (`log.exception("plugin %s", path)`) и **пропускается**; остальные плагины и программа стартуют.
   Тест `test_plugins_load_run_and_isolate_broken` кладёт рядом рабочий и сломанный файл и проверяет, что
   загрузился ровно один.
2. **В `enabled(ctx)`** — исключение трактуется как `False` (кнопка просто неактивна).
3. **В `run(ctx)`** — выполняется в `FunctionWorker`, где `run()` уже обёрнут в `try/except` → сигнал `error`
   → `MessageBox` с текстом, главный поток не затронут. Поскольку `run` не в GUI-потоке, даже `time.sleep(60)`
   в плагине не подвесит окно.

Чего плагин сделать всё-таки может — `os._exit()` или бесконечный цикл в фоне; от этого защищает только
здравый смысл автора, и это честно написано в docstring `plugins.py`.

---

## 6. Тестирование и CI/CD

### 6.1. GUI-тесты без дисплея и без AD

**Без дисплея.** Qt рисует через «платформенный плагин». `QT_QPA_PLATFORM=offscreen` (ставится в
`conftest.py` через `os.environ.setdefault`) подключает плагин, который рисует в память, а не в окно.
Виджеты создаются, лейауты считаются, `grab()` возвращает картинку, сигналы/слоты работают — только
пикселей на мониторе нет. Так же снимаются скриншоты для README (`docs/make_screenshots.py`).

**Без AD.** Всё общение с доменом идёт через объект `Connection` с методами `search/modify/add/delete/unbind`.
В тестах вместо `ldap3.Connection` подставляется `FakeConn` (`tests/test_gui.py`):

```python
class FakeConn:
    def __init__(self, entries): self._all = entries; self.result = {"controls": {}}
    def search(self, base, flt, scope, attributes=None, paged_size=None, paged_cookie=None):
        self.entries = [e for e in self._all if _matches(flt, e)]   # упрощённый фильтр по подстроке
        self.result = {"controls": {PAGED_OID: {"value": {"cookie": b""}}}}
    def modify(self, dn, changes): self.modified.append((dn, changes)); return True
```

`monkeypatch.setattr(ADApp, "get_conn", lambda self: FakeConn(entries))` — окно думает, что говорит с AD.
Сеть тоже подменяется: `netutils.get_computer_network_info = lambda n, **kw: ("10.0.0.9", True)`.
Фикстура `temp_db` даёт каждому тесту чистую SQLite во временной папке. `pywin32` на Linux — пустые модули-заглушки
в `sys.modules`.

Ожидание асинхронного результата — `_wait(cond, qapp, timeout)`: крутит `processEvents()` до выполнения
условия, а не `time.sleep`. Всего 132 теста, ~8 с.

### 6.2. Зачем 5 прогонов GUI-набора в CI

```yaml
run: for i in 1 2 3 4 5; do pytest -q tests/test_gui.py tests/test_tools.py || exit 1; done
```

GUI-тесты с потоками — **недетерминированные**: порядок доставки сигналов, момент `deleteLater`, гонка между
`closeEvent` и `finished` воркера зависят от загрузки машины. Такой тест может проходить 9 раз из 10 —
это называется *flaky*. Один прогон в CI такое пропустит; пять подряд с большой вероятностью хотя бы раз
ловят. Категории багов, которые так нашли в этом проекте:

- уничтожение `QThread` до завершения (§2.2) — падал 1 раз из ~7;
- слот, срабатывающий на уже удалённом диалоге (`RuntimeError: wrapped C/C++ object has been deleted`);
- «утечка» состояния между тестами: `access.set_readonly` без `reset()` в `finally` — следующий тест
  случайно шёл в режиме чтения (порядок тестов в pytest фиксирован, но при ошибке один раз меняется картина);
- `database is locked` при двух соединениях к одной SQLite из потока и теста.

Матрица — Python 3.10/3.11/3.12 (разные версии OpenSSL → проверяется и путь с MD4-fallback, и без).
Юнит-тесты быстрые и детерминированные, их гонять 5 раз незачем — поэтому в цикле только `test_gui`/`test_tools`.

### 6.3. Юнит-тесты чистых функций vs E2E, и как подменяется LDAP

**Юнит-тест чистой функции** — вход → выход, никаких окон, сети и баз. Он проверяет один кирпич:

```python
def test_parse_ping_output_windows():
    assert parse_ping_output("Ответ от 10.0.0.9: число байт=32 время<1мс TTL=128") is True

def test_access_two_rights_by_groups(monkeypatch):
    monkeypatch.setattr(config.settings, "pc_admin_groups", ("GT_Admins",))
    assert access.evaluate_rights(["gt_admins"])[:2] == (True, False)
```

Такие тесты — 70 % набора (`test_core.py`, `test_fleet.py`): MD4 по RFC-векторам, разбор ARP/ping/JSON,
классификация принтеров, статус УЗ по `userAccountControl`, `pgadapter`, транслитерация. Они выполняются
за миллисекунды и точно говорят, *что* сломалось.

**E2E-сценарий** (`test_gui.py`, `test_tools.py`) — «как пользователь»: создать `ADApp`, напечатать «иванов»,
дождаться воркера, кликнуть строку, открыть карточку, нажать «Отключить», проверить, что `FakeConn.modified`
получил `userAccountControl=514`, а в `audit_log` появилась запись `disable_user`. Он проверяет, что кирпичи
**соединены** правильно: сигналы подключены, debounce срабатывает, `access` не даёт нажать лишнее.
Таких тестов меньше, они медленнее (0,2–1,5 с) и именно их гоняют 5 раз.

**Подмена LDAP.** Вся прикладная логика получает соединение через фабрику `get_conn()` (окно) или параметр
`conn` (функции `ad.*`). Поэтому подмена — одна строка `monkeypatch`, а `FakeConn` реализует только те четыре
метода, которые реально зовёт код. Если завтра `ad.py` начнёт вызывать `conn.compare()`, тест упадёт с
`AttributeError` и подскажет, что заглушку надо расширить, — это тоже полезный сигнал.

Проектное правило, которое из этого следует: **новую логику писать как чистую функцию, а окно делать тонкой
обёрткой** — тогда 80 % поведения тестируется без Qt вообще.

---

### Если спросят «что бы вы улучшили»

- Полноценная DI-фабрика соединений вместо `get_conn` в окне (проще мокать).
- `pgadapter` покрывает только используемые конструкции SQL — при новых запросах его надо расширять.
- Массовые операции идут последовательно; при 500 учётках стоит батчить через `ThreadPoolExecutor`.
- Роли ADK — UX-слой; аудит действий стоит дублировать в события домена (4738/4740) на стороне SIEM.
