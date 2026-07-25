"""Interactive Streamlit workspace for the Experiment Copilot.

Redesigned for data-scientist ergonomics: polished visual hierarchy, CSV upload
for telemetry data, and clear information architecture across three tabs.
"""

from __future__ import annotations

import io
import os
import subprocess
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

API_URL = os.getenv("EXPILOT_API_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Backend shim (unchanged logic, improved error visibility)
# ---------------------------------------------------------------------------
_BACKEND_LOCK_PATH = Path("/tmp/exppilot_backend.lock")
_BACKEND_STARTUP_TIMEOUT_S = 30


def _backend_is_up() -> bool:
    try:
        return requests.get(f"{API_URL}/health", timeout=1).status_code == 200
    except requests.exceptions.RequestException:
        return False


def _ensure_backend_running() -> None:
    if os.getenv("EXPILOT_API_URL"):
        return
    if _backend_is_up():
        return

    spawned_here = False
    try:
        fd = os.open(_BACKEND_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        spawned_here = True
    except FileExistsError:
        pass

    if spawned_here:
        subprocess.Popen(
            ["uvicorn", "api.main:app", "--host", "127.0.0.1", "--port", "8000"],
            stdout=open("/tmp/uvicorn_stdout.log", "w"),
            stderr=open("/tmp/uvicorn_stderr.log", "w"),
        )

    deadline = time.time() + _BACKEND_STARTUP_TIMEOUT_S
    while time.time() < deadline:
        if _backend_is_up():
            return
        time.sleep(0.5)

    _BACKEND_LOCK_PATH.unlink(missing_ok=True)
    st.error(
        f"The ExpPilot API did not become reachable at {API_URL} within "
        f"{_BACKEND_STARTUP_TIMEOUT_S}s. Check the logs below."
    )
    for log in ("/tmp/uvicorn_stdout.log", "/tmp/uvicorn_stderr.log"):
        if Path(log).exists():
            st.code(Path(log).read_text()[-2000:], language="text")
    st.stop()


_ensure_backend_running()

# ---------------------------------------------------------------------------
# Page configuration & custom styling
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="ExpPilot – Experiment Copilot",
    page_icon="🧪",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Inject custom CSS for a polished, modern look
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    /* Global typography */
    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    /* Header styling */
    .main-title {
        font-size: 2.2rem;
        font-weight: 700;
        background: linear-gradient(135deg, #6366f1, #8b5cf6, #a78bfa);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0;
        letter-spacing: -0.03em;
    }
    .subtitle {
        font-size: 1rem;
        color: #94a3b8;
        margin-top: 0;
        margin-bottom: 1.5rem;
        font-weight: 400;
    }

    /* Card container */
    .metric-card {
        background: linear-gradient(135deg, rgba(99,102,241,0.08), rgba(139,92,246,0.06));
        border: 1px solid rgba(99,102,241,0.15);
        border-radius: 14px;
        padding: 1.25rem 1.5rem;
        margin-bottom: 0.75rem;
        transition: transform 0.15s ease, box-shadow 0.15s ease;
    }
    .metric-card:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 20px rgba(99,102,241,0.12);
    }
    .metric-card h4 {
        margin: 0 0 0.25rem 0;
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        color: #94a3b8;
        font-weight: 600;
    }
    .metric-card .value {
        font-size: 1.6rem;
        font-weight: 700;
        color: #e2e8f0;
        margin: 0;
    }
    .metric-card .detail {
        font-size: 0.8rem;
        color: #64748b;
        margin-top: 0.25rem;
    }

    /* Status badges */
    .badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 100px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .badge-scale   { background: rgba(34,197,94,0.15); color: #4ade80; }
    .badge-stop    { background: rgba(239,68,68,0.15); color: #f87171; }
    .badge-continue{ background: rgba(59,130,246,0.15); color: #60a5fa; }
    .badge-pause   { background: rgba(234,179,8,0.15); color: #facc15; }
    .badge-rollback{ background: rgba(249,115,22,0.15); color: #fb923c; }

    /* Hypothesis list */
    .hyp-card {
        background: rgba(30,41,59,0.5);
        border: 1px solid rgba(148,163,184,0.1);
        border-radius: 12px;
        padding: 1rem 1.25rem;
        margin-bottom: 0.75rem;
    }
    .hyp-card .hyp-statement {
        font-size: 0.95rem;
        color: #e2e8f0;
        font-weight: 500;
        margin-bottom: 0.4rem;
    }
    .hyp-card .hyp-meta {
        font-size: 0.78rem;
        color: #64748b;
    }

    /* Section dividers */
    .section-header {
        font-size: 1.15rem;
        font-weight: 600;
        color: #c4b5fd;
        margin-top: 1.5rem;
        margin-bottom: 0.75rem;
        border-bottom: 1px solid rgba(139,92,246,0.2);
        padding-bottom: 0.4rem;
    }

    /* Sidebar styling */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, rgba(15,23,42,0.98), rgba(30,41,59,0.95));
    }
    [data-testid="stSidebar"] .block-container {
        padding-top: 2rem;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 0.5rem;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        font-weight: 500;
    }

    /* Streamlit element overrides */
    .stButton button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1, #8b5cf6);
        border: none;
        border-radius: 10px;
        font-weight: 600;
        padding: 0.6rem 1.5rem;
        transition: all 0.2s ease;
    }
    .stButton button[kind="primary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 4px 15px rgba(99,102,241,0.35);
    }

    /* JSON viewer improvements */
    .stJson {
        border-radius: 10px;
        border: 1px solid rgba(148,163,184,0.1);
    }

    /* File uploader */
    [data-testid="stFileUploader"] {
        border: 2px dashed rgba(99,102,241,0.3);
        border-radius: 12px;
        padding: 0.5rem;
        transition: border-color 0.2s ease;
    }
    [data-testid="stFileUploader"]:hover {
        border-color: rgba(99,102,241,0.6);
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
st.markdown('<p class="main-title">🧪 ExpPilot</p>', unsafe_allow_html=True)
st.markdown(
    '<p class="subtitle">AI-assisted experiment design · deterministic statistics · human-approved decisions</p>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar – Experiment Creation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### 🎯 New Experiment")
    st.markdown(
        '<span style="color:#94a3b8;font-size:0.82rem;">'
        "Describe your business goal and we'll generate hypotheses, "
        "pick the right flag & audience, and compute sample size."
        "</span>",
        unsafe_allow_html=True,
    )
    st.markdown("")

    goal = st.text_area(
        "Business goal",
        "Increase checkout conversion on mobile",
        help="A clear product goal — e.g. 'Reduce churn for prepaid users'.",
    )

    col_b, col_t = st.columns(2)
    with col_b:
        baseline = st.number_input(
            "Baseline rate",
            0.001,
            0.99,
            0.10,
            0.005,
            format="%.3f",
            help="Current conversion rate (leave default to auto-detect from segment data).",
        )
    with col_t:
        traffic = st.number_input(
            "Daily traffic",
            100,
            10_000_000,
            2000,
            100,
            help="Avg daily users entering the experiment.",
        )

    st.markdown("")
    create = st.button("🚀  Generate Hypotheses", type="primary", use_container_width=True)

    st.markdown("---")
    with st.expander("🛠️ Admin Tools", expanded=False):
        if st.button("🧹 Clear All Running Experiments"):
            res = requests.post(f"{API_URL}/reset", timeout=10)
            if res.ok:
                st.toast("✅ All flags freed and experiments cleared!")
                if "proposal" in st.session_state:
                    del st.session_state["proposal"]
                st.rerun()
            else:
                st.error(res.text)

    st.markdown(
        '<span style="color:#64748b;font-size:0.75rem;">'
        "ExpPilot v0.2 · Deterministic decisioning engine"
        "</span>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Helper: action badge
# ---------------------------------------------------------------------------
def _action_badge(action: str) -> str:
    cls = {
        "scale": "badge-scale",
        "stop": "badge-stop",
        "continue": "badge-continue",
        "pause": "badge-pause",
        "rollback": "badge-rollback",
    }.get(action, "badge-continue")
    return f'<span class="badge {cls}">{action}</span>'


# ---------------------------------------------------------------------------
# Create experiment
# ---------------------------------------------------------------------------
if create:
    with st.spinner("Generating hypotheses and computing sample size…"):
        response = requests.post(
            f"{API_URL}/experiments",
            json={"goal": goal, "baseline_rate": baseline, "daily_traffic": traffic},
            timeout=90,
        )
    if response.ok:
        st.session_state["proposal"] = response.json()
        st.toast("✅ Experiment proposal ready!", icon="🧪")
    else:
        st.error(f"API error: {response.text}")

# ---------------------------------------------------------------------------
# Main content – three tabs
# ---------------------------------------------------------------------------
proposal = st.session_state.get("proposal")

tab_design, tab_monitor, tab_harness = st.tabs(
    ["📐  Experiment Design", "📊  Monitor & Analyze", "⚙️  Harness GitOps"]
)

# ===== TAB 1: Experiment Design ===========================================
with tab_design:
    if not proposal:
        st.markdown(
            """
            <div style="text-align:center; padding: 4rem 2rem;">
                <div style="font-size:3rem; margin-bottom:1rem;">🧪</div>
                <div style="font-size:1.2rem; font-weight:600; color:#e2e8f0; margin-bottom:0.5rem;">
                    No experiment yet
                </div>
                <div style="color:#94a3b8; max-width:28rem; margin:0 auto;">
                    Use the sidebar to describe a business goal and generate
                    hypotheses. ExpPilot will recommend the best flag, audience
                    segment, and guardrail metrics.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        config = proposal["config"]
        hypothesis = proposal.get("hypothesis", {})
        recommendation = proposal.get("recommendation", {})
        validation = proposal.get("validation", {})

        # ── Key metrics row ──────────────────────────────────────────────
        st.markdown('<div class="section-header">Experiment Design</div>', unsafe_allow_html=True)
        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.markdown(
                f'<div class="metric-card"><h4>Sample Size / Arm</h4>'
                f'<p class="value">{config["required_n_per_arm"]:,}</p>'
                f'<p class="detail">users needed per group</p></div>',
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f'<div class="metric-card"><h4>Est. Runtime</h4>'
                f'<p class="value">{config["estimated_days"]} days</p>'
                f'<p class="detail">at {config["daily_traffic"]:,} users/day</p></div>',
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f'<div class="metric-card"><h4>MDE</h4>'
                f'<p class="value">{config["mde"]:.1%}</p>'
                f'<p class="detail">minimum detectable effect</p></div>',
                unsafe_allow_html=True,
            )
        with m4:
            st.markdown(
                f'<div class="metric-card"><h4>Status</h4>'
                f'<p class="value" style="color:#a78bfa;">{config["status"].upper()}</p>'
                f'<p class="detail">flag: {config["flag_key"]}</p></div>',
                unsafe_allow_html=True,
            )

        # ── Hypotheses ───────────────────────────────────────────────────
        left, right = st.columns([3, 2])
        with left:
            st.markdown('<div class="section-header">Generated Hypotheses</div>', unsafe_allow_html=True)
            for i, hyp in enumerate(proposal.get("hypotheses", []), 1):
                st.markdown(
                    f'<div class="hyp-card">'
                    f'<div class="hyp-statement">{"👑 " if i == 1 else ""}{hyp["statement"]}</div>'
                    f'<div class="hyp-meta">'
                    f'Segment: <b>{hyp.get("segment", "–")}</b> · '
                    f'MDE: <b>{hyp.get("expected_mde", 0):.1%}</b> · '
                    f'Direction: <b>{hyp.get("direction", "–")}</b>'
                    f"</div></div>",
                    unsafe_allow_html=True,
                )

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("▶️  Start Experiment", type="primary", use_container_width=True):
                    result = requests.post(f"{API_URL}/experiments/{config['id']}/start", timeout=30)
                    if result.ok:
                        st.success("Experiment is now **running**!")
                        st.balloons()
                    else:
                        st.error(result.text)

            with btn_col2:
                if st.button("⏹️  Conclude / Stop", type="secondary", use_container_width=True):
                    result = requests.post(f"{API_URL}/experiments/{config['id']}/conclude", timeout=30)
                    if result.ok:
                        st.info("Experiment is now **concluded** and its flag/segment is freed!")
                    else:
                        st.error(result.text)


        with right:
            st.markdown('<div class="section-header">Configuration</div>', unsafe_allow_html=True)
            with st.expander("Full experiment config", expanded=False):
                st.json(config)
            with st.expander("Recommendation details", expanded=False):
                st.json(recommendation)
            with st.expander("Validation result", expanded=False):
                st.json(validation)

        # ── Ontology tree ────────────────────────────────────────────────
        st.markdown('<div class="section-header">Hypothesis Ontology</div>', unsafe_allow_html=True)
        col_tree, col_branch = st.columns([3, 2])
        with col_tree:
            st.json(proposal["ontology"])
        with col_branch:
            st.markdown("**Branch a hypothesis**")
            branch_statement = st.text_input("Child hypothesis", "Test clearer fee disclosure")
            branch_rationale = st.text_input("Rationale", "It may reduce price anxiety before checkout.")
            branch_segment = st.text_input("Target segment", "new_users")
            if st.button("➕  Queue Branch"):
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

# ===== TAB 2: Monitor & Analyze ==========================================
with tab_monitor:
    st.markdown('<div class="section-header">Upload Telemetry Data</div>', unsafe_allow_html=True)

    experiment_id = st.text_input(
        "Experiment ID",
        value=proposal["config"]["id"] if proposal else "",
        help="Paste the experiment ID from the Design tab.",
    )

    # ── Input mode: CSV upload or manual ─────────────────────────────
    input_mode = st.radio(
        "Data input",
        ["📂 Upload CSV", "✏️ Manual entry"],
        horizontal=True,
        help="Upload a CSV with daily telemetry or type numbers directly.",
    )

    if input_mode == "📂 Upload CSV":
        st.markdown(
            '<span style="color:#94a3b8;font-size:0.82rem;">'
            "Upload any CSV with your experiment telemetry. "
            "You'll map your columns to the fields ExpPilot needs."
            "</span>",
            unsafe_allow_html=True,
        )

        # Downloadable template
        template_df = pd.DataFrame(
            {
                "day": [1, 2, 3, 7],
                "control_n": [1500, 3000, 4500, 10000],
                "control_conversions": [150, 300, 450, 1000],
                "treatment_n": [1500, 3000, 4500, 10000],
                "treatment_conversions": [165, 330, 495, 1100],
                "guardrail_control_rate": [0.01, 0.01, 0.01, 0.01],
                "guardrail_treatment_rate": [0.01, 0.01, 0.01, 0.01],
            }
        )

        st.download_button(
            "📥  Download CSV template",
            template_df.to_csv(index=False),
            file_name="telemetry_template.csv",
            mime="text/csv",
        )

        uploaded = st.file_uploader(
            "Drop your telemetry CSV here",
            type=["csv"],
            help="One row per day of cumulative data. Any column names work — you'll map them below.",
        )

        if uploaded is not None:
            try:
                try:
                    df = pd.read_csv(uploaded, encoding="utf-8")
                except UnicodeDecodeError:
                    uploaded.seek(0)
                    df = pd.read_csv(uploaded, encoding="latin-1")
                csv_cols = list(df.columns)

                st.dataframe(df.head(5), use_container_width=True)
                st.caption(f"Showing first 5 of {len(df)} rows · {len(csv_cols)} columns detected")

                # ── Auto-match heuristics ────────────────────────
                # Try exact match first, then fuzzy keyword match
                _FIELD_HINTS: dict[str, list[str]] = {
                    "day": ["day", "day_number", "date", "period", "time"],
                    "control_n": ["control_n", "control_users", "control_size", "ctrl_n", "ctrl_users", "control_total", "visitors_control"],
                    "control_conversions": ["control_conversions", "control_conv", "ctrl_conversions", "ctrl_conv", "conversions_control", "control_success"],
                    "treatment_n": ["treatment_n", "treatment_users", "treatment_size", "treat_n", "variant_n", "test_users", "visitors_treatment"],
                    "treatment_conversions": ["treatment_conversions", "treatment_conv", "treat_conversions", "treat_conv", "variant_conv", "conversions_treatment", "test_success"],
                    "guardrail_control_rate": ["guardrail_control_rate", "guardrail_ctrl", "gr_control", "error_rate_control"],
                    "guardrail_treatment_rate": ["guardrail_treatment_rate", "guardrail_treat", "gr_treatment", "error_rate_treatment"],
                }

                def _auto_match(field: str) -> int:
                    """Return the best-matching column index, or 0 for '— not mapped —'."""
                    hints = _FIELD_HINTS.get(field, [])
                    lower_cols = [c.lower().strip() for c in csv_cols]
                    # Exact match
                    for hint in hints:
                        if hint in lower_cols:
                            return lower_cols.index(hint) + 1  # +1 because index 0 = "— not mapped —"
                    # Substring match
                    for hint in hints:
                        for i, col in enumerate(lower_cols):
                            if hint in col or col in hint:
                                return i + 1
                    return 0

                # ── Column mapper UI ─────────────────────────────
                st.markdown('<div class="section-header">Map Your Columns</div>', unsafe_allow_html=True)
                st.markdown(
                    '<span style="color:#94a3b8;font-size:0.82rem;">'
                    "Select which column in your CSV corresponds to each required field. "
                    "We've auto-detected what we can."
                    "</span>",
                    unsafe_allow_html=True,
                )

                options = ["— not mapped —"] + csv_cols

                REQUIRED_FIELDS = [
                    ("day", "Day number", "Row identifier — which day of the experiment (integer)."),
                    ("control_n", "Control group size", "Total users in the control arm (cumulative)."),
                    ("control_conversions", "Control conversions", "Conversions in the control arm (cumulative)."),
                    ("treatment_n", "Treatment group size", "Total users in the treatment arm (cumulative)."),
                    ("treatment_conversions", "Treatment conversions", "Conversions in the treatment arm (cumulative)."),
                ]

                GUARDRAIL_FIELDS = [
                    ("guardrail_control_rate", "Guardrail control rate", "Guardrail metric rate for control (e.g. error rate)."),
                    ("guardrail_treatment_rate", "Guardrail treatment rate", "Guardrail metric rate for treatment."),
                ]

                mapping: dict[str, str | None] = {}

                # Required fields
                req_cols = st.columns(len(REQUIRED_FIELDS))
                for col_ui, (field, label, help_text) in zip(req_cols, REQUIRED_FIELDS):
                    with col_ui:
                        idx = st.selectbox(
                            f"📌 {label}",
                            range(len(options)),
                            index=_auto_match(field),
                            format_func=lambda i, o=options: o[i],
                            help=help_text,
                            key=f"map_{field}",
                        )
                        mapping[field] = csv_cols[idx - 1] if idx > 0 else None

                # Guardrail fields — optional, can use a constant instead
                st.markdown("")
                st.markdown(
                    '<span style="color:#94a3b8;font-size:0.82rem;">'
                    "**Guardrail metrics** — map a column, or set a constant value if your CSV doesn't include them."
                    "</span>",
                    unsafe_allow_html=True,
                )

                guardrail_values: dict[str, tuple[str | None, float]] = {}
                gr_cols = st.columns(2)
                for col_ui, (field, label, help_text) in zip(gr_cols, GUARDRAIL_FIELDS):
                    with col_ui:
                        use_col = st.checkbox(f"Map from CSV column", value=_auto_match(field) > 0, key=f"use_{field}")
                        if use_col:
                            idx = st.selectbox(
                                f"📌 {label}",
                                range(len(options)),
                                index=_auto_match(field),
                                format_func=lambda i, o=options: o[i],
                                help=help_text,
                                key=f"map_{field}",
                            )
                            guardrail_values[field] = (csv_cols[idx - 1] if idx > 0 else None, 0.0)
                        else:
                            const = st.number_input(
                                f"Constant {label.lower()}",
                                0.0, 1.0, 0.01, 0.001,
                                format="%.4f",
                                key=f"const_{field}",
                            )
                            guardrail_values[field] = (None, const)

                # ── Validate mapping ─────────────────────────────
                unmapped = [label for field, label, _ in REQUIRED_FIELDS if mapping[field] is None]
                for field, label, _ in GUARDRAIL_FIELDS:
                    col_name, _ = guardrail_values[field]
                    # Only flag if user chose "map from CSV" but didn't select a column
                    if field in [f for f, _, _ in GUARDRAIL_FIELDS]:
                        use_csv = st.session_state.get(f"use_{field}", False)
                        if use_csv and col_name is None:
                            unmapped.append(label)

                if unmapped:
                    st.warning(f"⚠️ Unmapped required fields: **{', '.join(unmapped)}**")
                else:
                    # ── Build the mapped dataframe ────────────────
                    def _get_value(row, field):
                        """Get value from CSV column or constant."""
                        if field in mapping and mapping[field] is not None:
                            return row[mapping[field]]
                        if field in guardrail_values:
                            col_name, const = guardrail_values[field]
                            return row[col_name] if col_name else const
                        return None

                    # Preview mapped data
                    with st.expander("👁️ Preview mapped data", expanded=False):
                        preview_rows = []
                        for _, row in df.head(5).iterrows():
                            preview_rows.append({
                                "day": _get_value(row, "day"),
                                "control_n": _get_value(row, "control_n"),
                                "control_conversions": _get_value(row, "control_conversions"),
                                "treatment_n": _get_value(row, "treatment_n"),
                                "treatment_conversions": _get_value(row, "treatment_conversions"),
                                "guardrail_control_rate": _get_value(row, "guardrail_control_rate"),
                                "guardrail_treatment_rate": _get_value(row, "guardrail_treatment_rate"),
                            })
                        st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)

                    st.markdown("")
                    selected_day_col = mapping["day"]
                    day_values = sorted(df[selected_day_col].unique())
                    selected_day = st.selectbox(
                        "Select day to analyze",
                        day_values,
                        index=len(day_values) - 1,
                        help="Pick a row (cumulative day) to run the decision engine against.",
                    )

                    row = df[df[selected_day_col] == selected_day].iloc[0]

                    def _build_payload(row):
                        return {
                            "experiment_id": experiment_id,
                            "day": int(_get_value(row, "day")),
                            "control_n": int(_get_value(row, "control_n")),
                            "control_conversions": int(_get_value(row, "control_conversions")),
                            "treatment_n": int(_get_value(row, "treatment_n")),
                            "treatment_conversions": int(_get_value(row, "treatment_conversions")),
                            "guardrail_control_rate": float(_get_value(row, "guardrail_control_rate")),
                            "guardrail_treatment_rate": float(_get_value(row, "guardrail_treatment_rate")),
                        }

                    btn1, btn2 = st.columns(2)
                    with btn1:
                        if st.button("🔬  Analyze Selected Day", type="primary"):
                            if not experiment_id:
                                st.warning("Please enter an Experiment ID above.")
                            else:
                                with st.spinner("Running statistical analysis…"):
                                    response = requests.post(
                                        f"{API_URL}/monitor",
                                        json=_build_payload(row),
                                        timeout=30,
                                    )
                                if response.ok:
                                    result = response.json()
                                    st.session_state["decision"] = result
                                    st.session_state["csv_df"] = df
                                else:
                                    st.error(response.text)

                    with btn2:
                        if st.button("📈  Analyze All Days (Batch)"):
                            if not experiment_id:
                                st.warning("Please enter an Experiment ID above.")
                            else:
                                progress = st.progress(0, text="Analyzing…")
                                all_results = []
                                for i, (_, r) in enumerate(df.iterrows()):
                                    response = requests.post(
                                        f"{API_URL}/monitor",
                                        json=_build_payload(r),
                                        timeout=30,
                                    )
                                    if response.ok:
                                        all_results.append(response.json())
                                    progress.progress((i + 1) / len(df), text=f"Day {int(_get_value(r, 'day'))}…")
                                progress.empty()

                                if all_results:
                                    st.session_state["decision"] = all_results[-1]
                                    st.session_state["batch_results"] = all_results
                                    st.toast(f"✅ Analyzed {len(all_results)} days!", icon="📊")
                                    st.rerun()

            except Exception as e:
                st.error(f"Error reading CSV: {e}")


    else:
        # ── Manual entry ─────────────────────────────────────────────
        values = st.columns(4)
        control_n = values[0].number_input("Control users", 1, value=10000)
        control_conv = values[1].number_input("Control conversions", 0, value=1000)
        treatment_n = values[2].number_input("Treatment users", 1, value=10000)
        treatment_conv = values[3].number_input("Treatment conversions", 0, value=1100)

        gr1, gr2, day_col = st.columns(3)
        with gr1:
            gr_control = st.number_input("Guardrail control rate", 0.0, 1.0, 0.01, 0.001, format="%.4f")
        with gr2:
            gr_treatment = st.number_input("Guardrail treatment rate", 0.0, 1.0, 0.01, 0.001, format="%.4f")
        with day_col:
            day_num = st.number_input("Day", 1, 365, 7)

        if st.button("🔬  Analyze Day", type="primary") and experiment_id:
            with st.spinner("Running statistical analysis…"):
                response = requests.post(
                    f"{API_URL}/monitor",
                    json={
                        "experiment_id": experiment_id,
                        "day": day_num,
                        "control_n": control_n,
                        "control_conversions": control_conv,
                        "treatment_n": treatment_n,
                        "treatment_conversions": treatment_conv,
                        "guardrail_control_rate": gr_control,
                        "guardrail_treatment_rate": gr_treatment,
                    },
                    timeout=30,
                )
            if response.ok:
                st.session_state["decision"] = response.json()
            else:
                st.error(response.text)

    # ── Decision results ─────────────────────────────────────────────
    decision = st.session_state.get("decision")
    if decision:
        st.markdown('<div class="section-header">Decision Engine Result</div>', unsafe_allow_html=True)

        d1, d2, d3, d4 = st.columns(4)
        with d1:
            st.markdown(
                f'<div class="metric-card"><h4>Recommended Action</h4>'
                f'<p class="value">{_action_badge(decision["action"])}</p></div>',
                unsafe_allow_html=True,
            )
        with d2:
            st.markdown(
                f'<div class="metric-card"><h4>Confidence</h4>'
                f'<p class="value">{decision["confidence"]:.1%}</p></div>',
                unsafe_allow_html=True,
            )
        with d3:
            stats = decision["reasoning_stats"]
            st.markdown(
                f'<div class="metric-card"><h4>Absolute Lift</h4>'
                f'<p class="value">{stats["lift_abs"]:+.4f}</p>'
                f'<p class="detail">CI: [{stats["ci_low"]:.4f}, {stats["ci_high"]:.4f}]</p></div>',
                unsafe_allow_html=True,
            )
        with d4:
            st.markdown(
                f'<div class="metric-card"><h4>P(beats control)</h4>'
                f'<p class="value">{stats["prob_beats_control"]:.1%}</p>'
                f'<p class="detail">p-value: {stats["p_value"]:.4f}</p></div>',
                unsafe_allow_html=True,
            )

        # Narrative
        st.info(f"📝 **Narrative:** {decision['narrative']}")

        # Detail expanders
        col_stats, col_alerts = st.columns(2)
        with col_stats:
            with st.expander("Full statistical details", expanded=False):
                st.json(stats)
        with col_alerts:
            with st.expander("Decision metadata", expanded=False):
                st.json(
                    {
                        "requires_human": decision["requires_human"],
                        "human_verdict": decision["human_verdict"],
                        "srm_flag": stats["srm_flag"],
                        "guardrail_breach": stats["guardrail_breach"],
                    }
                )

    # ── Batch timeline chart ─────────────────────────────────────────
    batch = st.session_state.get("batch_results")
    if batch and len(batch) > 1:
        st.markdown('<div class="section-header">Day-by-Day Trend</div>', unsafe_allow_html=True)

        trend_data = pd.DataFrame(
            [
                {
                    "Day": r["reasoning_stats"]["day"],
                    "Lift": r["reasoning_stats"]["lift_abs"],
                    "P(beats control)": r["reasoning_stats"]["prob_beats_control"],
                    "Action": r["action"],
                }
                for r in batch
            ]
        )

        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.markdown("**Absolute Lift over Days**")
            st.line_chart(trend_data.set_index("Day")["Lift"], color="#8b5cf6")
        with chart_col2:
            st.markdown("**P(beats control) over Days**")
            st.line_chart(trend_data.set_index("Day")["P(beats control)"], color="#6366f1")

        st.dataframe(
            trend_data.style.applymap(
                lambda v: (
                    "color: #4ade80"
                    if v == "scale"
                    else "color: #f87171"
                    if v == "stop"
                    else "color: #60a5fa"
                    if v == "continue"
                    else ""
                ),
                subset=["Action"],
            ),
            use_container_width=True,
        )

# ===== TAB 3: Harness GitOps =============================================
with tab_harness:
    if not proposal:
        st.markdown(
            """
            <div style="text-align:center; padding: 4rem 2rem;">
                <div style="font-size:3rem; margin-bottom:1rem;">⚙️</div>
                <div style="font-size:1.2rem; font-weight:600; color:#e2e8f0; margin-bottom:0.5rem;">
                    No experiment created yet
                </div>
                <div style="color:#94a3b8; max-width:28rem; margin:0 auto;">
                    Create an experiment first, then analyze telemetry data.
                    Once the decision engine recommends an action, you can
                    generate a reviewable Harness flag-change manifest here.
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    elif not decision or decision.get("action") not in {"scale", "stop", "rollback", "pause"}:
        st.info(
            "🔒 The Harness GitOps panel activates when the decision engine recommends "
            "**scale**, **stop**, **rollback**, or **pause**. Analyze telemetry data in "
            "the Monitor tab first."
        )
    else:
        st.markdown('<div class="section-header">Reviewable Flag Change</div>', unsafe_allow_html=True)
        st.caption(
            "This generates a reviewable YAML manifest only. "
            "It does **not** mutate Harness or production flags."
        )

        st.markdown(
            f"Current recommendation: {_action_badge(decision['action'])} "
            f"with {decision['confidence']:.1%} confidence",
            unsafe_allow_html=True,
        )

        if st.button("📄  Generate Manifest", type="primary"):
            with st.spinner("Generating manifest…"):
                response = requests.post(
                    f"{API_URL}/experiments/{proposal['config']['id']}/harness-gitops",
                    json={"action": decision["action"]},
                    timeout=30,
                )
            if response.ok:
                change = response.json()
                st.code(change["manifest"], language="yaml")
                st.download_button(
                    "⬇️  Download manifest",
                    change["manifest"],
                    file_name=change["filename"].split("/")[-1],
                    mime="application/x-yaml",
                )
            else:
                st.error(response.text)
