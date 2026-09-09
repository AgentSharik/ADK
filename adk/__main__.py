"""Точка входа: ``python -m adk``."""
from __future__ import annotations

import logging
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


def _excepthook(exc_type, exc, tb):
    """Необработанное исключение в слоте не должно молча ронять процесс."""
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.error("Необработанное исключение:\n%s", text)
    try:
        MessageBox.critical(None, "Внутренняя ошибка", f"{exc_type.__name__}: {exc}\nПодробности — в журнале.")
    except Exception:  # noqa: BLE001
        pass


_CLI_FLAGS = ("--find", "--export-inventory", "--attention", "--wol", "--ping", "--scan", "--serve", "--version", "-h", "--help")


def main() -> int:
    if any(arg.split("=")[0] in _CLI_FLAGS for arg in sys.argv[1:]):
        from .cli import main as cli_main
        return cli_main(sys.argv[1:])
    config.setup_logging()
    config.write_default_config()
    log.info("ADK %s, config: %s", __version__, config.INI_FILE)
    i18n.set_language(config.settings.language)
    plugins.write_example(config.settings.plugins_dir)
    sys.excepthook = _excepthook

    app = QApplication(sys.argv)
    app.setApplicationName("ADK")
    app.setQuitOnLastWindowClosed(not config.settings.minimize_to_tray)
    apply_theme(config.settings.design)

    try:
        db.init_db()
    except Exception as exc:  # noqa: BLE001
        MessageBox.critical(None, "База данных", f"Не удалось открыть {config.settings.db_path}:\n{exc}")
        return 2
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

    window = ADApp(user, password)
    window.show()
    # справка по роли показывается самим окном — после того, как роль определена по группам AD
    # (ADApp.resolve_access → show_role_welcome), а не до проверки, иначе она могла описать не ту роль
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
