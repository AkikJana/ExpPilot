"""FastAPI surface for ExpPilot. Thin orchestration only — no business logic, no statistics.

Starts and serves without an LLM_API_KEY: the monitoring loop (launch -> advance -> decide)
is fully deterministic and keyless. Only the generative design endpoints require a key, and
they return 503 when it is absent.
"""
from __future__ import annotations

import json
import math
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agents import memory as agent_memory
from data.db import get_conn, init_db
from data.synth import SCENARIOS, make_experiment
from shared.models import Alert, Decision, ExperimentConfig, Hypothesis, MemoryRecord, StatsResult

app = FastAPI(title="ExpPilot API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "http://localhost:8501", "http://127.0.0.1:8501"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    """Ensure the schema exists before serving any request."""
    init_db()


# ---------------------------------------------------------------------------
# Request / response contracts
# ---------------------------------------------------------------------------


class GoalRequest(BaseModel):
    """Body for POST /hypotheses."""

    goal: str


class Clarification(BaseModel):
    """Returned instead of a Hypothesis when the goal names no measurable metric."""

    needs_clarification: bool
    question: str
    options: list[str]


class HypothesisResponse(BaseModel):
    """Either a hypothesis or a clarification request — never both."""

    hypothesis: Hypothesis | None = None
    clarification: Clarification | None = None


class DesignRequest(BaseModel):
    """Body for POST /experiments."""

    hypothesis_id: str
    answers: dict[str, str] | None = None


class DesignResponse(BaseModel):
    """A validated config, or the validation errors that blocked it."""

    config: ExperimentConfig | None = None
    validation_errors: list[str] = []
    validation_message: str | None = None


class AdvanceRequest(BaseModel):
    """Body for POST /experiments/{id}/advance."""

    days: int = 1


class TickResponse(BaseModel):
    """The full result of one monitoring tick."""

    experiment_id: str
    day: int
    stats: StatsResult
    alert: Alert
    decision: Decision


class VerdictRequest(BaseModel):
    """Body for POST /decisions/{exp_id}/verdict."""

    verdict: Literal["approved", "rejected"]
    reason: str


class ExperimentSummary(BaseModel):
    """One row of GET /experiments."""

    id: str
    status: str
    flag_key: str
    audience_segment: str
    latest_day: int | None


class ExperimentDetail(BaseModel):
    """GET /experiments/{id} — config plus the most recent stats and decision, if any."""

    config: ExperimentConfig
    status: str
    latest_day: int | None
    latest_stats: StatsResult | None
    latest_decision: Decision | None


class ExperimentList(BaseModel):
    """GET /experiments."""

    experiments: list[ExperimentSummary]


class AgentRun(BaseModel):
    """One audit-trail row."""

    node: str
    input: str
    output: str
    timestamp: str


class AuditResponse(BaseModel):
    """GET /experiments/{id}/audit."""

    experiment_id: str
    decisions: list[Decision]
    agent_runs: list[AgentRun]


class MemoryResponse(BaseModel):
    """GET /memory."""

    records: list[MemoryRecord]


class Flag(BaseModel):
    """One feature-flag registry entry."""

    key: str
    segment: str
    status: str
    running_experiment_id: str | None


class FlagList(BaseModel):
    """GET /flags."""

    flags: list[Flag]


class EvalSummary(BaseModel):
    """POST /evals/run."""

    overall_accuracy: float
    aa_false_positive_rate: float
    srm_detection_rate: float
    mean_days_to_decision: float
    per_scenario_accuracy: dict[str, float]
    n_experiments: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_llm() -> None:
    """Raise 503 when an LLM-dependent endpoint is called without a configured key."""
    if not os.environ.get("LLM_API_KEY"):
        raise HTTPException(status_code=503, detail="LLM_API_KEY not set")


def _load_experiment_row(experiment_id: str):
    """Fetch an experiment row or raise 404."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id, config, status FROM experiments WHERE id = ?", (experiment_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"experiment '{experiment_id}' not found")
    return row


def _latest_day(experiment_id: str) -> int | None:
    """Highest revealed day, or None. Unrevealed days are stored negated, so filter to day > 0."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT MAX(day) AS d FROM day_stats WHERE experiment_id = ? AND day > 0", (experiment_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["d"] if row and row["d"] is not None else None


def _latest_decision(experiment_id: str) -> Decision | None:
    """Most recent persisted Decision for an experiment, or None."""
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT data FROM decisions WHERE experiment_id = ? ORDER BY day DESC LIMIT 1", (experiment_id,)
        ).fetchone()
    finally:
        conn.close()
    return Decision.model_validate_json(row["data"]) if row else None


def _set_status(experiment_id: str, status: str) -> None:
    """Update an experiment's status column and its stored config JSON in step."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT config FROM experiments WHERE id = ?", (experiment_id,)).fetchone()
        if row is None:
            return
        config = json.loads(row["config"])
        config["status"] = status
        conn.execute(
            "UPDATE experiments SET status = ?, config = ? WHERE id = ?",
            (status, json.dumps(config), experiment_id),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Design endpoints (LLM-dependent)
# ---------------------------------------------------------------------------


@app.post("/hypotheses", response_model=HypothesisResponse)
def create_hypothesis(body: GoalRequest) -> HypothesisResponse:
    """Run hypothesis_node on a business goal; returns a Hypothesis or a clarification request."""
    from agents import nodes

    # hypothesis_node decides keylessly whether the goal needs clarification and only then
    # calls the LLM, so run it first and convert a missing key into the documented 503.
    try:
        result = nodes.hypothesis_node({"goal": body.goal})
    except nodes.LLMNotConfiguredError:
        raise HTTPException(status_code=503, detail="LLM_API_KEY not set")

    if result.get("needs_clarification"):
        return HypothesisResponse(clarification=Clarification(**result["needs_clarification"]))

    hypothesis = Hypothesis.model_validate(result["hypothesis"])
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO memory (id, kind, category, content, source_experiment_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"hyp_cache_{hypothesis.id}",
                "episodic",
                "hypothesis",
                hypothesis.model_dump_json(),
                None,
                agent_memory.now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return HypothesisResponse(hypothesis=hypothesis)


@app.post("/experiments", response_model=DesignResponse)
def design_experiment(body: DesignRequest) -> DesignResponse:
    """Continue the design flow through designer + validator for a stored hypothesis."""
    _require_llm()
    from agents import nodes

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT content FROM memory WHERE id = ?", (f"hyp_cache_{body.hypothesis_id}",)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"hypothesis '{body.hypothesis_id}' not found")

    hypothesis = json.loads(row["content"])
    state: dict = {"hypothesis": hypothesis, "repair_loops": 0}
    state.update(nodes.designer_node(state))
    state.update(nodes.validator_node(state))

    errors = state.get("validation_errors") or []
    if errors:
        return DesignResponse(
            config=None, validation_errors=errors, validation_message=state.get("validation_message")
        )
    return DesignResponse(config=ExperimentConfig.model_validate(state["config"]))


# ---------------------------------------------------------------------------
# Lifecycle endpoints (keyless)
# ---------------------------------------------------------------------------


@app.post("/experiments/{experiment_id}/launch", response_model=ExperimentDetail)
def launch_experiment(
    experiment_id: str, demo_scenario: str = Query(default="true_lift")
) -> ExperimentDetail:
    """Attach a synthetic ground-truth data stream to a validated config and set it running."""
    if demo_scenario not in SCENARIOS:
        raise HTTPException(
            status_code=422, detail=f"unknown demo_scenario '{demo_scenario}', must be one of {list(SCENARIOS)}"
        )

    row = _load_experiment_row(experiment_id)
    if row["status"] != "validated":
        raise HTTPException(
            status_code=409,
            detail=f"experiment '{experiment_id}' has status '{row['status']}', must be 'validated' to launch",
        )

    stored_config = json.loads(row["config"])
    _, day_stats, ground_truth = make_experiment(demo_scenario, seed=abs(hash(experiment_id)) % 10_000)

    conn = get_conn()
    try:
        conn.execute(
            "UPDATE experiments SET status = ?, ground_truth = ? WHERE id = ?",
            ("running", json.dumps(ground_truth), experiment_id),
        )
        stored_config["status"] = "running"
        conn.execute("UPDATE experiments SET config = ? WHERE id = ?", (json.dumps(stored_config), experiment_id))
        for day in day_stats:
            payload = day.model_dump()
            payload["experiment_id"] = experiment_id
            conn.execute(
                "INSERT OR REPLACE INTO day_stats (experiment_id, day, data) VALUES (?, ?, ?)",
                (experiment_id, 0 - payload["day"], json.dumps(payload)),
            )
        conn.commit()
    finally:
        conn.close()

    return ExperimentDetail(
        config=ExperimentConfig.model_validate(stored_config),
        status="running",
        latest_day=None,
        latest_stats=None,
        latest_decision=None,
    )


@app.post("/experiments/{experiment_id}/advance", response_model=TickResponse)
def advance_experiment(experiment_id: str, body: AdvanceRequest) -> TickResponse:
    """Reveal the next day(s) of data and run a monitoring tick. Fully deterministic, no LLM."""
    import agents.graph as graph

    row = _load_experiment_row(experiment_id)
    if row["status"] not in {"running", "paused"}:
        raise HTTPException(
            status_code=409,
            detail=f"experiment '{experiment_id}' has status '{row['status']}', must be running to advance",
        )
    if body.days < 1:
        raise HTTPException(status_code=422, detail="days must be >= 1")

    config = json.loads(row["config"])
    current = _latest_day(experiment_id) or 0

    conn = get_conn()
    try:
        for _ in range(body.days):
            target = current + 1
            pending = conn.execute(
                "SELECT data FROM day_stats WHERE experiment_id = ? AND day = ?", (experiment_id, -target)
            ).fetchone()
            if pending is None:
                break
            conn.execute(
                "INSERT OR REPLACE INTO day_stats (experiment_id, day, data) VALUES (?, ?, ?)",
                (experiment_id, target, pending["data"]),
            )
            conn.execute("DELETE FROM day_stats WHERE experiment_id = ? AND day = ?", (experiment_id, -target))
            current = target
        conn.commit()
    finally:
        conn.close()

    if current == 0:
        raise HTTPException(status_code=409, detail=f"experiment '{experiment_id}' has no remaining days to reveal")

    state = graph.run_monitor_tick(experiment_id, current, config, thread_id=f"{experiment_id}-day{current}")
    decision = Decision.model_validate(state["decision"])

    if decision.action in {"pause", "rollback", "stop"} and not decision.requires_human:
        _set_status(experiment_id, "paused" if decision.action == "pause" else "concluded")

    return TickResponse(
        experiment_id=experiment_id,
        day=current,
        stats=StatsResult.model_validate(state["stats_result"]),
        alert=Alert.model_validate(state["alert"]),
        decision=decision,
    )


@app.post("/decisions/{experiment_id}/verdict", response_model=Decision)
def submit_verdict(experiment_id: str, body: VerdictRequest) -> Decision:
    """Resume a decision paused at the human gate with an approve/reject verdict."""
    import agents.graph as graph

    _load_experiment_row(experiment_id)
    pending = _latest_decision(experiment_id)
    if pending is None:
        raise HTTPException(status_code=404, detail=f"no decision recorded for experiment '{experiment_id}'")
    if not pending.requires_human or pending.human_verdict != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"decision for '{experiment_id}' day {pending.day} is not awaiting a human verdict",
        )

    state = graph.resume_human_gate(
        experiment_id, pending.day, body.verdict, body.reason, thread_id=f"{experiment_id}-day{pending.day}"
    )
    decision = Decision.model_validate(state["decision"])

    if body.verdict == "approved":
        _set_status(experiment_id, "concluded" if decision.action in {"scale", "rollback"} else "running")

    return decision


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


@app.get("/experiments", response_model=ExperimentList)
def list_experiments() -> ExperimentList:
    """List every experiment with its status and latest revealed day."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, config, status FROM experiments ORDER BY id").fetchall()
    finally:
        conn.close()

    summaries = []
    for row in rows:
        config = json.loads(row["config"])
        summaries.append(
            ExperimentSummary(
                id=row["id"],
                status=row["status"],
                flag_key=config.get("flag_key", ""),
                audience_segment=config.get("audience_segment", ""),
                latest_day=_latest_day(row["id"]),
            )
        )
    return ExperimentList(experiments=summaries)


@app.get("/experiments/{experiment_id}", response_model=ExperimentDetail)
def get_experiment(experiment_id: str) -> ExperimentDetail:
    """Experiment detail with its most recent stats and decision."""
    row = _load_experiment_row(experiment_id)
    config = ExperimentConfig.model_validate(json.loads(row["config"]))
    decision = _latest_decision(experiment_id)
    return ExperimentDetail(
        config=config,
        status=row["status"],
        latest_day=_latest_day(experiment_id),
        latest_stats=decision.reasoning_stats if decision else None,
        latest_decision=decision,
    )


@app.get("/experiments/{experiment_id}/audit", response_model=AuditResponse)
def get_audit(experiment_id: str) -> AuditResponse:
    """Full audit trail: every decision and every agent run for this experiment."""
    _load_experiment_row(experiment_id)
    conn = get_conn()
    try:
        decision_rows = conn.execute(
            "SELECT data FROM decisions WHERE experiment_id = ? ORDER BY day", (experiment_id,)
        ).fetchall()
        run_rows = conn.execute(
            "SELECT node, input, output, timestamp FROM agent_runs WHERE input LIKE ? OR output LIKE ? ORDER BY id",
            (f"%{experiment_id}%", f"%{experiment_id}%"),
        ).fetchall()
    finally:
        conn.close()

    return AuditResponse(
        experiment_id=experiment_id,
        decisions=[Decision.model_validate_json(r["data"]) for r in decision_rows],
        agent_runs=[
            AgentRun(node=r["node"], input=r["input"], output=r["output"], timestamp=r["timestamp"])
            for r in run_rows
        ],
    )


@app.get("/memory", response_model=MemoryResponse)
def get_memory(kind: str | None = None, category: str | None = None) -> MemoryResponse:
    """Memory records, optionally filtered by kind and category."""
    return MemoryResponse(records=agent_memory.fetch_all(kind=kind, category=category))


@app.get("/flags", response_model=FlagList)
def get_flags() -> FlagList:
    """The feature-flag registry."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT key, segment, status, running_experiment_id FROM flags ORDER BY key").fetchall()
    finally:
        conn.close()
    return FlagList(flags=[Flag(**dict(r)) for r in rows])


@app.post("/evals/run", response_model=EvalSummary)
def run_evals(n_per_scenario: int = 6, seed_base: int = 100) -> EvalSummary:
    """Run the eval harness and return its summary metrics."""
    from evals.run_evals import run_harness

    summary = run_harness(n_per_scenario=n_per_scenario, seed_base=seed_base)
    return EvalSummary(**summary)
