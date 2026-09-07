# 🛠️ Разработка и архитектура

[← README](../README.md)

```bash
pip install -r requirements-dev.txt
pyflakes adk tests
QT_QPA_PLATFORM=offscreen pytest -q      # 213 тестов: MD4 (RFC 1320), БД, разбор ping, статус УЗ, принтеры, инструменты 3.0/3.1, CLI, GUI с заглушкой LDAP
QT_QPA_PLATFORM=offscreen python tests/e2e_scenario.py   # сквозной сценарий, 46 проверок
QT_QPA_PLATFORM=offscreen python tests/e2e_round2.py     # раунд 2: карточка, создание УЗ, сканер, DPAPI, CSV — 82 проверки
QT_QPA_PLATFORM=offscreen python tests/e2e_round3.py     # раунд 3: принтеры, режимы поиска, точность, производительность — 44 проверки
QT_QPA_PLATFORM=offscreen python docs/make_screenshots.py # скриншоты и GIF для README
python docs/make_report.py                                # PDF-отчёт о тестировании → docs/TEST_REPORT.pdf
```

CI: `.github/workflows/tests.yml` — pyflakes, pytest, три E2E и 5-кратный прогон GUI-набора (ловит
крэши времени жизни потоков, которые проявляются не при каждом запуске) на Python 3.11 и 3.12.

### 🧵 Правила работы с потоками (выстраданы багами)

- Ни одного сетевого/LDAP-вызова в GUI-потоке: всё через `run_in_background` или `*Worker(QThread)`.
- Поток создаётся **без Qt-родителя** и живёт в реестре `_LIVE_WORKERS` до сигнала `finished`; удалять его раньше нельзя —
  `isRunning()` становится `False` чуть раньше фактического завершения, и `deleteLater` в этом окне даёт segfault.
- Окно при закрытии **отцепляет** свои слоты (`detach_background_workers`), а не убивает поток.
- Атрибуты `QThread`-наследников не должны называться `start`, `finished`, `result`, `done` — это методы/сигналы Qt.
- Ссылки на завершившиеся потоки обнуляются в обработчике `finished`; обращения к ним обёрнуты в `try/RuntimeError`.

## 🗂️ Структура

```
adk/
  config.py       настройки (config.ini), константы, логирование
  credentials.py  DPAPI / keyring
  ad.py           подключение (LDAPS, таймауты), paged_search, операции с УЗ, MD4-fallback
  md4.py          чистый MD4 для NTLM на OpenSSL 3
  db.py           SQLite: привязки, инвентарь, история, журнал
  netutils.py     ping/DNS (с кэшем), инвентарные CSV, классификация принтеров, команды удалённых действий
  workers.py      QThread-воркеры: поиск, сканер парка, ping, свободный IP, опись
  theme.py        палитра и генерация QSS
  widgets.py      безрамочные окна, TitleBar, MessageBox/InputDialog, BadgeButton/FlowLayout, run_in_background, fit_columns
  dialogs.py      вход, карточка пользователя, регистрация, принтеры парка, журнал, оформление (реэкспорт диалогов ниже)
  freeip_ui.py    3.2.2: «Свободный IP» — карта подсети SubnetMap, вердикт DHCP, история находок
  inventory_ui.py 3.2.2: «Excel-опись» — организации, предпросмотр, выбор колонок
  pingui.py       3.2.1–3.2.3: окно пинга — график отклика, статистика, единая лента журнала с фильтром
  colorpicker.py  3.2.3: собственный выбор цвета (квадрат S×V, полоса оттенка, HEX/RGB, пресеты)
  dhcp.py         3.2.1: чтение областей/аренд DHCP и классификация адреса (free/lease/reserved/excluded/outside)
  tools.py        инструменты 3.0: массовые операции, заметки, «группы как у…», история
  fleet.py        3.1: массовый пинг + WoL, установленное ПО, входы за 24 ч, сравнение двух ПК (диалоги)
  nettools.py     3.1: magic-пакет, разбор arp -a, параллельный пинг, msg
  software.py     3.1: опрос ПО/hotfix через PowerShell, кэш pc_software, поиск по парку
  logons.py       3.1: события 4624/4625, фильтр шума, расшифровка отказов
  attention.py    3.1: сводка «Внимание» (чистая логика) · attention_ui.py — диалог
  templates.py    3.1: шаблоны регистрации (templates.json, группы, OU)
  notify.py       3.1: почтовые уведомления из журнала (хук db.AUDIT_HOOKS)
  extras.py       настройки почтовых уведомлений, текст карточки с паролем
  cli.py          3.1: --find/--export-inventory/--attention/--ping/--wol/--scan/--serve
  pgadapter.py    3.1: PostgreSQL поверх psycopg — транслятор SQLite-диалекта
  access.py       роль «только чтение» (config + группы AD)
  plugins.py      загрузка *.py-плагинов с классом Action
  health.py       PowerShell/CIM-опрос ПК, разбор S.M.A.R.T., карта диска (squarify), журнал событий Windows, пороги
  health_ui.py    окно «Здоровье ПК»: обзор, диски, treemap, вкладка «Ошибки»
  export.py       XLSX/CSV экспорт результатов
  updates.py      проверка version-файла на сетевом диске
  tray.py         иконка трея, глобальная горячая клавиша (RegisterHotKey)
  i18n.py         словарь ru→en и tr()
  main_window.py  дашборд, единый поиск, таблица, инспектор
run.py            точка входа без установки (PyCharm / PyInstaller)
adk.spec   сборка exe; build_exe.bat / build_exe.ps1 — «одной кнопкой»
assets/           иконка приложения
.run/             конфигурации запуска PyCharm
tests/            pytest (offscreen Qt, FakeConn вместо ldap3) — файлы по областям, не по версиям:
                  test_core (ядро, БД) · test_gui (главное окно и диалоги) · test_accounts (учётки, поиск, карточка)
                  test_ping · test_health · test_network · test_fleet (парк, CLI) · test_tools · test_ui · test_learning
```

## Отчёт о тестировании

`docs/TEST_REPORT.pdf` собирает `docs/make_report.py` из логов `docs/test-logs/`. Лог pytest нужен **с `-v`**
(`pytest -v tests > docs/test-logs/pytest.txt`) — иначе таблица «по файлам» пуста; скрипт это проверяет и
отказывается собирать отчёт, если число тестов по файлам не сходится с итогом.
