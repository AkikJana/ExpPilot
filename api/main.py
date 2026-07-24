"""FastAPI surface for ExpPilot. Thin adapters over api.service; no logic here."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from api import service
from evals import harness

app = FastAPI(title="ExpPilot API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    service.ensure_ready()


# ------------------------------- schemas ---------------------------------- #
class GoalRequest(BaseModel):
    goal: str


class ConfigRequest(BaseModel):
    hypothesis: dict
    category: str | None = None


class CreateRequest(BaseModel):
    config: dict
    scenario: str = "true_lift"
    seed: int = 2026


class VerdictRequest(BaseModel):
    verdict: str
    reason: str | None = None


# ------------------------------- routes ----------------------------------- #
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/flags")
def flags() -> list[dict]:
    return service.get_flags()


@app.get("/history")
def history(category: str | None = None) -> list[dict]:
    return service.get_history(category)


@app.post("/copilot/hypotheses")
def hypotheses(req: GoalRequest) -> dict:
    return service.copilot_hypotheses(req.goal)


@app.post("/copilot/config")
def config(req: ConfigRequest) -> dict:
    return service.copilot_config(req.hypothesis, req.category)


@app.post("/experiments")
def create(req: CreateRequest) -> dict:
    return service.create_experiment(req.config, req.scenario, req.seed)


@app.get("/experiments")
def experiments() -> list[dict]:
    return service.list_experiments()


@app.get("/experiments/{experiment_id}")
def experiment(experiment_id: str) -> dict:
    try:
        return service.get_experiment(experiment_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="experiment not found")


@app.post("/experiments/{experiment_id}/advance")
def advance(experiment_id: str) -> dict:
    try:
        return service.advance_experiment(experiment_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="experiment not found")


@app.post("/experiments/{experiment_id}/decisions/{day}/verdict")
def verdict(experiment_id: str, day: int, req: VerdictRequest) -> dict:
    try:
        return service.record_verdict(experiment_id, day, req.verdict, req.reason)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/experiments/{experiment_id}/audit")
def audit(experiment_id: str) -> dict:
    return service.get_audit(experiment_id)


@app.get("/memory")
def memory(kind: str | None = None, category: str | None = None) -> list[dict]:
    return service.get_memory(kind=kind, category=category)


@app.get("/metrics/adoption")
def adoption() -> dict:
    return service.adoption_stats()


@app.post("/evals/run")
def run_evals(seeds_per_scenario: int = 3) -> dict:
    return harness.run(seeds_per_scenario=seeds_per_scenario)
