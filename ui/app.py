"""Interactive Streamlit workspace for the Experiment Copilot."""

from __future__ import annotations

import os

import requests
import streamlit as st

API_URL = os.getenv("EXPILOT_API_URL", "http://localhost:8000")

st.set_page_config(page_title="ExpPilot", layout="wide")
st.title("Experiment Copilot")
st.caption("Cursor-assisted ideation • deterministic statistics • human-approved actions")

with st.sidebar:
    st.subheader("Create an experiment")
    goal = st.text_area("Business goal", "Increase checkout conversion on mobile")
    baseline = st.number_input("Baseline conversion", 0.001, 0.99, 0.10, 0.005)
    traffic = st.number_input("Daily traffic", 100, 10_000_000, 2000, 100)
    create = st.button("Generate hypotheses", type="primary")

if create:
    response = requests.post(
        f"{API_URL}/experiments",
        json={"goal": goal, "baseline_rate": baseline, "daily_traffic": traffic},
        timeout=90,
    )
    if response.ok:
        st.session_state["proposal"] = response.json()
    else:
        st.error(response.text)

proposal = st.session_state.get("proposal")
if proposal:
    config = proposal["config"]
    left, right = st.columns(2)
    with left:
        st.subheader("Proposed experiment")
        st.json(config)
        if st.button("Start experiment"):
            result = requests.post(f"{API_URL}/experiments/{config['id']}/start", timeout=30)
            st.success("Experiment running" if result.ok else result.text)
    with right:
        st.subheader("Hypothesis ontology")
        st.json(proposal["ontology"])
        with st.expander("Branch a hypothesis"):
            branch_statement = st.text_input("Child hypothesis", "Test clearer fee disclosure")
            branch_rationale = st.text_input("Why it may work", "It may reduce price anxiety before checkout.")
            branch_segment = st.text_input("Target segment", "new_users")
            if st.button("Queue branch"):
                response = requests.post(
                    f"{API_URL}/experiments/{config['id']}/ontology/branches",
                    json={
                        "parent_id": proposal["ontology"]["id"],
                        "statement": branch_statement,
                        "rationale": branch_rationale,
                        "segment": branch_segment,
                    },
                    timeout=30,
                )
                if response.ok:
                    proposal["ontology"] = response.json()
                    st.success("Queued for review; no experiment was launched.")
                    st.rerun()
                else:
                    st.error(response.text)

st.divider()
st.subheader("Monitor telemetry")
experiment_id = st.text_input("Experiment ID", value=proposal["config"]["id"] if proposal else "")
values = st.columns(4)
control_n = values[0].number_input("Control users", 1, value=10000)
control_conv = values[1].number_input("Control conversions", 0, value=1000)
treatment_n = values[2].number_input("Treatment users", 1, value=10000)
treatment_conv = values[3].number_input("Treatment conversions", 0, value=1100)
if st.button("Analyze current day") and experiment_id:
    response = requests.post(
        f"{API_URL}/monitor",
        json={
            "experiment_id": experiment_id,
            "day": 7,
            "control_n": control_n,
            "control_conversions": control_conv,
            "treatment_n": treatment_n,
            "treatment_conversions": treatment_conv,
            "guardrail_control_rate": 0.01,
            "guardrail_treatment_rate": 0.01,
        },
        timeout=30,
    )
    if response.ok:
        result = response.json()
        st.session_state["decision"] = result
        st.metric("Recommended action", result["action"].upper(), f"confidence {result['confidence']:.1%}")
        st.info(result["narrative"])
        st.json(result["reasoning_stats"])
    else:
        st.error(response.text)

decision = st.session_state.get("decision")
if proposal and decision and decision["action"] in {"scale", "stop", "rollback", "pause"}:
    st.subheader("Harness GitOps proposal")
    st.caption("This is a reviewable manifest only. It does not mutate Harness or production flags.")
    if st.button("Generate reviewable flag change"):
        response = requests.post(
            f"{API_URL}/experiments/{proposal['config']['id']}/harness-gitops",
            json={"action": decision["action"]},
            timeout=30,
        )
        if response.ok:
            change = response.json()
            st.code(change["manifest"], language="yaml")
            st.download_button("Download manifest", change["manifest"], file_name=change["filename"].split("/")[-1])
        else:
            st.error(response.text)
