"""Idempotent CSV -> SQLite loader.

Data scientists edit the CSVs under data/seeds/ (feature flags, audience
segments, the metrics catalog, historical experiment results) and re-run this
module; nothing here requires touching Python code to add or change a row.
Every load is INSERT OR REPLACE keyed on each table's natural primary key, so
running this twice is safe and re-running it after editing a CSV updates the
existing rows rather than duplicating them.
"""

from __future__ import annotations

import csv
from pathlib import Path

from data.db import get_conn, init_db

SEEDS_DIR = Path(__file__).resolve().parent / "seeds"


def _read_csv(name: str) -> list[dict[str, str]]:
    path = SEEDS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"seed file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _load_segments(conn) -> int:
    rows = _read_csv("segments.csv")
    conn.executemany(
        """
        INSERT INTO segments (segment_key, display_name, population, daily_traffic,
                               baseline_conversion_rate, description)
        VALUES (:segment_key, :display_name, :population, :daily_traffic,
                :baseline_conversion_rate, :description)
        ON CONFLICT(segment_key) DO UPDATE SET
            display_name = excluded.display_name,
            population = excluded.population,
            daily_traffic = excluded.daily_traffic,
            baseline_conversion_rate = excluded.baseline_conversion_rate,
            description = excluded.description
        """,
        [
            {
                **row,
                "population": int(row["population"]),
                "daily_traffic": int(row["daily_traffic"]),
                "baseline_conversion_rate": float(row["baseline_conversion_rate"]),
            }
            for row in rows
        ],
    )
    return len(rows)


def _load_metrics_catalog(conn) -> int:
    rows = _read_csv("metrics_catalog.csv")
    conn.executemany(
        """
        INSERT INTO metrics_catalog (metric_key, display_name, kind, direction, unit,
                                      baseline_value, description)
        VALUES (:metric_key, :display_name, :kind, :direction, :unit,
                :baseline_value, :description)
        ON CONFLICT(metric_key) DO UPDATE SET
            display_name = excluded.display_name,
            kind = excluded.kind,
            direction = excluded.direction,
            unit = excluded.unit,
            baseline_value = excluded.baseline_value,
            description = excluded.description
        """,
        [{**row, "baseline_value": float(row["baseline_value"])} for row in rows],
    )
    return len(rows)


def _load_feature_flags(conn) -> int:
    rows = _read_csv("feature_flags.csv")
    conn.executemany(
        """
        INSERT INTO feature_flags (flag_key, segment_key, status, owner, category, description)
        VALUES (:flag_key, :segment_key, :status, :owner, :category, :description)
        ON CONFLICT(flag_key) DO UPDATE SET
            segment_key = excluded.segment_key,
            status = excluded.status,
            owner = excluded.owner,
            category = excluded.category,
            description = excluded.description
        """,
        rows,
    )
    return len(rows)


def _load_historical_experiments(conn) -> int:
    rows = _read_csv("historical_experiments.csv")
    conn.executemany(
        """
        INSERT INTO historical_experiments (id, category, segment_key, primary_metric,
                                             hypothesis_text, lift_observed, outcome, concluded_at)
        VALUES (:id, :category, :segment_key, :primary_metric,
                :hypothesis_text, :lift_observed, :outcome, :concluded_at)
        ON CONFLICT(id) DO UPDATE SET
            category = excluded.category,
            segment_key = excluded.segment_key,
            primary_metric = excluded.primary_metric,
            hypothesis_text = excluded.hypothesis_text,
            lift_observed = excluded.lift_observed,
            outcome = excluded.outcome,
            concluded_at = excluded.concluded_at
        """,
        [{**row, "lift_observed": float(row["lift_observed"])} for row in rows],
    )
    return len(rows)


def seed() -> dict[str, int]:
    """Load every CSV under data/seeds/ into SQLite. Safe to call repeatedly."""
    init_db()
    conn = get_conn()
    try:
        counts = {
            "segments": _load_segments(conn),
            "metrics_catalog": _load_metrics_catalog(conn),
            "feature_flags": _load_feature_flags(conn),
            "historical_experiments": _load_historical_experiments(conn),
        }
        conn.commit()
        return counts
    finally:
        conn.close()


def ensure_seeded() -> bool:
    """Idempotently seed the catalog tables if they are empty, creating the
    schema first if needed. Returns True if a seed was actually performed.

    Callers (api/service.py) call this instead of bare init_db(), so a fresh
    database -- including a test's temp-directory DB -- is self-sufficient:
    the recommender, validator, and diagnostics modules all depend on these
    catalog tables being populated, and a data scientist should not have to
    remember a separate manual seeding step before the API works.
    """
    init_db()
    conn = get_conn()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM segments").fetchone()["c"]
    finally:
        conn.close()
    if count == 0:
        seed()
        return True
    return False


if __name__ == "__main__":
    for table, count in seed().items():
        print(f"{table}: {count} rows loaded")
