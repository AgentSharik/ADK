"""Фоновые потоки. Каждый воркер имеет сигнал ``error`` — ошибки не глотаются."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from . import ad, db, netutils
from .config import CREATE_NO_WINDOW, SEARCH_RESULT_LIMIT, settings

log = logging.getLogger(__name__)

ConnFactory = Callable[[], "ad.Connection"]


class BaseWorker(QThread):
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


class FunctionWorker(BaseWorker):
    """Выполняет произвольную функцию в фоне (LDAP-запросы из диалогов)."""
    done = pyqtSignal(object)

    def __init__(self, fn: Callable, *args, parent=None, **kwargs):
        super().__init__(parent)
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self) -> None:
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # noqa: BLE001
            log.exception("FunctionWorker")
            self.error.emit(ad.describe_ldap_error(exc))


# --------------------------------------------------------------------------- поиск
class SearchWorker(BaseWorker):
    """Единый поиск: ФИО / логин / телефон / почта / отдел / имя ПК / IP → строки таблицы."""
    results_ready = pyqtSignal(list, str, bool)  # rows, query, truncated
    net_ready = pyqtSignal(dict, str)            # {ПК: (ip, online)} — второй шаг: результат проверки сети, query

    PRINTER_PREFIX = "printer:"

    def __init__(self, conn_factory: ConnFactory, query: str, include_archives=False,
                 include_disabled=False, parent=None):
        super().__init__(parent)
        self.conn_factory = conn_factory
        self.raw_query = query.strip()          # как ввёл пользователь — по нему главное окно отбрасывает устаревшие ответы
        self.query = self.normalize_query(query)
        self.printer_query: str | None = None
        if self.query.lower().startswith(self.PRINTER_PREFIX):
            self.printer_query = self.query[len(self.PRINTER_PREFIX):].strip()
        self.include_archives = include_archives
        self.include_disabled = include_disabled
        self._printer_comps: set[str] = set()
        self._net_pending: set[str] = set()      # ПК, чей статус сети ещё не проверен (см. _assemble(defer_net=True))

    @staticmethod
    def normalize_query(query: str) -> str:
        """Схлопывает пробелы; «CORP\\ivanov» / «ivanov@corp.example» → «ivanov» (так вставляют из буфера)."""
        q = " ".join(query.split())
        if q.lower().startswith(SearchWorker.PRINTER_PREFIX):
            return q
        if "\\" in q and " " not in q:
            q = q.split("\\")[-1]
        if re.fullmatch(r"[\w.\-]+@[\w.\-]+", q):
            q = q.split("@", 1)[0]
        return q

    def _build_filter(self) -> str | None:
        """LDAP-фильтр по запросу. ``None`` — в AD искать нечего (например, IP/ПК без привязанных логинов).

        Что ищем: фамилия/имя/логин/displayName/почта/отдел/организация/должность/кабинет, имя ПК и IP
        (через инвентарь → логины), принтер (``printer:`` или подстрока в кэше принтеров → логины).
        Телефоны намеренно НЕ ищутся — по просьбе пользователей это создавало шум на числовых запросах.
        """
        q = ad.escape_filter_chars(self.query)
        disabled = "" if self.include_disabled else "(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
        if self.printer_query is not None:  # явный режим printer: — только владельцы принтера
            owners = db.printer_owners(self.printer_query)
            self._printer_comps = {o["comp"] for o in owners}
            logins = db.logins_by_printer(self.printer_query, limit=50)
            login_filters = "".join(f"(sAMAccountName={ad.escape_filter_chars(l)})" for l in logins)
            return (f"(&(objectCategory=person)(objectClass=user){disabled}(|{login_filters}))"
                    if login_filters else None)
        logins = set(db.logins_by_computer_or_ip(self.query, limit=50))
        if not netutils.is_ip_query(self.query):  # IP-запрос — это про ПК; владельцев принтеров с таким IP не примешиваем
            logins |= db.logins_by_printer(self.query, limit=50)
        login_filters = "".join(f"(sAMAccountName={ad.escape_filter_chars(l)})" for l in logins)
        if netutils.is_ip_query(self.query) and not login_filters:
            return None  # IP/префикс, за которым никто не сидит — в AD искать нечего (принтер по IP найдёт _printer_rows)
        text = "" if netutils.is_ip_query(self.query) else (
            f"(sAMAccountName=*{q}*)(displayName=*{q}*)(sn={q}*)(givenName={q}*)"
            f"(mail=*{q}*)(department=*{q}*)(company=*{q}*)(title=*{q}*)(physicalDeliveryOfficeName={q})")
        return ("(&(objectCategory=person)(objectClass=user)(!(sAMAccountName=*$))"
                f"{disabled}(|{text}{login_filters}))")

    def run(self) -> None:
        if len(self.query) < 2 or self.cancelled:
            return
        flt = self._build_filter()
        printer_rows = self._printer_rows()
        if flt is None:
            # запрос — просто IP принтера: показываем только сам принтер (кто подключён — в его инспекторе),
            # иначе рядом появлялись «пустые» строки ПК без ФИО
            free = [] if (printer_rows and self.printer_query is None) else self._free_pc_rows()
            if self.cancelled:
                return
            self.results_ready.emit(printer_rows + free, self.raw_query, False)
            self._emit_net()
            return
        try:
            conn = self.conn_factory()
            try:
                entries = ad.paged_search(conn, flt, ad.USER_ATTRS,
                                          limit=SEARCH_RESULT_LIMIT + 1)  # +1 — узнать об усечении
            finally:
                conn.unbind()
        except Exception as exc:  # noqa: BLE001
            log.exception("SearchWorker: LDAP")
            self.error.emit(ad.describe_ldap_error(exc))
            return
        if self.cancelled:
            return
        truncated = len(entries) > SEARCH_RESULT_LIMIT
        entries = entries[:SEARCH_RESULT_LIMIT]
        try:
            # 3.5.4: два шага. Сначала строки из AD + инвентаря (мгновенно), сеть проверяем потом и досылаем
            # net_ready — на слабом ПК/сети таблица появляется сразу, а не через 1–2 с после DNS и пингов.
            rows = self._assemble(entries, defer_net=True)
        except Exception as exc:  # noqa: BLE001
            log.exception("SearchWorker: assemble")
            self.error.emit(str(exc))
            return
        if self.cancelled:
            return
        self.results_ready.emit(printer_rows + rows, self.raw_query, truncated)
        self._emit_net()

    def resolve_pending(self, rows: list[dict]) -> list[dict]:
        """Синхронный вариант второго шага (CLI): проверить сеть у ожидающих ПК и вписать ответ в строки."""
        if self._net_pending:
            net = self._probe_network(self._net_pending) or {}
            for r in rows:
                comp = db.clean_computer_name(r.get("comp") or "")
                if comp in net:
                    ip, online = net[comp]
                    if ip != "Не найден":
                        r["ip"] = ip
                    r["is_online"] = online
                r["net_pending"] = False
            self._net_pending = set()
        return rows

    def _emit_net(self) -> None:
        """Второй шаг: проверить сеть у ПК из ``_net_pending`` и дослать net_ready (если поиск не отменён)."""
        if not self._net_pending:
            return
        net = self._probe_network(self._net_pending)
        if net is not None and not self.cancelled:
            self.net_ready.emit(net, self.raw_query)

    def _probe_network(self, comps: set[str]) -> dict[str, tuple[str, bool]] | None:
        """DNS + доступность для набора ПК параллельно; None — поиск отменён."""
        net: dict[str, tuple[str, bool]] = {}
        with ThreadPoolExecutor(max_workers=min(50, max(1, len(comps)))) as ex:
            futs = {ex.submit(netutils.get_computer_network_info, pc): pc for pc in comps}
            for f in as_completed(futs):
                if self.cancelled:
                    return None
                try:
                    net[futs[f]] = f.result()
                except Exception:  # noqa: BLE001
                    net[futs[f]] = ("Не найден", False)
        return net

    def _printer_rows(self) -> list[dict]:
        """Сами принтеры, подходящие под запрос (модель или IP), — первыми строками результата.

        Пользователь вводит просто IP: если он принадлежит принтеру из кэша ``pc_printers`` — покажем принтер
        (в сети / не в сети, сколько ПК подключено), а ниже — людей, у которых он стоит. Доступность
        проверяется TCP-портами 9100/631/80 (ping у принтеров часто закрыт), параллельно и с кэшем.
        """
        key = self.printer_query if self.printer_query is not None else self.query
        try:
            groups = db.printer_matches(key, limit=self.PRINTER_ROWS_LIMIT)
        except Exception as exc:  # noqa: BLE001
            log.debug("printer_matches: %s", exc)
            return []
        if not groups or self.cancelled:
            return []
        alive: dict[str, bool] = {}
        probes: dict[str, dict] = {}
        ips = sorted({g["ip"] for g in groups if g["ip"]})
        if ips:
            with ThreadPoolExecutor(max_workers=min(16, len(ips))) as ex:
                futs = {ex.submit(netutils.is_printer_alive, ip): ip for ip in ips}
                for f in as_completed(futs):
                    if self.cancelled:
                        return []
                    try:
                        alive[futs[f]] = f.result()
                    except Exception:  # noqa: BLE001
                        alive[futs[f]] = False
            if netutils.is_ip_query(key):
                # запрос — конкретный IP: честно проверяем, принтер ли это на самом деле (порты печати / веб-панель)
                with ThreadPoolExecutor(max_workers=min(16, len(ips))) as ex:
                    futs = {ex.submit(netutils.probe_printer, ip): ip for ip in ips}
                    for f in as_completed(futs):
                        if self.cancelled:
                            return []
                        try:
                            probes[futs[f]] = f.result()
                        except Exception:  # noqa: BLE001
                            probes[futs[f]] = {"alive": False, "is_printer": None, "evidence": "проверка не удалась"}
        rows = []
        for g in groups:
            row = self.printer_row(g, alive.get(g["ip"], False))
            row["probe"] = probes.get(g["ip"])
            rows.append(row)
        return rows

    PRINTER_ROWS_LIMIT = 20

    @staticmethod
    def printer_row(g: dict, is_online: bool) -> dict:
        """Строка таблицы для принтера (та же схема ключей, что у людей, + ``kind='printer'`` и ``printer``)."""
        online_pcs = sum(1 for x in g["pcs"] if x["is_online"])
        return {"kind": "printer", "entry": None, "login": "—", "fio": g["name"], "full_fio": g["name"],
                "is_disabled": False, "comp": "", "ip": g["ip"] or "—", "is_online": is_online,
                "phone": "", "ip_phone": "", "office": "", "address": "", "company": "", "dept": "",
                "title": "", "mail": "", "last_logon": f"ПК: {len(g['pcs'])} (в сети {online_pcs})",
                "last_seen_online": None, "printers": [], "printer": g}

    def _free_pc_rows(self, exclude: set[str] | None = None) -> list[dict]:
        """IP/имя ПК без пользователя в AD: показываем сами ПК из инвентаря (как drill-down)."""
        rows = []
        src = (db.inventory_rows_for_computers(self._printer_comps) if self.printer_query is not None
               else db.inventory_rows_matching(self.query))
        src = [r for r in src if not (exclude and r[0] in exclude)]
        printers_by_pc = db.printers_for_computers([r[0] for r in src]) if src else {}
        for name, ip, on, user, specs, _last, last_logon, seen in src:
            # статус «В сети» из инвентаря — это последний скан, а не «сейчас»: как и для людей, показываем
            # «Проверка…» и досылаем честный ответ вторым шагом (net_ready), если свежего кэша нет
            hit = netutils.cached_network_info(name)
            if hit is not None:
                ip, on = (hit[0] if hit[0] != "Не найден" else ip), hit[1]
            else:
                self._net_pending.add(name)
            rows.append({"entry": None, "login": (user or "").strip() or "—", "fio": "—" if (user or "").strip() else "— (свободный ПК)",
                         "full_fio": user or "", "is_disabled": False, "comp": name, "ip": ip or "Не найден",
                         "is_online": bool(on), "net_pending": hit is None, "phone": "", "ip_phone": "", "office": "", "address": "",
                         "company": "", "dept": "", "title": "", "mail": "", "specs_custom": specs or "",
                         "last_logon": last_logon or "Нет данных", "last_seen_online": seen,
                         "printers": printers_by_pc.get(name, [])})
        return rows

    def _assemble(self, entries, defer_net: bool = False) -> list[dict]:
        """Строки таблицы из записей AD + инвентаря. ``defer_net=True`` — сеть не проверять: для ПК без свежего
        кэша строка получает статус из инвентаря и ``net_pending=True``, а их имена копятся в ``_net_pending``."""
        inv, perm, pcm = db.load_inventory_maps()
        audit = db.load_audit_map() if self.include_archives else {}

        inv_by_user: dict[str, list[str]] = {}
        for comp, data in inv.items():
            if data["user"] and (self.include_archives or data["is_online"]):
                inv_by_user.setdefault(data["user"], []).append(comp)

        by_user: dict[str, list[str]] = {}
        to_check: set[str] = set()
        for e in entries:
            login = db.normalize_login(ad.get_ad_value(e, "sAMAccountName"))
            keys = {login, ad.get_full_fio(e).lower().strip(), ad.get_ad_value(e, "displayName").lower().strip()}
            comps: set[str] = set()
            if login in perm:
                comps.add(perm[login]["comp"])
            if login in pcm:
                comps.add(pcm[login])
            for k in keys:
                comps.update(inv_by_user.get(k, ()))
            if self._printer_comps:  # режим printer: — показываем только ПК с этим принтером
                comps &= self._printer_comps
                if not comps:
                    continue
            if self.include_archives:
                for k in keys:
                    for item in audit.get(k, []):
                        comps.add(item["comp"])
            comps.discard("")
            by_user[login] = sorted(comps) or [""]
            to_check.update(c for c in comps if c)

        printers_by_pc = db.printers_for_computers(to_check) if to_check else {}
        net: dict[str, tuple[str, bool]] = {}
        pending: set[str] = set()
        if to_check and defer_net:
            for pc in to_check:
                hit = netutils.cached_network_info(pc)
                if hit is None:
                    pending.add(pc)
                else:
                    net[pc] = hit
        elif to_check:
            probed = self._probe_network(to_check)
            if probed is None:
                return []
            net = probed
        self._net_pending |= pending

        rows: list[dict] = []
        for e in entries:
            login_orig = ad.get_ad_value(e, "sAMAccountName")
            login = db.normalize_login(login_orig)
            if login not in by_user:
                continue
            fio_full = ad.get_full_fio(e)
            disp = ad.get_ad_value(e, "displayName")
            disabled = ad.is_disabled(e)
            badge_text, badge_kind = ad.account_badge(e)
            base = {
                "entry": e, "login": login_orig, "fio": ad.short_fio(fio_full or disp), "full_fio": fio_full or disp,
                "is_disabled": disabled, "account_text": badge_text, "account_kind": badge_kind,
                "phone": ad.get_ad_value(e, "telephoneNumber"), "ip_phone": ad.get_ad_value(e, "ipPhone"),
                "address": ad.get_ad_value(e, "streetAddress"),
                "office": ad.get_ad_value(e, "physicalDeliveryOfficeName"),
                "company": ad.get_ad_value(e, "company"), "dept": ad.get_ad_value(e, "department"),
                "title": ad.get_ad_value(e, "title"), "mail": ad.get_ad_value(e, "mail"),
            }
            for comp in by_user.get(login, [""]):
                is_pending = comp in pending
                if is_pending:      # до проверки — данные инвентаря (последний скан), а не «не в сети» наугад
                    ip, online = inv.get(comp, {}).get("ip", "Не найден"), bool(inv.get(comp, {}).get("is_online"))
                else:
                    ip, online = net.get(comp, ("Не найден", False))
                last_logon = "Нет данных"
                if comp in inv:
                    last_logon = inv[comp]["last_logon"]
                    if ip == "Не найден":
                        ip = inv[comp]["ip"]
                elif self.include_archives:
                    for k in (login, fio_full.lower(), disp.lower()):
                        for item in audit.get(k, []):
                            if item["comp"] == comp:
                                last_logon = item["date"]
                                if ip == "Не найден":
                                    ip = item["ip"]
                if self.include_archives and comp and last_logon in ("Нет данных", "Неизвестно"):
                    csv_ip, csv_date = netutils.get_pc_info_from_csv(comp, login, disp)
                    if csv_date != "Нет данных":
                        last_logon = csv_date
                    if ip == "Не найден":
                        ip = csv_ip
                rows.append({**base, "comp": comp or "—", "ip": ip if comp else "Не найден",
                             "is_online": online, "net_pending": is_pending, "last_logon": last_logon,
                             "last_seen_online": inv.get(comp, {}).get("last_seen_online"),
                             "printers": printers_by_pc.get(comp, [])})
        if self._printer_comps:  # ПК с принтером, чьих пользователей AD не вернул (отключены/нет) — тоже показать
            shown = {r["comp"] for r in rows}
            rows += self._free_pc_rows(exclude=shown)
        rows.sort(key=lambda r: (not r["is_online"], r["comp"]))
        return rows


# --------------------------------------------------------------------------- сканер парка
_PS_SCANNER = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$data = Get-Content -LiteralPath '__INPUT__' -Encoding UTF8 -Raw | ConvertFrom-Json
$validPrefixes = @(__PREFIXES__)
$compDir = '__COMP_DIR__'; $compExitDir = '__COMPEXIT_DIR__'
$pool = [runspacefactory]::CreateRunspacePool(1, 50); $pool.Open()
$jobs = @()
foreach ($item in $data) {
  $ps = [powershell]::Create(); $ps.RunspacePool = $pool
  [void]$ps.AddScript({
    param($pc, $validPrefixes, $compDir, $compExitDir)
    $actualIp = "Не найден"; $status = "OFFLINE"; $user = ""; $latest = [datetime]::MinValue
    foreach ($dir in @($compDir, $compExitDir)) {
      if (-not $dir) { continue }
      $path = Join-Path $dir "$pc.csv"
      if ([System.IO.File]::Exists($path)) {
        $mtime = [System.IO.File]::GetLastWriteTime($path)
        if ($mtime -gt $latest) {
          $latest = $mtime
          try {
            $line = [System.IO.File]::ReadLines($path, [System.Text.Encoding]::GetEncoding(1251)) | Select-Object -First 1
            if ($line) { $p = $line.Split(@(';', ','))[1]; if ($p) { $user = $p.Trim() } }
          } catch {}
        }
      }
    }
    $lastLogon = if ($latest -eq [datetime]::MinValue) { "Неизвестно" } else { $latest.ToString("dd.MM.yyyy HH:mm") }
    try {
      foreach ($ip in [System.Net.Dns]::GetHostAddresses($pc)) {
        if ($ip.AddressFamily -ne 'InterNetwork') { continue }
        $s = $ip.ToString()
        foreach ($pref in $validPrefixes) { if ($s.StartsWith($pref)) { $actualIp = $s; break } }
        if ($actualIp -ne "Не найден") { break }
      }
    } catch {}
    if ($actualIp -ne "Не найден") {
      try { $r = (New-Object System.Net.NetworkInformation.Ping).Send($actualIp, 300)
            if ($r.Status -eq 'Success') { $status = "ACTIVE" } } catch {}
    }
    [PSCustomObject]@{ Hostname = $pc; ActualIp = $actualIp; Status = $status; User = $user; LastLogon = $lastLogon }
  }).AddArgument($item.Name).AddArgument($validPrefixes).AddArgument($compDir).AddArgument($compExitDir)
  $jobs += [PSCustomObject]@{ PS = $ps; H = $ps.BeginInvoke(); Name = $item.Name }
}
$results = foreach ($j in $jobs) {
  try { $j.PS.EndInvoke($j.H) } catch { [PSCustomObject]@{ Hostname = $j.Name; ActualIp = "Не найден"; Status = "OFFLINE"; User = ""; LastLogon = "Неизвестно" } }
  $j.PS.Dispose()
}
$pool.Close(); $pool.Dispose()
@($results) | ConvertTo-Json -Compress
"""


class _Emitter:
    """Замена pyqtSignal для синхронного режима: ``.emit(x)`` → вызов функции."""

    def __init__(self, fn):
        self.emit = fn


class PCScannerWorker(BaseWorker):
    """Список рабочих станций из AD → DNS/ping/журналы (PowerShell RunspacePool) → pc_inventory."""
    progress = pyqtSignal(str)
    finished_scan = pyqtSignal(int)

    def __init__(self, conn_factory: ConnFactory, parent=None):
        super().__init__(parent)
        self.conn_factory = conn_factory
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def workstation_names(entries) -> list[str]:
        pattern = re.compile(settings.host_pattern, re.IGNORECASE)
        exclude = re.compile(settings.host_exclude, re.IGNORECASE) if settings.host_exclude else None
        out = []
        for e in entries:
            name = ad.get_ad_value(e, "name").rstrip("$").upper()
            if pattern.match(name) and not (exclude and exclude.search(name)):
                out.append(name)
        return out

    def run(self) -> None:
        try:
            self.progress.emit("🔍 Запрос списка рабочих станций из AD…")
            conn = self.conn_factory()
            try:
                entries = ad.paged_search(
                    conn,
                    "(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                    "(!(operatingSystem=*Server*)))",
                    ["name"],
                )
            finally:
                conn.unbind()
            hosts = self.workstation_names(entries)
            if not hosts:
                self.progress.emit("⚠️ Целевые ПК не найдены (проверьте host_pattern в config.ini)")
                self.finished_scan.emit(0)
                return
            if os.name != "nt":
                self.progress.emit("ℹ️ Сканер парка доступен только на Windows (PowerShell)")
                self._index_printers(hosts)
                self.finished_scan.emit(len(hosts))
                return

            self.progress.emit(f"⚡ Опрос {len(hosts)} ПК (DNS, ping, журналы)…")
            results = self._run_powershell(hosts)
            if self.cancelled:
                return
            self.progress.emit("💾 Обновление инвентаря…")
            db.batch_update_inventory(results, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._index_printers(hosts)
            self.finished_scan.emit(len(hosts))
        except Exception as exc:  # noqa: BLE001
            log.exception("PCScannerWorker")
            self.error.emit(f"Ошибка сканирования: {exc}")
            self.finished_scan.emit(0)

    @classmethod
    def scan_once(cls, conn_factory: ConnFactory, progress=None) -> int:
        """Синхронный проход сканера без Qt-потока — для CLI (`adk --scan`) и серверного планировщика."""
        w = cls.__new__(cls)
        w.conn_factory, w._proc, w._cancelled = conn_factory, None, False
        w.progress = _Emitter(progress or (lambda m: log.info("%s", m)))
        conn = conn_factory()
        try:
            entries = ad.paged_search(conn, "(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                                            "(!(operatingSystem=*Server*)))", ["name"])
        finally:
            conn.unbind()
        hosts = w.workstation_names(entries)
        if not hosts:
            return 0
        if os.name == "nt":
            results = w._run_powershell(hosts)
            db.batch_update_inventory(results, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        w._index_printers(hosts)
        return len(hosts)

    def _index_printers(self, hosts: list[str]) -> None:
        """Индекс принтеров из инвентарных CSV — чтобы поиск «printer:» работал без открытия карточек."""
        if not os.path.isdir(settings.invent_hardware_dir):
            return
        self.progress.emit("🖨️ Индексация принтеров из CSV…")
        n = netutils.index_printers(hosts, lambda i, t: self.progress.emit(f"🖨️ Принтеры: {i}/{t}"),
                                    lambda: self.cancelled)
        self.progress.emit(f"🖨️ Принтеры проиндексированы: {n} ПК с CSV")

    def _run_powershell(self, hosts: list[str]) -> list[dict]:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump([{"Name": h} for h in hosts], tmp, ensure_ascii=False)
            input_path = tmp.name
        script = (_PS_SCANNER
                  .replace("__INPUT__", input_path)
                  .replace("__PREFIXES__", ", ".join(f"'{p}'" for p in settings.valid_subnets))
                  .replace("__COMP_DIR__", settings.invent_comp_dir)
                  .replace("__COMPEXIT_DIR__", settings.invent_compexit_dir))
        try:
            self._proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
                creationflags=CREATE_NO_WINDOW,
            )
            out, err = self._proc.communicate(timeout=900)
        finally:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if err.strip():
            log.warning("scanner stderr: %s", err.strip()[:2000])
        if not out.strip():
            return []
        data = json.loads(out)
        return [data] if isinstance(data, dict) else list(data)

    def cancel(self) -> None:
        super().cancel()
        if self._proc and self._proc.poll() is None:
            self._proc.kill()


# --------------------------------------------------------------------------- пинг
class PingWorker(BaseWorker):
    ping_event = pyqtSignal(dict)

    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.target = target
        self._proc: subprocess.Popen | None = None

    def run(self) -> None:
        if not netutils.is_valid_hostname(self.target):
            self.error.emit(f"Недопустимое имя узла: {self.target}")
            return
        args = (["ping", self.target, "-t", "-w", "1000"] if os.name == "nt"
                else ["ping", "-i", "1", self.target])
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          text=True, encoding="cp866" if os.name == "nt" else "utf-8",
                                          errors="ignore", creationflags=CREATE_NO_WINDOW)
            assert self._proc.stdout is not None
            for line in self._proc.stdout:
                if self.cancelled:
                    break
                parsed = netutils.parse_ping_line(line, self.target)
                if parsed:
                    self.ping_event.emit(parsed)
        except OSError as exc:
            self.error.emit(f"Не удалось запустить ping: {exc}")
        finally:
            self.stop_process()

    def stop_process(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.kill()

    def stop(self) -> None:
        self.cancel()
        self.stop_process()
        self.wait(2000)


# --------------------------------------------------------------------------- свободный IP
class FreeIPWorker(BaseWorker):
    """Ищет первый адрес подсети, не отвечающий ни на ICMP, ни по DNS PTR, не известный инвентарю
    и (если настроены ``[Scanner] dhcp_servers``) не занятый по данным DHCP.

    DHCP читается один раз на подсеть (``dhcp.query``) и переиспользуется между «Следующий».
    ``finished_search(ip)`` + ``dhcp_info(dict)`` — вердикт DHCP по найденному адресу.
    """
    progress = pyqtSignal(str)
    finished_search = pyqtSignal(str)
    dhcp_info = pyqtSignal(dict)
    host_checked = pyqtSignal(int, str)   # (последний октет, статус) — для карты подсети в диалоге

    # статусы host_checked: inventory · alive · ptr · lease · reserved · free
    _dhcp_cache: dict[str, dict] = {}   # prefix → данные DHCP (на время работы приложения)

    def __init__(self, prefix: str, start_host: int = 1, parent=None, use_dhcp: bool = True):
        super().__init__(parent)
        self.prefix = prefix.strip().rstrip(".")
        self.start_host = max(1, min(254, start_host))
        self.use_dhcp = use_dhcp and bool(settings.dhcp_servers)
        self.dhcp_data: dict | None = None
        try:
            rows = db.db_execute_with_retry(
                "SELECT ip_address FROM pc_inventory WHERE ip_address IS NOT NULL", fetch="all")
            self.known = {r[0].strip() for r in rows or [] if r[0]}
        except Exception:  # noqa: BLE001
            self.known = set()

    def _load_dhcp(self):
        if not self.use_dhcp:
            return
        cached = self._dhcp_cache.get(self.prefix)
        if cached is None:
            from . import dhcp
            self.progress.emit(f"📡 Читаю DHCP: {', '.join(settings.dhcp_servers)}…")
            cached = dhcp.query(self.prefix)
            if cached.get("scopes") or not cached.get("errors"):
                self._dhcp_cache[self.prefix] = cached
        self.dhcp_data = cached
        for e in cached.get("errors") or []:
            self.progress.emit(f"⚠️ DHCP: {e}")

    def _dhcp_busy(self, ip: str) -> bool:
        if not self.dhcp_data or not self.dhcp_data.get("scopes"):
            return False
        from . import dhcp
        return dhcp.classify(ip, self.dhcp_data)["status"] in ("lease", "reserved")

    def _reason(self, ip: str) -> str:
        """Почему адрес занят (или ``free``). Порядок — от дешёвого к дорогому: инвентарь → DHCP → ICMP → PTR."""
        if self.cancelled:
            return "cancelled"
        if ip in self.known:
            return "inventory"
        if self.dhcp_data and self.dhcp_data.get("scopes"):
            from . import dhcp
            st = dhcp.classify(ip, self.dhcp_data)["status"]
            if st in ("lease", "reserved"):
                return st
        if netutils.is_host_alive(ip, timeout=1.0):
            return "alive"
        import socket
        try:
            socket.gethostbyaddr(ip)
            return "ptr"  # есть PTR — адрес кем-то занят
        except OSError:
            return "free"

    def _is_free(self, ip: str) -> bool:
        return self._reason(ip) == "free"

    def _check(self, ip: str) -> str:
        r = self._reason(ip)
        if r != "cancelled":
            self.host_checked.emit(int(ip.rsplit(".", 1)[1]), r)
        return r

    def verdict(self, ip: str) -> dict:
        """Что DHCP думает о найденном адресе (для подписи в диалоге)."""
        if not self.use_dhcp:
            return {"status": "n/a", "text": "сверка не выполнялась — сервер DHCP не указан в настройках, проверьте адрес на DHCP вручную", "detail": ""}
        if not self.dhcp_data or not self.dhcp_data.get("scopes"):
            return {"status": "unavailable", "text": "DHCP недоступен — сверьте вручную", "detail": "; ".join(self.dhcp_data.get("errors") or []) if self.dhcp_data else ""}
        from . import dhcp
        return dhcp.classify(ip, self.dhcp_data)

    def run(self) -> None:
        if not re.match(r"^\d{1,3}(\.\d{1,3}){2}$", self.prefix):
            # только error: finished_search затёр бы сообщение об ошибке в диалоге
            self.error.emit("Префикс подсети должен быть вида 10.0.2")
            return
        self._load_dhcp()
        batch = 30
        cur = self.start_host
        while cur <= 254 and not self.cancelled:
            end = min(cur + batch - 1, 254)
            self.progress.emit(f"⚡ Проверка {self.prefix}.{cur}–{end}…")
            ips = [f"{self.prefix}.{h}" for h in range(cur, end + 1)]
            with ThreadPoolExecutor(max_workers=batch) as ex:
                verdict = dict(zip(ips, ex.map(self._check, ips)))
            if self.cancelled:
                return
            free = [ip for ip in ips if verdict.get(ip) == "free"]
            if free:
                self.dhcp_info.emit(self.verdict(free[0]))
                self.finished_search.emit(free[0])
                return
            cur += batch
        if not self.cancelled:
            self.finished_search.emit("")


# --------------------------------------------------------------------------- опись в Excel
INVENTORY_COLUMNS = (
    ("fio", "Сотрудник"), ("login", "Логин"), ("title", "Должность"), ("dept", "Отдел"), ("office", "Кабинет"),
    ("comp", "Имя ПК"), ("ip", "IP"), ("online", "В сети"), ("os", "ОС"), ("cpu", "Процессор"), ("ram", "ОЗУ"),
    ("disks", "Диски"), ("printers", "Принтеры"), ("last_seen", "Был в сети"),
)
INVENTORY_DEFAULT = ("fio", "title", "dept", "office", "comp", "ip", "os", "cpu", "ram", "disks", "printers")


def build_inventory_rows(entries: list, cancelled=lambda: False, progress=lambda i, n: None) -> list[dict]:
    """Строки описи по записям AD (чистая функция для предпросмотра и тестов).

    Характеристики — из инвентарных CSV (только чтение), IP/статус — из ``pc_inventory``, принтеры — из кэша.
    """
    rows: list[dict] = []
    people = []
    for e in entries:
        disp = ad.get_ad_value(e, "displayName").strip()
        if disp:
            people.append((e, disp, ad.get_ad_value(e, "sAMAccountName")))
    comps = {login: db.get_computer_by_login(login) for _e, _d, login in people}
    inv, _perm, _pcm = db.load_inventory_maps()
    printers = db.printers_for_computers([c for c in comps.values() if c])
    for i, (e, disp, login) in enumerate(people, 1):
        if cancelled():
            return rows
        if i % 20 == 0:
            progress(i, len(people))
        comp = comps.get(login) or ""
        lines = netutils.get_computer_specs_summary(comp).split("\n") if comp else []
        get = lambda idx, pref: lines[idx].replace(pref, "", 1) if len(lines) > idx else "—"  # noqa: E731
        info = inv.get(db.clean_computer_name(comp), {}) if comp else {}
        rows.append({
            "fio": disp, "login": login, "title": ad.get_ad_value(e, "title"), "dept": ad.get_ad_value(e, "department"),
            "office": ad.get_ad_value(e, "physicalDeliveryOfficeName"),
            "comp": comp or "Не привязан", "ip": info.get("ip") or "—",
            "online": ("да" if info.get("is_online") else "нет") if info else "—",
            "os": get(0, "ОС: "), "cpu": get(1, "CPU: "), "ram": get(2, "ОЗУ: "), "disks": get(3, "Диски: "),
            "printers": "; ".join(netutils.printer_label(p) for p in printers.get(comp, [])) or "—",
            "last_seen": info.get("last_seen_online") or "—",
        })
    return rows


def write_inventory_xlsx(path: str, company: str, rows: list[dict], columns: tuple[str, ...]) -> None:
    """Книга Excel: лист «Опись ПК» (заголовок, автофильтр, закреплённая шапка, зебра, подсветка «Не привязан»)
    и лист «Сводка» (сотрудники/ПК/в сети, распределение по ОС, ОЗУ и отделам)."""
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    labels = dict(INVENTORY_COLUMNS)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Опись ПК"
    ws["A1"] = f"Опись ПК — {company}"
    ws["A1"].font = Font(bold=True, size=14)
    ws["A2"] = f"Сформировано ADK {datetime.now():%d.%m.%Y %H:%M} · сотрудников: {len(rows)}"
    ws["A2"].font = Font(italic=True, color="666666")
    head_row = 4
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for c, key in enumerate(columns, 1):
        cell = ws.cell(row=head_row, column=c, value=labels[key])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border
    zebra = PatternFill(start_color="F2F5FA", end_color="F2F5FA", fill_type="solid")
    warn = PatternFill(start_color="FDE9D9", end_color="FDE9D9", fill_type="solid")
    for r, row in enumerate(rows, head_row + 1):
        for c, key in enumerate(columns, 1):
            cell = ws.cell(row=r, column=c, value=row.get(key, ""))
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=key in ("disks", "printers"))
            if (r - head_row) % 2 == 0:
                cell.fill = zebra
            if key == "comp" and row.get("comp") == "Не привязан":
                cell.fill = warn
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    if rows:
        ws.auto_filter.ref = f"A{head_row}:{get_column_letter(len(columns))}{head_row + len(rows)}"
    widths = {"fio": 30, "login": 14, "title": 24, "dept": 22, "office": 10, "comp": 14, "ip": 15, "online": 8,
              "os": 24, "cpu": 30, "ram": 16, "disks": 36, "printers": 40, "last_seen": 18}
    for c, key in enumerate(columns, 1):
        ws.column_dimensions[get_column_letter(c)].width = widths.get(key, 16)
    ws.row_dimensions[head_row].height = 30

    sm = wb.create_sheet("Сводка")
    total = len(rows)
    with_pc = sum(1 for r in rows if r["comp"] != "Не привязан")
    online = sum(1 for r in rows if r["online"] == "да")
    sm.append(["Показатель", "Значение"])
    for k, v in (("Организация", company), ("Сотрудников", total), ("С привязанным ПК", with_pc),
                 ("Без ПК", total - with_pc), ("ПК в сети на момент описи", online)):
        sm.append([k, v])
    sm.append([])
    for title, key in (("Операционные системы", "os"), ("Объём ОЗУ", "ram"), ("Отделы", "dept")):
        counts: dict[str, int] = {}
        for r in rows:
            counts[r.get(key) or "—"] = counts.get(r.get(key) or "—", 0) + 1
        sm.append([title, "Сотрудников"])
        sm.cell(row=sm.max_row, column=1).font = Font(bold=True)
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1]):
            sm.append([k, v])
        sm.append([])
    for cell in sm[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F3864", end_color="1F3864", fill_type="solid")
    sm.column_dimensions["A"].width = 40
    sm.column_dimensions["B"].width = 16
    wb.save(path)


class InventoryWorker(BaseWorker):
    """Два режима: ``preview=True`` — только собрать строки (``rows_ready``); иначе — собрать (или взять
    готовые ``rows``) и записать Excel (``finished_export(ok, путь|ошибка)``)."""
    progress = pyqtSignal(str)
    rows_ready = pyqtSignal(list)
    finished_export = pyqtSignal(bool, str)

    def __init__(self, conn_factory: ConnFactory, company: str, output_dir: str, parent=None,
                 columns: tuple[str, ...] = INVENTORY_DEFAULT, preview: bool = False, rows: list[dict] | None = None):
        super().__init__(parent)
        self.conn_factory, self.company, self.output_dir = conn_factory, company, output_dir
        self.columns, self.preview, self.rows = tuple(columns), preview, rows

    def _fetch(self) -> list:
        conn = self.conn_factory()
        try:
            return ad.paged_search(
                conn,
                f"(&(objectClass=user)(company={ad.escape_filter_chars(self.company)})"
                "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))",
                ["displayName", "sAMAccountName", "title", "department", "physicalDeliveryOfficeName"],
            )
        finally:
            conn.unbind()

    def run(self) -> None:
        try:
            rows = self.rows
            if rows is None:
                self.progress.emit("Получение сотрудников из AD…")
                entries = self._fetch()
                if not entries:
                    self.finished_export.emit(False, f"В организации «{self.company}» нет сотрудников")
                    return
                rows = build_inventory_rows(entries, cancelled=lambda: self.cancelled,
                                            progress=lambda i, n: self.progress.emit(f"Обработано {i}/{n}…"))
                if self.cancelled:
                    return
            if self.preview:
                self.rows_ready.emit(rows)
                return
            import importlib.util
            if importlib.util.find_spec("openpyxl") is None:
                self.finished_export.emit(False, "Не установлен openpyxl: pip install openpyxl")
                return
            safe = re.sub(r'[\\/*?:"<>|«»„“]', "", self.company).strip().replace(" ", "_")
            path = os.path.join(self.output_dir, f"Опись_ПК_{safe}_{datetime.now():%Y-%m-%d_%H-%M-%S}.xlsx")
            self.progress.emit("Запись Excel…")
            write_inventory_xlsx(path, self.company, rows, self.columns)
            self.finished_export.emit(True, path)
        except Exception as exc:  # noqa: BLE001
            log.exception("InventoryWorker")
            self.finished_export.emit(False, f"Ошибка описи: {exc}")
