"""Точка входа: ``python -m adk``."""
from __future__ import annotations

import logging
import os
import sys
import traceback

from PyQt6.QtWidgets import QApplication, QDialog

from . import __version__, config, db, i18n, plugins
from .credentials import clear_credentials, load_credentials
from .widgets import MessageBox, apply_theme

log = logging.getLogger("adk")


def _install_notify_hook() -> None:
    """Каждая запись журнала действий проходит через notify.notify_event (почта по настройкам [Notify])."""
    from . import notify

    def hook(admin: str, action: str, target: str, details: str) -> None:
        notify.notify_event(admin, action, target, details, labels=db.ACTION_LABELS)
    if hook not in db.AUDIT_HOOKS:
        db.AUDIT_HOOKS.append(hook)


def _sweep_stale_ps1() -> None:
    """3.9.0: убрать осиротевшие ``adk_*.ps1`` из %TEMP% (остаются после жёсткого прерывания запуска —
    например, вынули питание). Свои текущие файлы не трогаем: им меньше суток."""
    import contextlib
    import glob
    import tempfile
    import time
    try:
        for p in glob.glob(os.path.join(tempfile.gettempdir(), "adk_*.ps1")):
            try:
                if os.path.getmtime(p) < time.time() - 86400:
                    with contextlib.suppress(OSError):
                        os.remove(p)
            except OSError:
                continue
    except Exception:  # noqa: BLE001
        pass


def _excepthook(exc_type, exc, tb):
    """Необработанное исключение в слоте не должно молча ронять процесс."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Необработанное исключение:\n%s", text)
    try:
        MessageBox.critical(None, "Внутренняя ошибка", f"{exc_type.__name__}: {exc}\nПодробности — в журнале.")
    except Exception:  # noqa: BLE001
        pass


def _db_hang_exit(err) -> None:
    """3.6.0: сторож базы сработал — окно с объяснением и выход. Вызывается из любого потока; окно показываем в GUI-потоке."""
    from PyQt6.QtCore import QThread, QTimer
    from PyQt6.QtWidgets import QApplication
    from . import db

    app = QApplication.instance()
    if app is None:
        return

    def show():
        if getattr(app, "_adk_hang_shown", False):
            return
        app._adk_hang_shown = True                 # noqa: SLF001
        backups = db.list_backups()
        last = f"\n\nПоследняя резервная копия: {backups[-1]}" if backups else ""
        MessageBox.critical(None, "Соединение с базой признано зависшим", DB_HANG_TEXT + f"\n\nПодробности: {err}{last}")
        app.exit(3)

    if app.thread() is QThread.currentThread():
        show()
    else:
        QTimer.singleShot(0, app, show)             # таймер принадлежит app → слот выполнится в GUI-потоке


DB_HANG_TEXT = (
    "Запрос к базе ADK выполнялся дольше минуты и был прерван. Ваше соединение с базой признано зависшим — вы отрезаны "
    "от базы до следующего запуска программы, ADK сейчас закроется.\n\n"
    "Если проблема повторится, сначала обратитесь к вашим системным администраторам.\n"
    "Если вы администратор: переименуйте файл базы (например, pc_mapping.db → pc_mapping.old.db) и запустите ADK заново — "
    "база будет создана и заполнена повторно; прежние данные лежат в папке backups рядом с базой.\n"
    "Если не помогло — обратитесь к администратору ПО.")


_CLI_FLAGS = ("--find", "--export-inventory", "--attention", "--wol", "--ping", "--scan", "--serve", "--version", "-h", "--help")


def _fetch_computer_names(user: str | None, password: str | None) -> list[str]:
    """Список рабочих станций домена для окна выбора парка (3.10.0). Ошибка — не критична: окно покажет ввод без счётчика."""
    try:
        from . import ad
        conn = ad.make_connection(user, password)
        try:
            entries = ad.paged_search(
                conn,
                "(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                "(!(operatingSystem=*Server*)))", ["name"])
        finally:
            conn.unbind()
        return [ad.get_ad_value(e, "name").rstrip("$").upper() for e in entries]
    except Exception:  # noqa: BLE001
        log.exception("список ПК для окна выбора парка")
        return []


def main() -> int:
    if any(arg.split("=")[0] in _CLI_FLAGS for arg in sys.argv[1:]):
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    config.setup_logging()
    config.write_default_config()
    _sweep_stale_ps1()
    log.info("ADK %s, config: %s", __version__, config.INI_FILE)
    i18n.set_language(config.settings.language)
    plugins.write_example(config.settings.plugins_dir)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("ADK")
    app.setQuitOnLastWindowClosed(not config.settings.minimize_to_tray)
    apply_theme(config.settings.design)

    # 3.5.11: первый запуск — спросить, где лежит (или будет лежать) база, ещё до окна входа
    initial_fill = False
    db_setup_ran = False
    from .setup_ui import needs_db_setup
    if needs_db_setup():
        from .setup_ui import DbSetupDialog
        setup = DbSetupDialog()
        if setup.exec() != QDialog.DialogCode.Accepted:
            return 0
        db_setup_ran = True
        initial_fill = setup.is_new
        setup.deleteLater()          # settings уже перечитаны — db.* открывают соединение по новому db_path

    try:
        db.init_db()
    except Exception as exc:  # noqa: BLE001
        MessageBox.critical(None, "База данных", f"Не удалось открыть {config.settings.db_path}:\n{exc}")
        return 2
    db.set_hang_hook(_db_hang_exit)          # 3.6.0: запрос дольше минуты → окно и выход
    db.backup_periodic()                     # 3.6.0: копия при старте, если последней больше backup_every_hours
    _install_notify_hook()

    from .dialogs import LoginDialog
    from .main_window import ADApp

    user, password = load_credentials()
    error = ""
    while True:
        if user and password:
            try:
                from . import ad
                c = ad.make_connection(user, password)
                c.unbind()
                break
            except Exception as exc:  # noqa: BLE001
                from . import ad
                error = ad.describe_ldap_error(exc)
                if ad.is_auth_error(exc):
                    clear_credentials()        # пароль сменили/учётку заблокировали — забываем
                    password = None
                else:
                    error += "\nСохранённые учётные данные подставлены — проверьте сеть и повторите."
        dlg = LoginDialog(error, saved_user=user, saved_password=password)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return 0
        user, password = dlg.username, dlg.password
        dlg.deleteLater()
        break

    # 3.10.0: сразу после выбора базы (первый запуск) — какие серии ПК образуют парк, если маска ещё не задана.
    # Живой счётчик «найдено: N» считается по реальному списку ПК домена, результат пишется в [Scanner] host_mask.
    if db_setup_ran and not os.environ.get("ADK_TESTS") and not config.settings.host_mask.strip():
        try:
            from .setup_ui import ParkMaskDialog
            names = _fetch_computer_names(user, password)
            pm = ParkMaskDialog(None, names)
            pm.exec()
            pm.deleteLater()
        except Exception:  # noqa: BLE001
            log.exception("окно выбора парка")

    # 3.9.0: вопрос «что собрать» — строго до главного окна и только один раз, при пустой базе
    # (первый запуск / свежий файл). Дальше база обновляется кнопкой и по расписанию — вопрос не беспокоит.
    startup_choice = ""
    if not os.environ.get("ADK_TESTS"):
        try:
            row = db.db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory", fetch="one")
            base_empty = not (row and row[0])
        except Exception:  # noqa: BLE001
            base_empty = False
        if base_empty:
            from .scan_ui import ask_startup_scan
            startup_choice = ask_startup_scan(None)

    window = ADApp(user, password, initial_fill=initial_fill, startup_choice=startup_choice)
    window.show()
    # справка по роли показывается самим окном — после того, как роль определена по группам AD
    # (ADApp.resolve_access → show_role_welcome), а не до проверки, иначе она могла описать не ту роль
    code = app.exec()
    # 3.9.0: гарантированный выход — фоновые PowerShell/пинг-процессы не должны оставаться в диспетчере задач.
    # Через секунду после нашего выхода taskkill /T /F убивает всё дерево процесса (детей psrun/пулов),
    # а сам ADK завершается сразу и со своим кодом выхода (важно для сторожа базы).
    log.info("выход (%s)", code)
    logging.shutdown()
    if os.name == "nt":
        try:
            import subprocess
            subprocess.Popen(
                ["cmd", "/c", f"timeout /t 1 /nobreak >nul & taskkill /PID {os.getpid()} /T /F"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception:  # noqa: BLE001
            pass
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())
