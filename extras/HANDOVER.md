# ADK — Active Directory Kit · руководство для продолжения работы в новом чате

Актуально на 2026-09-09. Версия проекта **3.5.1**, тестов **234**, e2e 46 + 82 + 45.
Внутренний документ: лежит в `extras/`, не входит в zip и не упоминается в README/CHANGELOG.
Прочитать целиком до первой правки — здесь всё от А до Я, включая производство видео и чистку истории git.

---

## А. Первые 10 минут в новом чате

Песочница между сессиями сбрасывается (пакеты, apt, процессы), а воркплейс `/home/user` может отставать от GitHub.
Поэтому строго по порядку:

```bash
cd /home/user
# 1. Синхронизация с GitHub (GitHub — источник истины, воркплейс может быть старее)
git fetch -q origin main && git status -sb
git reset -q --hard FETCH_HEAD            # только если нет своих незакоммиченных правок!
# 2. Зависимости
pip install -q -r requirements.txt -r requirements-dev.txt reportlab imageio-ffmpeg
sudo -n apt-get install -y -qq libxkbcommon0 libxkbcommon-x11-0 libegl1 libgl1 libglib2.0-0 libfontconfig1 libdbus-1-3 fonts-dejavu-core
# 3. Проверка, что всё живое
python -m pyflakes adk tests docs                         # пусто
QT_QPA_PLATFORM=offscreen python -m pytest -q             # 234 passed
```

Если `.git` отсутствует (песочница пересоздана):
```bash
cd /home/user && git init -q -b main && git remote add origin https://github.com/AgentSharik/ADK.git
git fetch -q origin main && git reset -q --hard FETCH_HEAD
```

Токен GitHub (fine-grained, Contents + Workflows на репозиторий ADK) есть у автора — спросить в начале чата, если
нужен push. Токен передавать **только в URL команды push**, в `.git/config` не сохранять, в выводе маскировать.

Затем прочитать верхний блок `CHANGELOG.md` и `README.md`.

---

## Б. Что это за проект и как о нём говорить

**ADK (Active Directory Kit)** — наша настольная утилита администратора домена: PyQt6 + ldap3 + pywin32 (Windows),
SQLite для 1–3 админов или PostgreSQL (`adk --serve`). Начиналась как однофайловый монолит автора; после аудита —
пакет `adk/`, тесты, документация, отчёт. Позиционируется как портфолио-проект.

Правила речи (требования автора, не обсуждаются):
- Проект — «мой/наш». Версия коллеги (модульная) — «его»; с ней **не сливаемся**, ничего не передаём, тему не поднимаем.
- Продукт везде — **ADK**. О прежнем названии — только слово «ребрендинг», само имя не писать нигде.
- Средство удалённого доступа — только **RMS**. Другие названия не писать никогда.
- Никаких реальных доменов, UNC, ФИО, IP организации: только `example.local`, `CORP\admin`, `WS-101`, `10.0.2.x`.
- **Нигде** (код, комментарии, CHANGELOG, README, docs, титры) не упоминать, чей визуальный стиль взят за образец: ни
  компаний, ни наборов иконок, ни «в духе …». Писать сухо: «контурные иконки», «объёмные кнопки», «исправления интерфейса».
- В CHANGELOG запись 3.4.0 — «исправления интерфейса», **не** «добавлены 10 тем». Не возвращать.
- Источник ТЗ по дизайну (промт) не упоминать.
- Весь промежуточный текст, комментарии, docstring, сводки — **по-русски**. Автор — новичок в тестах и не сетевой
  эксперт: объяснять без жаргона, на примерах его кода; DHCP/PTR/WoL — простыми словами.
- Замечания по производству видео (что и как исправлялось в роликах) в сводках не упоминать.

Чего делать **нельзя**: «Сессии на ПК» (query user / msg / logoff) — зарезервировано за автором; Telegram,
автообновление (только баннер проверки версии), QR-коды, «временный пароль», строка «Был в сети», слово «кэш» в
интерфейсе, пункты про истечение пароля в «Внимание», кнопка «Выход» в шапке, тёмно-синие/navy/cyan темы и чистый чёрный.

---

## В. Структура репозитория

Репозиторий **https://github.com/AgentSharik/ADK** = воркплейс `/home/user/` целиком, ветка `main`.
Проект — в корне репозитория; **всё, что не проект, — в `extras/`**.

```
/home/user/
├── adk/                 пакет (см. раздел Г)
├── tests/               234 теста (conftest.py, test_*.py) + e2e_scenario.py, e2e_round2.py, e2e_round3.py
├── docs/                FEATURES.md, INSTALL.md, DEVELOPMENT.md, SCREENSHOTS.md, TEST_REPORT.pdf,
│                        make_screenshots.py, make_report.py, *.png, demo_*.gif, test-logs/
├── assets/              иконка приложения и логотип (главную иконку не трогать)
├── README.md CHANGELOG.md run.py requirements*.txt pyproject.toml adk.spec build_exe.* config.example.ini version_info.txt
├── .github/workflows/   tests.yml, build-exe.yml (exe собирает CI, в песочнице нельзя)
├── .run/                конфигурации запуска IDE
└── extras/              НЕ проект: не в zip, не в README/CHANGELOG
    ├── HANDOVER.md      этот файл
    ├── QA_ARCHITECTURE.md   приватная шпаргалка по архитектуре тестирования
    ├── adk.zip          поставка (только проект, см. раздел И)
    ├── videos/          РОВНО ОДИН ролик: ADK_demo_23.mp4
    ├── demo/            РОВНО ОДИН сценарий make_demo23.py + make_music.py
    └── data/ADK/        рабочие config.ini, pc_mapping.db, adk.log автора
```

`/home/user/uploads/` — скриншоты автора, в git не добавлять (untracked, не коммитить).
`.gitignore` исключает кэши, `extras/demo/frames*/`.

---

## Г. Карта кода (где что лежит)

| Модуль | Что внутри |
|---|---|
| `adk/__main__.py` | вход: LoginDialog → `ADApp`; справку по роли **не** показывает (это делает главное окно) |
| `adk/main_window.py` | `ADApp`: дашборд, поиск, таблица (`COLUMNS`, `DEFAULT_VISIBLE`, `COL_PC`, `PRINTER_CONN`), инспектор, `resolve_access` → `apply_access` → `show_role_welcome`, плагины (`_add_plugin_buttons`, `reload_plugins`, `run_plugin`), `StatusItem`, `BadgeDelegate` |
| `adk/dialogs.py` | `LoginDialog`, `RoleWelcomeDialog` (справка по роли, `hide_role_welcome`), `RoleInfoDialog`, `PluginsDialog` (менеджер плагинов), `UserCardDialog`, `ResetPasswordDialog`, `RegisterUserDialog`, `PrintersDialog`, `AuditLogDialog`, `DesignSettingsDialog` (`Swatch`, `ThemeTile`), реэкспорт `FreeIPDialog`, `InventoryDialog`, `PingDialog` |
| `adk/plugins.py` | `Action` (name/icon/needs_pc/modifying/order, `enabled`, `run`), `load_plugins`, `run_action`, `TEMPLATE` + `write_template` (самодокументированный шаблон), `list_plugin_files`, `set_enabled` (переименование `_`), `write_example` |
| `adk/access.py` | роли по группам AD: `policy_configured`, `resolve`, `set_rights`, `reset`, `can*`, `role_summary` |
| `adk/theme.py` | `Palette`, `PRESET_THEMES` (10 тем), `theme_design`, `relief()`, `build_stylesheet` (в т.ч. разделители выделенных строк `sel_line`) |
| `adk/icons.py` | контурные SVG-иконки: `PATHS` (в т.ч. `printer`, `printer.network/usb/shared`, `puzzlepiece`), `install()` (эмодзи → иконка автоматически), `icon(name, role=)`, `refresh()`, `strip()` |
| `adk/widgets.py` | `FramelessDialog`, `MessageBox`, `BadgeButton`, `FlowLayout`, `DrivePicker`, `apply_theme`, `app_palette`, `run_in_background`, `retheme` |
| `adk/health_ui.py` / `health.py` | «Здоровье ПК»: Обзор, S.M.A.R.T., Карта диска (`Treemap`, `show_usage`, `show_hogs` — подпись «На компьютере …, диск X:»), Ошибки, Диагностика (заглушка) |
| `adk/freeip_ui.py` / `dhcp.py` / `workers.py` | «Свободный IP»: `SubnetMap`, карточка результата, `FreeIPWorker._reason/verdict` |
| `adk/pingui.py` | окно «Пинг» (`PingWorker`, фильтры `filter_btns`, `toggle_pause`, `copy_report`) — **ожидается полная переработка, автор ещё не описал** |
| `adk/fleet.py`, `tools.py` | ПО, сравнение ПК, массовый пинг; группы как у…, заметки, история, массовые операции |
| `adk/inventory_ui.py`, `attention_ui.py`, `colorpicker.py` | опись Excel, «Внимание», свой выбор цвета |
| `adk/netutils.py`, `ad.py`, `db.py`, `config.py`, `credentials.py`, `software.py`, `templates.py`, `export.py`, `updates.py`, `i18n.py`, `pgadapter.py`, `md4.py` | сеть/принтеры (`classify_printer_port`), LDAP, БД, настройки (`hide_role_welcome`, `plugins_dir`), хранилище паролей, ПО, шаблоны пользователей и пр. |

Слои: `config/theme/md4/plugins` внизу → `db/ad/netutils` → `widgets` → диалоги → `main_window`. Импорты только вниз.

---

## Д. Как выглядит и ведёт себя интерфейс (накопленные требования автора)

- **Шапка**: эмблема + жирное «ADK»; без «Выход»; окно разворачивается/полноэкранно; логотип прозрачный. Кнопки шапки: «Плагины» (контурная иконка пазла), «Дизайн».
- **Поиск**: по ФИО/логину/почте/отделу/кабинету/имени ПК/IP/принтеру, **без телефонов**; живой, неблокирующий. Простой IP принтера → в таблице только принтер.
- **Таблица**: столбцы по умолчанию — Логин, ФИО, Учётка, Имя ПК, Сеть, Телефон, IP-тел; меню столбцов — простой чек-лист. ФИО — только имена («Фамилия И.О.», в инспекторе полностью), никогда логин. Учётка — ровно два состояния «Активна / Не активна» (пилюли). Ширина по тексту, сетка, разделители видны и у выделенных строк (в т.ч. при мультивыборе — `sel_line` темнее подсветки), акцентная полоска слева у выбранной строки, заметные скроллбары. Заполнять при выключенной сортировке.
- **Строка принтера** (поиск по IP): «Логин» = «—» без иконки; заголовок «Имя ПК» переключается на **«Подключение»** (иконка + «сетевой / общий (через сервер) / USB / локальный» из `classify_printer_port`); столбец существует только в этом режиме, вручную не включается. Тип не угадывать — если порт не распознан, честное «локальный».
- **Инспектор**: полное ФИО, ПК, IP, состояние сети; «Пинг»; блок «Действия с ПК»: RMS, Диск (C$ сразу или меню томов), Управление ПК, Питание ПК (меню: WoL, блокировка экрана, выход, сон, перезагрузка, выключение), Здоровье ПК, Установленное ПО, Входы за 24 ч, далее кнопки плагинов; принтеры ПК — одна иконка (вид подключения), «· по умолчанию» словами; живой опрос принтеров БД не меняет. Нет «Был в сети». Инспектор принтера: кто подключён, «Пинг», «Веб-панель».
- **Карточка пользователя**: шапка с ФИО и бейджем, кнопки Копировать/Заметки/История/Группы как у…/Смена пароля/Снять блокировку/Отключить-Включить; вкладки Профиль, Группы, Учётная запись, Характеристики ПК. Без дублей быстрых действий инспектора. Снятие блокировки → вопрос «задать новый пароль?».
- **Роли** (`[Access]`): право «ПК» и право «AD». Роль «ПК» — всё про ПК работает, изменения AD скрыты (пароль, блокировка, отключение, «Сохранить», группы ±, новый пользователь, массовые операции; поля readonly). Без политики групп — полный доступ. Бейдж роли в статус-баре → `RoleInfoDialog`. **Справка по роли** — модальное окно `RoleWelcomeDialog`, которое открывает главное окно **после определения роли** (`resolve_access` → `show_role_welcome`), один раз за сеанс; галочка «Больше не показывать при входе» → `UI.hide_role_welcome`. Не статический блок в окне входа.
- **Плагины**: `*.py` в `[Plugins] dir` с классами `Action`; файлы с `_` выключены. Менеджер: таблица (файл · кнопки · права · состояние), Включить/Выключить (переименование), Открыть файл, Удалить, Папка плагинов, Перечитать (без перезапуска: `ADApp.reload_plugins`), **«Создать шаблон плагина»** → `_template_plugin.py` со всей документацией внутри (атрибуты, методы, `ctx`, что импортировать, примеры). Повтор не затирает (`_template_plugin_2.py`).
- **Пароли**: одно окно «Смена пароля», без QR/«временного». **Внимание** без пунктов об истечении пароля. **Дашборд** честный: цифры сходятся с поиском.
- **Здоровье ПК**: S.M.A.R.T. как в утилитах диагностики дисков; карта диска — только ручной обход, объёмные плитки без наложений, таблицы Папки/Файлы/Почистить **справа**; «Почистить» — подмножество тех же данных; подпись называет компьютер и букву тома, ничего не выдумывать про Корзину на несистемном диске; «Диагностика» — заглушка.
- **Свободный IP**: сверка с DHCP; цвет ячейки = статус; ячейки вмещают 3 цифры; легенда честная (без «в инвентаре ADK»); карточка результата растянута, содержимое по центру, примечание DHCP в 2 строки внутри карточки.
- **Сравнение ПК / ПО**: живой опрос, сохранённое — только если ПК недоступен, с датой.
- **Оформление**: ровно 10 тем — тёмные `dark` Графит (по умолчанию), `emerald` Тёмная мята, `plum` Тёмная слива, `zinc` Тёплый графит, `amethyst` Тёмный кварц; светлые `light` Светлая, `sand` Персик, `sage` Мята, `frost` Лаванда, `quartz` Индиго. Заливки непрозрачные; кнопки/панели объёмные (`relief`); иконки 22 px в кнопках, 19 px в подписях, контрастные на заливных кнопках; ничего не сливается с фоном. Свой выбор цвета («было» = «стало»). Никаких двойных иконок рядом (эмодзи + иконка, ★ + иконка), если это не две разные функции.
- **Пинг**: индикатор, живой график, статистика, лента (по умолчанию только события); офлайн-ПК не отвечает (согласовано с «Сеть»). Полная переработка — ждёт описания автора.
- **БД/CSV** ничем не портить.

---

## Е. Порядок работы над партией правок

1. **Понять запрос** — по пунктам автора; проверить, не сделано ли уже (grep по коду, offscreen-скриншот).
2. **Правки кода.** Патчить Python-скриптом с `assert a in s` перед `replace`; никогда не `open(p,'w').write(open(p).read()…)` одной строкой. Не переписывать методы «по памяти» — копировать оригинал.
3. **Тесты на каждую функцию/исправление** (раздел Ж).
4. **Чек-лист** (раздел З) → версия/CHANGELOG/README/docs → скриншоты → zip → коммит + push (раздел И).
5. **Видео**, если автор просит или партия заметная (раздел К) → ротация и чистка истории (раздел Л).
6. **Итог автору** — компактная русская сводка: что сделано по его пунктам, причины, цифры проверок, пути, что осталось.

---

## Ж. Тесты

Инфраструктура: `tests/conftest.py` (offscreen, подмена pywin32, временная БД, `hide_role_welcome=True` — окно роли в
тестах не всплывает; фикстура `qapp` вызывает `icons.install()`), `tests/test_gui.py` (`FakeConn`, `FakeEntry`,
`ENTRIES` — ivanov активный/WS-101 в сети, petrov отключён; `fake_conn`, `_main(qapp, fake_conn, monkeypatch)`, `_wait(cond, app, ms)`).

Куда класть: `test_core` (логика, БД), `test_gui` (главное окно, инспектор, принтеры), `test_accounts` (карточка, роли, вход),
`test_fleet`, `test_health`, `test_ping`, `test_network` (свободный IP, DHCP), `test_tools` (плагины, экспорт, доступ),
`test_ui` (геометрия, темы, диалоги, менеджер плагинов, окно роли), `test_audit_341`, `test_learning` (учебные для автора).

Стиль: docstring по-русски «что и почему» («3.5.1: раньше … → теперь …»); всё внешнее через `monkeypatch`
(`subprocess.Popen`, `MessageBox.question`, сеть); роли — `access.set_rights(...)` и `access.reset()` в `finally`;
экран 800×600; после `show()` — `processEvents()`; при смене поведения обновлять тест, не удалять. Иконки: текст
сравнивать через `icons.strip()` или `btn._adk_icon`; ячейки с иконкой — `item.icon().isNull()`.

e2e: `for f in e2e_scenario e2e_round2 e2e_round3; do PYTHONPATH=/home/user QT_QPA_PLATFORM=offscreen python tests/$f.py 2>&1 | grep -v propagate | tail -n 1; done`
→ `46/46`, `82/82`, `45/45`.

---

## З. Чек-лист перед сдачей

```bash
cd /home/user
python -m pyflakes adk tests docs                                          # пусто
QT_QPA_PLATFORM=offscreen python -m pytest -v tests > docs/test-logs/pytest.txt; tail -1 docs/test-logs/pytest.txt
for f in e2e_scenario e2e_round2 e2e_round3; do PYTHONPATH=/home/user QT_QPA_PLATFORM=offscreen python tests/$f.py 2>&1 | grep -v propagate | tail -n 1; done
QT_QPA_PLATFORM=offscreen python docs/make_screenshots.py                  # PNG + GIF в docs/ (тема dark)
python docs/make_report.py                                                 # docs/TEST_REPORT.pdf
grep -rn "AnyDesk\|ADManager\|AD Manager" adk docs README.md CHANGELOG.md tests extras/HANDOVER.md   # пусто
```
Плюс визуальный смоук изменённых окон: offscreen-скрипт с `apply_theme({**config.settings.design, **theme_design(PRESET_THEMES["emerald"])})`
(и одна светлая, напр. `quartz`), `widget.grab().save(...)`, посмотреть картинку (наезды, обрезанный текст, двойные иконки).
Без `apply_theme` окна рендерятся «голыми».

Версия и документы:
- Версию менять в **четырёх** местах: `adk/__init__.py`, `pyproject.toml`, `version_info.txt` (строки и кортежи), плюс `README.md` («📜 История», число тестов) и `docs/DEVELOPMENT.md` (число тестов).
- `CHANGELOG.md`: блок сверху `## X.Y.Z — заголовок`, подразделы **✨ Новое / 🐞 Исправлено / 🧪 Тесты**; у исправлений — причина.
- README короткий (~75 строк), подробности в `docs/*.md`; новое окно → кадр в `docs/make_screenshots.py` + строка в `docs/SCREENSHOTS.md`.
- Видео, zip, HANDOVER в README/CHANGELOG не упоминать.

---

## И. Поставка (zip), коммит, push

```bash
cd /home/user && rm -f extras/adk.zip && zip -qr extras/adk.zip adk assets docs tests .github .run CHANGELOG.md README.md adk.spec build_exe.bat build_exe.ps1 config.example.ini pyproject.toml requirements.txt requirements-dev.txt run.py version_info.txt -x "*/__pycache__/*" "*.pyc" "*/.pytest_cache/*"
unzip -l extras/adk.zip | grep -c "extras/\|\.mp4\|make_demo\|HANDOVER\|QA_ARCH"     # 0
```

Коммит после **каждой** завершённой партии (GitHub — зеркало воркплейса, удаления тоже коммитить):
```bash
cd /home/user && find . -name __pycache__ -type d -not -path "./.git/*" -exec rm -rf {} + 2>/dev/null; rm -rf .pytest_cache
git add -A && git -c user.name="AgentSharik" -c user.email="agentsharik@users.noreply.github.com" commit -q -m "X.Y.Z: что сделано, по-русски"
git push -q "https://x-access-token:${T}@github.com/AgentSharik/ADK.git" main
curl -s https://api.github.com/repos/AgentSharik/ADK/commits/main | grep -m1 '"sha"'        # сверить с git rev-parse HEAD
```
Если push отклонён из-за workflow-файлов — у токена нет права Workflows: попросить автора.

---

## К. Видео-демонстрация — полный рецепт

**Что должно получиться.** `extras/videos/ADK_demo_NN.mp4`, 1400×820, 25 fps, H.264 + AAC (фоновая музыка из
`make_music.py`). Выглядит как реальная запись экрана: курсор, набор текста, нажатия, живые графики; **без склеек,
кроссфейдов, повторов, без подписей внизу кадра** — только демонстрация. Титры: в начале и в конце **одна и та же
версия** (`VERSION` в скрипте = `adk.__version__`). **Новый ролик никогда не короче предыдущего** (23-й — см. длительность
в разделе М; сцены только добавляются, не удаляются).

**Обязательные сцены** (все есть в `make_demo23.py`, в этом порядке): вход (показать/скрыть пароль, «запомнить»);
дашборд → окно «Роль и права доступа» (роль AD) → бейдж роли; менеджер плагинов (создать шаблон → включить → кнопка
плагина в инспекторе); поиск, меню столбцов, инспектор; фильтры (отдел, мультивыбор с разделителями, отключённые,
организация, архивы); **пинг офлайн-ПК (WS-133), затем онлайн (WS-101)** с фильтрами, паузой, копированием; принтеры
(бейджи, живой опрос, поиск по IP → столбец «Подключение», инспектор принтера, принтеры парка); Диск (меню томов), Питание
ПК, Здоровье (все 5 вкладок, карта C: и D:, «Почистить», ошибки); карточка AD (пароль, снятие блокировки, отключение/
включение, группы, характеристики); группы как у…, заметки, история; массовые операции, массовый пинг; ПО, сравнение
ПК; Excel-опись; свободный IP («Следующий»); новый пользователь, «Внимание», журнал; **роль «ПК»** (окно роли, стартовый
экран, RoleInfoDialog, инспектор, карточка только для чтения); оформление (все 10 тем, свой акцент).

**Как устроен сценарий** (`extras/demo/make_demoNN.py`, ~1050 строк):
- Шапка: пути `OUT_DIR = extras/demo/framesNN`, `OUT_MP4 = extras/videos/ADK_demo_NN.mp4`, `W,H = 1400,820`, `FPS = 25`.
- Демо-данные: 5 основных сотрудников + 115 сгенерированных, `DemoConn` (заглушка LDAP с поиском по подстроке и группам),
  инвентарь, принтеры (`PRINTERS` → CSV в `invent_hardware_dir`), ПО, заметки, история, `FakePingWorker` (WS-133 всегда
  таймаут, остальные отвечают), заглушки DHCP/`is_host_alive`/`gethostbyaddr`, JSON здоровья/карты диска/событий.
- «Камера»: кадры пишутся сразу в ffmpeg через pipe (`_encoder`, rawvideo → libx264), без PNG на диске.
  Помощники: `snap(S, n)` (n кадров), `sec(s)`, `hold(S, сек)`, `move_to(S, widget)`, `press(S, button)`,
  `click_fx`, `type_text(S, line_edit, text)`, `show_dialog(S, dlg)` / `hide_dialog(S, dlg)`, `live(S, сек)` (реальное
  время — для пинга), `title_card(TITLE)`. `S` — список «слоёв» [главное окно, диалоги…]; диалоги рисуются по центру не
  выше y=64 (`dlg_origin`), можно задать `dlg._demo_pos`. `draw_caption` намеренно ничего не рисует.
- Финал: `w.quit_app()`, закрыть pipe, `make_music.py <сек> music.wav`, ffmpeg мультиплексирует, `framesNN` удаляется.

**Как сделать следующий ролик (NN+1):**
1. `cp extras/demo/make_demo23.py extras/demo/make_demo24.py`; заменить в нём `frames23`→`frames24`, `ADK_demo_23`→`ADK_demo_24`,
   `VERSION`. Добавить новые сцены (по образцу существующих), старые не удалять.
2. `python -m pyflakes extras/demo/make_demo24.py`.
3. Быстрый прогон на ошибки (1–3 минуты): временная копия с `def sec(s): return 1`, `wait(min(ms,60))`, `live` ≤ 1 с и
   путями в `/tmp` — сценарий должен дойти до конца (падение на `make_music.py` из-за пути в копии — нормально).
4. Реальный рендер (10–15 минут, через `start_process`, а не bash):
   `cd /home/user && rm -rf extras/demo/frames24 && QT_QPA_PLATFORM=offscreen timeout 1750 python extras/demo/make_demo24.py`
5. Проверка: `FF=$(python -c "import imageio_ffmpeg,os;print(imageio_ffmpeg.get_ffmpeg_exe())"); $FF -i extras/videos/ADK_demo_24.mp4 2>&1 | grep -E "Duration|Stream"`
   — есть Video и Audio, длительность ≥ предыдущей. Контактный лист кадров (ffmpeg `-vf fps=1/20` → PIL) — посмотреть глазами.
6. Удалить `extras/demo/make_demo23.py` и `extras/videos/ADK_demo_23.mp4`, обновить этот файл (разделы В, К, М).
7. Коммит + push, затем чистка истории (раздел Л).

Грабли: ffmpeg берётся из `imageio_ffmpeg` (apt-версии может не быть); `ad.reset_password` требует `conn.extend.microsoft`
(`_Ext`); диалоги создавать с `parent=w`, чтобы тема применилась; `PluginsDialog.reload()` дергает `w.reload_plugins()` —
папку плагинов подменять `config.settings.plugins_dir` на временную; подпись пути в менеджере на видео заменяется на
Windows-путь (`plg.lbl_dir.setText(...)`) после каждого `reload()`.

---

## Л. Ротация тяжёлых файлов и чистка истории git («удалять с корнями»)

В репозитории хранятся только актуальные `extras/adk.zip`, `extras/videos/ADK_demo_NN.mp4`, `extras/demo/make_demoNN.py`.
Старые ролики/скрипты/zip после ротации должны исчезнуть **и из истории**, иначе клон весит сотни мегабайт.

```bash
cd /home/user && pip install -q git-filter-repo
# что тяжёлого есть в истории
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob" && $3>1000000' | sort -k3 -n -r | head
# 1) спрятать актуальные файлы (они тоже попадут под фильтр)
mkdir -p /tmp/keep && cp extras/videos/ADK_demo_NN.mp4 extras/demo/make_demoNN.py extras/adk.zip /tmp/keep/
# 2) вычистить ВСЕ ролики, сценарии и zip из всей истории (все ветки/теги)
git filter-repo --force --invert-paths \
  --path-glob 'extras/videos/*.mp4' --path-glob 'extras/demo/make_demo*.py' --path extras/adk.zip \
  --path-glob 'videos/*' --path-glob 'demo/*' --path adk.zip
# 3) вернуть актуальные файлы одним коммитом
cp /tmp/keep/ADK_demo_NN.mp4 extras/videos/ && cp /tmp/keep/make_demoNN.py extras/demo/ && cp /tmp/keep/adk.zip extras/
git add -A && git commit -q -m "extras: актуальные ролик NN, сценарий и zip после чистки истории"
# 4) добить мусор и проверить размер
git reflog expire --expire=now --all && git gc --prune=now --aggressive -q && du -sh .git
git rev-list --objects --all | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' | awk '$1=="blob" && $3>1000000'
```
После шага 4 в истории должен остаться **ровно один** блоб `.mp4` (актуальный) и один `adk.zip`. Проверка на GitHub:
`git clone --depth=1000 https://github.com/AgentSharik/ADK.git /tmp/chk && du -sh /tmp/chk/.git` — десятки мегабайт, не сотни.
Именно так делалось в 3.5.1 (история переписана, `git push --force`; клон стал ~54 МБ вместо ~140).
Грабли: в песочнице файл `.git/config` не сохраняется между сессиями — `git filter-repo` падает с
`ValueError: dictionary update sequence element #0 has length 1`. Лечение: перед запуском создать `.git/config`
(секции `[core]`, `[remote "origin"] url = https://github.com/AgentSharik/ADK.git`, `[branch "main"]`).

После filter-repo remote удаляется — восстановить и запушить принудительно:
```bash
git remote add origin https://github.com/AgentSharik/ADK.git
git reflog expire --expire=now --all && git gc --prune=now --aggressive
git push -q --force "https://x-access-token:${T}@github.com/AgentSharik/ADK.git" main
du -sh .git      # для контроля; свежий клон должен весить ~ размер актуального ролика + zip + docs
```
На стороне GitHub старые объекты уходят из клона сразу; из внутреннего хранилища — после их сборки мусора (это нормально).

---

## М. История версий (что уже сделано — не переделывать)

2.0 — переработка после аудита (модули, LDAPS, тесты). 2.1–2.2 — сброс пароля, журнал, принтеры, точный поиск.
3.0 — ребрендинг в ADK, набор инструментов. 3.1 — инструменты парка, CLI, PostgreSQL. 3.2 — S.M.A.R.T., карта диска.
3.2.1 — новое окно пинга, журнал ошибок, DHCP, живой опрос принтеров. 3.2.2 — карта подсети, опись с предпросмотром.
3.2.3 — единая лента пинга, свой выбор цвета, читаемые таблицы. 3.2.4 — честный дашборд, принтер по IP. 3.2.5 — новая
карточка, два состояния учётки, объёмная карта диска. 3.2.6 — честный пинг офлайн-ПК. 3.2.7 — выбор тома для карты.
3.2.8 — живое ПО в сравнении ПК, таблицы карты справа. 3.2.9 — новое окно входа, «Диск» с выбором тома, честная
«Почистить», пароль после разблокировки. 3.3.0 — меню «Питание ПК», роль «ПК» в карточке только просмотр, вкладка
«Диагностика», компактный «Новый пользователь». 3.4.0 — 10 тем и контурные иконки (в CHANGELOG — «исправления
интерфейса»). 3.4.1 — аудит логики/наложений (`tests/test_audit_341.py`). 3.4.2 — пересборка тем/кнопок/иконок,
честная легенда карты подсети. 3.4.3 — объёмные элементы (`theme.relief()`), вкладки-сегмент. 3.4.4 — контрастные
иконки на заливных кнопках. 3.4.5 — роль в статус-баре (`RoleInfoDialog`), кнопка «Плагины» вместо «Экспорт», карта
диска без вкладки «Профили». 3.5.0 — `RoleWelcomeDialog`, `PluginsDialog`, сетка таблиц, `_item_apply` без двойных
иконок, центрирование карточки свободного IP.
**3.5.1** (текущая) — справка по роли после определения прав (главное окно, один раз, честная роль); менеджер плагинов
(таблица, включение переименованием, «Создать шаблон плагина» с полной документацией внутри файла, перечитывание без
перезапуска); столбец «Подключение» для принтеров с иконкой (заголовок переключается только в режиме принтера); новые
иконки принтера; одна иконка у бейджа принтера («по умолчанию» словами); подпись «Почистить» с именем ПК и буквой тома
(исправлен `self.comp`); разделители внутри мультивыделения; растянутая карточка свободного IP; окно роли без обрезанного
текста. Тестов 234. Видео — `extras/videos/ADK_demo_23.mp4` (сценарий `extras/demo/make_demo23.py`, без подписей,
одна версия в титрах, длительность — в заметке ниже).

Длительность роликов (для правила «не короче предыдущего»): 21 — 8:51 (531 с); **23 — 9:22 (562 с)** (файл ~19 МБ).
Темп статичных пауз задаётся константой `HOLD_SCALE` в `make_demo23.py` (сейчас 1.25) — если новый ролик выходит короче,
проще всего поднять её, а не резать сцены; живые сегменты пинга — `live(S, 14.0)` / `live(S, 12.0)`.

Открытые задачи: **полная переработка окна «Пинг»** (автор ещё не описал желаемое); при следующем видео — номер 24.

---

## Н. Технические грабли

- Патчить файлы только скриптом с `assert a in s`; после — `grep`. Не переписывать методы по памяти.
- `pytest -q` для лога отчёта не годится — `make_report.py` читает `-v`.
- Offscreen: без `apply_theme` окна без стилей; экран 800×600; QTableWidget заполнять при выключенной сортировке;
  `WA_TranslucentBackground` в QScrollArea даёт мусор; при очистке QLayout через `takeAt` stretch-элементы дают `widget() is None`.
- Иконки: путь SVG с `var(--bg)` заливается цветом иконки (не «вырез») — для полых частей рисовать контур; кнопка с
  эмодзи в конце текста иконку не получит — эмодзи в начало; в ячейках таблиц эмодзи не писать (ставить `setIcon`).
- `app_palette()` возвращает объект `Palette` (атрибуты `text`, `subtext`, `title_accent`, `danger`, `success`, `selection`…).
- `FreeIPDialog._set_checks(items)` принимает список `(text, kind)`; `health_ui.HealthDialog.comp` — имя ПК (не `computer`).
- `RoleWelcomeDialog` в приложении открывается через `dlg.open()` (модально к окну, не блокируя цикл событий); в тестах
  `hide_role_welcome=True` по умолчанию (conftest), тест включает показ явно.
- Пути Windows в JSON здоровья — через `ntpath`. `LIKE … ESCAPE '\\'` в обычной строке Python. reportlab `Paragraph`
  не понимает `<wbr/>`. pyflakes игнорирует `# noqa: F401` — использовать `__all__`.
- `git show <commit>:extras/demo/make_demoXX.py` для вычищенных файлов не работает — только по хэшу blob через `git cat-file -p`.
- Реальный `.exe` в песочнице не собрать (это делает CI).
