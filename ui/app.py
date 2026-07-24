"""ExpPilot Streamlit UI. Performs zero statistics and zero LLM calls — every number comes from the API."""
from __future__ import annotations

import os

import pandas as pd
import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://localhost:8000")
SCENARIOS = ["true_lift", "aa_null", "srm", "guardrail_breach", "underpowered"]

st.set_page_config(page_title="ExpPilot", layout="wide")


def api_get(path: str, params: dict | None = None) -> dict | None:
    """GET a JSON payload from the API, surfacing errors in the UI instead of raising."""
    try:
        response = requests.get(f"{API_URL}{path}", params=params, timeout=30)
    except requests.RequestException as exc:
        st.error(f"Cannot reach the API at {API_URL}: {exc}")
        return None
    if response.status_code >= 400:
        st.error(f"{response.status_code}: {response.json().get('detail', response.text)}")
        return None
    return response.json()


def api_post(path: str, body: dict | None = None, params: dict | None = None) -> dict | None:
    """POST to the API, surfacing errors in the UI instead of raising."""
    try:
        response = requests.post(f"{API_URL}{path}", json=body, params=params, timeout=120)
    except requests.RequestException as exc:
        st.error(f"Cannot reach the API at {API_URL}: {exc}")
        return None
    if response.status_code >= 400:
        st.error(f"{response.status_code}: {response.json().get('detail', response.text)}")
        return None
    return response.json()


def _experiment_ids() -> list[str]:
    """List every known experiment id."""
    data = api_get("/experiments")
    return [e["id"] for e in data["experiments"]] if data else []


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def page_create() -> None:
    """Goal -> hypothesis -> config review -> launch."""
    st.header("Create an experiment")
    st.caption("The copilot proposes; the stats core computes every number.")

    goal = st.text_input("Business goal", placeholder="increase checkout conversion on mobile")

    if st.button("Generate hypothesis", disabled=not goal):
        st.session_state["hypothesis_response"] = api_post("/hypotheses", {"goal": goal})

    response = st.session_state.get("hypothesis_response")
    if not response:
        return

    if response.get("clarification"):
        clarification = response["clarification"]
        st.warning(clarification["question"])
        cols = st.columns(len(clarification["options"]))
        for col, option in zip(cols, clarification["options"]):
            if col.button(option, key=f"clarify_{option}"):
                st.session_state["hypothesis_response"] = api_post(
                    "/hypotheses", {"goal": f"{goal} ({option})"}
                )
                st.rerun()
        return

    hypothesis = response.get("hypothesis")
    if not hypothesis:
        return

    st.subheader("Hypothesis")
    st.markdown(f"**{hypothesis['statement']}**")
    st.write(hypothesis["rationale"])
    left, right = st.columns(2)
    left.metric("Expected MDE", f"{hypothesis['expected_mde'] * 100:.1f} pp")
    right.metric("Segment", hypothesis["segment"])
    st.caption(
        "Cited precedents: " + (", ".join(hypothesis["precedent_ids"]) or "none — no similar past experiments")
    )

    if st.button("Design experiment"):
        st.session_state["design_response"] = api_post("/experiments", {"hypothesis_id": hypothesis["id"]})

    design = st.session_state.get("design_response")
    if not design:
        return

    if design.get("validation_errors"):
        st.error(design.get("validation_message") or "Validation failed")
        for error in design["validation_errors"]:
            st.markdown(f"- :red[{error}]")
        return

    config = design.get("config")
    if not config:
        return

    st.subheader("Configuration")
    st.dataframe(
        pd.DataFrame(
            [
                {"field": "flag_key", "value": config["flag_key"], "source": "LLM choice"},
                {"field": "audience_segment", "value": config["audience_segment"], "source": "LLM choice"},
                {"field": "baseline_rate", "value": f"{config['baseline_rate']:.3f}", "source": "LLM estimate"},
                {"field": "mde", "value": f"{config['mde']:.3f}", "source": "LLM estimate"},
                {
                    "field": "required_n_per_arm",
                    "value": f"{config['required_n_per_arm']:,}",
                    "source": "computed by stats core",
                },
                {
                    "field": "estimated_days",
                    "value": config["estimated_days"],
                    "source": "computed by stats core",
                },
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )

    scenario = st.selectbox("Demo scenario", SCENARIOS, help="Controls the synthetic ground truth for the demo.")
    if st.button("Launch", type="primary"):
        result = api_post(f"/experiments/{config['id']}/launch", params={"demo_scenario": scenario})
        if result:
            st.success(f"Launched {config['id']} as scenario '{scenario}'.")


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------


def page_monitor() -> None:
    """Live experiment monitoring with a cumulative conversion-rate chart and alert banner."""
    st.header("Monitor")

    ids = _experiment_ids()
    if not ids:
        st.info("No experiments yet — create one on the Create page.")
        return

    experiment_id = st.selectbox("Experiment", ids)
    detail = api_get(f"/experiments/{experiment_id}")
    if not detail:
        return

    left, right = st.columns(2)
    if left.button("Advance 1 day"):
        api_post(f"/experiments/{experiment_id}/advance", {"days": 1})
        st.rerun()
    if right.button("Advance to day 14"):
        api_post(f"/experiments/{experiment_id}/advance", {"days": 14})
        st.rerun()

    decision = detail.get("latest_decision")
    if not decision:
        st.info("No data revealed yet. Use 'Advance 1 day' to begin.")
        return

    stats = decision["reasoning_stats"]

    audit = api_get(f"/experiments/{experiment_id}/audit")
    if audit and audit["decisions"]:
        rows = [
            {
                "day": d["day"],
                "control": d["reasoning_stats"]["lift_abs"] * 0,
                "treatment": d["reasoning_stats"]["lift_abs"],
            }
            for d in audit["decisions"]
        ]
        chart = pd.DataFrame(rows).set_index("day")
        chart.columns = ["control (baseline)", "treatment lift over control"]
        st.line_chart(chart)

    alert_kind = None
    for run in (audit or {}).get("agent_runs", []):
        if run["node"] == "monitor_node":
            alert_kind = run["output"]

    cols = st.columns(4)
    cols[0].metric("Lift (abs)", f"{stats['lift_abs'] * 100:+.2f} pp")
    cols[1].metric("P(beats control)", f"{stats['prob_beats_control'] * 100:.1f}%")
    cols[2].metric("Expected loss if ship", f"{stats['expected_loss_ship']:.4f}")
    cols[3].metric("Day", decision["day"])

    if stats["srm_flag"]:
        st.error(f"SRM detected (p={stats['srm_p_value']:.3g}) — assignment is skewed, results untrustworthy.")
    elif stats["guardrail_breach"]:
        st.error(f"Guardrail breached by {stats['guardrail_margin']:.4f}.")
    else:
        st.success("Guardrails healthy, no sample-ratio mismatch.")

    st.caption(decision["narrative"])


# ---------------------------------------------------------------------------
# Decide
# ---------------------------------------------------------------------------


def page_decide() -> None:
    """The decision card with approve/reject controls and the full audit trail."""
    st.header("Decide")

    ids = _experiment_ids()
    if not ids:
        st.info("No experiments yet.")
        return

    experiment_id = st.selectbox("Experiment", ids, key="decide_experiment")
    detail = api_get(f"/experiments/{experiment_id}")
    if not detail:
        return

    decision = detail.get("latest_decision")
    if not decision:
        st.info("No decision yet — advance the experiment on the Monitor page.")
        return

    badge = {
        "scale": "🟢 SCALE",
        "continue": "🔵 CONTINUE",
        "stop": "🟠 STOP",
        "rollback": "🔴 ROLLBACK",
        "pause": "🟡 PAUSE",
    }.get(decision["action"], decision["action"].upper())

    st.subheader(f"{badge} — day {decision['day']}")
    st.metric("Confidence", f"{decision['confidence'] * 100:.1f}%")
    st.write(decision["narrative"])

    with st.expander("show the math"):
        st.json(decision["reasoning_stats"])

    if decision["requires_human"] and decision["human_verdict"] == "pending":
        st.warning("This action requires human approval.")
        reason = st.text_area("Reason", placeholder="Why are you approving or rejecting this?")
        approve, reject = st.columns(2)
        if approve.button("Approve", type="primary"):
            api_post(f"/decisions/{experiment_id}/verdict", {"verdict": "approved", "reason": reason})
            st.rerun()
        if reject.button("Reject"):
            api_post(f"/decisions/{experiment_id}/verdict", {"verdict": "rejected", "reason": reason})
            st.rerun()
    elif decision["human_verdict"]:
        st.info(f"Human verdict: **{decision['human_verdict']}** — {decision['human_reason'] or 'no reason given'}")

    st.subheader("Audit trail")
    audit = api_get(f"/experiments/{experiment_id}/audit")
    if audit and audit["agent_runs"]:
        st.dataframe(
            pd.DataFrame([{"node": r["node"], "timestamp": r["timestamp"]} for r in audit["agent_runs"]]),
            hide_index=True,
            use_container_width=True,
        )


# ---------------------------------------------------------------------------
# Memory / Evals
# ---------------------------------------------------------------------------


def page_memory() -> None:
    """Long-term agent memory, filtered by record kind."""
    st.header("Memory")
    st.caption("Memory shapes generation — never acceptance. The stats thresholds are frozen.")

    kind = st.selectbox("Kind", ["all", "lesson", "episodic", "exemplar"])
    data = api_get("/memory", params=None if kind == "all" else {"kind": kind})
    if not data:
        return

    records = data["records"]
    if not records:
        st.info("No memory records yet.")
        return

    for record in records:
        if record["kind"] == "lesson":
            st.warning(f"**lesson · {record['category']}** — {record['content']}")
        else:
            with st.expander(f"{record['kind']} · {record['category']} · {record['created_at'][:19]}"):
                st.write(record["content"])


def page_evals() -> None:
    """Run the eval harness and render its summary."""
    st.header("Evals")
    st.caption("Ground-truth synthetic experiments scored against the copilot's decisions.")

    if st.button("Run evals", type="primary"):
        with st.spinner("Running the harness..."):
            st.session_state["eval_summary"] = api_post("/evals/run")

    summary = st.session_state.get("eval_summary")
    if not summary:
        return

    cols = st.columns(4)
    cols[0].metric("Overall accuracy", f"{summary['overall_accuracy'] * 100:.1f}%")
    cols[1].metric("A/A false positives", f"{summary['aa_false_positive_rate'] * 100:.1f}%")
    cols[2].metric("SRM detection", f"{summary['srm_detection_rate'] * 100:.0f}%")
    cols[3].metric("Mean days to decision", f"{summary['mean_days_to_decision']:.1f}")

    st.dataframe(
        pd.DataFrame(
            [{"scenario": k, "accuracy": v} for k, v in summary["per_scenario_accuracy"].items()]
        ),
        hide_index=True,
        use_container_width=True,
    )
    st.caption(f"Scored {summary['n_experiments']} experiments. Full runs in the MLflow UI.")


PAGES = {
    "Create": page_create,
    "Monitor": page_monitor,
    "Decide": page_decide,
    "Memory": page_memory,
    "Evals": page_evals,
}


def main() -> None:
    """Render the sidebar navigation and the selected page."""
    st.sidebar.title("ExpPilot")
    st.sidebar.caption("Generation is free; acceptance is forbidden.")
    choice = st.sidebar.radio("Page", list(PAGES))
    st.sidebar.divider()
    st.sidebar.caption(f"API: {API_URL}")
    PAGES[choice]()


main()
