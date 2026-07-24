"""LangGraph graph definitions: the design flow and the per-tick monitor flow.

Two compiled graphs share the same SqliteSaver checkpointer (checkpoints.db) and the
same node implementations from agents/nodes.py. Splitting them mirrors the two
separate invocation points described in the master build prompt: a single
goal -> validated-config run for design, and one tick per day for monitoring.
"""
from __future__ import annotations

import contextlib
import os

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from agents import nodes
from agents.nodes import GraphState
from shared.models import Decision

try:
    import mlflow
    import mlflow.langchain

    mlflow.langchain.autolog()
    _MLFLOW_AVAILABLE = True
except Exception:
    _MLFLOW_AVAILABLE = False

_CHECKPOINTS_DB = os.path.join(os.path.dirname(__file__), "..", "checkpoints.db")

MAX_REPAIR_LOOPS = 2


@contextlib.contextmanager
def _span(name: str):
    """Manual MLflow tracing span, falling back to a no-op if autolog doesn't cover this call."""
    if _MLFLOW_AVAILABLE:
        try:
            with mlflow.start_span(name=name):
                yield
                return
        except Exception:
            pass
    yield


def _traced(fn, name: str):
    """Wrap a node function so every invocation is captured as an MLflow span."""

    def _wrapped(state: GraphState) -> dict:
        with _span(name):
            return fn(state)

    _wrapped.__name__ = name
    return _wrapped


def _route_after_hypothesis(state: GraphState) -> str:
    """END immediately if hypothesis_node asked for clarification instead of a Hypothesis."""
    return END if state.get("needs_clarification") else "designer_node"


def _route_after_validator(state: GraphState) -> str:
    """Loop back to the designer up to MAX_REPAIR_LOOPS times on validation failure, else END."""
    if state.get("validation_errors") and state.get("repair_loops", 0) < MAX_REPAIR_LOOPS:
        return "designer_node"
    return END


def _build_design_graph(checkpointer):
    """entry -> hypothesis_node -> designer_node -> validator_node -> (repair loop | END)."""
    builder = StateGraph(GraphState)
    builder.add_node("hypothesis_node", _traced(nodes.hypothesis_node, "hypothesis_node"))
    builder.add_node("designer_node", _traced(nodes.designer_node, "designer_node"))
    builder.add_node("validator_node", _traced(nodes.validator_node, "validator_node"))

    builder.add_edge(START, "hypothesis_node")
    builder.add_conditional_edges(
        "hypothesis_node", _route_after_hypothesis, {"designer_node": "designer_node", END: END}
    )
    builder.add_edge("designer_node", "validator_node")
    builder.add_conditional_edges(
        "validator_node", _route_after_validator, {"designer_node": "designer_node", END: END}
    )
    return builder.compile(checkpointer=checkpointer)


def _build_monitor_graph(checkpointer):
    """monitor_node -> analyst_node -> decision_node -> human_gate(interrupt) -> reflection_node -> END."""
    builder = StateGraph(GraphState)
    builder.add_node("monitor_node", _traced(nodes.monitor_node, "monitor_node"))
    builder.add_node("analyst_node", _traced(nodes.analyst_node, "analyst_node"))
    builder.add_node("decision_node", _traced(nodes.decision_node, "decision_node"))
    builder.add_node("human_gate", _traced(nodes.human_gate, "human_gate"))
    builder.add_node("reflection_node", _traced(nodes.reflection_node, "reflection_node"))

    builder.add_edge(START, "monitor_node")
    builder.add_edge("monitor_node", "analyst_node")
    builder.add_edge("analyst_node", "decision_node")
    builder.add_edge("decision_node", "human_gate")
    builder.add_edge("human_gate", "reflection_node")
    builder.add_edge("reflection_node", END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=["human_gate"])


_checkpointer_cm = SqliteSaver.from_conn_string(_CHECKPOINTS_DB)
checkpointer = _checkpointer_cm.__enter__()

design_graph = _build_design_graph(checkpointer)
monitor_graph = _build_monitor_graph(checkpointer)


def run_design_flow(goal: str, thread_id: str) -> GraphState:
    """Invoke the design flow from a fresh goal. Resumable by thread_id across process restarts."""
    config = {"configurable": {"thread_id": thread_id}}
    return design_graph.invoke({"goal": goal, "repair_loops": 0}, config)


def resume_design_flow(thread_id: str) -> GraphState:
    """Resume a design flow thread from its last checkpoint (e.g. after a process restart)."""
    config = {"configurable": {"thread_id": thread_id}}
    return design_graph.invoke(None, config)


def run_monitor_tick(experiment_id: str, day: int, config: dict, thread_id: str | None = None) -> GraphState:
    """Run one monitor tick. Auto-resumes the human_gate interrupt when the action needs no approval.

    Resuming a static interrupt_before pause requires update_state() + invoke(None, ...): invoking
    with a non-None input on an existing thread re-triggers the graph from START instead of
    continuing past the interrupt.
    """
    tid = thread_id or f"{experiment_id}-day{day}"
    graph_config = {"configurable": {"thread_id": tid}}
    state = monitor_graph.invoke(
        {"experiment_id": experiment_id, "day": day, "config": config}, graph_config
    )
    decision = Decision.model_validate(state["decision"])
    if not decision.requires_human:
        monitor_graph.update_state(
            graph_config,
            {"human_verdict": "approved", "human_reason": "auto-approved: action does not require human review"},
        )
        state = monitor_graph.invoke(None, graph_config)
    return state


def resume_human_gate(experiment_id: str, day: int, verdict: str, reason: str, thread_id: str | None = None) -> GraphState:
    """Resume a paused monitor tick with a human's verdict on a scale/rollback recommendation."""
    tid = thread_id or f"{experiment_id}-day{day}"
    graph_config = {"configurable": {"thread_id": tid}}
    monitor_graph.update_state(graph_config, {"human_verdict": verdict, "human_reason": reason})
    return monitor_graph.invoke(None, graph_config)
