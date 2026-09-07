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


def write_example(directory: str) -> str | None:
    """Кладёт пример плагина (выключенный — с подчёркиванием) при первом запуске."""
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "_example_user_profile.py")
        if not os.path.exists(path):
            open(path, "w", encoding="utf-8").write(
                '"""Пример плагина ADK: открыть папку профиля пользователя на его ПК. '
                'Уберите подчёркивание из имени файла, чтобы включить."""\n'
                "from adk.plugins import Action\n\n\n"
                "class UserProfile(Action):\n"
                '    name = "Профиль пользователя"\n    icon = "📂"\n    needs_pc = True\n\n'
                "    def run(self, ctx):\n"
                "        import subprocess\n"
                '        subprocess.Popen(["explorer", rf"\\\\{ctx[\'comp\']}\\C$\\Users\\{ctx[\'login\']}"])\n'
                '        return f"Открыт профиль {ctx[\'login\']} на {ctx[\'comp\']}"\n')
        return path
    except OSError:
        return None
