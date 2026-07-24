"""Decision Service — FastAPI surface over the pure evaluator.

Stateless, synchronous, no LLM calls, no network calls during evaluation. This is
deliberately the most boring service in the platform (docs/distributed-architecture.md
§4, §7): the one thing that must never be down, so it does as little as possible.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from distributed.decision_service.audit import get_audit_sink
from distributed.decision_service.evaluator import DEFAULT_PACK, evaluate
from distributed.decision_service.schemas import EvaluationInput, EvaluationResult, RulePack

app = FastAPI(title="Decision Service", version="1.0.0")

# In-memory pack registry for this service instance. Production replaces this with
# the `rules.rule_packs` Postgres table (§2); the evaluator itself is unaware of
# where packs come from.
_PACKS: dict[str, RulePack] = {DEFAULT_PACK.id: DEFAULT_PACK}


class EvaluateRequest(BaseModel):
    input: EvaluationInput
    pack_id: str = DEFAULT_PACK.id


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/packs")
def list_packs() -> list[RulePack]:
    return list(_PACKS.values())


@app.get("/packs/{pack_id}")
def get_pack(pack_id: str) -> RulePack:
    pack = _PACKS.get(pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"rule pack '{pack_id}' not found")
    return pack


@app.post("/packs")
def register_pack(pack: RulePack) -> RulePack:
    """Register a new (or new-version) rule pack. Production gates this behind PR
    review + eval-harness regression (§4) — this endpoint is the mechanical half."""
    _PACKS[pack.id] = pack
    return pack


@app.post("/evaluate", response_model=EvaluationResult)
def evaluate_endpoint(req: EvaluateRequest) -> EvaluationResult:
    pack = _PACKS.get(req.pack_id)
    if pack is None:
        raise HTTPException(status_code=404, detail=f"rule pack '{req.pack_id}' not found")

    result = evaluate(pack, req.input)
    get_audit_sink().record(result)
    return result


@app.get("/experiments/{experiment_id}/history")
def evaluation_history(experiment_id: str) -> list[EvaluationResult]:
    return get_audit_sink().history(experiment_id)
