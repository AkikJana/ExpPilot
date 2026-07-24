"""SQLite connection and schema for ExpPilot."""

from __future__ import annotations

import sqlite3
import os
from pathlib import Path

DB_PATH = Path(os.getenv("EXPILOT_DB_PATH", Path(__file__).resolve().parent.parent / "exppilot.db"))


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY, config TEXT NOT NULL, status TEXT NOT NULL,
                ground_truth TEXT
            );
            CREATE TABLE IF NOT EXISTS day_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
                day INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(experiment_id, day),
                FOREIGN KEY(experiment_id) REFERENCES experiments(id)
            );
            CREATE INDEX IF NOT EXISTS idx_day_stats_experiment_id ON day_stats(experiment_id);
            CREATE TABLE IF NOT EXISTS flags (
                key TEXT PRIMARY KEY, segment TEXT NOT NULL, status TEXT NOT NULL,
                running_experiment_id TEXT
            );
            CREATE TABLE IF NOT EXISTS history (
                id TEXT PRIMARY KEY, category TEXT NOT NULL, hypothesis_text TEXT NOT NULL,
                lift_observed REAL NOT NULL, outcome TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL,
                content TEXT NOT NULL, source_experiment_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, experiment_id TEXT NOT NULL,
                day INTEGER NOT NULL, data TEXT NOT NULL, UNIQUE(experiment_id, day),
                FOREIGN KEY(experiment_id) REFERENCES experiments(id)
            );
            CREATE TABLE IF NOT EXISTS agent_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, node TEXT NOT NULL, input TEXT NOT NULL,
                output TEXT NOT NULL, timestamp TEXT NOT NULL, thread_id TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()
