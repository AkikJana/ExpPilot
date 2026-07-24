"""Scenario-generation pipeline (the LLM stage of the engine).

Wraps a switchable backend behind a stable, Hugging-Face-pipeline-style call:
``ScenarioPipeline(config)(request)`` -> validated ``ScenarioSpec``. Supports
batch generation for building eval/gold sets quickly.
"""
from __future__ import annotations

from synthgen.backends import get_backend
from synthgen.capabilities import Capabilities, detect
from synthgen.config import SynthGenConfig
from synthgen.spec import ScenarioRequest, ScenarioSpec


class ScenarioPipeline:
    """Turns natural-language goals into typed, simulator-ready scenario specs."""

    def __init__(self, config: SynthGenConfig | None = None, caps: Capabilities | None = None):
        self.config = config or SynthGenConfig.from_env()
        self.caps = caps or detect()
        self.backend = get_backend(self.config, self.caps)

    @property
    def backend_name(self) -> str:
        return getattr(self.backend, "name", "unknown")

    def __call__(self, request: ScenarioRequest | str) -> ScenarioSpec:
        if isinstance(request, str):
            request = ScenarioRequest(goal=request)
        spec = self.backend.propose(request, self.config)
        # honor per-request size overrides
        if request.n_rows:
            spec.daily_traffic = max(1, request.n_rows // spec.n_days)
        return spec

    def batch(self, goals: list[str]) -> list[ScenarioSpec]:
        return [self(g) for g in goals]
