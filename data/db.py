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

    def __init__(self, conn: Any) -> None:
        self._conn = conn

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
        self._conn.close()


def get_conn():
    if is_postgres():
        try:
            import psycopg
            from psycopg.rows import dict_row

            # Handle Supabase connection strings (e.g. postgresql://... or postgres://...)
            url = DATABASE_URL
            if url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql://", 1)

            raw_conn = psycopg.connect(url, row_factory=dict_row)
            return PgConnWrapper(raw_conn)
        except ImportError:
            try:
                import psycopg2
                import psycopg2.extras

                url = DATABASE_URL
                raw_conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
                return PgConnWrapper(raw_conn)
            except ImportError:
                raise ImportError(
                    "DATABASE_URL is set for PostgreSQL/Supabase, but neither 'psycopg' nor 'psycopg2' is installed. "
                    "Run `pip install psycopg[binary]`."
                )
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn


def init_db() -> None:
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
