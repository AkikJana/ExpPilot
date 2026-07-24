"""Typed graph-state definitions for the LangGraph orchestration."""
from __future__ import annotations

from typing import Any, TypedDict


class CreateState(TypedDict, total=False):
    """State threaded through the create/validate pipeline."""

    goal: str
    category: str
    precedents: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    chosen_hypothesis: dict[str, Any]
    config: dict[str, Any]
    validation: dict[str, Any]
    logs: list[str]


class AnalyzeState(TypedDict, total=False):
    """State threaded through the monitor/decide pipeline for one day."""

    experiment_id: str
    scenario: str
    seed: int
    day: int
    config: dict[str, Any]
    precedents: list[dict[str, Any]]
    stats: dict[str, Any]
    action: str
    alerts: list[dict[str, Any]]
    decision: dict[str, Any]
    logs: list[str]
