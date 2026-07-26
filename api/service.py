"""Application service: deterministic decisioning with Cursor-assisted ideation.

Recommendations (which flag, audience, and metrics), validation, and driver
diagnostics are all deterministic and grounded in the SQL data layer
(agents/recommender.py, agents/validator.py, stats/diagnostics.py) -- Cursor
never chooses a flag, computes a statistic, or invents a number here. Its only
role is prose: hypothesis statements (agents/llm.py) and the business
narrative (agents/narrator.py), and both are numerically guarded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from agents.llm import hypotheses_for_goal
from agents.narrator import narrate_decision
from agents.recommender import find_precedents, infer_category, recommend
from agents.validator import validate_experiment
from data.db import get_conn, init_db
from data.derive import persist_segments
from data.seed import ensure_seeded
from data.synth import SyntheticSpec, synthesize
from harness.gitops import gitops_proposal
from ontology.tree import initial_tree
from rules_engine.decision import evaluate_decision
from shared.models import (
    DayStats,
    Decision,
    DecisionRecommendation,
    ExperimentConfig,
    Hypothesis,
    SegmentDayStats,
    ValidationBlocked,
)
from stats.core import compute_day_stats, decide, power_analysis
from stats.diagnostics import analyze_drivers


def _dump(model: object) -> dict:
    return model.model_dump(mode="json")  # type: ignore[attr-defined]


def _load_experiment(experiment_id: str) -> ExperimentConfig:
    # Defensive, matching the other read paths in this module: against a database
    # whose schema has not been created yet, the SELECT would raise "no such
    # table" and surface as a 500. An unknown experiment should be a clean 404
    # whether or not anything has been written yet.
    init_db()
    conn = get_conn()
    try:
        row = conn.execute("SELECT config FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"Unknown experiment: {experiment_id}")
    return ExperimentConfig.model_validate_json(row["config"])


def create_experiment(
    goal: str,
    baseline_rate: float | None = None,
    daily_traffic: int | None = None,
    segment_key: str | None = None,
    hypothesis_index: int = 0,
) -> dict:
    """Create a proposal grounded in real flags, audiences, metrics, and
    historical precedents.

    baseline_rate and daily_traffic, when omitted, are derived from the
    recommended audience segment's actual observed traffic and conversion
    rate rather than guessed. Passing explicit values (as the existing test
    suite does) always takes precedence over the data-driven default.
    """
    ensure_seeded()
    category = infer_category(goal)
    precedents = find_precedents(category, segment_key, limit=5)
    candidates = hypotheses_for_goal(goal, precedents=precedents)
    # The caller picks which candidate to configure (Objective 1's "pick one"
    # step). Index is clamped rather than trusted, so a stale UI selection can
    # never IndexError the request.
    selected_index = max(0, min(int(hypothesis_index or 0), len(candidates) - 1))
    candidate = candidates[selected_index]

    recommendation = recommend(goal, preferred_segment=segment_key or candidate.get("segment"))

    resolved_baseline_rate = (
        baseline_rate if baseline_rate is not None else recommendation.segment["baseline_conversion_rate"]
    )
    resolved_daily_traffic = (
        daily_traffic if daily_traffic is not None else recommendation.segment["daily_traffic"]
    )

    hypothesis = Hypothesis(
        id=f"hyp_{uuid4().hex[:10]}",
        goal=goal,
        statement=candidate["statement"],
        primary_metric="conversion_rate",
        expected_direction=candidate.get("direction", "increase"),
        expected_mde=float(candidate.get("expected_mde", 0.01)),
        segment=recommendation.segment["segment_key"],
        rationale=candidate.get("rationale", ""),
        precedent_ids=[precedent["id"] for precedent in recommendation.precedents],
    )
    required_n = power_analysis(resolved_baseline_rate, hypothesis.expected_mde)
    experiment_id = f"exp_{uuid4().hex[:10]}"
    flag_key = recommendation.flag["flag_key"] if recommendation.flag else f"{experiment_id}_flag"
    guardrail_keys = [metric["metric_key"] for metric in recommendation.guardrail_metrics] or ["error_rate"]

    config = ExperimentConfig(
        id=experiment_id,
        hypothesis_id=hypothesis.id,
        flag_key=flag_key,
        audience_segment=hypothesis.segment,
        traffic_split={"control": 0.5, "treatment": 0.5},
        baseline_rate=resolved_baseline_rate,
        mde=hypothesis.expected_mde,
        required_n_per_arm=required_n,
        estimated_days=max(1, round((2 * required_n) / resolved_daily_traffic)),
        guardrail_metrics=guardrail_keys,
        daily_traffic=resolved_daily_traffic,
        status="validated",
    )

    validation = validate_experiment(config, primary_metric_key=recommendation.primary_metric["metric_key"])
    if not validation.passed:
        # Nothing is persisted when a blocking check fails -- but the caller gets
        # the full structured report, not a flattened sentence, so the UI can
        # show which specific check failed and why.
        raise ValidationBlocked(validation)

    tree = initial_tree(goal, candidates)
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO experiments(id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (config.id, config.model_dump_json(), config.status, json.dumps({"ontology": tree.as_dict()})),
        )
        conn.execute(
            """
            INSERT INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                segment = excluded.segment,
                status = excluded.status,
                running_experiment_id = excluded.running_experiment_id
            """,
            (config.flag_key, config.audience_segment, "validated", None),
        )

        # Keep the rich flag catalog in sync: a flag claimed by a draft/validated
        # experiment should not be recommended again for a different one. This
        # is a no-op UPDATE if flag_key is a synthetic fallback key not in the
        # catalog (recommendation.flag was None).
        conn.execute("UPDATE feature_flags SET status = 'validated' WHERE flag_key = ?", (config.flag_key,))
        conn.commit()
    finally:
        conn.close()
    return {
        "hypotheses": candidates,
        "selected_hypothesis_index": selected_index,
        "hypothesis": _dump(hypothesis),
        "config": _dump(config),
        "ontology": tree.as_dict(),
        "recommendation": {
            "category": recommendation.category,
            "precedents": recommendation.precedents,
            "primary_metric": recommendation.primary_metric,
            "guardrail_metrics": recommendation.guardrail_metrics,
            "issues": recommendation.issues,
        },
        "validation": validation.as_dict(),
    }


def persist_derived_segments(segments: list[dict], replace_seeded: bool = False) -> dict:
    """Store audience segments measured from the user's own data.

    The seeded catalog is fabricated; these rows are computed from a real upload,
    so once they land the recommender, validator and sample-size maths all plan
    against measured numbers instead of invented ones.
    """
    ensure_seeded()
    counts = persist_segments(segments, replace_seeded=replace_seeded)
    return {"status": "ok", **counts, "segment_keys": [s["segment_key"] for s in segments]}


def list_segments() -> list[dict]:
    """The audience catalog, flagged by whether each row was derived or seeded."""
    ensure_seeded()
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT segment_key, display_name, population, daily_traffic, "
            "baseline_conversion_rate, description FROM segments ORDER BY daily_traffic DESC"
        ).fetchall()
    finally:
        conn.close()
    return [
        {**dict(row), "derived": str(row["description"] or "").startswith("Derived from uploaded data")}
        for row in rows
    ]


def simulate_experiment(
    experiment_id: str,
    scenario: str = "true_win",
    seed: int = 42,
    days: int | None = None,
    lift_abs: float | None = None,
) -> dict:
    """Replay a simulated experiment day by day through the real decision engine.

    The telemetry is generated with a known true effect, so the response carries
    both the ground truth and what the engine concluded -- which turns the demo
    into a check: did the engine actually recover the effect it was given?

    Everything written here is simulated. It is persisted like real telemetry so
    the timeline view works, and every response is tagged `synthetic: true`.
    """
    config = _load_experiment(experiment_id)
    generated = synthesize(config, SyntheticSpec(scenario=scenario, lift_abs=lift_abs, days=days, seed=seed))
    series: list[DayStats] = generated["days"]

    timeline: list[dict] = []
    final_decision: Decision | None = None
    pending: list[tuple[DayStats, Decision]] = []

    for index, day_stats in enumerate(series):
        # Narrate only the last day: the rest exist for the trend line and would
        # otherwise cost one model call each.
        is_last = index == len(series) - 1
        decision, _ = _decide_for_day(config, day_stats, None, allow_llm=is_last)
        pending.append((day_stats, decision))
        timeline.append(
            {
                "day": decision.day,
                "action": decision.action_code,
                "confidence": decision.confidence,
                "lift_abs": decision.reasoning_stats.lift_abs,
                "prob_beats_control": decision.reasoning_stats.prob_beats_control,
                "p_value": decision.reasoning_stats.p_value,
                "srm_flag": decision.reasoning_stats.srm_flag,
                "guardrail_breach": decision.reasoning_stats.guardrail_breach,
            }
        )
        if is_last:
            final_decision = decision

    # One transaction and one audit entry for the whole replay, rather than a
    # round trip per simulated day.
    _persist_days(pending)
    audit_event(
        "simulate_experiment",
        {"experiment_id": experiment_id, "scenario": scenario, "seed": seed, "days": len(series)},
        {"action": final_decision.action_code if final_decision else None, "synthetic": True},
    )

    ground_truth = generated["ground_truth"]
    engine_action = final_decision.action_code if final_decision else None
    return {
        "synthetic": True,
        "experiment_id": experiment_id,
        "ground_truth": ground_truth,
        "engine_action": engine_action,
        "engine_recovered_truth": engine_action == ground_truth["expected_action"],
        "timeline": timeline,
        "decision": final_decision.model_dump(mode="json") if final_decision else None,
    }


def list_experiments() -> list[dict]:
    """Every experiment with enough context to resume one after a page reload.

    Without this the UI can only ever act on whatever is in the current session,
    which is why the monitor step used to demand a hand-pasted experiment id.
    """
    ensure_seeded()
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, config, status FROM experiments").fetchall()
    finally:
        conn.close()

    experiments: list[dict] = []
    for row in rows:
        config = json.loads(row["config"])
        experiments.append(
            {
                "id": row["id"],
                "status": row["status"],
                "flag_key": config.get("flag_key"),
                "audience_segment": config.get("audience_segment"),
                "required_n_per_arm": config.get("required_n_per_arm"),
                "estimated_days": config.get("estimated_days"),
            }
        )
    # Running first, then validated/draft, then concluded -- the order a user
    # actually cares about when resuming work.
    rank = {"running": 0, "validated": 1, "draft": 2, "paused": 3, "concluded": 4}
    experiments.sort(key=lambda e: (rank.get(e["status"], 9), e["id"]))
    return experiments


def start_experiment(experiment_id: str) -> ExperimentConfig:
    config = _load_experiment(experiment_id)
    config.status = "running"
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE experiments SET config = ?, status = ? WHERE id = ?",
            (config.model_dump_json(), "running", experiment_id),
        )
        conn.execute(
            "UPDATE flags SET status = 'running', running_experiment_id = ? WHERE key = ?",
            (experiment_id, config.flag_key),
        )
        conn.execute("UPDATE feature_flags SET status = 'running' WHERE flag_key = ?", (config.flag_key,))
        conn.commit()
    finally:
        conn.close()
    return config


def conclude_experiment(experiment_id: str) -> ExperimentConfig:
    """Mark an experiment as concluded and free its flag/segment for reuse."""
    config = _load_experiment(experiment_id)
    config.status = "concluded"
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE experiments SET config = ?, status = ? WHERE id = ?",
            (config.model_dump_json(), "concluded", experiment_id),
        )
        conn.execute(
            "UPDATE flags SET status = 'free', running_experiment_id = NULL WHERE key = ?",
            (config.flag_key,),
        )
        conn.execute("UPDATE feature_flags SET status = 'free' WHERE flag_key = ?", (config.flag_key,))
        conn.commit()
    finally:
        conn.close()
    return config


def reset_all_experiments() -> dict:
    """Clear all experiments and free all flags. For testing/demo use only."""
    init_db()
    conn = get_conn()
    try:
        conn.execute("DELETE FROM decisions")
        conn.execute("DELETE FROM day_stats")
        conn.execute("DELETE FROM experiments")
        conn.execute("UPDATE flags SET status = 'free', running_experiment_id = NULL")
        conn.execute("UPDATE feature_flags SET status = 'free'")
        conn.commit()
    finally:
        conn.close()
    return {"status": "reset", "detail": "All experiments cleared, all flags freed."}



def analyze_day(
    day: DayStats,
    segments: list[SegmentDayStats] | None = None,
    allow_llm: bool = True,
) -> Decision:
    """Compute the deterministic decision for one day using evaluate_decision,
    then narrate it in business language.

    `allow_llm=False` forces the deterministic template narrative; used when
    replaying many days at once so the run does not make one model call per day.
    """
    config = _load_experiment(day.experiment_id)
    decision, narrative_source = _decide_for_day(config, day, segments, allow_llm=allow_llm)
    _persist_days([(day, decision)])
    audit_event(
        "analyze_day",
        {"experiment_id": day.experiment_id, "day": day.day, "segments": bool(segments)},
        {"action": decision.action_code, "narrative_source": narrative_source},
    )
    return decision


def _decide_for_day(
    config: ExperimentConfig,
    day: DayStats,
    segments: list[SegmentDayStats] | None = None,
    allow_llm: bool = True,
) -> tuple[Decision, str]:
    """The pure decision for one day: statistics in, Decision out, no database.

    Split out so a whole experiment can be replayed in memory and written once,
    instead of one transaction per day.
    """
    result = compute_day_stats(day, config, seed=day.day)
    rec = evaluate_decision(result, config)
    action = rec.action_code

    driver_analysis = None
    if segments:
        driver_analysis = analyze_drivers(result.lift_abs, segments)

    narrative, narrative_source = narrate_decision(rec, result, driver_analysis, allow_llm=allow_llm)

    decision = Decision(
        experiment_id=day.experiment_id,
        day=day.day,
        action=action,
        confidence=float(rec.confidence_score),
        reasoning_stats=result,
        narrative=narrative,
        requires_human=action in {"scale", "rollback", "stop", "pause"},
        human_verdict="pending",
        human_reason=None,
        recommendation=rec,
    )
    return decision, narrative_source


def _persist_days(rows: list[tuple[DayStats, Decision]]) -> None:
    """Write telemetry and decisions for one or more days in a single transaction.

    Batching matters against a hosted database: a per-day connection and commit
    cost several network round trips each, which made replaying a 14-day
    experiment take about a minute.
    """
    if not rows:
        return
    conn = get_conn()
    try:
        for day, decision in rows:
            conn.execute(
                """
                INSERT INTO day_stats(experiment_id, day, data) VALUES (?, ?, ?)
                ON CONFLICT(experiment_id, day) DO UPDATE SET data = excluded.data
                """,
                (day.experiment_id, day.day, day.model_dump_json()),
            )
            conn.execute(
                """
                INSERT INTO decisions(experiment_id, day, data) VALUES (?, ?, ?)
                ON CONFLICT(experiment_id, day) DO UPDATE SET data = excluded.data
                """,
                (day.experiment_id, day.day, decision.model_dump_json()),
            )
        conn.commit()
    finally:
        conn.close()


def get_timeline(experiment_id: str) -> dict:
    """The full day-by-day decision series for an experiment (Objective 5:
    'continuously monitors experiment performance'). A single POST /monitor
    call only ever shows one day; this is the trend.

    Calls init_db() defensively: unlike the other read paths in this module,
    this one may reasonably be the first call made against a given database
    (a monitoring dashboard polling before any experiment has been created
    in this process), and querying a table that does not exist yet should
    raise the documented KeyError, not a raw sqlite3.OperationalError.
    """
    init_db()
    _load_experiment(experiment_id)  # raises KeyError if unknown, matching other endpoints
    conn = get_conn()
    try:
        day_rows = conn.execute(
            "SELECT day, data FROM day_stats WHERE experiment_id = ? ORDER BY day", (experiment_id,)
        ).fetchall()
        decision_rows = conn.execute(
            "SELECT day, data FROM decisions WHERE experiment_id = ? ORDER BY day", (experiment_id,)
        ).fetchall()
    finally:
        conn.close()

    decisions_by_day = {row["day"]: json.loads(row["data"]) for row in decision_rows}
    series = []
    for row in day_rows:
        day_number = row["day"]
        entry = {"day": day_number, "telemetry": json.loads(row["data"])}
        decision = decisions_by_day.get(day_number)
        if decision:
            entry["action"] = decision["action"]
            entry["confidence"] = decision["confidence"]
            entry["lift_abs"] = decision["reasoning_stats"]["lift_abs"]
            entry["prob_beats_control"] = decision["reasoning_stats"]["prob_beats_control"]
            entry["narrative"] = decision["narrative"]
        series.append(entry)

    latest_action = series[-1]["action"] if series and "action" in series[-1] else None
    return {
        "experiment_id": experiment_id,
        "days_observed": len(series),
        "latest_action": latest_action,
        "series": series,
    }


def get_ontology(experiment_id: str) -> dict:
    conn = get_conn()
    try:
        row = conn.execute("SELECT ground_truth FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"Unknown experiment: {experiment_id}")
    return json.loads(row["ground_truth"])["ontology"]


def branch_ontology(experiment_id: str, parent_id: str, statement: str, rationale: str, segment: str) -> dict:
    """Add a queued child hypothesis; it is never launched automatically."""
    ontology = get_ontology(experiment_id)

    def visit(node: dict) -> bool:
        if node["id"] == parent_id:
            node.setdefault("children", []).append(
                {
                    "id": f"hyp_{uuid4().hex[:10]}",
                    "parent_id": parent_id,
                    "statement": statement,
                    "rationale": rationale,
                    "segment": segment,
                    "status": "queued",
                    "children": [],
                }
            )
            return True
        return any(visit(child) for child in node.get("children", []))

    if not visit(ontology):
        raise KeyError(f"Unknown ontology node: {parent_id}")
    conn = get_conn()
    try:
        conn.execute("UPDATE experiments SET ground_truth = ? WHERE id = ?", (json.dumps({"ontology": ontology}), experiment_id))
        conn.commit()
    finally:
        conn.close()
    return ontology


def propose_harness_gitops(experiment_id: str, action: str, segment: str | None = None) -> dict[str, str]:
    """Build the reviewable flag change for a terminal decision.

    Attaches the most recent stored decision so the manifest carries the numbers
    that justified the change; a reviewer should not have to go and look them up.
    """
    config = _load_experiment(experiment_id)
    decision = _latest_decision(experiment_id)
    return gitops_proposal(config, action, segment, decision)


def _latest_decision(experiment_id: str) -> Decision | None:
    """The highest-day decision on record, or None if nothing has been analysed."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT data FROM decisions WHERE experiment_id = ? ORDER BY day DESC LIMIT 1",
            (experiment_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return Decision.model_validate_json(row["data"])
    except Exception:  # noqa: BLE001 - provenance is a bonus, never a hard failure
        return None


def audit_event(node: str, input_data: dict, output_data: dict, thread_id: str | None = None) -> None:
    init_db()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO agent_runs(node, input, output, timestamp, thread_id) VALUES (?, ?, ?, ?, ?)",
            (node, json.dumps(input_data), json.dumps(output_data), datetime.now(UTC).isoformat(), thread_id),
        )
        conn.commit()
    finally:
        conn.close()
