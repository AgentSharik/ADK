"""Командная строка ADK — для скриптов и планировщика, без окна.

    adk --find ivanov [--json]           поиск (как в главном окне), таблица или JSON
    adk --export-inventory parc.xlsx     выгрузка инвентаря ПК (xlsx/csv)
    adk --attention [--json]             сводка «Внимание»
    adk --wol WS-101 [WS-102 ...]        Wake-on-LAN по сохранённым MAC
    adk --ping WS-101 WS-102             массовый пинг
    adk --scan                           один проход сканера парка (для планировщика на сервере)
    adk --version

Учётные данные: сохранённые (DPAPI) или ADK_USER / ADK_PASSWORD из окружения. Без пароля — SSO (Kerberos).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from . import __version__

log = logging.getLogger("adk.cli")


def _conn_factory():
    from . import ad
    from .credentials import load_credentials
    user, pwd = load_credentials()
    user = os.environ.get("ADK_USER") or user
    pwd = os.environ.get("ADK_PASSWORD") or pwd

    def factory():
        return ad.make_connection(user, pwd) if user and pwd else ad.make_connection()
    return factory


def _search(query: str, archives: bool = False, disabled: bool = False) -> list[dict]:
    """Синхронный поиск теми же правилами, что и SearchWorker (без Qt-потока)."""
    from . import ad
    from .config import SEARCH_RESULT_LIMIT
    from .workers import SearchWorker
    w = SearchWorker.__new__(SearchWorker)          # без QThread.__init__ — нам нужны только чистые методы
    w.conn_factory = _conn_factory()
    w.raw_query = query.strip()
    w.query = SearchWorker.normalize_query(query)
    w.printer_query = w.query[len(SearchWorker.PRINTER_PREFIX):].strip() if w.query.lower().startswith(SearchWorker.PRINTER_PREFIX) else None
    w.include_archives, w.include_disabled, w._printer_comps, w._cancelled = archives, disabled, set(), False
    w._net_pending = set()
    flt = w._build_filter()
    printers = w._printer_rows()
    if flt is None:
        return printers + w.resolve_pending(w._free_pc_rows())
    c = w.conn_factory()
    try:
        entries = ad.paged_search(c, flt, ad.USER_ATTRS, limit=SEARCH_RESULT_LIMIT)
    finally:
        c.unbind()
    return printers + w.resolve_pending(w._assemble(entries))


def _print_table(rows: list[list[str]]) -> None:
    if not rows:
        return
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    for n, r in enumerate(rows):
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))
        if n == 0:
            print("  ".join("-" * w for w in widths))


def serve(interval_min: int, iterations: int | None = None, sleep=None) -> int:
    """Серверный режим без GUI: цикл «скан парка → сводка → уведомления». ``iterations``/``sleep`` — для тестов."""
    import time

    from . import attention, config, db, notify
    from .workers import PCScannerWorker
    sleep = sleep or time.sleep
    factory = _conn_factory()
    log.info("ADK %s: серверный режим, интервал %d мин, БД: %s", __version__, interval_min,
             config.settings.db_backend if config.settings.db_backend != "sqlite" else config.settings.db_path)
    n = 0
    while iterations is None or n < iterations:
        n += 1
        try:
            done = PCScannerWorker.scan_once(factory, lambda m: log.info("%s", m))
            items = attention.collect_from_settings(factory, config.settings.attention)
            log.info("Проход %d: ПК — %d; %s", n, done, attention.summary_line(items))
            high = [i for i in items if i["severity"] == "high"]
            if high and config.settings.notify.get("smtp_host"):
                text = "\n".join(f"{i['icon']} {i['title']}: {i['subject']} — {i['text']}" for i in high[:20])
                notify.send_all(f"ADK: {attention.summary_line(items)}\n{text}", config.settings.notify)
            db.log_action("server", "scan", "*", f"{done} ПК")
        except KeyboardInterrupt:
            return 0
        except Exception as exc:  # noqa: BLE001
            log.exception("serve: %s", exc)
        if iterations is not None and n >= iterations:
            break
        try:
            sleep(interval_min * 60)
        except KeyboardInterrupt:
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adk", description="ADK — Active Directory Kit (режим командной строки)")
    p.add_argument("--find", metavar="ЗАПРОС", help="поиск: фамилия / логин / ПК / IP / printer:…")
    p.add_argument("--archives", action="store_true", help="включить архивные ПК пользователя")
    p.add_argument("--disabled", action="store_true", help="включить отключённые учётки")
    p.add_argument("--export-inventory", metavar="ФАЙЛ", help="выгрузить инвентарь ПК в .xlsx/.csv")
    p.add_argument("--attention", action="store_true", help="сводка «Внимание»")
    p.add_argument("--wol", nargs="+", metavar="ПК", help="Wake-on-LAN по сохранённым MAC")
    p.add_argument("--ping", nargs="+", metavar="ПК", help="массовый пинг")
    p.add_argument("--scan", action="store_true", help="один проход сканера парка")
    p.add_argument("--serve", nargs="?", const=-1, type=int, metavar="МИН",
                   help="серверный режим: сканер парка по расписанию (по умолчанию auto_scan_interval_min из config.ini) "
                        "+ уведомления по сводке «Внимание»; общая БД — [Paths] db_backend = postgres")
    p.add_argument("--json", action="store_true", help="вывод в JSON")
    p.add_argument("--version", action="store_true")
    a = p.parse_args(argv)

    if a.version:
        print(f"ADK {__version__}")
        return 0

    from . import config, db
    config.setup_logging()
    db.init_db()

    if a.find:
        rows = _search(a.find, a.archives, a.disabled)
        if a.json:
            keep = ("login", "full_fio", "is_disabled", "comp", "ip", "is_online", "dept", "title", "mail", "phone", "last_logon")
            print(json.dumps([{k: r.get(k) for k in keep} for r in rows], ensure_ascii=False, indent=2))
        else:
            from .export import rows_to_table
            _print_table(rows_to_table(rows))
        return 0 if rows else 1

    if a.export_inventory:
        from .export import export_inventory
        n = export_inventory(a.export_inventory)
        print(f"Экспортировано ПК: {n} → {a.export_inventory}")
        return 0

    if a.attention:
        from . import attention
        items = attention.collect_from_settings(_conn_factory(), config.settings.attention)
        if a.json:
            print(json.dumps(items, ensure_ascii=False, indent=2))
        else:
            print(attention.summary_line(items))
            _print_table([["", "Тип", "Кто/что", "Подробности"]] + [[i["icon"], i["title"], i["subject"], i["text"]] for i in items])
        return 0

    if a.wol:
        from . import nettools
        code = 0
        for comp in a.wol:
            mac = db.get_mac(comp)
            if not mac:
                print(f"{comp}: MAC неизвестен (откройте ПК в ADK, когда он в сети — MAC запомнится)")
                code = 1
                continue
            ok = nettools.wake(mac)
            print(f"{comp}: {mac} — {'отправлено' if ok else 'ошибка'}")
            db.log_action(os.environ.get("USERNAME", "cli"), "wol", comp, mac)
        return code

    if a.ping:
        from . import nettools
        res = nettools.mass_ping(a.ping)
        if a.json:
            print(json.dumps([{"comp": c, "ip": ip, "online": on} for c, ip, on in res], ensure_ascii=False))
        else:
            _print_table([["ПК", "IP", "Сеть"]] + [[c, ip, "в сети" if on else "нет"] for c, ip, on in sorted(res)])
        return 0

    if a.scan:
        from .workers import PCScannerWorker
        n = PCScannerWorker.scan_once(_conn_factory(), print)
        print(f"Сканирование завершено: ПК обработано — {n}")
        return 0

    if a.serve is not None:
        interval = a.serve if a.serve > 0 else int(getattr(config.settings, "auto_scan_interval_min", 0) or 60)
        return serve(interval)

    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
