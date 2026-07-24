"""LangGraph orchestration for the two copilot pipelines.

- create graph:  retrieve -> hypothesize   (returns 3 grounded hypotheses)
- config graph:  config -> validate         (conditional gate: validated | blocked)
- analyze graph: stats -> monitor -> decide (SRM/guardrail route straight to decide)

If langgraph is unavailable the same node functions run through a tiny sequential
fallback, so the product never hard-depends on the framework being installed.

Every node is wrapped by `_audited`, which appends a row to `agent_runs` and opens an
MLflow span. Wrapping here rather than inside each node keeps the audit trail in one
place and covers the sequential fallback path identically to the compiled graphs.
"""
from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone

from agents import nodes
from agents.state import AnalyzeState, CreateState
from data.db import get_conn

try:
    from langgraph.graph import END, START, StateGraph

    _HAS_LANGGRAPH = True
except Exception:  # pragma: no cover - exercised only when langgraph missing
    _HAS_LANGGRAPH = False

try:
    import mlflow

    _HAS_MLFLOW = True
except Exception:  # pragma: no cover
    _HAS_MLFLOW = False


@contextlib.contextmanager
def _span(name: str):
    """Open an MLflow span if tracing is available, else a no-op.

    Entered and exited explicitly rather than with a nested `with`: wrapping the yield in
    a try/except would swallow exceptions thrown in from the body and then yield a second
    time, which raises "generator didn't stop after throw()". Tracing failures are
    suppressed; failures in the wrapped node are not.
    """
    span = None
    if _HAS_MLFLOW:
        try:
            span = mlflow.start_span(name=name)
            span.__enter__()
        except Exception:
            span = None
    try:
        yield
    finally:
        if span is not None:
            try:
                span.__exit__(None, None, None)
            except Exception:
                pass


def _write_agent_run(node: str, state_in: dict, state_out: dict) -> None:
    """Append one row to agent_runs — the audit trail of record. Never raises."""
    try:
        conn = get_conn()
        try:
            conn.execute(
                "INSERT INTO agent_runs (node, input, output, timestamp, thread_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    node,
                    json.dumps(state_in, default=str)[:20000],
                    json.dumps(state_out, default=str)[:20000],
                    datetime.now(timezone.utc).isoformat(),
                    str(state_in.get("experiment_id") or state_in.get("goal") or ""),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        # Auditing must never break the pipeline it observes.
        pass


def _audited(fn, name: str):
    """Wrap a node so every invocation is traced and recorded in agent_runs."""

    def _wrapped(state):
        with _span(name):
            out = fn(state)
        _write_agent_run(name, dict(state), dict(out))
        return out

    _wrapped.__name__ = name
    return _wrapped


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
    g.add_node("retrieve", _audited(nodes.retrieve_node, "retrieve"))
    g.add_node("hypothesize", _audited(nodes.hypothesis_node, "hypothesize"))
    g.add_edge(START, "retrieve")
    g.add_edge("retrieve", "hypothesize")
    g.add_edge("hypothesize", END)
    return g.compile()


def build_config_graph():
    """config -> validate (conditional gate)."""
    if not _HAS_LANGGRAPH:
        return None
    g = StateGraph(CreateState)
    # Node names must not collide with CreateState keys ('config'), or StateGraph raises
    # "already being used as a state key" the moment langgraph is installed.
    g.add_node("design_config", _audited(nodes.config_node, "design_config"))
    g.add_node("validate", _audited(nodes.validation_node, "validate"))
    g.add_edge(START, "design_config")
    g.add_edge("design_config", "validate")
    g.add_conditional_edges("validate", _route_validation, {"ok": END, "blocked": END})
    return g.compile()


def build_analyze_graph():
    """stats -> monitor -> decide, with a critical short-circuit edge."""
    if not _HAS_LANGGRAPH:
        return None
    g = StateGraph(AnalyzeState)
    # 'stats' is an AnalyzeState key, so the node must be named differently (see above).
    g.add_node("compute_stats", _audited(nodes.stats_node, "compute_stats"))
    g.add_node("monitor", _audited(nodes.monitor_node, "monitor"))
    g.add_node("decide", _audited(nodes.decision_node, "decide"))
    g.add_edge(START, "compute_stats")
    g.add_conditional_edges(
        "compute_stats", _route_quality, {"critical": "monitor", "normal": "monitor"}
    )
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
    state.update(_audited(nodes.retrieve_node, "retrieve")(state))
    state.update(_audited(nodes.hypothesis_node, "hypothesize")(state))
    return state


def run_config(chosen_hypothesis: dict, category: str | None = None) -> dict:
    graph = build_config_graph()
    state = {"chosen_hypothesis": chosen_hypothesis, "category": category}
    if graph is not None:
        return dict(graph.invoke(state))
    state.update(_audited(nodes.config_node, "design_config")(state))
    state.update(_audited(nodes.validation_node, "validate")(state))
    return state


def run_analyze(
    experiment_id: str,
    scenario: str,
    seed: int,
    day: int,
    config: dict,
    precedents: list | None = None,
) -> dict:
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
    state.update(_audited(nodes.stats_node, "compute_stats")(state))
    state.update(_audited(nodes.monitor_node, "monitor")(state))
    state.update(_audited(nodes.decision_node, "decide")(state))
    return state
