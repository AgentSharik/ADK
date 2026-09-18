"""Живой опрос парка (3.5.10): принтеры и ПО всех доступных ПК прямо сейчас — без инвентарных CSV и без снимка в БД.

До 3.5.10 окна «Принтеры парка» и «ПО парка» показывали только то, что уже лежало в базе (кэш CSV сканера или ранее
опрошенные ПК). У организации без CSV они были пустыми. Теперь оба окна умеют опросить парк сами:

* список ПК — из инвентаря сканера (``pc_inventory``: сначала те, что в сети), а если инвентарь пуст — из AD;
* каждый ПК опрашивается тем же кодом, что и карточка (:func:`adk.netutils.get_live_printers`,
  :func:`adk.software.get_software`) — WinRM → WMI/DCOM → удалённый реестр;
* параллельно (:data:`WORKERS` потоков), с прогрессом и кнопкой «Стоп»; недоступные ПК просто пропускаются;
* принтеры **не пишутся** в базу автоматически (живой взгляд ≠ снимок) — есть отдельная кнопка «Сохранить в базу»;
  ПО кэшируется как и при одиночном опросе (по нему работает поиск «у кого установлено»).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from PyQt6.QtCore import pyqtSignal

from . import db, netutils
from .workers import BaseWorker

log = logging.getLogger(__name__)

WORKERS = 8          # одновременных опросов: PowerShell-процесс на каждый, больше — ПК администратора начинает тормозить
PER_HOST_TIMEOUT = 45


def fleet_hosts(conn_factory: Callable | None = None, online_only: bool = True) -> list[str]:
    """ПК для опроса: инвентарь сканера (в сети — первыми), при пустом инвентаре — рабочие станции из AD."""
    rows = db.db_execute_with_retry(
        "SELECT computer_name, is_online FROM pc_inventory ORDER BY is_online DESC, computer_name", fetch="all") or []
    if rows:
        return [r[0] for r in rows if r[0] and (r[1] or not online_only)]
    if conn_factory is None:
        return []
    from . import ad
    from .workers import ScanWorker
    conn = conn_factory()
    try:
        entries = ad.paged_search(conn, "(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                                        "(!(operatingSystem=*Server*)))", ["name"])
    finally:
        conn.unbind()
    return ScanWorker.workstation_names(entries)


def poll_fleet(hosts: list[str], fn: Callable[[str], dict], progress: Callable[[int, int, str], None] | None = None,
               cancelled: Callable[[], bool] | None = None, workers: int = WORKERS) -> dict[str, dict]:
    """Опросить ``hosts`` функцией ``fn`` параллельно. Возвращает {ПК: результат fn} — включая ``{"error": …}``.

    ``progress(i, n, host)`` — после каждого ПК; ``cancelled()`` — проверяется между ПК, начатые опросы дорабатывают.
    """
    out: dict[str, dict] = {}
    hosts = [h for h in dict.fromkeys(db.clean_computer_name(h) for h in hosts) if h]
    if not hosts:
        return out
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(hosts)))) as ex:
        futs = {}
        it = iter(hosts)
        # подаём задачи порциями, чтобы «Стоп» не ждал очередь из сотен ПК
        for h in it:
            futs[ex.submit(_safe, fn, h)] = h
            if len(futs) >= workers * 2:
                break
        while futs:
            for f in as_completed(list(futs)):
                h = futs.pop(f)
                out[h] = f.result()
                done += 1
                if progress:
                    progress(done, len(hosts), h)
                if not (cancelled and cancelled()):
                    nxt = next(it, None)
                    if nxt is not None:
                        futs[ex.submit(_safe, fn, nxt)] = nxt
                break
    return out


def _safe(fn: Callable[[str], dict], host: str) -> dict:
    try:
        return fn(host) or {}
    except Exception as exc:  # noqa: BLE001
        log.debug("fleet poll %s: %s", host, exc)
        return {"error": str(exc)}


def printers_live(host: str) -> dict:
    """Принтеры одного ПК для опроса парка: пропускаем выключенные быстро (TCP 445/ICMP), не пишем в БД."""
    ip, alive = netutils.get_computer_network_info(host)
    if not alive:
        return {"error": "не в сети", "skipped": True}
    return netutils.get_live_printers(host, timeout=PER_HOST_TIMEOUT)


def software_live(host: str) -> dict:
    from . import software
    ip, alive = netutils.get_computer_network_info(host)
    if not alive:
        return {"error": "не в сети", "skipped": True}
    return software.get_software(host, timeout=90)


def group_printers(results: dict[str, dict]) -> list[dict]:
    """Ответы по ПК → строки как у :func:`adk.db.printer_summary` (принтер → сколько ПК, какие)."""
    groups: dict[tuple[str, str], dict] = {}
    for comp, r in results.items():
        for p in r.get("printers") or []:
            key = (p["name"].casefold(), p.get("ip") or "")
            g = groups.setdefault(key, {"name": p["name"], "kind": p.get("kind", ""), "ip": p.get("ip") or "", "pcs": 0,
                                        "online": 0, "updated": "сейчас", "computers": [], "live": True})
            g["pcs"] += 1
            g["online"] += 1                       # ответил — значит в сети
            g["computers"].append(comp)
    out = sorted(groups.values(), key=lambda g: (-g["pcs"], g["name"].casefold()))
    for g in out:
        g["computers"] = ", ".join(sorted(g["computers"]))
    return out


def summarize(results: dict[str, dict]) -> dict:
    ok = sum(1 for r in results.values() if "error" not in r)
    skipped = sum(1 for r in results.values() if r.get("skipped"))
    failed = len(results) - ok - skipped
    return {"total": len(results), "ok": ok, "skipped": skipped, "failed": failed}


class FleetPollWorker(BaseWorker):
    """QThread: опрос парка в фоне. ``progress(i, n, host)``, ``finished_poll(results)``."""
    progress = pyqtSignal(int, int, str)
    finished_poll = pyqtSignal(dict)

    def __init__(self, hosts: list[str], fn: Callable[[str], dict], parent=None):
        super().__init__(parent)
        self.hosts, self.fn = list(hosts), fn

    def run(self) -> None:
        try:
            res = poll_fleet(self.hosts, self.fn, progress=lambda i, n, h: self.progress.emit(i, n, h),
                             cancelled=lambda: self.cancelled)
        except Exception as exc:  # noqa: BLE001
            log.exception("FleetPollWorker")
            self.error.emit(str(exc))
            return
        self.finished_poll.emit(res)
