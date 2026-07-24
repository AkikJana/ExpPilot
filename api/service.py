"""Business-logic service layer shared by the FastAPI app and the Streamlit UI.

No web framework imports live here, so both surfaces call the exact same code path.
This module owns persistence; the agent graph owns orchestration; stats.core owns
every number and the decision itself.
"""
from __future__ import annotations

import json

from agents import graph, memory, tools
from agents.rag import infer_category, search_past_experiments
from data.db import get_conn, init_db
from data.seed import main as seed_main
from shared.models import ExperimentConfig, MemoryRecord
from stats.core import compute_day_stats

MAX_DAYS = 14


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #
def ensure_ready() -> None:
    """Create the schema and seed registry/history/demos if the DB is empty."""
    init_db()
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) AS c FROM flags").fetchone()["c"]
    finally:
        conn.close()
    if n == 0:
        seed_main()


# --------------------------------------------------------------------------- #
# Registry reads
# --------------------------------------------------------------------------- #
def get_flags() -> list[dict]:
    return tools.list_flags()


def get_history(category: str | None = None) -> list[dict]:
    conn = get_conn()
    try:
        if category:
            rows = conn.execute("SELECT * FROM history WHERE category = ?", (category,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM history").fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Copilot: create pipeline
# --------------------------------------------------------------------------- #
def copilot_hypotheses(goal: str) -> dict:
    """Generate 3 grounded hypotheses for a business goal."""
    result = graph.run_hypotheses(goal)
    return {
        "goal": goal,
        "category": result.get("category"),
        "precedents": result.get("precedents", []),
        "hypotheses": result.get("hypotheses", []),
    }


def copilot_config(hypothesis: dict, category: str | None = None) -> dict:
    """Propose a validated experiment config for a chosen hypothesis."""
    result = graph.run_config(hypothesis, category=category)
    return {"config": result.get("config"), "validation": result.get("validation")}


def create_experiment(config: dict, scenario: str = "true_lift", seed: int = 2026) -> dict:
    """Persist a validated experiment as running and materialize day 1."""
    cfg = ExperimentConfig(**config)
    cfg = cfg.model_copy(update={"status": "running"})
    ground_truth = {"scenario": scenario, "seed": seed, "synthetic": True}
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (cfg.id, cfg.model_dump_json(), "running", json.dumps(ground_truth)),
        )
        # Config ids are deterministic (exp_<flag_key>), so a relaunch can land on an id that
        # already has data. Clear it: an experiment must start at day 1, never resume on top
        # of a previous run's counts and decisions.
        conn.execute("DELETE FROM day_stats WHERE experiment_id = ?", (cfg.id,))
        conn.execute("DELETE FROM decisions WHERE experiment_id = ?", (cfg.id,))
        # Mark the flag in use.
        conn.execute(
            "UPDATE flags SET status = 'in_use', running_experiment_id = ? WHERE key = ?",
            (cfg.id, cfg.flag_key),
        )
        # Materialize day 1 per-day counts.
        series = tools.per_day_series(scenario, seed, cfg.id)
        conn.execute(
            "INSERT OR REPLACE INTO day_stats (experiment_id, day, data) VALUES (?, ?, ?)",
            (cfg.id, 1, series[0].model_dump_json()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"experiment_id": cfg.id, "status": "running", "current_day": 1}


# --------------------------------------------------------------------------- #
# Experiment reads
# --------------------------------------------------------------------------- #
def list_experiments() -> list[dict]:
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, config, status FROM experiments").fetchall()
        out = []
        for r in rows:
            cfg = json.loads(r["config"])
            cur = conn.execute(
                "SELECT COALESCE(MAX(day), 0) AS d FROM day_stats WHERE experiment_id = ?",
                (r["id"],),
            ).fetchone()["d"]
            out.append(
                {
                    "id": r["id"],
                    "status": r["status"],
                    "flag_key": cfg.get("flag_key"),
                    "segment": cfg.get("audience_segment"),
                    "current_day": cur,
                }
            )
    finally:
        conn.close()
    return out


def _current_day(conn, experiment_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(MAX(day), 0) AS d FROM day_stats WHERE experiment_id = ?",
        (experiment_id,),
    ).fetchone()["d"]


def get_experiment(experiment_id: str) -> dict:
    """Full detail: config, per-day cumulative stats series, and latest decision.

    Never exposes the hidden `correct_action` label to the response.
    """
    config, meta = tools.load_config(experiment_id)
    scenario, seed = meta.get("scenario", "true_lift"), meta.get("seed", 2026)
    conn = get_conn()
    try:
        cur = _current_day(conn, experiment_id)
        dec_rows = conn.execute(
            "SELECT day, data FROM decisions WHERE experiment_id = ? ORDER BY day", (experiment_id,)
        ).fetchall()
    finally:
        conn.close()

    series = []
    for day in range(1, max(cur, 1) + 1):
        stats = compute_day_stats(
            tools.cumulative_daystats(scenario, seed, day, experiment_id), config, seed=0
        )
        series.append(stats.model_dump())

    decisions = {r["day"]: json.loads(r["data"]) for r in dec_rows}
    latest_decision = decisions.get(cur)
    return {
        "config": config.model_dump(),
        "simulator": {"scenario": scenario, "seed": seed},  # correct_action deliberately omitted
        "current_day": cur,
        "max_days": MAX_DAYS,
        "series": series,
        "latest_stats": series[-1] if series else None,
        "latest_decision": latest_decision,
        "decisions": decisions,
    }


# --------------------------------------------------------------------------- #
# Monitor / decide
# --------------------------------------------------------------------------- #
def advance_experiment(experiment_id: str) -> dict:
    """Advance one simulated day: materialize counts, run the analyze graph, persist."""
    config, meta = tools.load_config(experiment_id)
    scenario, seed = meta.get("scenario", "true_lift"), meta.get("seed", 2026)
    conn = get_conn()
    try:
        cur = _current_day(conn, experiment_id)
        if cur >= MAX_DAYS:
            next_day = MAX_DAYS
        else:
            next_day = cur + 1
            series = tools.per_day_series(scenario, seed, experiment_id)
            conn.execute(
                "INSERT OR REPLACE INTO day_stats (experiment_id, day, data) VALUES (?, ?, ?)",
                (experiment_id, next_day, series[next_day - 1].model_dump_json()),
            )
            conn.commit()
    finally:
        conn.close()

    precedents = search_past_experiments(config.flag_key, k=2)
    result = graph.run_analyze(
        experiment_id, scenario, seed, next_day, config.model_dump(), precedents
    )
    decision = result["decision"]

    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO decisions (experiment_id, day, data) VALUES (?, ?, ?)",
            (experiment_id, next_day, json.dumps(decision)),
        )
        # Reflect terminal actions on experiment status.
        status_map = {"scale": "concluded", "stop": "concluded", "rollback": "concluded", "pause": "paused"}
        new_status = status_map.get(decision["action"], "running")
        conn.execute("UPDATE experiments SET status = ? WHERE id = ?", (new_status, experiment_id))
        conn.commit()
    finally:
        conn.close()

    return {
        "experiment_id": experiment_id,
        "day": next_day,
        "stats": result["stats"],
        "action": result["action"],
        "alerts": result["alerts"],
        "decision": decision,
    }


def record_verdict(experiment_id: str, day: int, verdict: str, reason: str | None = None) -> dict:
    """Persist a human adopt/override verdict on a decision (adoption-rate metric).

    A rejection also closes the learning loop: it writes a lesson to long-term memory so
    future runs in the same category retrieve it. The lesson is stored verbatim from the
    human's stated reason — no LLM involved, so the record cannot drift from what was said.
    """
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT data FROM decisions WHERE experiment_id = ? AND day = ?", (experiment_id, day)
        ).fetchone()
        if row is None:
            raise KeyError(f"no decision for {experiment_id} day {day}")
        decision = json.loads(row["data"])
        decision["human_verdict"] = verdict
        decision["human_reason"] = reason
        conn.execute(
            "UPDATE decisions SET data = ? WHERE experiment_id = ? AND day = ?",
            (json.dumps(decision), experiment_id, day),
        )
        conn.commit()
    finally:
        conn.close()

    if verdict == "rejected":
        _write_rejection_lesson(experiment_id, decision, reason)

    return {"experiment_id": experiment_id, "day": day, "human_verdict": verdict}


def _write_rejection_lesson(experiment_id: str, decision: dict, reason: str | None) -> None:
    """Record why a recommendation was overridden. Best-effort; never breaks the verdict."""
    try:
        config, _ = tools.load_config(experiment_id)
        category = infer_category(f"{config.flag_key} {config.audience_segment}")
        memory.write(
            MemoryRecord(
                id=memory.new_id(),
                kind="lesson",
                category=category,
                content=(
                    f"Recommendation '{decision.get('action')}' rejected: "
                    f"{reason or 'no reason given'}"
                ),
                source_experiment_id=experiment_id,
                created_at=memory.now_iso(),
            )
        )
    except Exception:
        pass


def get_audit(experiment_id: str) -> dict:
    """Full audit trail for one experiment: every decision and every agent run."""
    conn = get_conn()
    try:
        dec_rows = conn.execute(
            "SELECT day, data FROM decisions WHERE experiment_id = ? ORDER BY day", (experiment_id,)
        ).fetchall()
        run_rows = conn.execute(
            "SELECT node, input, output, timestamp FROM agent_runs "
            "WHERE thread_id = ? OR input LIKE ? ORDER BY id",
            (experiment_id, f"%{experiment_id}%"),
        ).fetchall()
    finally:
        conn.close()
    return {
        "experiment_id": experiment_id,
        "decisions": [{"day": r["day"], **json.loads(r["data"])} for r in dec_rows],
        "agent_runs": [dict(r) for r in run_rows],
    }


def get_memory(kind: str | None = None, category: str | None = None) -> list[dict]:
    """Long-term memory records, newest first, optionally filtered."""
    return [r.model_dump() for r in memory.fetch_all(kind=kind, category=category)]


def adoption_stats() -> dict:
    """Aggregate adopt/override verdicts for the impact dashboard."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT data FROM decisions").fetchall()
    finally:
        conn.close()
    approved = rejected = pending = total = 0
    for r in rows:
        d = json.loads(r["data"])
        v = d.get("human_verdict")
        total += 1
        if v == "approved":
            approved += 1
        elif v == "rejected":
            rejected += 1
        else:
            pending += 1
    decided = approved + rejected
    return {
        "total_decisions": total,
        "approved": approved,
        "rejected": rejected,
        "pending": pending,
        "adoption_rate": round(approved / decided, 3) if decided else None,
    }
