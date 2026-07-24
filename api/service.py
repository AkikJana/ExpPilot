"""Application service: deterministic decisioning with Cursor-assisted ideation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from agents.llm import hypotheses_for_goal
from data.db import get_conn, init_db
from harness.gitops import gitops_proposal
from ontology.tree import initial_tree
from shared.models import DayStats, Decision, ExperimentConfig, Hypothesis
from stats.core import compute_day_stats, decide, power_analysis


def _dump(model: object) -> dict:
    return model.model_dump(mode="json")  # type: ignore[attr-defined]


def _load_experiment(experiment_id: str) -> ExperimentConfig:
    conn = get_conn()
    try:
        row = conn.execute("SELECT config FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise KeyError(f"Unknown experiment: {experiment_id}")
    return ExperimentConfig.model_validate_json(row["config"])


def create_experiment(goal: str, baseline_rate: float = 0.1, daily_traffic: int = 2000) -> dict:
    """Create a proposal; traffic math and validation remain deterministic."""
    init_db()
    candidates = hypotheses_for_goal(goal)
    candidate = candidates[0]
    hypothesis = Hypothesis(
        id=f"hyp_{uuid4().hex[:10]}",
        goal=goal,
        statement=candidate["statement"],
        primary_metric="conversion_rate",
        expected_direction=candidate.get("direction", "increase"),
        expected_mde=float(candidate.get("expected_mde", 0.01)),
        segment=candidate.get("segment", "all_users"),
        rationale=candidate.get("rationale", ""),
        precedent_ids=[],
    )
    required_n = power_analysis(baseline_rate, hypothesis.expected_mde)
    experiment_id = f"exp_{uuid4().hex[:10]}"
    config = ExperimentConfig(
        id=experiment_id,
        hypothesis_id=hypothesis.id,
        flag_key=f"{experiment_id}_flag",
        audience_segment=hypothesis.segment,
        traffic_split={"control": 0.5, "treatment": 0.5},
        baseline_rate=baseline_rate,
        mde=hypothesis.expected_mde,
        required_n_per_arm=required_n,
        estimated_days=max(1, round((2 * required_n) / daily_traffic)),
        guardrail_metrics=["error_rate"],
        daily_traffic=daily_traffic,
        status="validated",
    )
    tree = initial_tree(goal, candidates)
    conn = get_conn()
    try:
        conflict = conn.execute(
            "SELECT running_experiment_id FROM flags WHERE segment = ? AND status = 'running'",
            (config.audience_segment,),
        ).fetchone()
        if conflict:
            raise ValueError(f"Audience overlaps with live experiment {conflict['running_experiment_id']}")
        conn.execute(
            "INSERT INTO experiments(id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (config.id, config.model_dump_json(), config.status, json.dumps({"ontology": tree.as_dict()})),
        )
        conn.execute(
            "INSERT OR REPLACE INTO flags(key, segment, status, running_experiment_id) VALUES (?, ?, ?, ?)",
            (config.flag_key, config.audience_segment, "validated", None),
        )
        conn.commit()
    finally:
        conn.close()
    return {"hypotheses": candidates, "hypothesis": _dump(hypothesis), "config": _dump(config), "ontology": tree.as_dict()}


def start_experiment(experiment_id: str) -> ExperimentConfig:
    config = _load_experiment(experiment_id)
    config.status = "running"
    conn = get_conn()
    try:
        conn.execute("UPDATE experiments SET config = ?, status = ? WHERE id = ?", (config.model_dump_json(), "running", experiment_id))
        conn.execute("UPDATE flags SET status = 'running', running_experiment_id = ? WHERE key = ?", (experiment_id, config.flag_key))
        conn.commit()
    finally:
        conn.close()
    return config


def analyze_day(day: DayStats) -> Decision:
    config = _load_experiment(day.experiment_id)
    result = compute_day_stats(day, config, seed=day.day)
    action = decide(result, config)
    confidence = result.prob_beats_control if action == "scale" else 1 - result.prob_beats_control
    narrative = (
        f"Deterministic decision: {action.upper()}. "
        f"Day {day.day}; absolute lift {result.lift_abs:.2%}; "
        f"SRM p-value {result.srm_p_value:.4f}."
    )
    decision = Decision(
        experiment_id=day.experiment_id,
        day=day.day,
        action=action,
        confidence=float(confidence),
        reasoning_stats=result,
        narrative=narrative,
        requires_human=action in {"scale", "rollback", "stop", "pause"},
        human_verdict="pending",
        human_reason=None,
    )
    conn = get_conn()
    try:
        conn.execute("INSERT OR REPLACE INTO day_stats(experiment_id, day, data) VALUES (?, ?, ?)", (day.experiment_id, day.day, day.model_dump_json()))
        conn.execute("INSERT OR REPLACE INTO decisions(experiment_id, day, data) VALUES (?, ?, ?)", (day.experiment_id, day.day, decision.model_dump_json()))
        conn.commit()
    finally:
        conn.close()
    return decision


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
    return gitops_proposal(_load_experiment(experiment_id), action, segment)


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
