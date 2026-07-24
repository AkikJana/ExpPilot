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

            -- Legacy running-state table. api/service.py's overlap check and
            -- start_experiment() read/write this exact shape; keep it stable.
            CREATE TABLE IF NOT EXISTS flags (
                key TEXT PRIMARY KEY, segment TEXT NOT NULL, status TEXT NOT NULL,
                running_experiment_id TEXT
            );

            -- Rich flag catalog seeded from data/seeds/feature_flags.csv. This is
            -- what the recommender reads from; `flags` above only tracks which one
            -- is currently running so the overlap check stays cheap.
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

            -- Supersedes the old, always-empty `history` table. Seeded from
            -- data/seeds/historical_experiments.csv; this is what grounds
            -- hypothesis precedent_ids and the recommender's flag/metric choices.
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
