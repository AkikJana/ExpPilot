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
    propose_harness_gitops,
    start_experiment,
)
from shared.models import DayStats

app = FastAPI(title="ExpPilot", version="0.2.0")


class CreateRequest(BaseModel):
    goal: str = Field(min_length=5)
    baseline_rate: float = Field(default=0.1, gt=0, lt=1)
    daily_traffic: int = Field(default=2000, gt=0)


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
        return create_experiment(request.goal, request.baseline_rate, request.daily_traffic)
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
    try:
        return analyze_day(day).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
