"""Dual-engine database layer for ExpPilot.

Supports both Supabase / PostgreSQL (when DATABASE_URL is set in environment)
and local SQLite (when DATABASE_URL is absent).

Converts parameter placeholders ('?' -> '%s', ':name' -> '%(name)s') and wraps
Postgres dictionary rows so all service code (api/service.py, data/seed.py,
agents/recommender.py) works transparently against either engine.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from typing import Any

DB_PATH = Path(os.getenv("EXPILOT_DB_PATH", Path(__file__).resolve().parent.parent / "exppilot.db"))
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return bool(DATABASE_URL)


class DatabaseUnavailable(RuntimeError):
    """The configured database could not be reached.

    Raised instead of a bare driver exception so the API layer can answer with
    a 503 and an actionable message rather than an opaque 'Internal Server
    Error'. The message never contains the connection URL (it holds the
    password).
    """


class PgRow(dict):
    """Dictionary subclass supporting integer indexing if needed."""

    def __getitem__(self, item: Any) -> Any:
        if isinstance(item, int):
            return list(self.values())[item]
        return super().__getitem__(item)


class PgCursorWrapper:

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def _convert_sql(self, sql: str) -> str:
        # Convert SQLite named parameters :key to Postgres %(key)s
        sql = re.sub(r":([a-zA-Z0-9_]+)", r"%(\1)s", sql)
        # Convert positional ? to %s
        sql = sql.replace("?", "%s")
        return sql

    def execute(self, sql: str, params: Any = None) -> PgCursorWrapper:
        sql = self._convert_sql(sql)
        if params is not None:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self

    def executemany(self, sql: str, params_seq: Any) -> PgCursorWrapper:
        sql = self._convert_sql(sql)
        self._cursor.executemany(sql, params_seq)
        return self

    def fetchone(self) -> PgRow | None:
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgRow(row) if isinstance(row, dict) else PgRow(dict(row))

    def fetchall(self) -> list[PgRow]:
        rows = self._cursor.fetchall()
        return [PgRow(r) if isinstance(r, dict) else PgRow(dict(r)) for r in rows]


class PgConnWrapper:

    def __init__(self, conn: Any, pool: Any = None) -> None:
        self._conn = conn
        # When the connection came from a pool, close() must return it rather
        # than tear down the TCP/TLS session -- reconnecting per query is what
        # made a hosted database unusably slow.
        self._pool = pool

    def cursor(self) -> PgCursorWrapper:
        return PgCursorWrapper(self._conn.cursor())

    def execute(self, sql: str, params: Any = None) -> PgCursorWrapper:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executemany(self, sql: str, params_seq: Any) -> PgCursorWrapper:
        cur = self.cursor()
        cur.executemany(sql, params_seq)
        return cur

    def executescript(self, sql_script: str) -> None:
        cur = self._conn.cursor()
        # Split statements on semicolon for postgres executescript
        for stmt in sql_script.split(";"):
            cleaned = stmt.strip()
            if cleaned:
                cur.execute(cleaned)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        """Release the connection: back to the pool, or actually close it."""
        if self._pool is not None:
            try:
                self._pool.putconn(self._conn)
                return
            except Exception:  # noqa: BLE001 - a bad connection must not leak
                pass
        self._conn.close()


def _normalized_url() -> str:
    """Accept either postgres:// or postgresql:// (Supabase hands out both)."""
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _connect_hint(exc: Exception) -> str:
    """Turn a driver-level connection failure into something actionable.

    The URL is never echoed back -- it carries the password.
    """
    detail = str(exc)
    if "Network is unreachable" in detail or "network is unreachable" in detail:
        return (
            "Could not reach the Postgres host. Supabase's direct connection "
            "(db.<ref>.supabase.co) resolves to IPv6 only, which many hosts -- including "
            "Railway containers -- cannot route. Use the Supabase *connection pooler* "
            "host instead (aws-0-<region>.pooler.supabase.com, port 6543)."
        )
    if "password authentication failed" in detail:
        return (
            "Postgres rejected the credentials. If the password contains characters like "
            "$ ? @ or #, they must be percent-encoded in DATABASE_URL."
        )
    if "timeout" in detail.lower():
        return "Timed out connecting to Postgres. Check the host is reachable and not paused."
    if "does not exist" in detail:
        return "The Postgres database or role in DATABASE_URL does not exist."
    return "Could not open a Postgres connection."


_pool: Any = None
_pool_url: str | None = None


def _get_pool() -> Any:
    """A lazily built connection pool for Postgres, or None if unavailable.

    Every call site here follows a get_conn()/close() pattern, and a single
    request touches the database roughly ten times. Against a hosted Postgres a
    fresh TCP+TLS handshake per query dominated everything else -- measured at
    ~1s each, so ~30s to create one experiment. Pooling reuses live sessions and
    removes that cost entirely.
    """
    global _pool, _pool_url

    if not is_postgres():
        return None

    url = _normalized_url()
    if _pool is not None and _pool_url == url:
        return _pool

    if _pool is not None:  # DATABASE_URL changed (tests do this); rebuild
        try:
            _pool.close()
        except Exception:  # noqa: BLE001
            pass
        _pool = None
        _pool_url = None

    try:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError:
        return None  # fall back to connect-per-query

    try:
        _pool = ConnectionPool(
            url,
            min_size=1,
            max_size=8,
            # Bounded deliberately: when the database is unreachable this is how
            # long a request waits before the 503. Long enough for a cold connect
            # over a long link, short enough that an outage fails fast rather
            # than hanging every caller.
            timeout=5,
            max_lifetime=600,
            kwargs={"row_factory": dict_row, "connect_timeout": 8},
            open=True,
        )
        _pool_url = url
        return _pool
    except Exception:  # noqa: BLE001 - pool construction failed; use direct connect
        _pool = None
        _pool_url = None
        return None


def get_conn():
    """Open a connection to whichever engine DATABASE_URL selects.

    Raises DatabaseUnavailable (never a bare driver error) when Postgres is
    configured but unreachable, so callers can report something useful instead
    of surfacing an opaque 500.
    """
    if not is_postgres():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    url = _normalized_url()

    pool = _get_pool()
    if pool is not None:
        try:
            return PgConnWrapper(pool.getconn(), pool=pool)
        except Exception as exc:
            raise DatabaseUnavailable(_connect_hint(exc)) from exc

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        psycopg = None  # type: ignore[assignment]

    if psycopg is not None:
        try:
            return PgConnWrapper(psycopg.connect(url, row_factory=dict_row, connect_timeout=10))
        except Exception as exc:  # driver-level failure -> typed, explainable
            raise DatabaseUnavailable(_connect_hint(exc)) from exc

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        raise DatabaseUnavailable(
            "DATABASE_URL is set for PostgreSQL/Supabase, but neither 'psycopg' nor "
            "'psycopg2' is installed. Run `pip install 'psycopg[binary]'`."
        ) from None

    try:
        return PgConnWrapper(
            psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor, connect_timeout=10)
        )
    except Exception as exc:
        raise DatabaseUnavailable(_connect_hint(exc)) from exc


def db_status() -> dict:
    """Cheap reachability probe for /health. Never raises."""
    engine = "postgres" if is_postgres() else "sqlite"
    try:
        conn = get_conn()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
        return {"engine": engine, "reachable": True, "detail": None}
    except DatabaseUnavailable as exc:
        return {"engine": engine, "reachable": False, "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - health must never raise
        return {"engine": engine, "reachable": False, "detail": f"{type(exc).__name__}: {exc}"}


_schema_ready: set[str] = set()


def _schema_target() -> str:
    """Identifies the database the schema would be created in.

    Keyed rather than a single boolean because the test suite repoints DB_PATH at
    a fresh temp file per test; a global flag would leave later tests querying a
    database whose tables were never created.
    """
    return f"pg:{DATABASE_URL}" if is_postgres() else f"sqlite:{DB_PATH}"


def init_db(force: bool = False) -> None:
    """Create the schema if needed. Cheap to call repeatedly.

    The statements are all IF NOT EXISTS, but on a remote Postgres each one is a
    network round trip -- roughly a dozen per call. Read paths call this
    defensively, and replaying a multi-day experiment calls those read paths once
    per day, so an un-memoized version added tens of seconds of latency against
    a hosted database. The schema does not change at runtime, so once per process
    per target is enough.
    """
    target = _schema_target()
    if not force and target in _schema_ready:
        return

    conn = get_conn()
    auto_inc = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
    try:
        conn.executescript(
            f"""
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
                ground_truth TEXT
            );
            CREATE TABLE IF NOT EXISTS day_stats (
                id {auto_inc}, experiment_id TEXT NOT NULL,
                day INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(experiment_id, day),
                FOREIGN KEY(experiment_id) REFERENCES experiments(id)
            );
            CREATE INDEX IF NOT EXISTS idx_day_stats_experiment_id ON day_stats(experiment_id);

            CREATE TABLE IF NOT EXISTS flags (
                key TEXT PRIMARY KEY, segment TEXT NOT NULL, status TEXT NOT NULL,
                running_experiment_id TEXT
            );

            CREATE TABLE IF NOT EXISTS feature_flags (
                flag_key TEXT PRIMARY KEY,
                segment_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'free',
                owner TEXT,
                category TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS segments (
                segment_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                population INTEGER NOT NULL,
                daily_traffic INTEGER NOT NULL,
                baseline_conversion_rate REAL NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS metrics_catalog (
                metric_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('primary', 'guardrail')),
                direction TEXT NOT NULL CHECK (direction IN ('increase_good', 'decrease_good')),
                unit TEXT NOT NULL,
                baseline_value REAL NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS historical_experiments (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                segment_key TEXT NOT NULL,
                primary_metric TEXT NOT NULL,
                hypothesis_text TEXT NOT NULL,
                lift_observed REAL NOT NULL,
                outcome TEXT NOT NULL CHECK (outcome IN ('shipped', 'abandoned', 'rolled_back')),
                concluded_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_historical_category ON historical_experiments(category);
            CREATE INDEX IF NOT EXISTS idx_historical_segment ON historical_experiments(segment_key);

            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL, source_experiment_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id {auto_inc}, experiment_id TEXT NOT NULL,
                day INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(experiment_id, day),
                FOREIGN KEY(experiment_id) REFERENCES experiments(id)
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id {auto_inc}, node TEXT NOT NULL, input TEXT NOT NULL,
                output TEXT NOT NULL, timestamp TEXT NOT NULL, thread_id TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
    _schema_ready.add(target)
