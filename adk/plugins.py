"""Плагины действий: ``<plugins_dir>/*.py`` с классом-наследником :class:`Action`.

Пример (``Documents/ADK/plugins/user_profile.py``)::

    from adk.plugins import Action

    class UserProfile(Action):
        name = "Профиль пользователя"
        icon = "📂"
        needs_pc = True            # кнопка активна только если у строки есть ПК
        modifying = False          # True — требует права «ПК» (pc_admin_groups)

        def run(self, ctx):        # ctx: {"login", "comp", "ip", "fio", "admin", "entry", "conn_factory"}
            import subprocess
            subprocess.Popen(["explorer", rf"\\\\{ctx['comp']}\\C$\\Users\\{ctx['login']}"])
            return f"Открыт профиль {ctx['login']} на {ctx['comp']}"   # строка → показывается в статусе

Плагин выполняется в GUI-потоке, поэтому долгие операции должен запускать сам (Popen/поток).
Ошибка внутри плагина не роняет приложение — показывается в статусе и пишется в лог.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys

log = logging.getLogger(__name__)


class Action:
    name: str = "Действие"
    icon: str = "🔌"
    needs_pc: bool = False
    modifying: bool = False
    order: int = 100
    place: str = "actions"     # "actions" — сетка «Действия с ПК»; "header" — кнопка рядом с ФИО в инспекторе

    def enabled(self, ctx: dict) -> bool:  # можно переопределить
        return bool(ctx.get("comp")) if self.needs_pc else True

    def run(self, ctx: dict):  # → str | None
        raise NotImplementedError

    @property
    def label(self) -> str:
        return f"{self.icon} {self.name}".strip()


def load_plugins(directory: str) -> list[Action]:
    """Импортирует все *.py из каталога; каждая ошибка изолирована. Возвращает экземпляры Action по ``order``."""
    actions: list[Action] = []
    if not directory or not os.path.isdir(directory):
        return actions
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".py") or fname.startswith("_"):
            continue
        path = os.path.join(directory, fname)
        modname = f"adk_plugin_{os.path.splitext(fname)[0]}"
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[modname] = mod
            spec.loader.exec_module(mod)
            for obj in vars(mod).values():
                if isinstance(obj, type) and issubclass(obj, Action) and obj is not Action:
                    actions.append(obj())
        except Exception as exc:  # noqa: BLE001
            log.exception("plugin %s: %s", fname, exc)
    actions.sort(key=lambda a: (a.order, a.name))
    return actions


def run_action(action: Action, ctx: dict) -> tuple[bool, str]:
    try:
        res = action.run(ctx)
        return True, str(res) if res else f"{action.name}: выполнено"
    except Exception as exc:  # noqa: BLE001
        log.exception("plugin %s", action.name)
        return False, f"{action.name}: {exc}"


TEMPLATE = '''"""Шаблон плагина ADK — скопируйте, переименуйте, допишите ``run()``.

═══════════════════════════════════════════════════════════════════════════════
КАК ЭТО РАБОТАЕТ
═══════════════════════════════════════════════════════════════════════════════
• Плагин — обычный файл ``*.py`` в папке плагинов (Плагины → «Папка плагинов»; путь задаётся в
  ``config.ini`` → ``[Plugins] dir``; по умолчанию ``Documents/ADK/plugins``).
• Файлы, чьё имя начинается с «_», ВЫКЛЮЧЕНЫ (так хранятся заготовки). Чтобы включить —
  уберите подчёркивание (или нажмите «Включить» в менеджере плагинов).
• В файле может быть один или несколько классов-наследников ``Action``. Каждый класс = одна
  кнопка в инспекторе (блок «Действия с ПК») и один пункт в контекстном меню строки таблицы.
• Плагины перечитываются при запуске программы и по кнопке «Перечитать» в менеджере.
• Ошибка внутри плагина НЕ роняет программу: текст ошибки показывается в строке состояния и
  пишется в журнал ``adk.log``. Каждый запуск плагина записывается в «Журнал действий».

═══════════════════════════════════════════════════════════════════════════════
АТРИБУТЫ КЛАССА
═══════════════════════════════════════════════════════════════════════════════
name       : str  — подпись кнопки (по-русски, коротко: «Профиль пользователя»).
icon       : str  — ведущий эмодзи; программа сама заменит его контурной иконкой в цвете темы.
                    Поддерживаются: 📂 📁 🖥️ 💻 🖨️ 🌐 🔑 🔒 🔓 👤 👥 📋 📡 ⚡ 🧹 🔄 🛠️ ⚙️ 📊 📦 🧩 ✉️ 🔍
needs_pc   : bool — True: кнопка активна, только если у выбранной строки есть ПК (ctx["comp"]).
modifying  : bool — True: действие ЧТО-ТО МЕНЯЕТ на ПК/в системе → показывается только роли «ПК»
                    (право pc_admin_groups). False: только чтение — доступно всем ролям.
order      : int  — порядок среди кнопок плагинов (меньше — левее/выше). По умолчанию 100.
place      : str  — где показать кнопку: "actions" (по умолчанию) — в сетке «Действия с ПК»;
                    "header" — компактная кнопка рядом с ФИО сотрудника (для частых действий).

═══════════════════════════════════════════════════════════════════════════════
МЕТОДЫ
═══════════════════════════════════════════════════════════════════════════════
enabled(ctx) -> bool
    Можно ли запускать для этой строки. По умолчанию: True, либо «есть ПК», если needs_pc.
    Переопределите, если нужна своя проверка (например, только для ПК в сети — ctx["ip"]).

run(ctx) -> str | None
    Само действие. Вернёте строку — она появится в строке состояния (и в журнале действий).
    Вернёте None — покажется «<name>: выполнено». Исключение — покажется как ошибка.

    ВАЖНО: run() выполняется в GUI-потоке. Долгие операции (опрос по сети, копирование)
    запускайте сами через ``subprocess.Popen`` или ``threading.Thread``, иначе окно «замрёт».

═══════════════════════════════════════════════════════════════════════════════
СЛОВАРЬ ctx (что передаёт программа)
═══════════════════════════════════════════════════════════════════════════════
ctx["login"]        — логин выбранного пользователя (sAMAccountName), "" для строки принтера
ctx["fio"]          — полное ФИО
ctx["comp"]         — имя ПК без домена и «$» (например, "WS-101"), "" если ПК не привязан
ctx["ip"]           — IP этого ПК по последнему сканированию, "" если не найден
ctx["admin"]        — логин администратора, который нажал кнопку (для журналов)
ctx["entry"]        — объект ldap3 Entry пользователя (ctx["entry"].mail.value и т.п.), может быть None
ctx["conn_factory"] — функция без аргументов → новое соединение ldap3 (не забудьте conn.unbind())
ctx["window"]       — главное окно ADK (QWidget): родитель для своих окон, чтобы они были в стиле программы
ctx["mail"]         — почта пользователя (может быть "")

═══════════════════════════════════════════════════════════════════════════════
ПОЛЕЗНОЕ ИЗ ADK, ЧТО МОЖНО ИМПОРТИРОВАТЬ
═══════════════════════════════════════════════════════════════════════════════
from adk import netutils   # netutils.ping(host) -> bool, netutils.get_computer_network_info(name)
from adk import nettools   # nettools.send_message(comp, text, seconds) — сообщение на экран ПК (msg.exe)
from adk.widgets import MessageBox, InputDialog   # окна в стиле ADK: MessageBox.information(parent, заголовок, текст),
                                                  # InputDialog.get_text(parent, заголовок, подпись) -> (текст, ok)
from adk import db         # db.log_action(admin, action, target, details) — своя запись в журнал
from adk import ad         # ad.get_ad_value(entry, "mail"), ad.paged_search(conn, filter, attrs)
from adk.config import settings   # settings.domain_netbios, settings.plugins_dir …

Ничего из этого не обязательно — плагин может быть полностью самостоятельным.

═══════════════════════════════════════════════════════════════════════════════
ПРИМЕР НИЖЕ: открыть папку профиля пользователя на его ПК (только чтение, нужен ПК)
═══════════════════════════════════════════════════════════════════════════════
"""
from adk.plugins import Action


class OpenUserProfile(Action):
    name = "Профиль пользователя"      # подпись кнопки
    icon = "📂"                         # эмодзи → контурная иконка
    needs_pc = True                     # без ПК кнопка неактивна
    modifying = False                   # ничего не меняет → доступно всем ролям
    order = 100

    def enabled(self, ctx: dict) -> bool:
        # пример своей проверки: нужен ПК И известен его IP (значит, он был в сети при сканировании)
        return bool(ctx.get("comp")) and bool(ctx.get("ip"))

    def run(self, ctx: dict):
        import subprocess
        path = rf"\\\\{ctx[\'comp\']}\\C$\\Users\\{ctx[\'login\']}"
        subprocess.Popen(["explorer", path])          # не ждём завершения — окно не замирает
        return f"Открыта папка {path}"                # текст в строку состояния и журнал


# ── Пример: кнопка рядом с ФИО, своё окно в стиле ADK и сообщение на экран ПК ────────────────
# class Message(Action):
#     name = "Сообщение"
#     icon = "✉️"
#     needs_pc = True
#     modifying = True            # что-то делает на ПК → только роль «ПК»
#     place = "header"            # компактная кнопка рядом с ФИО
#
#     def run(self, ctx: dict):
#         from adk.widgets import InputDialog, MessageBox
#         from adk import nettools
#         text, ok = InputDialog.get_text(ctx["window"], f"Сообщение для {ctx['fio']}",
#                                         f"Текст появится на экране {ctx['comp']}:")
#         if not ok or not text.strip():
#             return "Отменено"
#         sent, info = nettools.send_message(ctx["comp"], text)
#         (MessageBox.information if sent else MessageBox.warning)(ctx["window"], "Сообщение", info)
#         return info

# ── Второй пример: действие, которое что-то МЕНЯЕТ (видно только роли «ПК») ─────────────────
# class RestartSpooler(Action):
#     name = "Перезапустить печать"
#     icon = "🖨️"
#     needs_pc = True
#     modifying = True
#
#     def run(self, ctx: dict):
#         import subprocess
#         comp = ctx["comp"]
#         subprocess.run(["sc", rf"\\\\{comp}", "stop", "Spooler"], check=False, timeout=30)
#         subprocess.run(["sc", rf"\\\\{comp}", "start", "Spooler"], check=False, timeout=30)
#         return f"Служба печати на {comp} перезапущена"
'''


def write_template(directory: str, filename: str = "_template_plugin.py", overwrite: bool = False) -> str | None:
    """Создаёт файл-шаблон плагина с полной документацией внутри. Имя с «_» — выключен, пока не переименуют.
    Возвращает путь или None при ошибке записи."""
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        if os.path.exists(path) and not overwrite:
            base, ext = os.path.splitext(filename)
            n = 2
            while os.path.exists(os.path.join(directory, f"{base}_{n}{ext}")):
                n += 1
            path = os.path.join(directory, f"{base}_{n}{ext}")
        with open(path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)
        return path
    except OSError:
        return None


def list_plugin_files(directory: str) -> list[dict]:
    """Все *.py в папке плагинов с признаком «включён» (без «_» в начале) и найденными классами Action.
    Для менеджера плагинов: показывает и выключенные файлы, которые load_plugins пропускает."""
    out: list[dict] = []
    if not directory or not os.path.isdir(directory):
        return out
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(directory, fname)
        enabled = not fname.startswith("_")
        actions: list[Action] = []
        error = ""
        try:
            spec = importlib.util.spec_from_file_location(f"adk_plugin_probe_{os.path.splitext(fname)[0]}", path)
            if spec is not None and spec.loader is not None:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                for obj in vars(mod).values():
                    if isinstance(obj, type) and issubclass(obj, Action) and obj is not Action:
                        actions.append(obj())
        except Exception as exc:  # noqa: BLE001
            error = f"{exc.__class__.__name__}: {exc}"
        out.append({"file": fname, "path": path, "enabled": enabled, "actions": actions, "error": error})
    return out


def set_enabled(path: str, enabled: bool) -> str:
    """Включить/выключить плагин переименованием файла («_» в начале = выключен). Возвращает новый путь."""
    d, fname = os.path.split(path)
    stripped = fname.lstrip("_")
    new_name = stripped if enabled else "_" + stripped
    new_path = os.path.join(d, new_name)
    if new_path != path:
        os.replace(path, new_path)
    return new_path


def write_example(directory: str) -> str | None:
    """При первом запуске кладёт в папку плагинов выключенный шаблон с документацией (``_template_plugin.py``)."""
    path = os.path.join(directory, "_template_plugin.py")
    if os.path.exists(path):
        return path
    return write_template(directory)
