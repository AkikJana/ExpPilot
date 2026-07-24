"""FastAPI surface for the Experiment Copilot."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents.graph import run_copilot
from api.service import (
    analyze_day,
    branch_ontology,
    create_experiment,
    get_ontology,
    get_timeline,
    propose_harness_gitops,
    start_experiment,
)
from shared.models import DayStats

app = FastAPI(title="ExpPilot", version="0.2.0")


class CreateRequest(BaseModel):
    goal: str = Field(min_length=5)
    # None (the default) lets the recommender derive baseline_rate and
    # daily_traffic from the recommended audience segment's real observed
    # data instead of guessing 0.1/2000 for every experiment regardless of
    # audience. An explicit value always overrides the recommendation.
    baseline_rate: float | None = Field(default=None, gt=0, lt=1)
    daily_traffic: int | None = Field(default=None, gt=0)
    segment_key: str | None = None


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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/experiments")
def create(request: CreateRequest) -> dict:
    try:
        return create_experiment(
            request.goal, request.baseline_rate, request.daily_traffic, request.segment_key
        )
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
