"""LangGraph orchestration for the two copilot pipelines.

- create graph:  retrieve -> hypothesize   (returns 3 grounded hypotheses)
- config graph:  config -> validate         (conditional gate: validated | blocked)
- analyze graph: stats -> monitor -> decide (SRM/guardrail route straight to decide)

If langgraph is unavailable the same node functions run through a tiny sequential
fallback, so the product never hard-depends on the framework being installed.
"""
from __future__ import annotations

from agents import nodes
from agents.state import AnalyzeState, CreateState

try:
    from langgraph.graph import END, START, StateGraph

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - exercised only when langgraph missing
    _HAS_LANGGRAPH = False


def _route_validation(state: dict) -> str:
    return "blocked" if state.get("validation", {}).get("blocked") else "ok"


def _route_quality(state: dict) -> str:
    """SRM/guardrail short-circuit straight to the decision (skip 'underpowered' noise)."""
    stats = state.get("stats", {})
    if stats.get("srm_flag") or stats.get("guardrail_breach"):
        return "critical"
    return "normal"


def build_hypotheses_graph():
    """retrieve -> hypothesize."""
    if not _HAS_LANGGRAPH:
        return None
    g = StateGraph(CreateState)
    g.add_node("retrieve", nodes.retrieve_node)
    g.add_node("hypothesize", nodes.hypothesis_node)
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "hypothesize")
    g.add_edge("hypothesize", END)
    return g.compile()


def build_config_graph():
    """config -> validate (conditional gate)."""
    if not _HAS_LANGGRAPH:
        return None
    g = StateGraph(CreateState)
    g.add_node("config", nodes.config_node)
    g.add_node("validate", nodes.validation_node)
    g.add_edge(START, "config")
    g.add_edge("config", "validate")
    g.add_conditional_edges("validate", _route_validation, {"ok": END, "blocked": END})
    return g.compile()


def build_analyze_graph():
    """stats -> monitor -> decide, with a critical short-circuit edge."""
    if not _HAS_LANGGRAPH:
        return None
    g = StateGraph(AnalyzeState)
    g.add_node("stats", nodes.stats_node)
    g.add_node("monitor", nodes.monitor_node)
    g.add_node("decide", nodes.decision_node)
    g.add_edge(START, "stats")
    g.add_conditional_edges("stats", _route_quality, {"critical": "monitor", "normal": "monitor"})
    g.add_edge("monitor", "decide")
    g.add_edge("decide", END)
    return g.compile()


# --------------------------------------------------------------------------- #
# Sequential fallbacks (used when langgraph is not installed) + thin runners
# --------------------------------------------------------------------------- #
def run_hypotheses(goal: str) -> dict:
    graph = build_hypotheses_graph()
    state = {"goal": goal}
    if graph is not None:
        return dict(graph.invoke(state))
    state.update(nodes.retrieve_node(state))
    state.update(nodes.hypothesis_node(state))
    return state


def run_config(chosen_hypothesis: dict, category: str | None = None) -> dict:
    graph = build_config_graph()
    state = {"chosen_hypothesis": chosen_hypothesis, "category": category}
    if graph is not None:
        return dict(graph.invoke(state))
    state.update(nodes.config_node(state))
    state.update(nodes.validation_node(state))
    return state


def run_analyze(experiment_id: str, scenario: str, seed: int, day: int, config: dict, precedents: list | None = None) -> dict:
    graph = build_analyze_graph()
    state = {
        "experiment_id": experiment_id,
        "scenario": scenario,
        "seed": seed,
        "day": day,
        "config": config,
        "precedents": precedents or [],
    }
    if graph is not None:
        return dict(graph.invoke(state))
    state.update(nodes.stats_node(state))
    state.update(nodes.monitor_node(state))
    state.update(nodes.decision_node(state))
    return state
