"""Фоновые потоки. Каждый воркер имеет сигнал ``error`` — ошибки не глотаются."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from . import ad, db, netutils
from .config import CREATE_NO_WINDOW, SEARCH_RESULT_LIMIT, settings
from .i18n import tr  # noqa: E402

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
        self._printer_pending: set[str] = set()  # IP принтеров, чья доступность проверяется вторым шагом (3.5.6)

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
        if self.printer_query is not None:
            # 3.6.3: запрос «printer:…» (или клик по бейджу принтера) — только сам принтер,
            # кто подключён — в его инспекторе, а не списком людей рядом
            if self.cancelled:
                return
            self.results_ready.emit(printer_rows, self.raw_query, False)
            self._emit_net()
            return
        if flt is None:
            # запрос — просто IP принтера: показываем только сам принтер (кто подключён — в его инспекторе),
            # иначе рядом появлялись «пустые» строки ПК без ФИО
            free = [] if (printer_rows and self.printer_query is None) else self._free_pc_rows()
            if not printer_rows and not free:
                printer_rows = self._discovered_printer_rows()   # 3.5.10: принтера нет в базе — спросим сам адрес
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
        if not rows and not printer_rows and self.printer_query is None and not truncated:
            # в AD никого, но запрос похож на имя ПК из инвентаря (свободный ПК без пользователя, «WS-1351») —
            # показываем сам ПК, как и при поиске по IP; раньше здесь было честное, но бесполезное «Найдено: 0»
            rows = self._free_pc_rows()
        self.results_ready.emit(printer_rows + rows, self.raw_query, truncated)
        self._emit_net()

    @staticmethod
    def apply_net(rows: list[dict], net: dict) -> None:
        """Вписать результат второго шага в строки: ПК — ``{имя: (ip, online)}``, принтеры —
        ``{"printer:<ip>": (alive, probe|None)}``. Что не пришло (отмена/ошибка) — остаются данные инвентаря."""
        for r in rows:
            if r.get("kind") == "printer":
                hit = net.get(f"printer:{r.get('ip')}")
                if hit is not None:
                    r["is_online"], r["probe"] = hit[0], hit[1]
            else:
                comp = db.clean_computer_name(r.get("comp") or "")
                if comp in net:
                    ip, online = net[comp]
                    if ip != "Не найден":
                        r["ip"] = ip
                    r["is_online"] = online
            r["net_pending"] = False

    def resolve_pending(self, rows: list[dict]) -> list[dict]:
        """Синхронный вариант второго шага (CLI): проверить сеть у ожидающих ПК/принтеров и вписать ответ в строки."""
        if self._net_pending or self._printer_pending:
            self.apply_net(rows, self._probe_network(self._net_pending) or {})
            self._net_pending = set()
            self._printer_pending = set()
        return rows

    def _emit_net(self) -> None:
        """Второй шаг: проверить сеть у ПК из ``_net_pending`` и принтеров из ``_printer_pending``,
        дослать net_ready (если поиск не отменён)."""
        if not self._net_pending and not self._printer_pending:
            return
        net = self._probe_network(self._net_pending)
        if net is not None and not self.cancelled:
            self.net_ready.emit(net, self.raw_query)

    def _probe_network(self, comps: set[str]) -> dict | None:
        """DNS + доступность для набора ПК и доступность/«а принтер ли это» для отложенных принтеров —
        параллельно, одним пулом; None — поиск отменён."""
        net: dict = {}
        ips = sorted(self._printer_pending)
        want_probe = bool(ips) and netutils.is_ip_query(self.printer_query if self.printer_query is not None else self.query)
        with ThreadPoolExecutor(max_workers=min(50, max(1, len(comps) + len(ips)))) as ex:
            futs = {ex.submit(netutils.get_computer_network_info, pc): ("pc", pc) for pc in comps}
            futs.update({ex.submit(netutils.is_printer_alive, ip): ("pr", ip) for ip in ips})
            if want_probe:
                # запрос — конкретный IP: честно проверяем, принтер ли это на самом деле (порты печати / веб-панель)
                futs.update({ex.submit(netutils.probe_printer, ip): ("probe", ip) for ip in ips})
            alive: dict[str, bool] = {}
            probes: dict[str, dict | None] = {}
            for f in as_completed(futs):
                if self.cancelled:
                    return None
                kind, key = futs[f]
                try:
                    res = f.result()
                except Exception:  # noqa: BLE001
                    res = {"pc": ("Не найден", False), "pr": False,
                           "probe": {"alive": False, "is_printer": None, "evidence": "проверка не удалась"}}[kind]
                if kind == "pc":
                    net[key] = res
                elif kind == "pr":
                    alive[key] = res
                else:
                    probes[key] = res
        for ip in ips:
            net[f"printer:{ip}"] = (alive.get(ip, False), probes.get(ip))
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
        # 3.5.6: доступность принтера (TCP 9100/631/80 + ping, до ~1,5 с на молчащий адрес) и проверка «а принтер ли
        # это» больше не задерживают появление строк — как и у ПК, они проверяются вторым шагом (net_ready),
        # а строка до ответа показывает «Проверка…». Свежий кэш (≤ NET_CACHE_TTL) используется сразу.
        rows = []
        for g in groups:
            hit = netutils.cached_printer_alive(g["ip"]) if g["ip"] else None
            pending = bool(g["ip"]) and hit is None
            row = self.printer_row(g, bool(hit))
            row["probe"] = None
            row["net_pending"] = pending
            if pending:
                self._printer_pending.add(g["ip"])
            rows.append(row)
        return rows

    PRINTER_ROWS_LIMIT = 20

    def _discovered_printer_rows(self) -> list[dict]:
        """Полный IP, за которым в базе ничего нет: проверяем сам адрес (порты печати/SNMP/веб-панель) и, если это
        принтер, показываем его строкой — с моделью и честной пометкой «в базе не числится» (3.5.10)."""
        q = self.printer_query if self.printer_query is not None else self.query
        if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", q or ""):
            return []
        try:
            d = netutils.discover_printer(q)
        except Exception as exc:  # noqa: BLE001
            log.debug("discover_printer: %s", exc)
            return []
        if not d or self.cancelled:
            return []
        g = {"name": d["name"], "kind": "network", "ip": d["ip"], "port": d["host"] or "", "pcs": [], "discovered": True,
             "source": d["source"]}
        row = self.printer_row(g, True)
        row["probe"] = d["probe"]
        row["net_pending"] = False
        row["last_logon"] = f"в базе не числится · найден по адресу ({d['source']})"
        return [row]

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
        # 3.5.10: ПК, за которым человека видели за последние полгода (или который сам был в сети за полгода),
        # — не архив: он показывается всегда. «Архивы» добавляют только связки старше db.ARCHIVE_DAYS.
        recent = db.recent_links()

        inv_by_user: dict[str, list[str]] = {}
        for comp, data in inv.items():
            if data["user"]:
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
            for k in keys:
                comps.update(recent.get(k, {}).keys())
            if not comps:
                db_comp = db.get_computer_by_login(login)
                if db_comp:
                    comps.add(db_comp)
                ad_ws = ad.get_ad_value(e, "userWorkstations")
                if ad_ws:
                    c_clean = db.clean_computer_name(ad_ws.split(",")[0])
                    if c_clean:
                        comps.add(c_clean)
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
                else:
                    for k in (login, fio_full.lower(), disp.lower()):
                        when = recent.get(k, {}).get(comp)
                        if when:
                            last_logon = when
                        if self.include_archives:
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
$data = Get-Content -LiteralPath '__INPUT__' -Encoding UTF8 -Raw | ConvertFrom-Json
$validPrefixes = @(__PREFIXES__)
$compDir = '__COMP_DIR__'; $compExitDir = '__COMPEXIT_DIR__'
$pingMs = __PING_MS__          # таймаут пинга: полный опрос 300 мс, фоновый — 100 мс
$askUser = __ASK_USER__        # WMI «кто за ПК» — только полный/первичный опрос (первичная связка ПК↔человек);
                               # фоновый скан не дёргает WMI на каждом запуске (3.9.3)
$pool = [runspacefactory]::CreateRunspacePool(1, 50); $pool.Open()
$jobs = @()
foreach ($item in $data) {
  $ps = [powershell]::Create(); $ps.RunspacePool = $pool
  [void]$ps.AddScript({
    param($pc, $validPrefixes, $compDir, $compExitDir, $pingMs, $askUser)
    $actualIp = "Не найден"; $status = "OFFLINE"; $user = ""; $userSrc = ""; $latest = [datetime]::MinValue
    foreach ($dir in @($compDir, $compExitDir)) {
      if (-not $dir) { continue }
      $path = Join-Path $dir "$pc.csv"
      if ([System.IO.File]::Exists($path)) {
        $mtime = [System.IO.File]::GetLastWriteTime($path)
        if ($mtime -gt $latest) {
          $latest = $mtime
          try {
            $line = [System.IO.File]::ReadLines($path, [System.Text.Encoding]::GetEncoding(1251)) | Select-Object -First 1
            if ($line) { $p = $line.Split(@(';', ','))[1]; if ($p) { $user = $p.Trim(); $userSrc = "csv" } }
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
      try { $r = (New-Object System.Net.NetworkInformation.Ping).Send($actualIp, $pingMs)
            if ($r.Status -eq 'Success') { $status = "ACTIVE" } } catch {}
    }
    # 3.9.1: кто за ПК — спросить напрямую у машины (WMI); работает для онлайн-ПК и не зависит
    # ни от обратного DNS, ни от прав на журнал контроллера домена.
    # 3.9.3: только при $askUser (полный/первичный опрос) — фоновый скан без постоянных WMI-запросов.
    # 3.9.5: два транспорта — WinRM (Get-CimInstance), при отказе DCOM (Get-WmiObject): WinRM на
    # рабочих станциях часто выключен, и один WinRM оставлял «кто за ПК» пустым. Ответ машины
    # приоритетнее инвентарного CSV (его данные могли устареть); DOMAIN\login → login.
    # 3.9.6: UserName пуст (RDP-сессии) → владелец explorer.exe; источник ответа — в UserSrc
    # (wmi/csv): итог «кто за ПК» виден в строке статуса после полного опроса.
    if ($askUser -and $status -eq 'ACTIVE') {
      try {
        $u = $null
        try { $cs = Get-CimInstance -ClassName Win32_ComputerSystem -ComputerName $pc -OperationTimeoutSec 3 -ErrorAction Stop
              if ($cs -and $cs.UserName) { $u = $cs.UserName } } catch {}
        if (-not $u) {
          try { $wmi = Get-WmiObject -Class Win32_ComputerSystem -ComputerName $pc -ErrorAction Stop
                if ($wmi -and $wmi.UserName) { $u = $wmi.UserName } } catch {}
        }
        if (-not $u) {
          try { $o = @(Get-CimInstance -ClassName Win32_Process -Filter "Name='explorer.exe'" -ComputerName $pc -OperationTimeoutSec 3 -ErrorAction Stop | Invoke-CimMethod -MethodName GetOwner)[0]
                if ($o -and $o.User) { $u = $o.Domain + '/' + $o.User } } catch {}
        }
        if ($u) { $user = ($u -split '[\\/]')[-1].Trim(); $userSrc = "wmi" }
      } catch {}
    }
    [PSCustomObject]@{ Hostname = $pc; ActualIp = $actualIp; Status = $status; User = $user; UserSrc = $userSrc; LastLogon = $lastLogon }
  }).AddArgument($item.Name).AddArgument($validPrefixes).AddArgument($compDir).AddArgument($compExitDir).AddArgument($pingMs).AddArgument($askUser)
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


_COMPUTERS_FILTER = ("(&(objectClass=computer)(!(userAccountControl:1.2.840.113556.1.4.803:=2))"
                     "(!(operatingSystem=*Server*)))")


def host_list_from_ad(conn_factory: ConnFactory, progress=None) -> tuple[list[str], str]:
    """Список рабочих станций парка (3.11.0): сначала LDAP (ldap3), при пустом результате или ошибке —
    ADSI через PowerShell (так получала список старая программа — путь проверен в тех же сетях).

    Возвращает ``(hosts, путь)``: путь — ``'ldap'`` или ``'adsi'``, чтобы показать пользователю,
    откуда взяли список, если что-то пойдёт не так.
    """
    def say(msg: str):
        if progress:
            progress(msg)

    entries: list = []
    err = ""
    try:
        say("🔍 Запрос списка рабочих станций из AD (LDAP)…")
        conn = conn_factory()
        try:
            entries = ad.paged_search(conn, _COMPUTERS_FILTER, ["name"])
        finally:
            conn.unbind()
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
    hosts = PCScannerWorker.workstation_names(entries)
    if hosts:
        return hosts, "ldap"

    say("⚠️ LDAP не дал ни одного ПК" + (f": {err}" if err else "")
        + " — пробую запасной путь ADSI (так получала список старая программа)…")
    try:
        names = netutils.adsi_computer_names()
    except Exception as exc:  # noqa: BLE001
        say(f"⚠️ ADSI тоже не смог: {exc}")
        names = []
    return PCScannerWorker.filter_host_names(names), "adsi"


_DC_LOGON_PS = r"""
$ErrorActionPreference = 'Stop'
try {
  $ev = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4768} -ComputerName '__DC__' -MaxEvents __MAX__
  $out = New-Object System.Collections.Generic.List[object]
  foreach ($e in $ev) {
    try {
      $x = [xml]$e.ToXml()
      $d = @{}
      foreach ($n in $x.Event.EventData.Data) { $d[$n.Name] = [string]$n.'#text' }
      if ($d['ResultCode'] -notin @('0x0','0')) { continue }
      $u = $d['TargetUserName']; $ip = $d['IpAddress']
      if (-not $u -or $u.EndsWith('$')) { continue }
      if (-not $ip -or $ip -in @('-','::1','127.0.0.1','0.0.0.0')) { continue }
      $out.Add(@{u=$u; ip=$ip.Replace('::ffff:','')})
    } catch {}
  }
  $out | ConvertTo-Json -Compress
} catch { Write-Output ('DCLOGON_ERROR: ' + $_.Exception.Message); exit 1 }
"""


def dc_logon_events(dc_host: str, max_events: int = 20000, timeout: int = 240) -> list[tuple[str, str]]:
    """Пары (логин, ip) из журнала Security контроллера домена — события 4768, запрос билета Kerberos.

    Именно так определяла «кто за каким ПК» старая программа (win32evtlog → журнал КД) — путь,
    проверенный в тех же сетях, где инвентарных CSV может не быть. У нас — PowerShell Get-WinEvent,
    без pywin32. События идут от свежих к старым. Ошибка — исключение с понятной причиной.
    """
    from . import psrun
    if not dc_host:
        raise RuntimeError("dc_host не задан (config.ini → [AD])")
    script = _DC_LOGON_PS.replace("__DC__", dc_host).replace("__MAX__", str(max_events))
    res = psrun.run(script, timeout=timeout)
    if not res.ok or (res.stdout or "").startswith("DCLOGON_ERROR"):
        raise RuntimeError((res.error or res.stdout or "пустой ответ").strip()[:200])
    txt = (res.stdout or "").strip()
    if not txt:
        return []
    data = json.loads(txt)
    if isinstance(data, dict):
        data = [data]
    out = []
    for it in data:
        u, ip = str(it.get("u", "")).strip(), str(it.get("ip", "")).strip()
        if u and ip:
            out.append((u, ip))
    return out


def merge_dc_logons(results: list[dict], pairs: list[tuple[str, str]], progress=None) -> list[dict]:
    """Вписать пользователей из событий контроллера домена тем ПК, где опрос не нашёл «кто за ПК».

    ip → имя ПК обратным DNS (как в старой программе); если на ПК видели несколько входов —
    берётся самый свежий (события отсортированы от новых к старым).
    """
    import socket
    if not pairs or not results:
        return results

    def say(msg: str):
        if progress:
            progress(msg)

    say(f"👥 Входы с контроллера домена: {len(pairs)} — сопоставляю с ПК…")

    def resolve(ip: str) -> str:
        try:
            return socket.gethostbyaddr(ip)[0].split(".")[0].upper()
        except OSError:
            return ""

    with ThreadPoolExecutor(max_workers=32) as ex:
        names = list(ex.map(resolve, [ip for _u, ip in pairs]))
    pc_user: dict[str, str] = {}
    for (u, _ip), host in zip(pairs, names):
        if host and host not in pc_user:
            pc_user[host] = u
    for r in results:
        h = (r.get("Hostname") or "").upper()
        if h and not (r.get("User") or "").strip() and h in pc_user:
            r["User"] = pc_user[h]
            r["LastLogon"] = "по журналу контроллера домена"
    filled = sum(1 for r in results if (r.get("User") or "").strip())
    say(f"👥 За ПК видны пользователи: {filled} из {len(results)}")
    return results


def enrich_with_dc_logons(results: list[dict], progress=None) -> list[dict]:
    """Дозаполнить «кто за ПК» из журнала контроллера домена. Ошибка чтения — не критична (шаг пропускается)."""
    if not results:
        return results

    def say(msg: str):
        if progress:
            progress(msg)
    try:
        pairs = dc_logon_events(settings.dc_host)
    except Exception as exc:  # noqa: BLE001
        say(f"👥 Журнал контроллера домена недоступен ({str(exc)[:100]}) — «кто за ПК» по инвентарю/опросу")
        return results
    return merge_dc_logons(results, pairs, progress)


class PCScannerWorker(BaseWorker):
    """Список рабочих станций из AD → DNS/ping/журналы (PowerShell RunspacePool) → pc_inventory."""
    progress = pyqtSignal(str)
    finished_scan = pyqtSignal(int)

    def __init__(self, conn_factory: ConnFactory, parent=None, deep: bool = True):
        """``deep=True`` — полный/первичный опрос: пинг 300 мс + WMI «кто за ПК» у онлайн-машин + журнал КД (4768).
        ``deep=False`` — фоновый по расписанию (3.9.1): лёгкий, как в 3.8 — DNS + сверка с базой + пинг 100 мс;
        БЕЗ WMI и БЕЗ чтения журнала КД (3.9.3: WMI — только полный опрос, для первичной связки ПК↔человек;
        фоновый не должен дёргать WMI на каждом запуске)."""
        super().__init__(parent)
        self.conn_factory = conn_factory
        self.deep = deep

    @staticmethod
    def _mask_regex(mask: str) -> re.Pattern | None:
        """Маска имён ПК → regex: ? = одна цифра, * = любые символы, остальное буквально. '' → None (все ПК)."""
        if not mask or not mask.strip():
            return None
        parts = []
        for m in (x.strip() for x in mask.split(",")):
            if not m:
                continue
            rx = "".join(r"\d" if c == "?" else ".*" if c == "*" else re.escape(c) for c in m.upper())
            parts.append(f"(?:{rx})")
        return re.compile("^(?:" + "|".join(parts) + ")$", re.IGNORECASE) if parts else None

    @staticmethod
    def workstation_names(entries) -> list[str]:
        return PCScannerWorker.filter_host_names(
            [ad.get_ad_value(e, "name").rstrip("$").upper() for e in entries])

    @staticmethod
    def filter_host_names(names: list[str]) -> list[str]:
        """Применить к списку имён главное: маску парка (если задана) или host_pattern + exclude."""
        pattern = re.compile(settings.host_pattern, re.IGNORECASE)
        exclude = re.compile(settings.host_exclude, re.IGNORECASE) if settings.host_exclude else None
        mask = PCScannerWorker._mask_regex(settings.host_mask)
        out = []
        for raw in names:
            name = raw.rstrip("$").upper()
            # 3.9.0: заданная маска — главный фильтр: она определяет парк (host_pattern не действует,
            # иначе дефолтный ^(WS-\d+|PC-.*)$ вырезал бы ПК серий, которых нет в шаблоне). exclude — всегда.
            if mask is not None:
                if mask.match(name) and not (exclude and exclude.search(name)):
                    out.append(name)
            elif pattern.match(name) and not (exclude and exclude.search(name)):
                out.append(name)
        return out

    def run(self) -> None:
        try:
            hosts, via = host_list_from_ad(self.conn_factory,
                                           lambda m: self.progress.emit(m))
            if not hosts:
                # 3.11.0: честная причина — какой фильтр отсек всех и какой путь списка пробовался
                if settings.host_mask.strip():
                    self.progress.emit(f"⚠️ Не найдено ни одного ПК парка: маска «{settings.host_mask}» "
                                       f"([Scanner] host_mask); список ПК брался через {via.upper()}")
                else:
                    self.progress.emit(f"⚠️ Целевые ПК не найдены (host_pattern = {settings.host_pattern}); "
                                       f"список ПК брался через {via.upper()}")
                self.finished_scan.emit(0)
                return
            self.progress.emit(f"⚡ Опрос {len(hosts)} ПК (DNS, ping{' , журнал КД' if self.deep else ''})…")
            results = self.probe_hosts(hosts, ping_ms=300 if self.deep else 100)
            if self.cancelled:
                return
            if self.deep:
                results = enrich_with_dc_logons(results, lambda m: self.progress.emit(m))
            self.progress.emit("💾 Обновление инвентаря…")
            db.batch_update_inventory(results, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._index_printers(hosts)
            self.finished_scan.emit(len(hosts))
        except Exception as exc:  # noqa: BLE001
            log.exception("PCScannerWorker")
            self.error.emit(tr("Ошибка сканирования: {0}").format(exc))
            self.finished_scan.emit(0)

    @classmethod
    def scan_once(cls, conn_factory: ConnFactory, progress=None, deep: bool = True) -> int:
        """Синхронный проход сканера без Qt-потока — для CLI (`adk --scan`) и серверного планировщика.
        ``deep=False`` (3.9.4) — лёгкий проход повторных циклов сервера: DNS + пинг 100 мс + сверка
        с базой, без WMI «кто за ПК» и журнала КД — та же семантика, что фоновый скан GUI (3.9.3)."""
        w = cls.__new__(cls)
        w.conn_factory, w._cancelled = conn_factory, False
        w.progress = _Emitter(progress or (lambda m: log.info("%s", m)))
        w.deep = deep   # CLI --scan и первичное наполнение — True; повторные циклы сервера — False
        hosts, via = host_list_from_ad(conn_factory, w.progress.emit)
        if not hosts:
            if settings.host_mask.strip():
                raise RuntimeError(f"ни один ПК домена не подходит под маску парка «{settings.host_mask}» "
                                   f"([Scanner] host_mask в config.ini); список ПК брался через {via.upper()}")
            raise RuntimeError(f"ни один ПК домена не подходит под host_pattern "
                               f"«{settings.host_pattern}» (config.ini → [Scanner]); исключение: «{settings.host_exclude}»; "
                               f"список ПК брался через {via.upper()}")
        w.progress.emit(tr("⚡ Опрос {0} ПК (DNS, ping, журналы входов)…").format(len(hosts)))
        results = w.probe_hosts(hosts, ping_ms=300 if deep else 100)
        if deep:   # 3.9.4: журнал КД — только полный опрос, лёгкий проход его не читает
            results = enrich_with_dc_logons(results, w.progress.emit)
            uw = sum(1 for r in results if (r.get("UserSrc") or "") == "wmi")
            uc = sum(1 for r in results if (r.get("UserSrc") or "") == "csv")
            uo = sum(1 for r in results if (r.get("User") or "").strip() and not r.get("UserSrc"))
            on = sum(1 for r in results if r.get("Status") == "ACTIVE")
            if on:
                w.progress.emit(f"👥 Кто за ПК: машина ответила {uw} · CSV {uc} · журнал КД {uo} · "
                                f"не отвечено {on - uw - uc - uo} из {on} включённых")
        db.batch_update_inventory(results, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        w._index_printers(hosts)
        return len(hosts)

    def _wmi_user_default(self) -> bool:
        """WMI «кто за ПК» включён только в полном опросе (self.deep). Объекты, созданные через
        ``__new__`` без ``__init__`` (FullScanWorker, scan_once) — тоже полный опрос: атрибуты
        у такого QObject читать нельзя (RuntimeError), считаем глубоким."""
        try:
            return bool(getattr(self, "deep", True))
        except RuntimeError:
            return True

    def probe_hosts(self, hosts: list[str], ping_ms: int = 300, wmi_user: bool | None = None) -> list[dict]:
        """DNS + ping + журналы входов для каждого ПК. На Windows — PowerShell (RunspacePool, 50 потоков, журналы
        logon-скрипта); если PowerShell не дал результата или мы не на Windows — запасной путь на Python
        (DNS + TCP 445/ICMP, без журналов). Пустой ответ больше не считается успехом.

        ``wmi_user`` — дёргать ли WMI «кто за ПК» у онлайн-машин: None → по ``self.deep``
        (полный/первичный опрос — да, фоновый — нет); объекты, созданные через ``__new__``
        (FullScanWorker, scan_once), считаются полным опросом."""
        if wmi_user is None:
            wmi_user = self._wmi_user_default()
        if os.name == "nt":
            try:
                results = self._run_powershell(hosts, ping_ms, wmi_user)
                if results:
                    return results
                self.progress.emit("⚠️ PowerShell не вернул данных — опрашиваю ПК средствами Python…")
            except Exception as exc:  # noqa: BLE001
                log.warning("scanner powershell: %s", exc)
                self.progress.emit(tr("⚠️ PowerShell: {0} — опрашиваю ПК средствами Python…").format(str(exc)[:120]))
        return self._probe_python(hosts)

    def _probe_python(self, hosts: list[str]) -> list[dict]:
        """Запасной сканер без PowerShell: DNS → IP из valid_subnets, доступность по TCP 445 / ICMP."""
        import socket
        prefixes = tuple(settings.valid_subnets or ())

        def one(pc: str) -> dict:
            ip = "Не найден"
            try:
                for fam, _t, _p, _c, addr in socket.getaddrinfo(pc, None, socket.AF_INET):
                    cand = addr[0]
                    if not prefixes or cand.startswith(prefixes):
                        ip = cand
                        break
            except OSError:
                pass
            status = "ACTIVE" if ip != "Не найден" and netutils.is_host_alive(ip, timeout=1.0) else "OFFLINE"
            return {"Hostname": pc, "ActualIp": ip, "Status": status, "User": "", "LastLogon": "Неизвестно"}

        out: list[dict] = []
        total = len(hosts)
        with ThreadPoolExecutor(max_workers=48) as ex:
            futs = {ex.submit(one, h): h for h in hosts}
            for i, f in enumerate(as_completed(futs), 1):
                if self.cancelled:
                    break
                out.append(f.result())
                if i % 25 == 0 or i == total:
                    self.progress.emit(tr("⚡ Опрос ПК: {0}/{1}").format(i, total))
        return out

    def _index_printers(self, hosts: list[str]) -> None:
        """Индекс принтеров из инвентарных CSV — чтобы поиск «printer:» работал без открытия карточек."""
        if not os.path.isdir(settings.invent_hardware_dir):
            return
        self.progress.emit("🖨️ Индексация принтеров из CSV…")
        n = netutils.index_printers(hosts, lambda i, t: self.progress.emit(tr("🖨️ Принтеры: {0}/{1}").format(i, t)),
                                    lambda: self.cancelled)
        self.progress.emit(tr("🖨️ Принтеры проиндексированы: {0} ПК с CSV").format(n))

    def _run_powershell(self, hosts: list[str], ping_ms: int = 300, wmi_user: bool = True) -> list[dict]:
        """PowerShell-сканер через :mod:`psrun` (3.6.1; раньше — прямой ``powershell -Command <текст>``, который из
        exe без консоли мог завершиться молча). Ошибка → исключение с понятной причиной, пустой ответ → [].
        3.9.3: ``ping_ms`` и ``wmi_user`` реально подставляются в скрипт (раньше $pingMs приходил пустым)."""
        import tempfile
        from . import psrun

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump([{"Name": h} for h in hosts], tmp, ensure_ascii=False)
            input_path = tmp.name
        script = (_PS_SCANNER
                  .replace("__INPUT__", input_path)
                  .replace("__PREFIXES__", ", ".join(f"'{p}'" for p in settings.valid_subnets))
                  .replace("__COMP_DIR__", settings.invent_comp_dir)
                  .replace("__COMPEXIT_DIR__", settings.invent_compexit_dir)
                  .replace("__PING_MS__", str(int(ping_ms)))
                  .replace("__ASK_USER__", "True" if wmi_user else "False"))
        try:
            res = psrun.run(script, timeout=900, cancelled=lambda: self.cancelled)
        finally:
            try:
                os.remove(input_path)
            except OSError:
                pass
        if not res.ok:
            raise RuntimeError(res.error)
        data = json.loads(res.stdout)
        return [data] if isinstance(data, dict) else list(data)

    def cancel(self) -> None:
        super().cancel()          # psrun.run сам убьёт процесс по cancelled()


# --------------------------------------------------------------------------- пинг
class PingWorker(BaseWorker):
    ping_event = pyqtSignal(dict)

    def __init__(self, target: str, parent=None):
        super().__init__(parent)
        self.target = target
        self._proc: subprocess.Popen | None = None

    def run(self) -> None:
        if not netutils.is_valid_hostname(self.target):
            self.error.emit(tr("Недопустимое имя узла: {0}").format(self.target))
            return
        # -w 4000 — как у обычного ping в cmd (раньше стояло 1000: ответ за 1,2 с по Wi-Fi считался потерей,
        # и у живого ПК появлялся красный столбец; теперь цифры окна совпадают с тем, что админ видит в консоли)
        args = (["ping", self.target, "-t", "-w", "4000"] if os.name == "nt"
                else ["ping", "-i", "1", "-W", "4", self.target])
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
            self.error.emit(tr("Не удалось запустить ping: {0}").format(exc))
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
_FREEIP_TEXT = {
    "inventory": "занят ПК парка", "lease": "аренда DHCP", "reserved": "резерв DHCP",
    "alive": "отвечает на ping", "ptr": "есть имя в DNS", "free": "свободен",
}


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
    checking = pyqtSignal(int)            # ячейка проверяется прямо сейчас (3.8.0: рамка на карте)

    # статусы host_checked: inventory · alive · ptr · lease · reserved · free
    _dhcp_cache: dict[str, dict] = {}   # prefix → данные DHCP (на время работы приложения)

    def __init__(self, prefix: str, start_host: int = 1, parent=None, use_dhcp: bool = True):
        super().__init__(parent)
        self.prefix = prefix.strip().rstrip(".")
        self.start_host = max(1, min(254, start_host))
        self.use_dhcp = use_dhcp and bool(settings.dhcp_servers)
        self.dhcp_data: dict | None = None
        self._settled = False          # ответ уже отдан — начатые проверки завершаются без работы и без сигналов
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
            self.progress.emit(tr("⚠️ DHCP: {0}").format(e))

    def _dhcp_busy(self, ip: str) -> bool:
        if not self.dhcp_data or not self.dhcp_data.get("scopes"):
            return False
        from . import dhcp
        return dhcp.classify(ip, self.dhcp_data)["status"] in ("lease", "reserved")

    def _cheap(self, ip: str) -> str | None:
        """Дешёвые источники без сети: инвентарь ADK → DHCP. None — придётся спрашивать у сети."""
        if ip in self.known:
            return "inventory"
        if self.dhcp_data and self.dhcp_data.get("scopes"):
            from . import dhcp
            st = dhcp.classify(ip, self.dhcp_data)["status"]
            if st in ("lease", "reserved"):
                return st
        return None

    def _reason(self, ip: str) -> str:
        """Почему адрес занят (или ``free``). Порядок — от дешёвого к дорогому: инвентарь → DHCP → ICMP → PTR."""
        r = self._cheap(ip)
        if r:
            return r
        if self.cancelled or self._settled:
            return "cancelled"
        if netutils.is_host_alive(ip, timeout=1.0):
            return "alive"
        if self.cancelled or self._settled:
            return "cancelled"
        return "ptr" if _ptr_exists(ip) else "free"  # есть PTR — адрес кем-то занят

    def _is_free(self, ip: str) -> bool:
        return self._reason(ip) == "free"

    def _check(self, ip: str) -> str:
        """Проверка одного адреса — БЕЗ эмиссии сигналов: статусы показывает только run(), строго
        по порядку адресов (3.8.0). Ping и PTR стартуют **одновременно** — «мёртвый» адрес стоит
        ``max(ping, PTR)`` ≈ 1,5 с вместо суммы ≈ 2,5 с, поэтому поиск идёт заметно быстрее."""
        r = self._cheap(ip)
        if r:
            return r
        if self.cancelled or self._settled:
            return "cancelled"
        ptr = _ptr_start(ip)                     # PTR пошёл сразу — параллельно ping
        t0 = time.monotonic()
        alive = netutils.is_host_alive(ip, timeout=1.0)
        if self.cancelled or self._settled:
            ptr.cancel()
            return "cancelled"
        if alive:
            ptr.cancel()
            return "alive"
        return "ptr" if _ptr_wait(ptr, max(0.05, self.PTR_WAIT - (time.monotonic() - t0))) else "free"

    def verdict(self, ip: str) -> dict:
        """Что DHCP думает о найденном адресе (для подписи в диалоге)."""
        if not self.use_dhcp:
            return {"status": "n/a", "text": "сверка не выполнялась — сервер DHCP не указан в настройках, проверьте адрес на DHCP вручную", "detail": ""}
        if not self.dhcp_data or not self.dhcp_data.get("scopes"):
            return {"status": "unavailable", "text": "DHCP недоступен — сверьте вручную", "detail": "; ".join(self.dhcp_data.get("errors") or []) if self.dhcp_data else ""}
        from . import dhcp
        return dhcp.classify(ip, self.dhcp_data)

    WINDOW = 48   # одновременно проверяемых адресов
    PTR_WAIT = 1.5   # сколько всего ждать PTR (стартует параллельно ping — см. _check)

    def run(self) -> None:
        if not re.match(r"^\d{1,3}(\.\d{1,3}){2}$", self.prefix):
            # только error: finished_search затёр бы сообщение об ошибке в диалоге
            self.error.emit("Префикс подсети должен быть вида 10.0.2")
            return
        self._load_dhcp()
        # 3.8.0: карта закрашивается СТРОГО по порядку адресов — без «прыжков» вперемешку. Проверки
        # внутри по-прежнему идут параллельно (окно WINDOW), но статус каждой ячейки показывается
        # только после её собственной проверки, и только когда очередь дошла до этой ячейки:
        # видно ровный пробег по подсети. Ответ — первый по порядку свободный адрес.
        hosts = list(range(self.start_host, 255))
        verdict: dict[int, str] = {}
        shown = self.start_host            # следующая ячейка, которой пора показать статус
        found = ""
        ex = ThreadPoolExecutor(max_workers=self.WINDOW)
        try:
            futs: dict = {}
            it = iter(hosts)
            for h in it:
                futs[ex.submit(self._check, f"{self.prefix}.{h}")] = h
                if len(futs) >= self.WINDOW:
                    break
            while futs and not self.cancelled and not self._settled:
                f = next(as_completed(list(futs)))
                h = futs.pop(f)
                try:
                    verdict[h] = f.result()
                except Exception:  # noqa: BLE001
                    verdict[h] = "free"
                while shown in verdict:            # показать всё, что накопилось, по порядку
                    r = verdict.pop(shown)
                    if r != "cancelled":
                        self.host_checked.emit(shown, r)
                        self.progress.emit(tr("⚡ {0}.{1} — {2}").format(self.prefix, shown, _FREEIP_TEXT.get(r, r)))
                        if r == "free":
                            found = f"{self.prefix}.{shown}"
                            break
                    shown += 1
                if found:
                    break
                if shown in futs.values():
                    self.checking.emit(shown)      # рамка «проверяю сейчас» на текущей ячейке
                nxt = next(it, None)
                if nxt is not None:
                    futs[ex.submit(self._check, f"{self.prefix}.{nxt}")] = nxt
        finally:
            for f in list(futs):
                f.cancel()                          # ещё не начатые проверки не нужны
            # wait=False: поток завершается сразу — «Следующий» не ждёт хвоста начатых проверок
            ex.shutdown(wait=False, cancel_futures=True)
        if found and not self.cancelled:
            self._settled = True
            self.dhcp_info.emit(self.verdict(found))
            self.finished_search.emit(found)
            return
        if not self.cancelled:
            self.finished_search.emit("")


_ptr_pool = ThreadPoolExecutor(max_workers=64, thread_name_prefix="ptr")


def _ptr_start(ip: str):
    """PTR-проверка стартует немедленно в пуле — бежит параллельно ping (3.8.0: «мёртвый» адрес
    стоит max(ping, PTR), а не сумму). Результат забирается :func:`_ptr_wait`."""
    import socket
    return _ptr_pool.submit(socket.gethostbyaddr, ip)


def _ptr_wait(fut, timeout: float) -> bool:
    """Дождаться PTR-проверки не дольше ``timeout``. ``gethostbyaddr`` не умеет таймаут и на «мёртвых»
    адресах может думать 5–10 с — не успел, считаем, что записи нет."""
    try:
        fut.result(timeout=timeout)
        return True
    except (OSError, TimeoutError):
        return False
    except Exception:  # noqa: BLE001 — concurrent.futures.TimeoutError на старых Python не наследует TimeoutError
        return False


def _ptr_exists(ip: str, timeout: float = 1.5) -> bool:
    """Есть ли у адреса PTR-запись (синхронно: старт + ожидание)."""
    return _ptr_wait(_ptr_start(ip), timeout)


# --------------------------------------------------------------------------- опись в Excel
INVENTORY_COLUMNS = (
    ("fio", "Сотрудник"), ("login", "Логин"), ("title", "Должность"), ("dept", "Отдел"), ("office", "Кабинет"),
    ("comp", "Имя ПК"), ("ip", "IP"), ("online", "В сети"), ("os", "ОС"), ("cpu", "Процессор"), ("ram", "ОЗУ"),
    ("disks", "Диски"), ("printers", "Принтеры"), ("last_seen", "Был в сети"),
)
INVENTORY_DEFAULT = ("fio", "title", "dept", "office", "comp", "ip", "os", "cpu", "ram", "disks", "printers")


def pick_computer(e, login: str, disp: str, inv: dict, inv_by_user: dict, perm: dict, pcm: dict, recent: dict) -> str:
    """Один «текущий» ПК сотрудника для описи — в том же порядке источников, что и поиск.

    Приоритет: постоянная привязка → pc_mapping → инвентарь (ПК, где он сейчас залогинен; в сети — первым) →
    связки за полгода (``db.recent_links``) → таблицы БД → AD ``userWorkstations``.
    """
    l = db.normalize_login(login)
    keys = [l, ad.get_full_fio(e).lower().strip(), (disp or "").lower().strip()]
    if l in perm and perm[l]["comp"]:
        return perm[l]["comp"]
    if l in pcm and pcm[l]:
        return pcm[l]
    cands: list[str] = []
    for k in keys:
        cands += inv_by_user.get(k, [])
    if cands:
        cands.sort(key=lambda c: (not inv.get(c, {}).get("is_online"), c))
        return cands[0]
    best, best_when = "", ""
    for k in keys:
        for comp, when in recent.get(k, {}).items():
            if when >= best_when:
                best, best_when = comp, when
    if best:
        return best
    comp = db.get_computer_by_login(login)
    if comp:
        return comp
    ad_ws = ad.get_ad_value(e, "userWorkstations")
    return db.clean_computer_name(ad_ws.split(",")[0]) if ad_ws else ""


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
    inv, perm, pcm = db.load_inventory_maps()
    # 3.5.10: ПК подбирается так же, как в поиске (привязки → инвентарь по логину/ФИО → свежая история → AD
    # userWorkstations), а не только по get_computer_by_login: у организации с данными опись показывала «Не привязан»
    inv_by_user: dict[str, list[str]] = {}
    for comp, data in inv.items():
        if data["user"]:
            inv_by_user.setdefault(data["user"], []).append(comp)
    recent = db.recent_links()
    comps: dict[str, str] = {}
    for e, disp, login in people:
        comps[login] = pick_computer(e, login, disp, inv, inv_by_user, perm, pcm, recent)
    printers = db.printers_for_computers([c for c in comps.values() if c])
    for i, (e, disp, login) in enumerate(people, 1):
        if cancelled():
            return rows
        if i % 20 == 0:
            progress(i, len(people))
        comp = comps.get(login) or ""
        summary = netutils.get_computer_specs_summary(comp) if comp else ""
        # 3.9.0: раньше строка «Характеристики не собраны» целиком попадала в колонку «ОС» —
        # теперь честные прочерки в колонках ОС/ЦП/ОЗУ/Диски (сами данные появляются после полного опроса)
        lines = [] if not comp or summary.startswith("Характеристики") else summary.split("\n")
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
                ["displayName", "sAMAccountName", "title", "department", "physicalDeliveryOfficeName", "company"],
            )
        finally:
            conn.unbind()

    def run(self) -> None:
        try:
            rows = self.rows
            if rows is None:
                self.progress.emit("Получение сотрудников из AD…")
                entries = self._fetch()
                # 3.5.7: страховка от «вся AD в одной организации» — в файл попадают только записи, у которых
                # company совпадает с выбранной (LDAP-фильтр уже это делает, но ответ контроллера перепроверяем)
                want = self.company.casefold().strip()
                entries = [e for e in entries if ad.get_ad_value(e, "company").casefold().strip() == want]
                if not entries:
                    self.finished_export.emit(False, f"В организации «{self.company}» нет сотрудников")
                    return
                rows = build_inventory_rows(entries, cancelled=lambda: self.cancelled,
                                            progress=lambda i, n: self.progress.emit(tr("Обработано {0}/{1}…").format(i, n)))
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
