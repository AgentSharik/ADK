"""Локальная база (SQLite): привязки пользователь↔ПК, инвентарь, история поиска."""
from __future__ import annotations

import contextlib
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any, Iterable, Sequence

from .config import DB_RETRY_ATTEMPTS, DB_RETRY_DELAY_SEC, settings

log = logging.getLogger(__name__)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS pc_mapping (login TEXT PRIMARY KEY, computer_name TEXT)",
    "CREATE TABLE IF NOT EXISTS permanent_mapping (login TEXT PRIMARY KEY, computer_name TEXT, "
    "ip_address TEXT, last_updated TEXT)",
    "CREATE TABLE IF NOT EXISTS pc_inventory (computer_name TEXT PRIMARY KEY, ip_address TEXT, "
    "is_online INTEGER, current_user TEXT, last_checked TEXT, specs TEXT, last_logon TEXT, "
    "last_seen_online TEXT)",
    "CREATE TABLE IF NOT EXISTS search_history (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "user_login TEXT, query TEXT, query_key TEXT, timestamp TEXT)",
    "CREATE TABLE IF NOT EXISTS audit_cache (login TEXT, computer_name TEXT, ip_address TEXT, "
    "full_name TEXT, timestamp TEXT, raw_data TEXT, file_path TEXT UNIQUE, file_mtime REAL)",
    "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, "
    "admin TEXT, action TEXT, target TEXT, details TEXT)",
    # кэш принтеров из инвентарных CSV: обновляется при просмотре ПК и при сканировании
    "CREATE TABLE IF NOT EXISTS pc_printers (computer_name TEXT, name TEXT, port TEXT, kind TEXT, "
    "ip_address TEXT, is_default INTEGER, updated TEXT, PRIMARY KEY (computer_name, name))",
    "CREATE INDEX IF NOT EXISTS ix_printers_name ON pc_printers (name)",
    "CREATE INDEX IF NOT EXISTS ix_printers_ip ON pc_printers (ip_address)",
    "CREATE INDEX IF NOT EXISTS ix_inventory_user ON pc_inventory (current_user)",
    "CREATE INDEX IF NOT EXISTS ix_inventory_ip ON pc_inventory (ip_address)",
    "CREATE INDEX IF NOT EXISTS ix_audit_cache_login ON audit_cache (login)",
    "CREATE INDEX IF NOT EXISTS ix_audit_log_ts ON audit_log (ts)",
    # 3.0: заметки по пользователю/ПК («менял клавиатуру 03.09»)
    "CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY AUTOINCREMENT, subject TEXT, kind TEXT, "
    "text TEXT, admin TEXT, ts TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_notes_subject ON notes (subject)",
    # 3.0: история «кто за каким ПК / какой IP» — пишется сканером при каждом изменении
    "CREATE TABLE IF NOT EXISTS pc_history (id INTEGER PRIMARY KEY AUTOINCREMENT, computer_name TEXT, "
    "login TEXT, ip_address TEXT, first_seen TEXT, last_seen TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_pc_history_login ON pc_history (login)",
    "CREATE INDEX IF NOT EXISTS ix_pc_history_comp ON pc_history (computer_name)",
    # 3.0: кэш подсказок для автодополнения (фамилии/логины из выдачи)
    "CREATE TABLE IF NOT EXISTS suggest (term TEXT PRIMARY KEY, hits INTEGER DEFAULT 1, ts TEXT)",
    # 3.1: установленное ПО (кэш опроса), MAC-адреса для Wake-on-LAN, отложенные пункты сводки «Внимание»
    "CREATE TABLE IF NOT EXISTS pc_software (computer_name TEXT, name TEXT, version TEXT, publisher TEXT, installed TEXT, ts TEXT)",
    "CREATE INDEX IF NOT EXISTS ix_software_comp ON pc_software (computer_name)",
    "CREATE INDEX IF NOT EXISTS ix_software_name ON pc_software (name)",
    "CREATE TABLE IF NOT EXISTS pc_mac (computer_name TEXT PRIMARY KEY, mac TEXT, ts TEXT)",
    "CREATE TABLE IF NOT EXISTS attention_snooze (key TEXT PRIMARY KEY, until TEXT, admin TEXT)",
)

_MIGRATIONS = (
    "ALTER TABLE pc_inventory ADD COLUMN last_logon TEXT",
    "ALTER TABLE pc_inventory ADD COLUMN last_seen_online TEXT",
    "ALTER TABLE search_history ADD COLUMN query_key TEXT",
    "UPDATE search_history SET query_key = LOWER(query) WHERE query_key IS NULL",
)


def get_db_connection(path: str | None = None):
    """SQLite по умолчанию; ``[Paths] db_backend = postgres`` → общая БД отдела через :mod:`adk.pgadapter`."""
    if getattr(settings, "db_backend", "sqlite") == "postgres" and getattr(settings, "db_dsn", ""):
        from .pgadapter import connect
        return connect(settings.db_dsn)
    conn = sqlite3.connect(path or settings.db_path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000;")
    with contextlib.suppress(sqlite3.Error):
        conn.execute("PRAGMA synchronous=NORMAL")    # с WAL безопасно и заметно быстрее на обычных дисках
    return conn


def db_execute_with_retry(query: str, params: Sequence[Any] = (), fetch: str | None = None):
    """Выполняет запрос с повтором при блокировке; соединение всегда закрывается."""
    last_exc: Exception | None = None
    for attempt in range(1, DB_RETRY_ATTEMPTS + 1):
        try:
            with contextlib.closing(get_db_connection()) as conn:
                cur = conn.execute(query, params)
                if fetch == "one":
                    result = cur.fetchone()
                elif fetch == "all":
                    result = cur.fetchall()
                else:
                    result = None
                conn.commit()
                return result
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if attempt < DB_RETRY_ATTEMPTS and "locked" in str(exc).lower():
                time.sleep(DB_RETRY_DELAY_SEC)
                continue
            raise
    raise last_exc  # pragma: no cover


SCHEMA_VERSION = 4   # растёт при добавлении миграций; перед их применением снимается копия файла


def backup_sqlite(path: str, keep: int = 3) -> str | None:
    """Копия ``<db>.bak-YYYYmmdd-HHMMSS`` через ``sqlite3.Connection.backup`` (корректно даже под нагрузкой).
    Хранится ``keep`` последних. Возвращает путь копии или None, если файла ещё нет."""
    if not path or not os.path.exists(path):
        return None
    dst = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    with contextlib.closing(sqlite3.connect(path, timeout=30)) as src, contextlib.closing(sqlite3.connect(dst)) as out:
        src.backup(out)
    olds = sorted(f for f in os.listdir(os.path.dirname(path) or ".") if f.startswith(os.path.basename(path) + ".bak-"))
    for f in olds[:-keep]:
        with contextlib.suppress(OSError):
            os.remove(os.path.join(os.path.dirname(path) or ".", f))
    return dst


def quick_check(path: str | None = None) -> str:
    """``PRAGMA quick_check`` — «ok» или текст проблемы. Только чтение."""
    try:
        with contextlib.closing(sqlite3.connect(path or settings.db_path, timeout=30)) as conn:
            return str(conn.execute("PRAGMA quick_check").fetchone()[0])
    except sqlite3.Error as exc:
        return f"error: {exc}"


def init_db() -> None:
    sqlite = getattr(settings, "db_backend", "sqlite") != "postgres"
    parent = os.path.dirname(settings.db_path)
    if parent and sqlite:
        os.makedirs(parent, exist_ok=True)
    if sqlite and os.path.exists(settings.db_path):
        # схема меняется только при обновлении ADK — тогда сначала резервная копия, потом ALTER TABLE
        with contextlib.closing(sqlite3.connect(settings.db_path, timeout=30)) as conn:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if current < SCHEMA_VERSION:
            with contextlib.suppress(sqlite3.Error, OSError):
                backup_sqlite(settings.db_path)
    with contextlib.closing(get_db_connection()) as conn:
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
        for stmt in _MIGRATIONS:
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception:  # noqa: BLE001 — столбец уже есть (sqlite3.OperationalError / psycopg DuplicateColumn)
                if hasattr(conn, "rollback"):
                    conn.rollback()
        if sqlite:
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
            # 3.5.4: WAL — фоновый сканер пишет инвентарь, а поиск в это время читает без ожиданий; на медленном
            # SSD/HDD это заметно (раньше запись блокировала чтение до commit). Режим хранится в самом файле БД.
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")


# --------------------------------------------------------------------------- helpers
def like_escape(text: str) -> str:
    """Экранирует спецсимволы LIKE. Использовать вместе с ``ESCAPE '\\'``."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_IP_QUERY_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){0,3}\.?$")


def ip_like_patterns(query: str) -> tuple[str, str] | None:
    """Для IP-подобного запроса — пара LIKE-шаблонов (точное совпадение, префикс по октетам).

    «10.0.2» → ('10.0.2', '10.0.2.%'): попадает 10.0.2.15, но не 10.0.20.1 и не 110.0.2.1.
    «10.0.2.» → ('10.0.2.%', '10.0.2.%'). Для не-IP запросов возвращает None.
    """
    q = query.strip()
    if not q or not _IP_QUERY_RE.match(q) or q.count(".") == 0:
        return None
    esc = like_escape(q)
    if q.endswith("."):
        return esc + "%", esc + "%"
    return esc, esc + ".%"


_IPV4_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def clean_computer_name(name: str | None) -> str:
    """FQDN/имя с '$' → короткое имя в верхнем регистре. IP-адрес не «обрезается» до первого октета."""
    if not name or str(name).strip() in ("", "—", "None", "Не указан", "Не найден"):
        return ""
    s = str(name).strip().rstrip("$")
    if _IPV4_RE.match(s):
        return s
    return s.split(".")[0].strip().upper()


def normalize_login(login: str) -> str:
    """'CORP\\Ivanov ', 'ivanov@corp.example', 'Ivanov' → 'ivanov'."""
    s = (login or "").strip().casefold().split("\\")[-1]
    if "@" in s:
        s = s.split("@", 1)[0]
    return s.strip()


# --------------------------------------------------------------------------- search history
def save_search_query(query_text: str, user: str) -> None:
    q = query_text.strip()
    if len(q) < 2 or re.fullmatch(r"[\-\s._]+", q):
        return
    # LOWER() в SQLite работает только для ASCII — нормализуем регистр в Python
    key = q.casefold()
    try:
        row = db_execute_with_retry(
            "SELECT id FROM search_history WHERE query_key = ?", (key,), fetch="one"
        )
        if row:
            db_execute_with_retry(
                "UPDATE search_history SET timestamp = datetime('now','localtime'), query = ? WHERE id = ?",
                (q, row[0]),
            )
        else:
            db_execute_with_retry(
                "INSERT INTO search_history (user_login, query, query_key, timestamp) "
                "VALUES (?, ?, ?, datetime('now','localtime'))",
                (user, q, key),
            )
    except sqlite3.Error as exc:
        log.debug("save_search_query: %s", exc)


def get_recent_searches(limit: int = 5) -> list[str]:
    try:
        rows = db_execute_with_retry(
            "SELECT query FROM search_history GROUP BY query_key "
            "ORDER BY MAX(timestamp) DESC LIMIT ?",
            (limit,),
            fetch="all",
        )
        return [r[0] for r in rows or []]
    except sqlite3.Error:
        return []


# --------------------------------------------------------------------------- mapping
def get_computer_by_login(login: str) -> str:
    if not login:
        return ""
    l_clean = normalize_login(login)
    for sql, arg in (
        ("SELECT computer_name FROM permanent_mapping WHERE login = ?", l_clean),
        ("SELECT computer_name FROM audit_cache WHERE login = ? ORDER BY timestamp DESC", l_clean),
        ("SELECT computer_name FROM pc_mapping WHERE LOWER(login) = ?", l_clean),
    ):
        row = db_execute_with_retry(sql, (arg,), fetch="one")
        if row and row[0]:
            return clean_computer_name(row[0])
    return ""


def save_computer_for_login(login: str, computer_name: str) -> None:
    clean = clean_computer_name(computer_name)
    l_clean = normalize_login(login)
    db_execute_with_retry(
        "INSERT INTO pc_mapping (login, computer_name) VALUES (?, ?) "
        "ON CONFLICT(login) DO UPDATE SET computer_name = excluded.computer_name",
        (l_clean, clean),
    )
    db_execute_with_retry(
        "INSERT INTO permanent_mapping (login, computer_name, last_updated) "
        "VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(login) DO UPDATE SET computer_name = excluded.computer_name, "
        "last_updated = excluded.last_updated",
        (l_clean, clean),
    )


def logins_by_computer_or_ip(query: str, limit: int = 50) -> set[str]:
    """Логины, связанные с ПК/IP, содержащими подстроку query (для расширения LDAP-поиска)."""
    like = f"%{like_escape(query)}%"
    ip_exact, ip_prefix = ip_like_patterns(query) or (like, like)
    result: set[str] = set()
    sqls = (
        "SELECT current_user FROM pc_inventory WHERE computer_name LIKE ? ESCAPE '\\' "
        "OR ip_address LIKE ? ESCAPE '\\' OR ip_address LIKE ? ESCAPE '\\'",
        "SELECT login FROM audit_cache WHERE computer_name LIKE ? ESCAPE '\\' "
        "OR ip_address LIKE ? ESCAPE '\\' OR ip_address LIKE ? ESCAPE '\\'",
        "SELECT login FROM permanent_mapping WHERE computer_name LIKE ? ESCAPE '\\' "
        "OR ip_address LIKE ? ESCAPE '\\' OR ip_address LIKE ? ESCAPE '\\'",
    )
    try:
        with contextlib.closing(get_db_connection()) as conn:
            for sql in sqls:
                for (val,) in conn.execute(sql, (like, ip_exact, ip_prefix)):
                    if val:
                        result.add(normalize_login(val))
                    if len(result) >= limit:
                        return result
            for (val,) in conn.execute(
                "SELECT login FROM pc_mapping WHERE computer_name LIKE ? ESCAPE '\\'", (like,)
            ):
                if val:
                    result.add(normalize_login(val))
    except sqlite3.Error as exc:
        log.debug("logins_by_computer_or_ip: %s", exc)
    return set(list(result)[:limit])


# --------------------------------------------------------------------------- inventory
def get_inventory_stats() -> tuple[int, str | None]:
    online = db_execute_with_retry("SELECT COUNT(*) FROM pc_inventory WHERE is_online = 1", fetch="one")
    last = db_execute_with_retry("SELECT MAX(last_checked) FROM pc_inventory", fetch="one")
    return (online[0] if online else 0), (last[0] if last else None)


def get_inventory_summary() -> dict:
    """Сводка для дашборда: сколько ПК в сети / не в сети по инвентарю и когда сканировали.

    «Не в сети» считается прямо по строкам инвентаря (``is_online = 0``), а не как разница с числом ПК в AD —
    иначе, пока AD не ответил, карточка показывала бы 0.
    """
    row = db_execute_with_retry(
        "SELECT SUM(CASE WHEN is_online = 1 THEN 1 ELSE 0 END), SUM(CASE WHEN is_online = 1 THEN 0 ELSE 1 END), "
        "COUNT(*), MAX(last_checked) FROM pc_inventory", fetch="one") or (0, 0, 0, None)
    return {"online": row[0] or 0, "offline": row[1] or 0, "total": row[2] or 0, "last": row[3]}


def load_inventory_maps() -> tuple[dict, dict, dict]:
    """Снимок таблиц для поискового воркера: inventory, permanent_mapping, pc_mapping."""
    inv: dict[str, dict] = {}
    perm: dict[str, dict] = {}
    pcm: dict[str, str] = {}
    with contextlib.closing(get_db_connection()) as conn:
        for name, ip, on, user, last_logon, seen in conn.execute(
            "SELECT computer_name, ip_address, is_online, current_user, last_logon, last_seen_online "
            "FROM pc_inventory"
        ):
            inv[clean_computer_name(name)] = {
                "ip": ip or "Не найден",
                "is_online": bool(on),
                "user": normalize_login(user or ""),
                "last_logon": last_logon or "Нет данных",
                "last_seen_online": seen,
            }
        for login, comp, ip, upd in conn.execute(
            "SELECT login, computer_name, ip_address, last_updated FROM permanent_mapping"
        ):
            perm[normalize_login(login)] = {
                "comp": clean_computer_name(comp),
                "ip": ip or "Не найден",
                "date": upd or "Нет данных",
            }
        for login, comp in conn.execute("SELECT login, computer_name FROM pc_mapping"):
            pcm[normalize_login(login)] = clean_computer_name(comp)
    return inv, perm, pcm


def load_audit_map() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    with contextlib.closing(get_db_connection()) as conn:
        for login, fio, comp, ip, ts in conn.execute(
            "SELECT login, full_name, computer_name, ip_address, timestamp FROM audit_cache"
        ):
            obj = {"comp": clean_computer_name(comp), "ip": ip or "Не найден", "date": ts or "Нет данных"}
            for key in (normalize_login(login or ""), (fio or "").lower().strip()):
                if key:
                    out.setdefault(key, []).append(obj)
    return out


def inventory_rows_matching(query: str, limit: int = 100) -> list[tuple]:
    """ПК из инвентаря по подстроке имени или IP (для запросов, за которыми нет пользователей AD)."""
    like = f"%{like_escape(query)}%"
    ip_exact, ip_prefix = ip_like_patterns(query) or (like, like)
    return db_execute_with_retry(
        "SELECT computer_name, ip_address, is_online, current_user, specs, last_checked, last_logon, "
        "last_seen_online FROM pc_inventory WHERE computer_name LIKE ? ESCAPE '\\' "
        "OR ip_address LIKE ? ESCAPE '\\' OR ip_address LIKE ? ESCAPE '\\' "
        "ORDER BY is_online DESC, computer_name LIMIT ?",
        (like, ip_exact, ip_prefix, int(limit)), fetch="all",
    ) or []


def inventory_rows_for_computers(names: Iterable[str]) -> list[tuple]:
    names = sorted({clean_computer_name(n) for n in names if n})
    if not names:
        return []
    q = ",".join("?" * len(names[:500]))
    return db_execute_with_retry(
        "SELECT computer_name, ip_address, is_online, current_user, specs, last_checked, last_logon, "
        f"last_seen_online FROM pc_inventory WHERE computer_name IN ({q}) ORDER BY is_online DESC, computer_name",
        tuple(names[:500]), fetch="all",
    ) or []


# --------------------------------------------------------------------------- принтеры
def replace_printers(computer_name: str, printers: Iterable[dict]) -> None:
    """Полная замена принтеров ПК: [{name, port, kind, ip, is_default}]."""
    comp = clean_computer_name(computer_name)
    if not comp:
        return
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    with contextlib.closing(get_db_connection()) as conn:
        conn.execute("DELETE FROM pc_printers WHERE computer_name = ?", (comp,))
        conn.executemany(
            "INSERT OR REPLACE INTO pc_printers (computer_name, name, port, kind, ip_address, is_default, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(comp, p["name"], p.get("port", ""), p.get("kind", ""), p.get("ip", ""),
              1 if p.get("is_default") else 0, now) for p in printers if p.get("name")],
        )
        conn.commit()


def printers_for_computers(names: Iterable[str]) -> dict[str, list[dict]]:
    names = [clean_computer_name(n) for n in names if n]
    if not names:
        return {}
    out: dict[str, list[dict]] = {}
    with contextlib.closing(get_db_connection()) as conn:
        for i in range(0, len(names), 500):  # лимит переменных SQLite
            chunk = names[i:i + 500]
            q = ",".join("?" * len(chunk))
            for comp, name, port, kind, ip, dflt in conn.execute(
                f"SELECT computer_name, name, port, kind, ip_address, is_default FROM pc_printers "
                f"WHERE computer_name IN ({q}) ORDER BY is_default DESC, name", chunk):
                out.setdefault(comp, []).append({"name": name, "port": port or "", "kind": kind or "",
                                                 "ip": ip or "", "is_default": bool(dflt)})
    return out


def printer_owners(query: str, limit: int = 100) -> list[dict]:
    """Кто подключён к принтеру (подстрока имени или IP): [{comp, user, name, port, kind, ip, is_default, is_online}]."""
    like = f"%{like_escape(query)}%"
    ip_exact, ip_prefix = ip_like_patterns(query) or (like, like)
    rows = db_execute_with_retry(
        "SELECT p.computer_name, i.current_user, p.name, p.port, p.kind, p.ip_address, p.is_default, i.is_online "
        "FROM pc_printers p LEFT JOIN pc_inventory i ON i.computer_name = p.computer_name "
        "WHERE p.name LIKE ? ESCAPE '\\' OR p.ip_address LIKE ? ESCAPE '\\' OR p.ip_address LIKE ? ESCAPE '\\' "
        "ORDER BY p.name, p.computer_name LIMIT ?", (like, ip_exact, ip_prefix, int(limit)), fetch="all") or []
    return [{"comp": r[0], "user": normalize_login(r[1] or ""), "name": r[2], "port": r[3] or "", "kind": r[4] or "",
             "ip": r[5] or "", "is_default": bool(r[6]), "is_online": bool(r[7])} for r in rows]


def printer_matches(query: str, limit: int = 30) -> list[dict]:
    """Сами принтеры (а не их владельцы), подходящие под запрос: подстрока модели или IP/префикс IP.

    Группирует записи pc_printers по (модель, IP): [{name, kind, ip, port, pcs:[{comp, user, is_online, is_default}]}],
    самые «популярные» первыми. Один SELECT по индексам — безопасно вызывать на каждый поисковый запрос.
    """
    q = (query or "").strip()
    if not q or (len(q) < 3 and not ip_like_patterns(q)):
        return []
    groups: dict[tuple[str, str], dict] = {}
    for o in printer_owners(q, limit=3000):
        key = (o["name"].lower(), o["ip"])
        g = groups.setdefault(key, {"name": o["name"], "kind": o["kind"], "ip": o["ip"], "port": o["port"], "pcs": []})
        g["pcs"].append({"comp": o["comp"], "user": o["user"], "is_online": o["is_online"], "is_default": o["is_default"]})
    out = sorted(groups.values(), key=lambda g: (-len(g["pcs"]), g["name"].lower()))
    for g in out:
        g["pcs"].sort(key=lambda x: (not x["is_online"], x["comp"]))
    return out[:limit]


def printer_summary() -> list[dict]:
    """Сводка по парку: каждый принтер (имя+тип+IP) → сколько ПК, сколько из них в сети, когда обновлено."""
    rows = db_execute_with_retry(
        "SELECT p.name, p.kind, p.ip_address, COUNT(*), COALESCE(SUM(i.is_online), 0), MAX(p.updated), "
        "GROUP_CONCAT(p.computer_name, ', ') FROM pc_printers p "
        "LEFT JOIN pc_inventory i ON i.computer_name = p.computer_name "
        "GROUP BY p.name, p.kind, p.ip_address ORDER BY COUNT(*) DESC, p.name", fetch="all") or []
    return [{"name": r[0], "kind": r[1] or "", "ip": r[2] or "", "pcs": r[3], "online": r[4], "updated": r[5] or "",
             "computers": r[6] or ""} for r in rows]


def logins_by_printer(query: str, limit: int = 50) -> set[str]:
    if not query or len(query) < 3:
        return set()
    out: set[str] = set()
    for o in printer_owners(query, limit=limit * 2):
        if o["user"]:
            out.add(o["user"])
        else:  # ПК без текущего пользователя — берём привязку
            row = db_execute_with_retry("SELECT login FROM permanent_mapping WHERE computer_name = ?", (o["comp"],), fetch="one")
            if row and row[0]:
                out.add(normalize_login(row[0]))
        if len(out) >= limit:
            break
    return out


def inventory_rows(category: str) -> list[tuple]:
    where = {"online": "WHERE is_online = 1", "offline": "WHERE is_online = 0"}.get(category, "")
    return db_execute_with_retry(
        "SELECT computer_name, ip_address, is_online, current_user, specs, last_checked, last_logon, "
        f"last_seen_online FROM pc_inventory {where} ORDER BY is_online DESC, computer_name",
        fetch="all",
    ) or []


def set_pc_online(computer_name: str, is_online: bool) -> None:
    db_execute_with_retry(
        "UPDATE pc_inventory SET is_online = ?, "
        "last_seen_online = CASE WHEN ? THEN datetime('now','localtime') ELSE last_seen_online END "
        "WHERE computer_name = ?",
        (int(is_online), int(is_online), clean_computer_name(computer_name)),
    )


def batch_update_inventory(results: Iterable[dict], now_str: str) -> None:
    """Upsert результатов сканера; ПК, исчезнувшие из AD, удаляются через временную таблицу."""
    results = list(results)
    with contextlib.closing(get_db_connection()) as conn:
        mapping = {
            clean_computer_name(c): l
            for c, l in conn.execute("SELECT computer_name, login FROM pc_mapping")
            if c
        }
        for c, l in conn.execute("SELECT computer_name, login FROM permanent_mapping"):
            if c:
                mapping[clean_computer_name(c)] = l

        rows = []
        for r in results:
            name = clean_computer_name(r["Hostname"])
            user = (r.get("User") or "").strip() or mapping.get(name, "")
            is_on = 1 if r.get("Status") == "ACTIVE" else 0
            rows.append((name, r.get("ActualIp") or "Не найден", is_on, user, now_str,
                         r.get("LastLogon") or "Неизвестно", now_str if is_on else None))

        conn.executemany(
            """INSERT INTO pc_inventory (computer_name, ip_address, is_online, current_user,
                                         last_checked, last_logon, last_seen_online)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(computer_name) DO UPDATE SET
                 ip_address = excluded.ip_address,
                 is_online = excluded.is_online,
                 current_user = CASE WHEN excluded.current_user != '' THEN excluded.current_user
                                     ELSE pc_inventory.current_user END,
                 last_checked = excluded.last_checked,
                 last_logon = CASE WHEN excluded.last_logon != 'Неизвестно' THEN excluded.last_logon
                                   ELSE pc_inventory.last_logon END,
                 last_seen_online = COALESCE(excluded.last_seen_online, pc_inventory.last_seen_online)""",
            rows,
        )
        _record_history(conn, rows, now_str)
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS scanned (name TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM scanned")
        conn.executemany("INSERT OR IGNORE INTO scanned VALUES (?)", [(r[0],) for r in rows])
        if rows:
            conn.execute("DELETE FROM pc_inventory WHERE computer_name NOT IN (SELECT name FROM scanned)")
        conn.commit()


def _record_history(conn: sqlite3.Connection, rows: list[tuple], now_str: str) -> None:
    """Таймлайн: новая запись, когда у ПК сменился пользователь или IP; иначе продлевается last_seen."""
    last: dict[str, tuple[int, str, str]] = {}
    for hid, comp, login, ip in conn.execute(
        "SELECT h.id, h.computer_name, h.login, h.ip_address FROM pc_history h "
        "JOIN (SELECT computer_name, MAX(id) AS mid FROM pc_history GROUP BY computer_name) m ON m.mid = h.id"
    ):
        last[comp] = (hid, login or "", ip or "")
    extend, insert = [], []
    for name, ip, _on, user, *_ in rows:
        login = normalize_login(user or "")
        ip = ip if ip and ip != "Не найден" else ""
        if not login and not ip:
            continue
        prev = last.get(name)
        if prev and prev[1] == login and prev[2] == ip:
            extend.append((now_str, prev[0]))
        else:
            insert.append((name, login, ip, now_str, now_str))
    if extend:
        conn.executemany("UPDATE pc_history SET last_seen = ? WHERE id = ?", extend)
    if insert:
        conn.executemany("INSERT INTO pc_history (computer_name, login, ip_address, first_seen, last_seen) "
                         "VALUES (?, ?, ?, ?, ?)", insert)


def history_for(login: str = "", computer_name: str = "", limit: int = 200) -> list[dict]:
    """Таймлайн по пользователю и/или ПК: [{comp, login, ip, first_seen, last_seen}], новые сверху."""
    where, params = [], []
    if login:
        where.append("login = ?"); params.append(normalize_login(login))
    if computer_name:
        where.append("computer_name = ?"); params.append(clean_computer_name(computer_name))
    if not where:
        return []
    rows = db_execute_with_retry(
        "SELECT computer_name, login, ip_address, first_seen, last_seen FROM pc_history "
        f"WHERE {' OR '.join(where)} ORDER BY last_seen DESC, id DESC LIMIT ?", (*params, int(limit)), fetch="all") or []
    return [{"comp": r[0], "login": r[1] or "", "ip": r[2] or "", "first_seen": r[3] or "", "last_seen": r[4] or ""} for r in rows]


# --------------------------------------------------------------------------- notes
def add_note(subject: str, text: str, admin: str, kind: str = "user") -> int:
    """subject — логин (kind=user) или имя ПК (kind=pc). Возвращает id."""
    subj = normalize_login(subject) if kind == "user" else clean_computer_name(subject)
    text = (text or "").strip()
    if not subj or not text:
        return 0
    with contextlib.closing(get_db_connection()) as conn:
        cur = conn.execute("INSERT INTO notes (subject, kind, text, admin, ts) VALUES (?, ?, ?, ?, datetime('now','localtime'))",
                           (subj, kind, text, admin))
        conn.commit()
        return int(cur.lastrowid)


def notes_for(subject: str, kind: str = "user", limit: int = 50) -> list[dict]:
    subj = normalize_login(subject) if kind == "user" else clean_computer_name(subject)
    if not subj:
        return []
    rows = db_execute_with_retry("SELECT id, text, admin, ts FROM notes WHERE subject = ? AND kind = ? "
                                 "ORDER BY id DESC LIMIT ?", (subj, kind, int(limit)), fetch="all") or []
    return [{"id": r[0], "text": r[1], "admin": r[2] or "", "ts": r[3] or ""} for r in rows]


def delete_note(note_id: int) -> None:
    db_execute_with_retry("DELETE FROM notes WHERE id = ?", (int(note_id),))


def notes_count(subjects: Iterable[str], kind: str = "user") -> dict[str, int]:
    subs = sorted({(normalize_login(s) if kind == "user" else clean_computer_name(s)) for s in subjects if s})
    if not subs:
        return {}
    q = ",".join("?" * len(subs[:500]))
    rows = db_execute_with_retry(f"SELECT subject, COUNT(*) FROM notes WHERE kind = ? AND subject IN ({q}) GROUP BY subject",
                                 (kind, *subs[:500]), fetch="all") or []
    return {r[0]: r[1] for r in rows}


# --------------------------------------------------------------------------- suggestions (QCompleter)
def remember_terms(terms: Iterable[str]) -> None:
    """Запоминает фамилии/логины/имена ПК из выдачи — для автодополнения в поле поиска."""
    vals = sorted({t.strip() for t in terms if t and 2 < len(t.strip()) < 64})
    if not vals:
        return
    try:
        with contextlib.closing(get_db_connection()) as conn:
            conn.executemany("INSERT INTO suggest (term, hits, ts) VALUES (?, 1, datetime('now','localtime')) "
                             "ON CONFLICT(term) DO UPDATE SET hits = hits + 1, ts = excluded.ts", [(v,) for v in vals])
            conn.commit()
    except sqlite3.Error as exc:
        log.debug("suggest: %s", exc)


def suggestions(limit: int = 500) -> list[str]:
    rows = db_execute_with_retry("SELECT term FROM suggest ORDER BY hits DESC, ts DESC LIMIT ?", (int(limit),), fetch="all") or []
    hist = db_execute_with_retry("SELECT query FROM search_history ORDER BY id DESC LIMIT 100", fetch="all") or []
    out: list[str] = []
    seen: set[str] = set()
    for (t,) in [*hist, *rows]:
        k = (t or "").casefold()
        if t and k not in seen and not t.startswith("["):
            seen.add(k); out.append(t)
    return out[:limit]


# --------------------------------------------------------------------------- 3.1: MAC / snooze
def save_mac(computer_name: str, mac: str) -> None:
    """MAC хранится в едином виде «00:1A:2B:3C:4D:5E», в каком бы формате его ни прислали (arp -a, ввод вручную)."""
    from .nettools import parse_mac
    comp, norm = clean_computer_name(computer_name), parse_mac(mac)
    if comp and norm:
        db_execute_with_retry("INSERT OR REPLACE INTO pc_mac (computer_name, mac, ts) VALUES (?, ?, datetime('now','localtime'))", (comp, norm))


def get_mac(computer_name: str) -> str:
    row = db_execute_with_retry("SELECT mac FROM pc_mac WHERE computer_name = ?", (clean_computer_name(computer_name),), fetch="one")
    return row[0] if row else ""


def snooze(key: str, days: int, admin: str) -> None:
    db_execute_with_retry("INSERT OR REPLACE INTO attention_snooze (key, until, admin) VALUES (?, datetime('now','localtime', ?), ?)",
                          (key, f"+{int(days)} days", admin))


def snoozed_keys() -> set[str]:
    rows = db_execute_with_retry("SELECT key FROM attention_snooze WHERE until > datetime('now','localtime')", fetch="all") or []
    return {r[0] for r in rows}


def stale_computers(days: int = 30, limit: int = 200) -> list[tuple[str, str, str]]:
    """ПК, не бывшие в сети N дней: (comp, user, last_seen_online). Никогда не виденные — в конце."""
    rows = db_execute_with_retry(
        "SELECT computer_name, current_user, last_seen_online FROM pc_inventory "
        "WHERE last_seen_online IS NULL OR last_seen_online < datetime('now','localtime', ?) "
        "ORDER BY last_seen_online IS NULL, last_seen_online LIMIT ?", (f"-{int(days)} days", int(limit)), fetch="all") or []
    return [(r[0], r[1] or "", r[2] or "") for r in rows]


# --------------------------------------------------------------------------- audit log
ACTION_LABELS = {
    "modify_user": "Изменение атрибутов", "enable_user": "Включение УЗ", "disable_user": "Отключение УЗ",
    "unlock": "Снятие блокировки", "reset_password": "Сброс пароля", "create_user": "Создание УЗ",
    "group_add": "Добавление в группу", "group_remove": "Удаление из группы", "bind_pc": "Привязка ПК",
    "restart": "Перезагрузка ПК", "shutdown": "Выключение ПК", "lock": "Блокировка экрана ПК", "logoff": "Выход пользователя с ПК",
    "sleep": "ПК в спящий режим", "disk_c": "Открытие диска C$", "disk_d": "Открытие диска D$", "disk_e": "Открытие диска E$", "disk_f": "Открытие диска F$",
    "compmgmt": "Управление компьютером", "rms": "RMS-подключение",
    "wol": "Wake-on-LAN", "msg": "Сообщение на ПК", "template_apply": "Шаблон регистрации", "software": "Опрос ПО",
    "note_add": "Заметка добавлена", "note_delete": "Заметка удалена", "bulk_group_add": "Массово: в группу",
    "bulk_group_remove": "Массово: из группы", "bulk_disable": "Массово: отключение УЗ", "bulk_enable": "Массово: включение УЗ",
    "bulk_reset_password": "Массово: сброс пароля", "bulk_unlock": "Массово: снятие блокировки", "export": "Экспорт результатов",
    "plugin": "Действие плагина", "groups_sync": "Группы «как у…»", "health": "Проверка здоровья ПК", "disk_usage": "Карта диска",
    "attention_snooze": "Сводка: отложено", "scan": "Сканирование парка", "logons": "Журнал входов ПК",
    "compare_pc": "Сравнение ПК", "notify_test": "Проверка уведомлений",
}


def audit_entries(limit: int = 500, admin: str = "", action: str = "", text: str = "",
                  since: str | None = None) -> list[tuple]:
    """Журнал действий с фильтрами: (ts, admin, action, target, details). Новые сверху."""
    where: list[str] = []
    params: list = []
    if admin:
        where.append("admin = ?")
        params.append(admin)
    if action:
        where.append("action = ?")
        params.append(action)
    if text:
        like = f"%{like_escape(text)}%"
        where.append("(target LIKE ? ESCAPE '\\' OR details LIKE ? ESCAPE '\\')")
        params += [like, like]
    if since:
        where.append("ts >= ?")
        params.append(since)
    sql = ("SELECT ts, admin, action, target, details FROM audit_log"
           + (" WHERE " + " AND ".join(where) if where else "")
           + " ORDER BY id DESC LIMIT ?")
    return db_execute_with_retry(sql, (*params, int(limit)), fetch="all") or []


def audit_admins() -> list[str]:
    rows = db_execute_with_retry("SELECT DISTINCT admin FROM audit_log ORDER BY admin", fetch="all") or []
    return [r[0] for r in rows if r[0]]


def known_ip(computer_name: str) -> str:
    """Последний известный IP ПК из инвентаря (pc_inventory), «» если ПК не сканировался или адрес не найден."""
    name = clean_computer_name(computer_name)
    if not name:
        return ""
    row = db_execute_with_retry("SELECT ip_address FROM pc_inventory WHERE computer_name = ?", (name,), fetch="one")
    ip = (row[0] if row else "") or ""
    return "" if ip in ("", "Не найден", "Не указан") else ip


def last_seen_online(computer_name: str) -> str | None:
    row = db_execute_with_retry("SELECT last_seen_online FROM pc_inventory WHERE computer_name = ?",
                                (clean_computer_name(computer_name),), fetch="one")
    return row[0] if row and row[0] else None


def humanize_since(ts: str | None, now: "datetime | None" = None) -> str:
    """'2026-09-04 10:00:00' → «2 ч. назад» / «3 дн. назад (01.09.2026)»; None → «—»."""
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ts
    secs = int(((now or datetime.now()) - dt).total_seconds())
    if secs < 60:
        return "только что"
    if secs < 3600:
        return f"{secs // 60} мин. назад"
    if secs < 86400:
        return f"{secs // 3600} ч. назад"
    return f"{secs // 86400} дн. назад ({dt:%d.%m.%Y})"


def log_action(admin: str, action: str, target: str, details: str = "") -> None:
    try:
        db_execute_with_retry(
            "INSERT INTO audit_log (ts, admin, action, target, details) "
            "VALUES (datetime('now','localtime'), ?, ?, ?, ?)",
            (admin, action, target, details),
        )
    except sqlite3.Error as exc:
        log.warning("audit_log: %s", exc)
    for hook in list(AUDIT_HOOKS):
        try:
            hook(admin, action, target, details)
        except Exception as exc:  # noqa: BLE001
            log.warning("audit hook: %s", exc)


AUDIT_HOOKS: list = []  # callables(admin, action, target, details) — например, notify.notify_event
