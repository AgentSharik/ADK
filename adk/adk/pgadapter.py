"""PostgreSQL как общий бэкенд для отдела: ``[Paths] db_backend = postgres``, ``db_dsn = postgresql://adk:...@host/adk``.

Весь ``db.py`` написан под SQLite. Вместо второй копии запросов — обёртка над psycopg-соединением, которая
переводит диалект на лету (:func:`translate`): плейсхолдеры, ``INSERT OR REPLACE/IGNORE``, ``datetime('now', …)``,
``AUTOINCREMENT``, ``CREATE TEMP TABLE``. Транслятор — чистая функция и покрыт тестами; сам psycopg нужен только
при реальном подключении (``pip install psycopg[binary]``).

Ограничения (осознанные): один сканер на отдел (планировщик, ``adk --scan``), у клиентов ``auto_scan_interval_min = 0``.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Sequence

log = logging.getLogger(__name__)

# ключи первичного ключа для INSERT OR REPLACE → ON CONFLICT (…) DO UPDATE
_PK: dict[str, tuple[str, ...]] = {
    "pc_mapping": ("login",), "permanent_mapping": ("login",), "pc_inventory": ("computer_name",),
    "pc_printers": ("computer_name", "name"), "suggest": ("term",), "pc_mac": ("computer_name",),
    "attention_snooze": ("key",), "scanned": ("name",),
}

_DT_NOW = re.compile(r"datetime\('now'\s*,\s*'localtime'(?:\s*,\s*(\?|'[^']*'))?\)", re.IGNORECASE)
_INS_REPLACE = re.compile(r"INSERT OR REPLACE INTO\s+(\w+)\s*\(([^)]*)\)", re.IGNORECASE)
_INS_IGNORE = re.compile(r"INSERT OR IGNORE INTO\s+(\w+)(\s*\(([^)]*)\))?", re.IGNORECASE)


def _now_expr(arg: str | None) -> str:
    base = "now()"
    if arg is None:
        return f"to_char({base}, 'YYYY-MM-DD HH24:MI:SS')"
    if arg == "?":
        return f"to_char({base} + (%s)::interval, 'YYYY-MM-DD HH24:MI:SS')"
    return f"to_char({base} + interval '{arg.strip(chr(39))}', 'YYYY-MM-DD HH24:MI:SS')"


def translate(sql: str) -> str:
    """SQLite-запрос → PostgreSQL. Чистая функция."""
    s = sql
    s = re.sub(r"INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY", s, flags=re.IGNORECASE)
    s = re.sub(r"CREATE TEMP TABLE IF NOT EXISTS", "CREATE TEMPORARY TABLE IF NOT EXISTS", s, flags=re.IGNORECASE)
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    s = re.sub(r"\bCOLLATE NOCASE\b", "", s, flags=re.IGNORECASE)
    s = _DT_NOW.sub(lambda m: _now_expr(m.group(1)), s)
    s = re.sub(r"ESCAPE '\\\\'", "ESCAPE '\\'", s)

    def repl_replace(m):
        table, cols = m.group(1), [c.strip() for c in m.group(2).split(",")]
        pk = _PK.get(table, (cols[0],))
        upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in pk) or f"{pk[0]} = EXCLUDED.{pk[0]}"
        return f"INSERT INTO {table} ({', '.join(cols)}) {{ON_CONFLICT ({', '.join(pk)}) DO UPDATE SET {upd}}}"
    s = _INS_REPLACE.sub(repl_replace, s)

    def repl_ignore(m):
        table = m.group(1)
        return f"INSERT INTO {table}{m.group(2) or ''} {{ON_CONFLICT DO NOTHING}}"
    s = _INS_IGNORE.sub(repl_ignore, s)
    # ON CONFLICT должен стоять после VALUES(...) — переносим маркер в конец оператора
    m = re.search(r"\{(ON_CONFLICT[^}]*)\}", s)
    if m:
        clause = m.group(1).replace("ON_CONFLICT", "ON CONFLICT")
        s = s[:m.start()] + s[m.end():]
        s = s.rstrip().rstrip(";") + " " + clause
    s = s.replace("?", "%s")
    # PostgreSQL: TRUE/FALSE вместо 1/0 в CASE WHEN %s — параметры передаём как есть, is_online INTEGER остаётся числом
    return s


class _Cursor:
    def __init__(self, cur):
        self._c = cur
        self.lastrowid = None

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c)


class PGConnection:
    """Минимальный интерфейс sqlite3.Connection поверх psycopg: execute / executemany / commit / close / контекст."""

    def __init__(self, dsn: str):
        import psycopg  # type: ignore
        self._conn = psycopg.connect(dsn, autocommit=False)

    def execute(self, sql: str, params: Sequence[Any] = ()):
        q = translate(sql)
        cur = self._conn.cursor()
        returning = q.lstrip().upper().startswith("INSERT INTO NOTES")
        if returning and "RETURNING" not in q.upper():
            q += " RETURNING id"
        try:
            cur.execute(q, tuple(params))
        except Exception:
            self._conn.rollback()
            raise
        w = _Cursor(cur)
        if returning:
            row = cur.fetchone()
            w.lastrowid = row[0] if row else None
        return w

    def executemany(self, sql: str, seq):
        q = translate(sql)
        cur = self._conn.cursor()
        try:
            cur.executemany(q, [tuple(p) for p in seq])
        except Exception:
            self._conn.rollback()
            raise
        return _Cursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            self.commit()
        else:
            self.rollback()


def connect(dsn: str) -> PGConnection:
    return PGConnection(dsn)
