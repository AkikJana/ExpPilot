"""LangGraph orchestration around the auditable Experiment Copilot lifecycle."""

from __future__ import annotations

from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph

from api.service import analyze_day, create_experiment
from shared.models import DayStats


class CopilotState(TypedDict):
    goal: str
    baseline_rate: NotRequired[float]
    daily_traffic: NotRequired[int]
    telemetry: NotRequired[dict]
    proposal: NotRequired[dict]
    decision: NotRequired[dict]


def _configure(state: CopilotState) -> dict:
    proposal = create_experiment(
        state["goal"], state.get("baseline_rate", 0.1), state.get("daily_traffic", 2000)
    )
    return {"proposal": proposal}


def _monitor(state: CopilotState) -> dict:
    telemetry = state.get("telemetry")
    if telemetry is None:
        return {}
    proposal = state["proposal"]
    day = DayStats(experiment_id=proposal["config"]["id"], **telemetry)
    return {"decision": analyze_day(day).model_dump(mode="json")}


def _route_after_configure(state: CopilotState) -> str:
    return "monitor" if state.get("telemetry") else "end"


def build_graph():
    graph = StateGraph(CopilotState)
    graph.add_node("configure", _configure)
    graph.add_node("monitor", _monitor)
    graph.add_edge(START, "configure")
    graph.add_conditional_edges("configure", _route_after_configure, {"monitor": "monitor", "end": END})
    graph.add_edge("monitor", END)
    return graph.compile()


def run_copilot(goal: str, baseline_rate: float = 0.1, daily_traffic: int = 2000, telemetry: dict | None = None) -> dict:
    return build_graph().invoke(
        {
            "goal": goal,
            "baseline_rate": baseline_rate,
            "daily_traffic": daily_traffic,
            "telemetry": telemetry,
        }
    )
