"""Interactive Streamlit workspace for the Experiment Copilot.

Redesigned for data-scientist ergonomics: polished visual hierarchy, CSV upload
for telemetry data, and clear information architecture across three tabs.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# `streamlit run ui/app.py` puts ui/ on sys.path, not the project root, so
# project imports below would fail with ModuleNotFoundError. Add the repo root
# before them -- this is why the rest of this file talks to the backend over HTTP.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Derivation runs in this process, not behind the API: a transaction log can be
# hundreds of thousands of rows, and only the handful of segments it reduces to
# needs to be sent to the backend.
from data.derive import ColumnMapping, derive_segments, suggest_mapping  # noqa: E402

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


class ApiError(Exception):
    """A failed API call, already translated into something worth showing.

    `validation` is populated when the API rejected the request because a
    pre-launch check blocked it, so the caller can render the report.
    """

    def __init__(self, message: str, *, hint: str | None = None, validation: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.validation = validation


def api(method: str, path: str, *, json: dict | None = None, timeout: int = 90):
    """Single funnel for every backend call.

    Every call site used to do its own `requests.post(...)` and then print
    `response.text` on failure -- which is how a database outage reached the
    screen as the word "Internal Server Error", and how a stopped API produced a
    raw Streamlit traceback. Transport faults and structured API errors both end
    up as one ApiError with a readable message.
    """
    try:
        response = requests.request(method, f"{API_URL}{path}", json=json, timeout=timeout)
    except requests.exceptions.ConnectionError as exc:
        raise ApiError(
            "Can't reach the ExpPilot API.",
            hint=f"Nothing is answering on {API_URL}. If you're running locally, start it with "
            "`uvicorn api.main:app --reload`.",
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise ApiError(
            f"The API didn't respond within {timeout}s.",
            hint="It may still be generating hypotheses. Try again, or check the API logs.",
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise ApiError(f"Request to the API failed: {type(exc).__name__}.") from exc

    if response.ok:
        return response.json()

    # FastAPI puts our payload in `detail`; our own handlers return it top level.
    try:
        body = response.json()
    except ValueError:
        raise ApiError(
            f"API returned HTTP {response.status_code}.",
            hint=(response.text or "").strip()[:300] or None,
        ) from None

    payload = body.get("detail", body) if isinstance(body, dict) else body
    if isinstance(payload, str):
        raise ApiError(payload, hint=None)
    if isinstance(payload, dict):
        raise ApiError(
            payload.get("detail") or payload.get("error") or f"API returned HTTP {response.status_code}.",
            hint=payload.get("hint"),
            validation=payload.get("validation"),
        )
    raise ApiError(f"API returned HTTP {response.status_code}.")


def show_api_error(exc: ApiError) -> None:
    """Render an ApiError the same way everywhere."""
    st.error(f"**{exc.message}**" + (f"\n\n{exc.hint}" if exc.hint else ""))


def render_validation(validation: dict) -> None:
    """Show the pre-launch report as a result, not a JSON blob.

    Detecting overlapping experiments and bad configuration is a headline
    capability; it used to be buried in a collapsed `st.json` expander that
    nobody would open.
    """
    blocking = validation.get("blocking", []) or []
    warnings = validation.get("warnings", []) or []

    if not blocking and not warnings:
        st.success("**All pre-launch checks passed.** No blocking issues, no warnings.")
        return

    if blocking:
        st.error(f"**{len(blocking)} blocking issue(s)** — the experiment cannot start until these are resolved.")
        for issue in blocking:
            st.markdown(f"- `{issue.get('code', 'blocking')}` — {issue.get('message', '')}")
    else:
        st.success("**No blocking issues.** Safe to start.")

    if warnings:
        st.warning(f"**{len(warnings)} warning(s)** — these do not block launch, but review them.")
        for issue in warnings:
            st.markdown(f"- `{issue.get('code', 'warning')}` — {issue.get('message', '')}")


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

    # Default to the catalog's real numbers. Pre-filled inputs used to override
    # them silently, so a segment with 21,500 actual daily users would still be
    # planned against a typed-in 2,000 -- and the runtime estimate came out an
    # order of magnitude too long.
    use_segment_data = st.checkbox(
        "Use audience segment's real data",
        value=True,
        help="Read baseline conversion rate and daily traffic from the recommended "
        "segment in the catalog. Uncheck to plan against your own numbers.",
    )

    if use_segment_data:
        baseline = None
        traffic = None
        st.markdown(
            '<span style="color:#64748b;font-size:0.76rem;">'
            "Baseline rate and daily traffic are read from the segment ExpPilot recommends."
            "</span>",
            unsafe_allow_html=True,
        )
    else:
        col_b, col_t = st.columns(2)
        with col_b:
            baseline = st.number_input(
                "Baseline rate",
                0.001,
                0.99,
                0.10,
                0.005,
                format="%.3f",
                help="Current conversion rate for the audience you are targeting.",
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
            try:
                api("POST", "/reset", timeout=10)
                st.toast("✅ All flags freed and experiments cleared!")
                for key in ("proposal", "decision", "batch_results", "blocked_validation"):
                    st.session_state.pop(key, None)
                st.rerun()
            except ApiError as exc:
                show_api_error(exc)

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
def build_proposal(
    goal_text: str,
    baseline_rate: float | None,
    daily_traffic: int | None,
    hypothesis_index: int = 0,
) -> None:
    """Ask the API for a proposal and store it, or explain why it could not.

    Also used when the user switches to a different hypothesis, which is why it
    takes an index rather than always configuring the top-ranked candidate.

    `baseline_rate`/`daily_traffic` of None are omitted from the payload rather
    than sent as null, which is how the API is asked to derive them from the
    recommended segment's real observed data instead of a typed-in guess.
    """
    payload: dict = {"goal": goal_text, "hypothesis_index": hypothesis_index}
    if baseline_rate is not None:
        payload["baseline_rate"] = baseline_rate
    if daily_traffic is not None:
        payload["daily_traffic"] = daily_traffic

    label = "Generating hypotheses and computing sample size…" if hypothesis_index == 0 else "Reconfiguring…"
    try:
        with st.spinner(label):
            st.session_state["proposal"] = api("POST", "/experiments", json=payload)
        st.session_state.pop("blocked_validation", None)
        st.session_state["goal_text"] = goal_text
        st.session_state["params_auto"] = baseline_rate is None and daily_traffic is None
    except ApiError as exc:
        # A blocking pre-launch check is a legitimate product outcome, not a
        # crash: keep the report so the Design tab can show which check failed.
        if exc.validation:
            st.session_state["blocked_validation"] = exc.validation
            st.session_state.pop("proposal", None)
        else:
            show_api_error(exc)


if create:
    build_proposal(goal, baseline, traffic, hypothesis_index=0)

# ---------------------------------------------------------------------------
# Where the user is in the lifecycle
# ---------------------------------------------------------------------------
proposal = st.session_state.get("proposal")
blocked_validation = st.session_state.get("blocked_validation")


def render_stepper(active: int) -> None:
    """A five-step lifecycle rail so the tabs stop feeling like three unrelated
    screens. `active` is 1-based; earlier steps render as done."""
    steps = ["Describe goal", "Choose hypothesis", "Pre-launch checks", "Run & monitor", "Decide"]
    cells = []
    for i, name in enumerate(steps, 1):
        if i < active:
            colour, mark, weight = "#22c55e", "✓", "500"
        elif i == active:
            colour, mark, weight = "#a78bfa", str(i), "700"
        else:
            colour, mark, weight = "#475569", str(i), "400"
        cells.append(
            f'<div style="display:flex;align-items:center;gap:.45rem;white-space:nowrap;">'
            f'<span style="display:inline-flex;align-items:center;justify-content:center;'
            f"width:1.35rem;height:1.35rem;border-radius:50%;border:1.5px solid {colour};"
            f'color:{colour};font-size:.72rem;font-weight:600;">{mark}</span>'
            f'<span style="color:{colour};font-size:.82rem;font-weight:{weight};">{name}</span>'
            f"</div>"
        )
    separator = '<span style="color:#334155;">──</span>'
    st.markdown(
        '<div style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:.2rem 0 1.4rem;">'
        + separator.join(cells)
        + "</div>",
        unsafe_allow_html=True,
    )


if blocked_validation:
    current_step = 3
elif not proposal:
    current_step = 1
elif st.session_state.get("decision"):
    current_step = 5
elif proposal["config"].get("status") == "running":
    current_step = 4
else:
    current_step = 2
render_stepper(current_step)

tab_data, tab_design, tab_monitor, tab_harness = st.tabs(
    [
        "🗂️  0 · Ground with your data",
        "📐  1–3 · Design & Validate",
        "📊  4–5 · Monitor & Decide",
        "⚙️  Ship · Harness GitOps",
    ]
)

# ===== TAB 0: Ground the catalog in real data ==============================
with tab_data:
    st.markdown(
        '<div class="section-header">Step 0 · Ground the catalog in your own data</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<span style="color:#94a3b8;font-size:0.85rem;">'
        "ExpPilot ships with a <b>fabricated</b> demo catalog. Upload a transaction or event log "
        "and it will measure your real audiences instead — population, daily traffic and baseline "
        "conversion rate, which are the numbers that drive sample size and runtime."
        "</span>",
        unsafe_allow_html=True,
    )
    st.info(
        "**What this can and cannot do.** A transaction log is observational — nobody was randomised "
        "into a control or treatment arm — so it can ground an experiment's **design** but can never "
        "supply its **measurement**. Precedent lifts stay synthetic until real experiments run.",
        icon="ℹ️",
    )

    upload = st.file_uploader(
        "Transaction or event log (CSV)",
        type=["csv"],
        key="derive_upload",
        help="One row per transaction, order line, or event. Needs at minimum a user id and a timestamp.",
    )

    if upload is not None:
        try:
            try:
                raw = pd.read_csv(upload, encoding="utf-8")
            except UnicodeDecodeError:
                upload.seek(0)
                raw = pd.read_csv(upload, encoding="latin-1")

            st.dataframe(raw.head(5), use_container_width=True)
            st.caption(f"{len(raw):,} rows · {len(raw.columns)} columns")

            cols = list(raw.columns)
            guess = suggest_mapping(cols)
            none_label = "— none —"

            st.markdown('<div class="section-header">Map the concepts</div>', unsafe_allow_html=True)
            st.markdown(
                '<span style="color:#94a3b8;font-size:0.82rem;">'
                "Derivation needs to know which column means what. Best guesses are pre-filled."
                "</span>",
                unsafe_allow_html=True,
            )

            def _idx(options: list[str], value: str | None) -> int:
                return options.index(value) if value in options else 0

            m1, m2, m3 = st.columns(3)
            with m1:
                user_col = st.selectbox(
                    "👤 User identifier", cols, index=_idx(cols, guess["user_col"]),
                    help="Identifies a person. Users, not rows, are what enter an experiment.",
                )
            with m2:
                ts_col = st.selectbox(
                    "🕒 Timestamp", cols, index=_idx(cols, guess["timestamp_col"]),
                    help="Used to compute distinct users per active day.",
                )
            with m3:
                seg_options = [none_label] + cols
                seg_choice = st.selectbox(
                    "🧩 Segment by (optional)", seg_options, index=_idx(seg_options, guess["segment_col"]),
                    help="Splits the audience into segments, e.g. Country. Leave as none for a single audience.",
                )

            st.markdown("")
            st.markdown("**What counts as a conversion?**")
            rule = st.radio(
                "Outcome rule",
                ["repeat_event", "value_threshold"],
                format_func=lambda r: {
                    "repeat_event": "Repeat activity — the user has more than one distinct event",
                    "value_threshold": "Value threshold — the user's summed value reaches a target",
                }[r],
                label_visibility="collapsed",
            )

            event_col = value_col = None
            threshold = 0.0
            if rule == "repeat_event":
                ev_options = [none_label] + cols
                ev_choice = st.selectbox(
                    "Distinct event column", ev_options, index=_idx(ev_options, guess["event_col"]),
                    help="e.g. InvoiceNo or order_id. Without it, each row counts as an event.",
                )
                event_col = None if ev_choice == none_label else ev_choice
            else:
                v1, v2 = st.columns(2)
                with v1:
                    val_options = [none_label] + cols
                    val_choice = st.selectbox(
                        "Value column", val_options, index=_idx(val_options, guess["value_col"]),
                        help="Numeric column summed per user, e.g. revenue or line total.",
                    )
                    value_col = None if val_choice == none_label else val_choice
                with v2:
                    threshold = st.number_input("Threshold (>=)", min_value=0.01, value=100.0, step=10.0)

            if st.button("📐  Derive audiences", type="primary"):
                mapping = ColumnMapping(
                    user_col=user_col,
                    timestamp_col=ts_col,
                    outcome_rule=rule,
                    event_col=event_col,
                    value_col=value_col,
                    value_threshold=threshold,
                    segment_col=None if seg_choice == none_label else seg_choice,
                )
                try:
                    with st.spinner("Measuring audiences…"):
                        # Aggregated here rather than server-side: a transaction log
                        # can be hundreds of thousands of rows, and only the handful
                        # of resulting segments needs to cross the wire.
                        st.session_state["derived"] = derive_segments(raw, mapping)
                except ValueError as exc:
                    st.error(f"**Could not derive audiences.**\n\n{exc}")
                    st.session_state.pop("derived", None)

            derived = st.session_state.get("derived")
            if derived:
                meta = derived["meta"]
                st.markdown('<div class="section-header">Measured audiences</div>', unsafe_allow_html=True)
                st.caption(
                    f"{meta['rows_used']:,} rows · {meta['distinct_users']:,} distinct users · "
                    f"{meta['date_range'][0]} → {meta['date_range'][1]} · "
                    f"conversion = {meta['outcome_definition']}"
                )
                st.dataframe(
                    pd.DataFrame(derived["segments"])[
                        ["segment_key", "display_name", "population", "daily_traffic", "baseline_conversion_rate"]
                    ],
                    use_container_width=True,
                )
                if derived["skipped"]:
                    st.warning(
                        "Skipped as too small to plan on: "
                        + ", ".join(f"`{s['segment_key']}` ({s['reason']})" for s in derived["skipped"])
                    )

                replace = st.checkbox(
                    "Replace the fabricated demo segments entirely",
                    value=False,
                    help="Off by default: the seeded precedents reference seeded segment keys, "
                    "so removing them strands that history.",
                )
                if st.button("✅  Use these audiences", type="primary"):
                    try:
                        res = api(
                            "POST",
                            "/segments/derived",
                            json={"segments": derived["segments"], "replace_seeded": replace},
                        )
                        st.success(
                            f"Catalog updated — {res['inserted']} added, {res['updated']} updated"
                            + (f", {res['deleted']} removed" if res.get("deleted") else "")
                            + ". New experiments will now plan against these numbers."
                        )
                    except ApiError as exc:
                        show_api_error(exc)
        except Exception as exc:  # noqa: BLE001 - surface parse errors, never a traceback
            st.error(f"**Could not read that CSV.** {type(exc).__name__}: {exc}")

    with st.expander("📚  Current audience catalog"):
        try:
            catalog = api("GET", "/segments", timeout=20).get("segments", [])
            if catalog:
                frame = pd.DataFrame(catalog)
                frame["source"] = frame["derived"].map({True: "your data", False: "demo seed"})
                st.dataframe(
                    frame[
                        ["segment_key", "source", "population", "daily_traffic", "baseline_conversion_rate"]
                    ],
                    use_container_width=True,
                )
            else:
                st.caption("Catalog is empty.")
        except ApiError as exc:
            show_api_error(exc)

# ===== TAB 1: Experiment Design ===========================================
with tab_design:
    if blocked_validation:
        # The API refused to create the experiment. Show exactly which check
        # failed rather than a 409 string.
        st.markdown('<div class="section-header">Blocked before launch</div>', unsafe_allow_html=True)
        st.markdown(
            '<span style="color:#94a3b8;font-size:0.85rem;">'
            "ExpPilot did not create this experiment, because a pre-launch check failed. "
            "Nothing was written and no feature flag was claimed."
            "</span>",
            unsafe_allow_html=True,
        )
        st.markdown("")
        render_validation(blocked_validation)
        st.markdown("")
        if st.button("← Start over"):
            st.session_state.pop("blocked_validation", None)
            st.rerun()
    elif not proposal:
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
        recommendation = proposal.get("recommendation", {})
        validation = proposal.get("validation", {})
        candidates = proposal.get("hypotheses", [])
        selected_index = proposal.get("selected_hypothesis_index", 0)

        # ── Step 2: choose the hypothesis ────────────────────────────────
        # This comes first because everything below is *derived from* it.
        # Previously the computed design was shown above a decorative list of
        # hypotheses that could not actually be selected.
        st.markdown('<div class="section-header">Step 2 · Choose a hypothesis</div>', unsafe_allow_html=True)
        st.markdown(
            '<span style="color:#94a3b8;font-size:0.82rem;">'
            "Ranked by precedent strength. Selecting a different one re-derives the flag, "
            "audience, metrics and sample size below."
            "</span>",
            unsafe_allow_html=True,
        )
        st.markdown("")

        def _hyp_label(i: int) -> str:
            hyp = candidates[i]
            crown = "👑 " if i == 0 else ""
            return (
                f'{crown}{hyp["statement"]}  ·  segment: {hyp.get("segment", "–")}'
                f'  ·  MDE {hyp.get("expected_mde", 0):.1%}'
            )

        if candidates:
            # No explicit key: the widget is driven by `index`, so it always
            # reflects the hypothesis the config was actually derived from. A
            # persisted key could hold an index that no longer exists after the
            # candidate list changes.
            chosen = st.radio(
                "Generated hypotheses",
                options=list(range(len(candidates))),
                index=min(selected_index, len(candidates) - 1),
                format_func=_hyp_label,
                label_visibility="collapsed",
            )
            if chosen != selected_index:
                if st.button("↻  Reconfigure for this hypothesis", type="primary"):
                    build_proposal(
                        st.session_state.get("goal_text", goal), baseline, traffic, hypothesis_index=int(chosen)
                    )
                    st.rerun()

        # ── The design derived from that choice ──────────────────────────
        st.markdown("")
        st.markdown('<div class="section-header">Derived design</div>', unsafe_allow_html=True)
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

        st.markdown(
            f'<div style="color:#94a3b8;font-size:0.84rem;margin-top:.7rem;">'
            f'Audience <b style="color:#e2e8f0;">{config["audience_segment"]}</b>'
            f'&nbsp;·&nbsp; Baseline <b style="color:#e2e8f0;">{config["baseline_rate"]:.2%}</b>'
            f'&nbsp;·&nbsp; Primary metric <b style="color:#e2e8f0;">'
            f'{recommendation.get("primary_metric", {}).get("metric_key", "conversion_rate")}</b>'
            f'&nbsp;·&nbsp; Guardrails <b style="color:#e2e8f0;">'
            f'{", ".join(config.get("guardrail_metrics", [])) or "none"}</b>'
            f"</div>",
            unsafe_allow_html=True,
        )
        # Say where the planning inputs came from -- the difference between the
        # segment's measured traffic and a typed-in number changes the runtime
        # estimate by an order of magnitude.
        if st.session_state.get("params_auto", True):
            st.caption(
                f"Baseline rate and daily traffic read from the **{config['audience_segment']}** "
                "segment's observed data. The MDE is the mean lift of shipped precedents in this category."
            )
        else:
            st.caption(
                "Baseline rate and daily traffic are your manual overrides, not the segment's observed data."
            )

        # ── Step 3: pre-launch checks, as a first-class panel ────────────
        st.markdown("")
        st.markdown('<div class="section-header">Step 3 · Pre-launch checks</div>', unsafe_allow_html=True)
        render_validation(validation)

        # ── Actions ─────────────────────────────────────────────────────
        st.markdown("")
        btn_col1, btn_col2, _ = st.columns([1, 1, 2])
        with btn_col1:
            if st.button("▶️  Start Experiment", type="primary", use_container_width=True):
                try:
                    api("POST", f"/experiments/{config['id']}/start", timeout=30)
                    proposal["config"]["status"] = "running"
                    st.success("Experiment is now **running** — open *Monitor & Decide* to feed it telemetry.")
                except ApiError as exc:
                    show_api_error(exc)
        with btn_col2:
            if st.button("⏹️  Conclude / Stop", use_container_width=True):
                try:
                    api("POST", f"/experiments/{config['id']}/conclude", timeout=30)
                    proposal["config"]["status"] = "concluded"
                    st.info("Concluded — the flag and audience segment are free again.")
                except ApiError as exc:
                    show_api_error(exc)

        # ── Supporting detail, deliberately out of the main path ────────
        st.markdown("")
        with st.expander("🌳  Hypothesis ontology & branching"):
            col_tree, col_branch = st.columns([3, 2])
            with col_tree:
                st.json(proposal["ontology"], expanded=False)
            with col_branch:
                st.markdown("**Branch a hypothesis**")
                branch_statement = st.text_input("Child hypothesis", "Test clearer fee disclosure")
                branch_rationale = st.text_input("Rationale", "It may reduce price anxiety before checkout.")
                branch_segment = st.text_input("Target segment", "new_users")
                if st.button("➕  Queue Branch"):
                    try:
                        proposal["ontology"] = api(
                            "POST",
                            f"/experiments/{config['id']}/ontology/branches",
                            json={
                                "parent_id": proposal["ontology"]["id"],
                                "statement": branch_statement,
                                "rationale": branch_rationale,
                                "segment": branch_segment,
                            },
                            timeout=30,
                        )
                        st.success("Queued for review; no experiment was launched.")
                        st.rerun()
                    except ApiError as exc:
                        show_api_error(exc)

        with st.expander("🔧  Developer details (raw payloads)"):
            st.caption("Experiment config")
            st.json(config, expanded=False)
            st.caption("Grounded recommendation")
            st.json(recommendation, expanded=False)
            st.caption("Validation report")
            st.json(validation, expanded=False)

# ===== TAB 2: Monitor & Analyze ==========================================
with tab_monitor:
    st.markdown('<div class="section-header">Step 4 · Feed it telemetry</div>', unsafe_allow_html=True)

    # Pick from what actually exists rather than asking the user to carry an id
    # across tabs by hand (which silently broke on every page reload).
    try:
        known = api("GET", "/experiments", timeout=20).get("experiments", [])
    except ApiError as exc:
        known = []
        show_api_error(exc)

    current_id = proposal["config"]["id"] if proposal else None
    experiment_id = current_id or ""

    if known:
        ids = [e["id"] for e in known]
        meta = {e["id"]: e for e in known}
        default_idx = ids.index(current_id) if current_id in ids else 0
        experiment_id = st.selectbox(
            "Experiment",
            ids,
            index=default_idx,
            format_func=lambda i: (
                f"{i}  ·  {meta[i]['status']}  ·  {meta[i].get('audience_segment', '–')}"
                f"  ·  {meta[i].get('flag_key', '–')}"
            ),
            help="Every experiment ExpPilot knows about — running ones first.",
        )
        if meta[experiment_id]["status"] == "validated":
            st.info("This experiment hasn't been started yet. You can still analyze telemetry against it.")
    elif current_id:
        st.caption(f"Using the experiment from the Design tab: `{current_id}`")
    else:
        st.info("No experiments yet — create one in **Design & Validate** first.")

    # ── Simulated telemetry ─────────────────────────────────────────────
    if experiment_id:
        with st.expander("🧪  Simulate telemetry — synthetic, with a known true effect"):
            st.markdown(
                '<span style="color:#94a3b8;font-size:0.82rem;">'
                "Real telemetry only exists once a flag has actually served two variants. "
                "This generates a simulated experiment with an effect <b>you choose</b>, then replays it "
                "day by day through the real decision engine — so you can check the engine reaches the "
                "right verdict, not just that it produces one."
                "</span>",
                unsafe_allow_html=True,
            )
            st.warning("Everything produced here is **simulated**. It is not production data.", icon="⚠️")

            scenarios = {
                "true_win": "Treatment genuinely better → should reach Scale",
                "no_effect": "No real difference → should keep saying Continue",
                "true_loss": "Treatment genuinely worse → should reach Stop",
                "srm": "Broken traffic split → should Pause and refuse to analyse",
                "guardrail_breach": "A guardrail degrades → should Rollback",
            }
            s1, s2, s3 = st.columns([2, 1, 1])
            with s1:
                scenario = st.selectbox(
                    "Scenario", list(scenarios), format_func=lambda s: scenarios[s]
                )
            with s2:
                sim_days = st.number_input("Days", 1, 120, 14)
            with s3:
                sim_seed = st.number_input("Seed", 0, 10_000, 42, help="Same seed → same data.")

            if st.button("▶️  Run simulation", type="primary"):
                try:
                    with st.spinner("Simulating and replaying through the decision engine…"):
                        sim = api(
                            "POST",
                            f"/experiments/{experiment_id}/simulate",
                            json={"scenario": scenario, "seed": int(sim_seed), "days": int(sim_days)},
                            timeout=120,
                        )
                    st.session_state["simulation"] = sim
                    st.session_state["decision"] = sim["decision"]
                except ApiError as exc:
                    show_api_error(exc)

            sim = st.session_state.get("simulation")
            if sim:
                truth = sim["ground_truth"]
                if sim["engine_recovered_truth"]:
                    st.success(
                        f"**Engine recovered the truth.** Simulated a *{truth['scenario']}* "
                        f"(true lift {truth['true_lift_abs']:+.2%}) and the engine concluded "
                        f"**{sim['engine_action']}**, which is what it should."
                    )
                else:
                    st.error(
                        f"**Engine did not match the ground truth.** Expected "
                        f"**{truth['expected_action']}**, got **{sim['engine_action']}**."
                    )
                g1, g2, g3 = st.columns(3)
                g1.metric("True lift", f"{truth['true_lift_abs']:+.2%}")
                g2.metric("Observed lift", f"{truth['observed_lift_abs']:+.2%}")
                g3.metric("Engine verdict", str(sim["engine_action"]).upper())

                trend = pd.DataFrame(sim["timeline"])
                st.caption("Verdict per day — watch the readiness gate hold until day 7")
                st.dataframe(
                    trend[["day", "action", "confidence", "lift_abs", "prob_beats_control"]],
                    use_container_width=True,
                    height=240,
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
                                st.warning("Select an experiment above first.")
                            else:
                                try:
                                    with st.spinner("Running statistical analysis…"):
                                        st.session_state["decision"] = api(
                                            "POST", "/monitor", json=_build_payload(row), timeout=30
                                        )
                                    st.session_state["csv_df"] = df
                                except ApiError as exc:
                                    show_api_error(exc)

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
            try:
                with st.spinner("Running statistical analysis…"):
                    st.session_state["decision"] = api(
                        "POST",
                        "/monitor",
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
            except ApiError as exc:
                show_api_error(exc)

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
            try:
                with st.spinner("Generating manifest…"):
                    change = api(
                        "POST",
                        f"/experiments/{proposal['config']['id']}/harness-gitops",
                        json={"action": decision["action"]},
                        timeout=30,
                    )
                st.code(change["manifest"], language="yaml")
                st.download_button(
                    "⬇️  Download manifest",
                    change["manifest"],
                    file_name=change["filename"].split("/")[-1],
                    mime="application/x-yaml",
                )
            except ApiError as exc:
                show_api_error(exc)
