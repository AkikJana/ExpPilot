"""ExpPilot Streamlit UI: copilot workspace + monitor + decision card + eval dashboard.

Imports the shared service layer directly (no running API required), so the demo
works from a single `streamlit run ui/app.py`.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os

import pandas as pd
import streamlit as st

# On hosted Streamlit, secrets live in st.secrets; mirror them into the environment
# so modules that read os.getenv (e.g. agents.llm -> GROQ_API_KEY) work unchanged.
try:
    for _k, _v in dict(st.secrets).items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:
    pass

from api import service
from agents.llm import active_provider, llm_available
from data.synth import SCENARIOS
from evals import harness

st.set_page_config(page_title="ExpPilot", page_icon="🧪", layout="wide")

_ACTION_STYLE = {
    "scale": ("#0f9d58", "🚀 SCALE"),
    "continue": ("#4285f4", "⏳ CONTINUE"),
    "stop": ("#9e9e9e", "🛑 STOP"),
    "rollback": ("#db4437", "⏪ ROLLBACK"),
    "pause": ("#f4b400", "⏸️ PAUSE (blocked)"),
}


def _init_state() -> None:
    service.ensure_ready()
    ss = st.session_state
    ss.setdefault("hyp_result", None)
    ss.setdefault("config_result", None)
    ss.setdefault("create_start", None)
    ss.setdefault("create_elapsed", None)
    ss.setdefault("accepts", 0)
    ss.setdefault("proposals", 0)


def _badge(text: str) -> str:
    return (
        f"<span style='background:#eef;border:1px solid #ccd;border-radius:4px;"
        f"padding:1px 6px;font-size:0.72rem;color:#334'>{text}</span>"
    )


# --------------------------------------------------------------------------- #
def page_create() -> None:
    st.header("1 · Copilot workspace — state a business goal")
    ss = st.session_state

    goal = st.text_input(
        "Business goal",
        value="Increase prepaid recharge completion on the new mobile checkout",
        key="goal_input",
    )
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Generate hypotheses", type="primary"):
            ss.create_start = time.time()
            ss.hyp_result = service.copilot_hypotheses(goal)
            ss.config_result = None
            ss.proposals += len(ss.hyp_result["hypotheses"])
    with c2:
        st.caption(
            f"LLM narration: {active_provider()} · "
            "every number is computed by the stats engine, never the LLM."
        )

    if not ss.hyp_result:
        return

    st.subheader(f"Grounded hypotheses  ·  category: `{ss.hyp_result['category']}`")
    for h in ss.hyp_result["hypotheses"]:
        with st.container(border=True):
            st.markdown(f"**{h['statement']}**")
            mde_txt = f"MDE {h['expected_mde'] * 100:.2f}pp"
            st.markdown(
                _badge("primary: " + h["primary_metric"]) + " "
                + _badge(mde_txt) + " "
                + _badge("segment: " + h["segment"]),
                unsafe_allow_html=True,
            )
            st.caption(h["rationale"])
            st.caption("Evidence: " + ", ".join(h["precedent_ids"]))
            if st.button(f"Select & configure → {h['id']}", key=f"sel_{h['id']}"):
                ss.accepts += 1
                ss.config_result = service.copilot_config(h, category=ss.hyp_result["category"])

    if ss.config_result:
        _render_config(ss.config_result)


def _render_config(result: dict) -> None:
    ss = st.session_state
    cfg = result["config"]
    val = result["validation"]
    st.header("2 · Proposed configuration + pre-launch validation")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Feature flag", cfg["flag_key"])
    c2.metric("Required n / arm", f"{cfg['required_n_per_arm']:,}")
    c3.metric("Est. runtime", f"{cfg['estimated_days']} days")
    c4.metric("Traffic split", "50 / 50")
    st.caption(
        f"baseline={cfg['baseline_rate']:.0%} · MDE={cfg['mde']*100:.2f}pp · "
        f"alpha={cfg['alpha']} · power={cfg['power']} · guardrails={cfg['guardrail_metrics']}"
    )

    if val["blocked"]:
        st.error("Launch BLOCKED by the validation gate — resolve critical issues first.")
    elif val["issues"]:
        st.warning("Validated with warnings — review before launch.")
    else:
        st.success("Validated — no conflicts detected.")
    for issue in val["issues"]:
        icon = {"critical": "⛔", "warning": "⚠️", "info": "ℹ️"}.get(issue["severity"], "•")
        st.write(f"{icon} **{issue['kind']}** — {issue['detail']}")

    st.subheader("Launch (with synthetic telemetry)")
    lc1, lc2 = st.columns([1, 2])
    scenario = lc1.selectbox("Demo scenario", SCENARIOS, index=0,
                             help="Ground-truth generator for the telemetry stream (simulator only).")
    if lc2.button("🚀 Launch experiment", disabled=val["blocked"], type="primary"):
        created = service.create_experiment(cfg, scenario=scenario, seed=2026)
        if ss.create_start:
            ss.create_elapsed = time.time() - ss.create_start
        st.success(f"Launched `{created['experiment_id']}` — go to 'Monitor & Decide'.")
        st.session_state["active_experiment"] = created["experiment_id"]


# --------------------------------------------------------------------------- #
def page_monitor() -> None:
    st.header("3 · Monitor & decide")
    exps = service.list_experiments()
    if not exps:
        st.info("No experiments yet. Create one in the workspace, or the demo seeds three.")
        return
    ids = [e["id"] for e in exps]
    default = st.session_state.get("active_experiment", ids[0])
    idx = ids.index(default) if default in ids else 0
    exp_id = st.selectbox("Experiment", ids, index=idx)

    ctrl1, ctrl2, _ = st.columns([1, 1, 3])
    if ctrl1.button("▶️ Advance one day"):
        service.advance_experiment(exp_id)
    if ctrl2.button("⏭️ Run to conclusion"):
        for _ in range(14):
            out = service.advance_experiment(exp_id)
            if out["action"] in ("scale", "stop", "rollback"):
                break

    detail = service.get_experiment(exp_id)
    series = detail["series"]
    if not series:
        st.info("Advance the experiment to generate data.")
        return

    df = pd.DataFrame(series)
    st.subheader(f"Day {detail['current_day']} of {detail['max_days']}")

    lc, rc = st.columns(2)
    with lc:
        st.caption("Posterior P(treatment > control) by day")
        st.line_chart(df.set_index("day")[["prob_beats_control"]])
    with rc:
        st.caption("Absolute lift with 95% CI (pp)")
        ci = df.set_index("day")[["lift_abs", "ci_low", "ci_high"]] * 100
        st.line_chart(ci)

    latest = series[-1]
    if latest["srm_flag"]:
        st.error(
            f"⛔ Sample-ratio mismatch detected (chi-square p={latest['srm_p_value']:.4g} < 0.001). "
            "Results are not trustworthy — analysis is blocked until randomization is fixed."
        )
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("P(beats control)", f"{latest['prob_beats_control']*100:.1f}%")
    m2.metric("Abs lift", f"{latest['lift_abs']*100:.2f}pp")
    m3.metric("p-value", f"{latest['p_value']:.4g}")
    m4.metric("Guardrail Δ", f"{latest['guardrail_margin']*100:.2f}pp")
    st.markdown(_badge("all figures computed by the deterministic stats engine"), unsafe_allow_html=True)

    if detail["latest_decision"]:
        _render_decision(exp_id, detail["latest_decision"])


def _render_decision(exp_id: str, decision: dict) -> None:
    st.header("4 · Decision card")
    color, label = _ACTION_STYLE.get(decision["action"], ("#333", decision["action"].upper()))
    st.markdown(
        f"<div style='background:{color};color:white;padding:14px 18px;border-radius:10px;"
        f"font-size:1.4rem;font-weight:700'>{label} &nbsp;·&nbsp; "
        f"confidence {decision['confidence']*100:.0f}%</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    st.markdown(f"**Why:** {decision['narrative']}")
    src = decision.get("_narration_source", "template")
    st.caption(f"Narration source: {src} · decision computed by policy engine (LLM-free)")

    cites = decision.get("_citations", [])
    if cites:
        with st.expander("Evidence (retrieved precedents)"):
            for c in cites:
                st.write(f"- `{c['id']}` — {c['hypothesis_text']} ({c['outcome']})")

    with st.expander("Audit trail (stats snapshot)"):
        st.json(decision["reasoning_stats"])

    if decision.get("requires_human"):
        st.warning("This high-stakes action requires human sign-off.")
        a1, a2 = st.columns(2)
        if a1.button("✅ Adopt recommendation", key=f"adopt_{exp_id}_{decision['day']}"):
            service.record_verdict(exp_id, decision["day"], "approved")
            st.success("Recorded: adopted.")
        if a2.button("❌ Override", key=f"override_{exp_id}_{decision['day']}"):
            service.record_verdict(exp_id, decision["day"], "rejected", "manual override")
            st.info("Recorded: overridden.")


# --------------------------------------------------------------------------- #
def page_evals() -> None:
    st.header("5 · Eval dashboard — recommendation accuracy vs expert")
    ss = st.session_state
    seeds = st.slider("Seeds per scenario", 1, 6, 3)
    if st.button("Run eval harness", type="primary"):
        ss["eval_result"] = harness.run(seeds_per_scenario=seeds, write_report=True)

    res = ss.get("eval_result")
    if not res:
        st.info("Run the harness to score the copilot against the labeled gold set.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Recommendation accuracy", f"{res['recommendation_accuracy']*100:.1f}%",
                  f"{res['correct']}/{res['n_scenarios']}")
        if res["significance_detection_accuracy"] is not None:
            c2.metric("Significance detection", f"{res['significance_detection_accuracy']*100:.1f}%")
        c3.metric("Scenarios", res["n_scenarios"])
        st.subheader("Confusion matrix (rows = expert, cols = copilot)")
        cm = pd.DataFrame(res["confusion_matrix"]).T[harness.ACTIONS]
        st.dataframe(cm)
        st.subheader("Per-scenario")
        st.dataframe(pd.DataFrame(res["rows"]))

    st.divider()
    st.subheader("Impact metrics (demo)")
    adopt = service.adoption_stats()
    k1, k2, k3, k4 = st.columns(4)
    elapsed = ss.get("create_elapsed")
    k1.metric("Creation time", f"{elapsed:.0f}s" if elapsed else "—", "vs ~40 min manual")
    accept_rate = (ss.accepts / ss.proposals) if ss.proposals else None
    k2.metric("Config acceptance", f"{accept_rate*100:.0f}%" if accept_rate is not None else "—")
    k3.metric("Recommendations", adopt["total_decisions"])
    k4.metric("Adoption rate", f"{adopt['adoption_rate']*100:.0f}%" if adopt["adoption_rate"] is not None else "—")


# --------------------------------------------------------------------------- #
def main() -> None:
    _init_state()
    st.sidebar.title("🧪 ExpPilot")
    st.sidebar.caption("AI Experiment Copilot & Decision Intelligence")
    page = st.sidebar.radio(
        "Lifecycle",
        ["Create", "Monitor & Decide", "Eval Dashboard"],
    )
    st.sidebar.divider()
    st.sidebar.caption(
        "Deterministic core decides · LLM only explains · every recommendation is auditable."
    )
    if page == "Create":
        page_create()
    elif page == "Monitor & Decide":
        page_monitor()
    else:
        page_evals()


if __name__ == "__main__":
    main()
