"""FastAPI surface for the Experiment Copilot."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.graph import run_copilot
from api.service import (
    analyze_day,
    branch_ontology,
    conclude_experiment,
    create_experiment,
    get_ontology,
    get_timeline,
    list_experiments,
    list_segments,
    persist_derived_segments,
    propose_harness_gitops,
    simulate_experiment,
    start_experiment,
)
from data.db import DatabaseUnavailable, db_status
from data.seed import ensure_seeded
from shared.models import DayStats, ValidationBlocked

logger = logging.getLogger("exppilot.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create and seed the schema once, at boot.

    This used to happen lazily inside request handlers, which meant an
    unreachable database first showed up as a 500 in the middle of a user's
    flow. Doing it here surfaces the problem in the logs at startup instead --
    and the app still boots, so /health can explain what is wrong rather than
    the container crash-looping.
    """
    try:
        ensure_seeded()
        logger.info("database ready")
    except DatabaseUnavailable as exc:
        logger.error("database unavailable at startup: %s", exc)
    except Exception:  # noqa: BLE001 - never prevent boot; /health reports it
        logger.exception("unexpected error preparing the database")
    yield


app = FastAPI(title="ExpPilot", version="0.2.0", lifespan=lifespan)


@app.exception_handler(DatabaseUnavailable)
async def _database_unavailable(request: Request, exc: DatabaseUnavailable) -> JSONResponse:
    """503 with a fix, instead of an opaque 'Internal Server Error'."""
    logger.error("database unavailable on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={
            "error": "database_unavailable",
            "detail": str(exc),
            "hint": "The API is up but cannot reach its database. Check DATABASE_URL.",
        },
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Anything unforeseen still answers with the error class and message.

    A bare 500 body tells the operator nothing; this keeps the traceback in the
    logs but returns enough for the UI to show a real diagnosis.
    """
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": f"{type(exc).__name__}: {exc}"[:500],
            "hint": "This is a bug in ExpPilot, not your input. See the API logs for the traceback.",
        },
    )


class CreateRequest(BaseModel):
    goal: str = Field(min_length=5)
    # None (the default) lets the recommender derive baseline_rate and
    # daily_traffic from the recommended audience segment's real observed
    # data instead of guessing 0.1/2000 for every experiment regardless of
    # audience. An explicit value always overrides the recommendation.
    baseline_rate: float | None = Field(default=None, gt=0, lt=1)
    daily_traffic: int | None = Field(default=None, gt=0)
    segment_key: str | None = None
    # Which generated hypothesis to configure. Defaults to the top-ranked one,
    # so existing callers are unaffected.
    hypothesis_index: int = Field(default=0, ge=0)


class CopilotRequest(CreateRequest):
    telemetry: dict | None = None


class BranchRequest(BaseModel):
    parent_id: str
    statement: str = Field(min_length=10)
    rationale: str = Field(min_length=3)
    segment: str = Field(min_length=2)


class GitOpsRequest(BaseModel):
    action: str
    segment: str | None = None


class DerivedSegment(BaseModel):
    """One audience segment measured from the caller's transactional data."""

    segment_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    population: int = Field(gt=0)
    daily_traffic: int = Field(gt=0)
    baseline_conversion_rate: float = Field(gt=0, lt=1)
    description: str = "Derived from uploaded data"


class DerivedSegmentsRequest(BaseModel):
    segments: list[DerivedSegment] = Field(min_length=1)
    # Off by default: seeded historical_experiments reference seeded segment keys,
    # so clearing them would strand every precedent.
    replace_seeded: bool = False


class SimulateRequest(BaseModel):
    scenario: Literal["true_win", "no_effect", "true_loss", "srm", "guardrail_breach"] = "true_win"
    seed: int = 42
    days: int | None = Field(default=None, gt=0, le=365)
    lift_abs: float | None = Field(default=None, gt=-1, lt=1)


@app.get("/health")
def health() -> dict:
    """Liveness plus whether the database behind it is actually reachable."""
    database = db_status()
    return {"status": "ok" if database["reachable"] else "degraded", "database": database}


@app.get("/experiments")
def list_all() -> dict:
    """All known experiments, so a client can resume one without a stored id."""
    return {"experiments": list_experiments()}


@app.get("/segments")
def segments() -> dict:
    """The audience catalog, marking which rows were derived from real uploads."""
    return {"segments": list_segments()}


@app.post("/segments/derived")
def save_derived_segments(request: DerivedSegmentsRequest) -> dict:
    """Persist audience segments measured from the caller's own transactional data.

    The caller does the aggregation and posts the handful of resulting rows --
    a transaction log can be hundreds of thousands of rows and has no business
    being shipped through JSON.
    """
    try:
        return persist_derived_segments(
            [segment.model_dump() for segment in request.segments],
            replace_seeded=request.replace_seeded,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/experiments/{experiment_id}/simulate")
def simulate(experiment_id: str, request: SimulateRequest) -> dict:
    """Replay simulated telemetry with a known true effect through the engine.

    Response is tagged `synthetic: true` and reports whether the engine's verdict
    matched the ground truth it was given.
    """
    try:
        return simulate_experiment(
            experiment_id,
            scenario=request.scenario,
            seed=request.seed,
            days=request.days,
            lift_abs=request.lift_abs,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/experiments")
def create(request: CreateRequest) -> dict:
    try:
        return create_experiment(
            request.goal,
            request.baseline_rate,
            request.daily_traffic,
            request.segment_key,
            request.hypothesis_index,
        )
    except ValidationBlocked as exc:
        # 409 as before, but the body carries the structured report so the caller
        # can render each failed check instead of one run-together sentence.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "validation_blocked",
                "detail": str(exc),
                "validation": exc.report.as_dict(),
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/copilot/run")
def run_agent_graph(request: CopilotRequest) -> dict:
    """Execute the LangGraph setup flow, optionally including one monitor pass."""
    try:
        return run_copilot(request.goal, request.baseline_rate, request.daily_traffic, request.telemetry)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/experiments/{experiment_id}/start")
def start(experiment_id: str) -> dict:
    try:
        return start_experiment(experiment_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/experiments/{experiment_id}/conclude")
def conclude(experiment_id: str) -> dict:
    try:
        return conclude_experiment(experiment_id).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/reset")
def reset_db() -> dict:
    from api.service import reset_all_experiments
    return reset_all_experiments()



@app.get("/experiments/{experiment_id}/ontology")
def ontology(experiment_id: str) -> dict:
    try:
        return get_ontology(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/experiments/{experiment_id}/ontology/branches")
def branch(experiment_id: str, request: BranchRequest) -> dict:
    try:
        return branch_ontology(experiment_id, request.parent_id, request.statement, request.rationale, request.segment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/experiments/{experiment_id}/harness-gitops")
def harness_gitops(experiment_id: str, request: GitOpsRequest) -> dict[str, str]:
    try:
        return propose_harness_gitops(experiment_id, request.action, request.segment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/monitor")
def monitor(day: DayStats) -> dict:
    """Run one day's decision. `day.segments`, when supplied, unlocks the
    driver diagnostics behind the business narrative (Objective 6). The
    request body shape is unchanged from before that field existed --
    callers that only send the aggregate fields are unaffected."""
    try:
        return analyze_day(day, day.segments or None).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/experiments/{experiment_id}/timeline")
def timeline(experiment_id: str) -> dict:
    """The full day-by-day decision series for continuous monitoring
    (Objective 5) -- a single POST /monitor call only ever shows one day."""
    try:
        return get_timeline(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
