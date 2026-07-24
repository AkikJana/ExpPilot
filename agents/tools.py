"""Deterministic tool contract exposed to the agent graph.

These are the ONLY tools the copilot may call to obtain facts. Everything that
touches a number routes through stats.core; everything that touches state routes
through the registry. No tool here calls an LLM, and none reads an experiment's
hidden ground-truth label.
"""
from __future__ import annotations

import json

from data.db import get_conn
from data.synth import make_experiment
from shared.models import DayStats, ExperimentConfig, StatsResult
from stats.core import compute_day_stats, decide, power_analysis

# Category -> (segment, ordered candidate flags). Mirrors data/seed.py registry.
CATEGORY_SEGMENT: dict[str, str] = {
    "checkout": "mobile_users",
    "device_bundles": "device_upgrade_eligible",
    "plan_upgrades": "plan_browsers",
    "churn": "at_risk_users",
    "onboarding": "new_users",
    "payments": "billing_users",
}


def estimate_sample_size(baseline_rate: float, mde: float, alpha: float = 0.05, power: float = 0.80) -> int:
    """Tool: required sample size per arm (delegates to the deterministic stats core)."""
    return power_analysis(baseline_rate, mde, alpha, power)


def list_flags(status: str | None = None, segment: str | None = None) -> list[dict]:
    """Tool: query the feature-flag registry, optionally filtered by status/segment."""
    conn = get_conn()
    try:
        sql = "SELECT key, segment, status, running_experiment_id FROM flags"
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if segment:
            clauses.append("segment = ?")
            params.append(segment)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def pick_free_flag(category: str) -> tuple[str, str]:
    """Tool: choose an unused flag in the category's segment. Returns (flag_key, segment)."""
    segment = CATEGORY_SEGMENT.get(category, "mobile_users")
    free = list_flags(status="free", segment=segment)
    if free:
        return free[0]["key"], segment
    # Fall back to any free flag if the segment is fully booked.
    any_free = list_flags(status="free")
    if any_free:
        return any_free[0]["key"], any_free[0]["segment"]
    return f"{category}_experiment_flag", segment


def check_overlap(flag_key: str, segment: str) -> dict:
    """Tool: detect experiment collisions before launch (deterministic set logic).

    Returns conflicts (flag itself already running) and overlaps (a *different*
    experiment already running on the same audience segment -> interaction risk).
    """
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT key, segment, status, running_experiment_id FROM flags WHERE status = 'in_use'"
        ).fetchall()
    finally:
        conn.close()

    conflicts, overlaps = [], []
    for r in rows:
        if r["key"] == flag_key:
            conflicts.append({"flag": r["key"], "experiment_id": r["running_experiment_id"]})
        elif r["segment"] == segment:
            overlaps.append(
                {
                    "flag": r["key"],
                    "segment": r["segment"],
                    "experiment_id": r["running_experiment_id"],
                }
            )
    clean = not conflicts and not overlaps
    return {"clean": clean, "conflicts": conflicts, "overlaps": overlaps}


def cumulative_daystats(scenario: str, seed: int, day: int, experiment_id: str) -> DayStats:
    """Tool: deterministically simulate cumulative counts through `day` (1..14)."""
    _, days, _ = make_experiment(scenario, seed)
    day = max(1, min(day, len(days)))
    cn = sum(d.control_n for d in days[:day])
    cc = sum(d.control_conversions for d in days[:day])
    tn = sum(d.treatment_n for d in days[:day])
    tc = sum(d.treatment_conversions for d in days[:day])
    # Traffic-weighted cumulative guardrail rates: a single day's rate is too noisy
    # at low traffic and produces false guardrail breaches.
    g_ctrl = sum(d.guardrail_control_rate * d.control_n for d in days[:day]) / cn
    g_trt = sum(d.guardrail_treatment_rate * d.treatment_n for d in days[:day]) / tn
    return DayStats(
        experiment_id=experiment_id,
        day=day,
        control_n=cn,
        control_conversions=cc,
        treatment_n=tn,
        treatment_conversions=tc,
        guardrail_control_rate=g_ctrl,
        guardrail_treatment_rate=g_trt,
    )


def per_day_series(scenario: str, seed: int, experiment_id: str) -> list[DayStats]:
    """Tool: the full 14-day per-day count series (for charting)."""
    _, days, _ = make_experiment(scenario, seed)
    out = []
    for d in days:
        dd = d.model_copy(update={"experiment_id": experiment_id})
        out.append(dd)
    return out


def analyze(scenario: str, seed: int, day: int, config: ExperimentConfig) -> tuple[StatsResult, str]:
    """Tool: compute the StatsResult for a day and the deterministic action.

    The action ALWAYS comes from stats.core.decide - never an LLM.
    """
    cum = cumulative_daystats(scenario, seed, day, config.id)
    stats = compute_day_stats(cum, config, seed=0)
    action = decide(stats, config)
    return stats, action


def load_config(experiment_id: str) -> tuple[ExperimentConfig, dict]:
    """Load a persisted experiment config and its (server-side) simulation metadata."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT config, ground_truth FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"experiment {experiment_id!r} not found")
    config = ExperimentConfig(**json.loads(row["config"]))
    meta = json.loads(row["ground_truth"]) if row["ground_truth"] else {}
    return config, meta
