# 📦 Установка, сборка exe, серверный режим

[← README](../README.md)

### Вариант 1 — из исходников (PyCharm / консоль)

```bash
git clone https://github.com/AgentSharik/ADK.git && cd ADK
python -m venv .venv && .venv\Scripts\activate      # Windows
pip install -r requirements.txt
python run.py                                        # или: python -m adk
```

**PyCharm:** File → Open → папка `adk`; интерпретатор — `.venv` (PyCharm сам предложит поставить
`requirements.txt`). В репозитории лежат готовые конфигурации запуска (`.run/`): **ADK** (запуск `run.py`)
и **Tests (pytest)** с `QT_QPA_PLATFORM=offscreen`. Либо просто правый клик по `run.py` → *Run 'run'*.

Первый запуск создаёт `%USERPROFILE%\Documents\ADK\config.ini` (образец — `config.example.ini`).
Заполните LDAP-параметры и запустите снова. Вход — NTLM (логин/пароль) или Windows SSO (Kerberos, нужен `winkerberos`).

### Вариант 2 — `ADK.exe` (PyInstaller)

```bat
build_exe.bat          :: или build_exe.ps1 — создаст .venv, поставит зависимости, соберёт dist\ADK\
```

Результат — папка `dist\ADK\` с `ADK.exe` и `_internal\` (Qt-библиотеки). **Копировать папку целиком**
(например, на сетевой диск или в `C:\Program Files\ADK`), ярлык — на exe. Python на машине пользователя не нужен.
Сборка описана в `adk.spec`: onedir (быстрый старт, меньше ложных срабатываний антивируса, чем onefile),
без консольного окна, иконка `assets/icon.ico`, метаданные версии `version_info.txt`.

Обычные вопросы:
- *SmartScreen «Неизвестный издатель»* — exe не подписан. Подпишите корпоративным сертификатом (`signtool sign /a /fd SHA256 "ADK.exe"`) или добавьте папку в исключения политики.
- *Антивирус ругается на PyInstaller* — из-за этого в spec отключён UPX и выбран onedir; при необходимости отправьте exe в whitelist.
- *Обновление* — заменить папку `dist\ADK`; конфиг, БД и лог живут в `Documents\ADK` и не трогаются.
- *Сборку на Windows делает CI* (`.github/workflows/build-exe.yml`, по тегу `v*`) — артефакт `ADK-<версия>-win64.zip`.

## 🗄️ Серверный режим, PostgreSQL и CLI

Для 1–3 администраторов достаточно локального SQLite и встроенного сканера. Для отдела (3.1):

```ini
[Paths]
db_backend = postgres
db_dsn = postgresql://adk:secret@dbhost/adk
[Scanner]
auto_scan_interval_min = 0        ; клиенты не сканируют — читают общий инвентарь
```

```bat
pip install "adk[postgres]"
adk --serve 30                    ; на сервере: скан парка каждые 30 мин + письмо при срочных пунктах сводки
```

`pgadapter.translate` переводит запросы `db.py` на лету (`?`→`%s`, `INSERT OR REPLACE`→`ON CONFLICT DO UPDATE`,
`datetime('now')`→`now()`, `AUTOINCREMENT`→`SERIAL`), поэтому вторая копия SQL не нужна, а транслятор покрыт тестами.
Инвентарь, история, заметки, кэш ПО, MAC-адреса и журнал становятся общими для всех клиентов.

CLI работает и с SQLite, и с PostgreSQL:

```bat
adk --find иванов                 ; таблица в консоль (код возврата 1, если пусто)
adk --find 10.0.2 --json          ; JSON для скриптов
adk --export-inventory парк.xlsx
adk --attention                   ; сводка «Внимание»
adk --ping WS-101 WS-105 --json
adk --wol WS-133                  ; по сохранённому MAC
adk --scan                        ; один проход сканера
```

Учётные данные — сохранённые (Windows DPAPI: `win32crypt` или `ctypes`+crypt32; иначе keyring) или `ADK_USER` / `ADK_PASSWORD`.
Если «Запомнить меня» не сработало, окно входа покажет причину; сохранённый пароль забывается только при ошибке учётных данных, а не при недоступном контроллере.

**Portable:** положите пустой файл `portable` рядом с `ADK.exe` (или задайте `ADK_PORTABLE=1` / `ADK_HOME=D:\adk-data`) —
config.ini, БД, лог, плагины и шаблоны будут в `data\` рядом с exe.

## 🔒 Роли

Изменяющие действия делятся на два класса, и у каждого — свой список групп AD:

| Право | Что открывает | Ключ `[Access]` |
|---|---|---|
| **ПК** | перезагрузка/выключение, WoL, опрос ПО, заметки, привязка ПК, «отложить» в сводке, изменяющие плагины | `pc_admin_groups` |
| **AD** | атрибуты, включение/отключение УЗ, снятие блокировки, сброс пароля, смарт-карта, создание пользователя, группы (в т. ч. массово и «Группы как у…»), шаблоны | `ad_admin_groups` |

Просмотр, поиск, пинг, здоровье ПК, входы, сравнения, экспорт, журнал — доступны всем всегда.

```ini
[Access]
pc_admin_groups = GT_Admins, IT-Admins   ; GT_Admins в AD только читает — но ПК обслуживает полностью
ad_admin_groups = IT-Admins              ; менять объекты домена могут только они
readonly_group = ADK-ReadOnly            ; члены — ни ПК, ни AD
admin_groups =                           ; совместимость: оба права сразу
```

Пустой список — право есть у всех. Членство администратора (`memberOf`) читается из AD в фоне после входа.
Без права кнопки скрываются, а в карточке AD поля становятся только для чтения; слоты дополнительно проверяют
`access.can("действие")` и показывают «Недостаточно прав». В шапке — бейдж `🔒 AD: только чтение` /
`🔒 ПК: только чтение` / `🔒 Только чтение`. Роли ADK — удобство и защита от случайного клика; настоящая граница —
ACL домена: запись в LDAP идёт под учёткой администратора, и без прав контроллер вернёт `insufficientAccessRights`.

## 🧩 Плагины

Любой `*.py` в `[Plugins] dir` (по умолчанию `Documents\ADK\plugins`) с классом-наследником `adk.plugins.Action`
становится кнопкой в инспекторе и пунктом контекстного меню. Файлы с `_` в начале имени выключены
(так поставляется шаблон `_template_plugin.py`). Ошибка в плагине пишется в лог и не ломает остальные.

Кнопка **🧩 Плагины** в шапке открывает менеджер: список файлов, «Включить/Выключить» (переименование с `_`),
«Открыть файл», «Удалить», «Перечитать» (без перезапуска программы) и **«Создать шаблон плагина»** —
файл со всей документацией внутри: атрибуты и методы `Action`, содержимое `ctx`, что можно импортировать из ADK, примеры.

```python
from adk.plugins import Action

class UserProfile(Action):
    name, icon = "Профиль пользователя", "📂"
    needs_pc = True        # кнопка активна только для строк с ПК
    modifying = False      # True — скрывается в режиме «только чтение»

    def run(self, ctx):    # ctx: login, comp, ip, fio, admin, entry (ldap3), conn_factory
        import subprocess
        subprocess.Popen(["explorer", rf"\\{ctx['comp']}\C$\Users\{ctx['login']}"])
        return f"Открыт профиль {ctx['login']} на {ctx['comp']}"   # строка попадает в статус и журнал
```

