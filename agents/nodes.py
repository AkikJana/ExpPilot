"""LangGraph node implementations. LLMs generate; stats.core decides. Every node audits itself."""
from __future__ import annotations

import json
import math
import os
import re
import uuid
from typing import TypedDict

from pydantic import BaseModel, ValidationError

from agents import memory
from data.db import get_conn
from shared.models import Alert, Decision, DayStats, ExperimentConfig, Hypothesis, MemoryRecord, StatsResult
from stats.core import compute_day_stats, decide, power_analysis

_PROMPTS_DIR = os.path.join(os.path.dirname(__file__), "prompts")
EUR_PER_CONVERSION = 40

CLARIFICATION_METRIC_KEYWORDS = ["conversion", "retention", "churn", "revenue", "ctr", "latency"]

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "checkout": ["checkout", "cart"],
    "device_bundles": ["bundle", "device", "handset"],
    "plan_upgrades": ["plan", "upgrade", "tier", "paywall"],
    "churn": ["churn", "retention", "cancel", "winback", "save offer"],
    "onboarding": ["onboarding", "welcome", "activation", "signup"],
    "payments": ["payment", "billing", "autopay", "invoice"],
}


class GraphState(TypedDict, total=False):
    """Shared LangGraph state. Populated incrementally by design-flow and monitor-flow nodes."""

    goal: str
    hypothesis: dict | None
    config: dict | None
    validation_errors: list[str]
    validation_message: str | None
    repair_loops: int
    needs_clarification: dict | None
    stats_result: dict | None
    alert: dict | None
    narrative: str
    decision: dict | None
    human_verdict: str | None
    human_reason: str | None
    reflection_written: list[str]
    thread_meta: dict
    experiment_id: str
    day: int
    demo_outcome: str | None


class LLMNotConfiguredError(RuntimeError):
    """Raised when an LLM-dependent node runs without LLM_API_KEY set."""


# ---------------------------------------------------------------------------
# LLM plumbing
# ---------------------------------------------------------------------------


def _llm_configured() -> bool:
    """True if an LLM_API_KEY is present in the environment."""
    return bool(os.environ.get("LLM_API_KEY"))


def _get_llm(temperature: float = 0.0):
    """Construct the single configured LLM provider, temperature 0 by default."""
    if not _llm_configured():
        raise LLMNotConfiguredError("LLM_API_KEY not set")
    from langchain_anthropic import ChatAnthropic

    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-5-20250929")
    return ChatAnthropic(model=model, api_key=os.environ["LLM_API_KEY"], temperature=temperature)


def _load_prompt(name: str) -> str:
    """Load a prompt template file from agents/prompts/."""
    with open(os.path.join(_PROMPTS_DIR, f"{name}.txt"), encoding="utf-8") as f:
        return f.read()


def _render(template: str, **kwargs: str) -> str:
    """Fill {placeholder} tokens via literal replacement (templates contain literal JSON braces)."""
    for key, value in kwargs.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def _extract_json(text: str) -> str:
    """Extract the first balanced {...} block from LLM output (defends against stray prose/fences)."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in LLM output")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    raise ValueError("unbalanced JSON object in LLM output")


def _call_llm_text(prompt: str) -> str:
    """Invoke the LLM and return its raw text content."""
    llm = _get_llm()
    response = llm.invoke(prompt)
    return response.content if isinstance(response.content, str) else str(response.content)


def _call_llm_json(prompt: str, model_cls: type[BaseModel]):
    """Call the LLM in JSON mode and parse with model_validate_json; retry once, then raise."""
    text = _call_llm_text(prompt + "\n\nOutput ONLY a single JSON object, no markdown fences, no prose.")
    try:
        return model_cls.model_validate_json(_extract_json(text))
    except (ValidationError, ValueError) as exc:
        retry_text = _call_llm_text(
            prompt
            + f"\n\nYour previous output failed validation with this error:\n{exc}\n"
            "Return ONLY the corrected JSON object."
        )
        return model_cls.model_validate_json(_extract_json(retry_text))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _infer_category(text: str) -> str:
    """Map free text to one of our known experiment categories via keyword match."""
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in lowered for kw in keywords):
            return category
    return "general"


def _write_agent_run(node: str, input_obj, output_obj, thread_id: str | None = None) -> None:
    """Append one row to agent_runs — the audit trail of record."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO agent_runs (node, input, output, timestamp, thread_id) VALUES (?, ?, ?, ?, ?)",
            (node, json.dumps(input_obj, default=str), json.dumps(output_obj, default=str), memory.now_iso(), thread_id),
        )
        conn.commit()
    finally:
        conn.close()


def _fetch_history(category: str, limit: int = 5) -> list[dict]:
    """Fetch historical experiments for a category, best lift first."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT id, category, hypothesis_text, lift_observed, outcome FROM history "
            "WHERE category = ? ORDER BY lift_observed DESC LIMIT ?",
            (category, limit),
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _format_context(history_rows: list[dict], lessons: list[MemoryRecord]) -> str:
    """Render retrieved history + lessons as prompt context."""
    lines: list[str] = []
    if history_rows:
        lines.append("Historical experiments:")
        for row in history_rows:
            lines.append(
                f"- id={row['id']} outcome={row['outcome']} lift={row['lift_observed']:+.3f}: {row['hypothesis_text']}"
            )
    else:
        lines.append("Historical experiments: none found for this category.")
    if lessons:
        lines.append("Lessons learned:")
        for lesson in lessons:
            lines.append(f"- {lesson.content}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Design-flow nodes
# ---------------------------------------------------------------------------


def hypothesis_node(state: GraphState) -> dict:
    """Turn a business goal into a falsifiable Hypothesis, or ask for clarification. LLM-dependent."""
    goal = state["goal"]
    lowered = goal.lower()
    if not any(kw in lowered for kw in CLARIFICATION_METRIC_KEYWORDS):
        clarification = {
            "needs_clarification": True,
            "question": "Which metric should this experiment move?",
            "options": list(CLARIFICATION_METRIC_KEYWORDS),
        }
        _write_agent_run("hypothesis_node", {"goal": goal}, clarification)
        return {"needs_clarification": clarification, "hypothesis": None}

    category = _infer_category(goal)
    history_rows = _fetch_history(category, limit=5)
    lessons = memory.fetch("lesson", category, limit=5)
    context = _format_context(history_rows, lessons)
    prompt = _render(_load_prompt("hypothesis"), context=context, goal=goal)

    hypothesis = _call_llm_json(prompt, Hypothesis)

    valid_ids = {row["id"] for row in history_rows}
    hypothesis.precedent_ids = [pid for pid in hypothesis.precedent_ids if pid in valid_ids]
    hypothesis.goal = goal
    hypothesis.id = "hyp_" + uuid.uuid4().hex[:8]

    _write_agent_run("hypothesis_node", {"goal": goal, "category": category}, hypothesis.model_dump())
    return {"hypothesis": hypothesis.model_dump(), "needs_clarification": None}


class _DesignerOutput(BaseModel):
    """LLM-facing subset of ExperimentConfig; power-analysis fields are always code-computed."""

    flag_key: str
    audience_segment: str
    traffic_split: dict[str, float]
    baseline_rate: float
    mde: float
    guardrail_metrics: list[str]
    daily_traffic: int


def _fetch_free_flags(segment: str | None) -> list[dict]:
    """Fetch free flags, preferring an exact segment match and falling back to all free flags."""
    conn = get_conn()
    try:
        rows = []
        if segment:
            rows = conn.execute(
                "SELECT key, segment, status FROM flags WHERE status = 'free' AND segment = ?", (segment,)
            ).fetchall()
        if not rows:
            rows = conn.execute("SELECT key, segment, status FROM flags WHERE status = 'free'").fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def designer_node(state: GraphState) -> dict:
    """Turn a Hypothesis into a config; power_analysis always overwrites the LLM's n/days. LLM-dependent."""
    hypothesis = state["hypothesis"]
    feedback = state.get("validation_errors") or []
    free_flags = _fetch_free_flags(hypothesis.get("segment"))

    flags_text = "\n".join(f"- {f['key']} (segment={f['segment']})" for f in free_flags) or "none available"
    feedback_text = ""
    if feedback:
        feedback_text = "Your previous proposal failed validation with these errors — fix them:\n" + "\n".join(
            f"- {e}" for e in feedback
        )

    prompt = _render(
        _load_prompt("designer"),
        flags=flags_text,
        hypothesis=json.dumps(hypothesis),
        feedback=feedback_text,
    )
    proposal = _call_llm_json(prompt, _DesignerOutput)

    valid_keys = {f["key"] for f in free_flags}
    flag_key = proposal.flag_key if proposal.flag_key in valid_keys else next(iter(valid_keys), proposal.flag_key)

    split_total = sum(proposal.traffic_split.values()) or 1.0
    normalized_split = {k: v / split_total for k, v in proposal.traffic_split.items()}
    baseline_rate = min(max(proposal.baseline_rate, 0.001), 0.999)
    daily_traffic = max(int(proposal.daily_traffic), 1)

    required_n_per_arm = power_analysis(baseline_rate, proposal.mde)
    estimated_days = math.ceil(required_n_per_arm * 2 / daily_traffic)

    try:
        config = ExperimentConfig(
            id="exp_" + uuid.uuid4().hex[:8],
            hypothesis_id=hypothesis["id"],
            flag_key=flag_key,
            audience_segment=proposal.audience_segment,
            traffic_split=normalized_split,
            baseline_rate=baseline_rate,
            mde=proposal.mde,
            required_n_per_arm=required_n_per_arm,
            estimated_days=estimated_days,
            guardrail_metrics=proposal.guardrail_metrics or ["latency_breach_rate"],
            daily_traffic=daily_traffic,
            status="draft",
        )
        result_state: dict = {"config": config.model_dump()}
    except ValidationError as exc:
        result_state = {"config": None, "validation_errors": [str(exc)]}

    _write_agent_run("designer_node", {"hypothesis_id": hypothesis["id"]}, result_state)
    return result_state


def _fetch_flag(key: str) -> dict | None:
    """Fetch a single flag row by key."""
    conn = get_conn()
    try:
        row = conn.execute("SELECT key, segment, status FROM flags WHERE key = ?", (key,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _audience_overlaps_running(segment: str, exclude_experiment_id: str) -> bool:
    """True if another running experiment already targets this audience segment."""
    conn = get_conn()
    try:
        rows = conn.execute("SELECT id, config FROM experiments WHERE status = 'running'").fetchall()
    finally:
        conn.close()
    for row in rows:
        if row["id"] == exclude_experiment_id:
            continue
        if json.loads(row["config"]).get("audience_segment") == segment:
            return True
    return False


def _save_experiment(config: ExperimentConfig) -> None:
    """Upsert an experiment's config+status, preserving any existing ground_truth."""
    conn = get_conn()
    try:
        existing = conn.execute("SELECT ground_truth FROM experiments WHERE id = ?", (config.id,)).fetchone()
        ground_truth = existing["ground_truth"] if existing else None
        conn.execute(
            "INSERT OR REPLACE INTO experiments (id, config, status, ground_truth) VALUES (?, ?, ?, ?)",
            (config.id, config.model_dump_json(), config.status, ground_truth),
        )
        conn.commit()
    finally:
        conn.close()


def _store_exemplar(hypothesis: dict, config: ExperimentConfig) -> None:
    """Store a gold exemplar for a config that validated first-try (no LLM required)."""
    category = _infer_category(hypothesis.get("goal", ""))
    record = MemoryRecord(
        id=memory.new_id(),
        kind="exemplar",
        category=category,
        content=json.dumps({"hypothesis": hypothesis, "config": config.model_dump()}),
        source_experiment_id=config.id,
        created_at=memory.now_iso(),
    )
    memory.write(record)


def _rephrase_errors(errors: list[str]) -> str:
    """LLM rephrases the error list into one sentence; falls back to a plain join on any failure."""
    try:
        prompt = _render(_load_prompt("validator"), errors="\n".join(f"- {e}" for e in errors))
        return _call_llm_text(prompt).strip()
    except Exception:
        return "; ".join(errors)


def validator_node(state: GraphState) -> dict:
    """Pure-code validation: flag freshness, split sum, rate bounds, runtime cap, audience overlap."""
    config_dict = state.get("config")
    repair_loops = state.get("repair_loops", 0)
    errors: list[str] = list(state.get("validation_errors") or []) if config_dict is None else []
    config: ExperimentConfig | None = None

    if config_dict is not None:
        config = ExperimentConfig.model_validate(config_dict)
        flag_row = _fetch_flag(config.flag_key)
        if flag_row is None:
            errors.append(f"flag '{config.flag_key}' does not exist in the flag registry")
        elif flag_row["status"] != "free":
            errors.append(f"flag '{config.flag_key}' is not free (status={flag_row['status']})")

        split_sum = sum(config.traffic_split.values())
        if abs(split_sum - 1.0) > 1e-6:
            errors.append(f"traffic_split sums to {split_sum}, not 1.0")

        if not (0 < config.baseline_rate < 1):
            errors.append(f"baseline_rate {config.baseline_rate} is not in (0, 1)")

        if config.estimated_days > 30:
            errors.append(f"underpowered design: estimated_days={config.estimated_days} exceeds the 30-day cap")

        if _audience_overlaps_running(config.audience_segment, exclude_experiment_id=config.id):
            errors.append(f"audience_segment '{config.audience_segment}' overlaps a currently running experiment")

    if errors:
        repair_loops += 1
        friendly_message = _rephrase_errors(errors) if _llm_configured() else "; ".join(errors)
        result_state = {
            "validation_errors": errors,
            "repair_loops": repair_loops,
            "validation_message": friendly_message,
        }
        _write_agent_run("validator_node", {"config": config_dict}, result_state)
        return result_state

    assert config is not None
    config.status = "validated"
    _save_experiment(config)
    if repair_loops == 0:
        _store_exemplar(state["hypothesis"], config)

    result_state = {"config": config.model_dump(), "validation_errors": [], "validation_message": None}
    _write_agent_run("validator_node", {"config": config_dict}, result_state)
    return result_state


# ---------------------------------------------------------------------------
# Monitor-flow nodes
# ---------------------------------------------------------------------------


def _cumulative_day_stats(experiment_id: str, day: int) -> DayStats:
    """Sum per-day DayStats rows through `day` into one cumulative DayStats."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT data FROM day_stats WHERE experiment_id = ? AND day > 0 AND day <= ? ORDER BY day",
            (experiment_id, day),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        raise ValueError(f"no day_stats found for experiment {experiment_id} up to day {day}")

    control_n = control_conv = treatment_n = treatment_conv = 0
    guardrail_control_weighted = guardrail_treatment_weighted = 0.0
    for row in rows:
        d = DayStats.model_validate_json(row["data"])
        control_n += d.control_n
        control_conv += d.control_conversions
        treatment_n += d.treatment_n
        treatment_conv += d.treatment_conversions
        guardrail_control_weighted += d.guardrail_control_rate * d.control_n
        guardrail_treatment_weighted += d.guardrail_treatment_rate * d.treatment_n

    return DayStats(
        experiment_id=experiment_id,
        day=day,
        control_n=control_n,
        control_conversions=control_conv,
        treatment_n=treatment_n,
        treatment_conversions=treatment_conv,
        guardrail_control_rate=(guardrail_control_weighted / control_n) if control_n else 0.0,
        guardrail_treatment_rate=(guardrail_treatment_weighted / treatment_n) if treatment_n else 0.0,
    )


def monitor_node(state: GraphState) -> dict:
    """Sum DayStats cumulatively through `day`, compute stats, and derive an Alert. No LLM."""
    experiment_id = state["experiment_id"]
    day = state["day"]
    config = ExperimentConfig.model_validate(state["config"])

    cumulative = _cumulative_day_stats(experiment_id, day)
    stats_result = compute_day_stats(cumulative, config, seed=0)

    if stats_result.srm_flag:
        alert = Alert(
            experiment_id=experiment_id,
            day=day,
            kind="srm",
            severity="critical",
            detail=f"Sample ratio mismatch detected (p={stats_result.srm_p_value:.4g}); results are untrustworthy.",
        )
    elif stats_result.guardrail_breach:
        alert = Alert(
            experiment_id=experiment_id,
            day=day,
            kind="guardrail",
            severity="critical",
            detail=f"Guardrail breached by {stats_result.guardrail_margin:.4f}.",
        )
    elif (
        day == 14
        and decide(stats_result, config) == "continue"
        and (cumulative.control_n < config.required_n_per_arm or cumulative.treatment_n < config.required_n_per_arm)
    ):
        alert = Alert(
            experiment_id=experiment_id,
            day=day,
            kind="underpowered",
            severity="warning",
            detail="Experiment reached day 14 without accruing the required sample size.",
        )
    else:
        alert = Alert(experiment_id=experiment_id, day=day, kind="none", severity="info", detail="Nominal.")

    result_state = {"stats_result": stats_result.model_dump(), "alert": alert.model_dump()}
    _write_agent_run("monitor_node", {"experiment_id": experiment_id, "day": day}, result_state)
    return result_state


def _template_narrative(stats_result: StatsResult, alert: Alert, config: ExperimentConfig) -> str:
    """Deterministic, code-generated narrative — used keylessly and as the final LLM fallback."""
    monthly_traffic = config.daily_traffic * 30
    expected_monthly_value = stats_result.lift_abs * monthly_traffic * EUR_PER_CONVERSION
    lines = [
        f"Day {stats_result.day}: observed lift {stats_result.lift_abs * 100:+.2f}pp "
        f"(95% CI {stats_result.ci_low * 100:+.2f}pp to {stats_result.ci_high * 100:+.2f}pp).",
        f"P(treatment beats control) = {stats_result.prob_beats_control * 100:.1f}%.",
        f"Expected monthly value if shipped: EUR {expected_monthly_value:,.0f}.",
    ]
    if alert.kind != "none":
        lines.append(f"Alert ({alert.severity}): {alert.detail}")
    return " ".join(lines)


def _numbers_match_stats(narrative: str, stats_result: StatsResult, config: ExperimentConfig) -> bool:
    """Every %/€ figure in the narrative must be within 1% of a value derivable from StatsResult."""
    monthly_traffic = config.daily_traffic * 30
    expected_monthly_value = stats_result.lift_abs * monthly_traffic * EUR_PER_CONVERSION
    allowed_pct = [
        stats_result.prob_beats_control * 100,
        (1 - stats_result.prob_beats_control) * 100,
        stats_result.lift_abs * 100,
        stats_result.ci_low * 100,
        stats_result.ci_high * 100,
        stats_result.expected_loss_ship * 100,
        stats_result.expected_loss_keep * 100,
        stats_result.guardrail_margin * 100,
        stats_result.p_value * 100,
    ]
    allowed_eur = [expected_monthly_value]

    for match in re.findall(r"-?\d+\.?\d*\s*%", narrative):
        value = float(match.replace("%", "").strip())
        if not any(abs(value - a) <= max(0.5, abs(a) * 0.01) for a in allowed_pct):
            return False

    for match in re.findall(r"€\s?[\d,]+\.?\d*|[\d,]+\.?\d*\s?€", narrative):
        cleaned = match.replace("€", "").replace(",", "").strip()
        if not cleaned:
            continue
        value = float(cleaned)
        if not any(abs(value - a) <= max(5.0, abs(a) * 0.01) for a in allowed_eur):
            return False
    return True


def analyst_node(state: GraphState) -> dict:
    """Write a business narrative; every number must trace to StatsResult. Works keylessly via template."""
    stats_result = StatsResult.model_validate(state["stats_result"])
    alert = Alert.model_validate(state["alert"])
    config = ExperimentConfig.model_validate(state["config"])

    if not _llm_configured():
        narrative = _template_narrative(stats_result, alert, config)
        _write_agent_run("analyst_node", {"day": stats_result.day}, {"narrative": narrative, "source": "template_keyless"})
        return {"narrative": narrative}

    context = f"flag={config.flag_key} segment={config.audience_segment} daily_traffic={config.daily_traffic}"
    try:
        prompt = _render(
            _load_prompt("analyst"),
            stats_json=stats_result.model_dump_json(),
            alert_json=alert.model_dump_json(),
            context=context,
            feedback="",
        )
        narrative = _call_llm_text(prompt)
        if not _numbers_match_stats(narrative, stats_result, config):
            retry_prompt = _render(
                _load_prompt("analyst"),
                stats_json=stats_result.model_dump_json(),
                alert_json=alert.model_dump_json(),
                context=context,
                feedback="Your previous draft stated a number not derivable from the stats JSON. Fix this.",
            )
            narrative = _call_llm_text(retry_prompt)
            if not _numbers_match_stats(narrative, stats_result, config):
                narrative = _template_narrative(stats_result, alert, config)
    except Exception:
        narrative = _template_narrative(stats_result, alert, config)

    _write_agent_run("analyst_node", {"day": stats_result.day}, {"narrative": narrative})
    return {"narrative": narrative}


def decision_node(state: GraphState) -> dict:
    """THE only node that turns stats into an action — calls stats.core.decide, nothing else. No LLM."""
    stats_result = StatsResult.model_validate(state["stats_result"])
    config = ExperimentConfig.model_validate(state["config"])
    action = decide(stats_result, config)
    requires_human = action in {"scale", "rollback"}
    confidence = (
        stats_result.prob_beats_control if action in {"scale", "continue"} else 1 - stats_result.prob_beats_control
    )
    decision = Decision(
        experiment_id=state["experiment_id"],
        day=state["day"],
        action=action,
        confidence=confidence,
        reasoning_stats=stats_result,
        narrative=state.get("narrative", ""),
        requires_human=requires_human,
        human_verdict="pending" if requires_human else None,
        human_reason=None,
    )
    _save_decision(decision)
    _write_agent_run(
        "decision_node", {"experiment_id": decision.experiment_id, "day": decision.day}, decision.model_dump()
    )
    return {"decision": decision.model_dump()}


def _save_decision(decision: Decision) -> None:
    """Upsert a Decision row keyed on (experiment_id, day)."""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO decisions (experiment_id, day, data) VALUES (?, ?, ?)",
            (decision.experiment_id, decision.day, decision.model_dump_json()),
        )
        conn.commit()
    finally:
        conn.close()


def human_gate(state: GraphState) -> dict:
    """Runs after the interrupt resumes; persists the human's verdict onto the Decision row."""
    decision = Decision.model_validate(state["decision"])
    verdict = state.get("human_verdict") or ("approved" if not decision.requires_human else "pending")
    reason = state.get("human_reason")
    decision.human_verdict = verdict
    decision.human_reason = reason
    _save_decision(decision)
    _write_agent_run(
        "human_gate", {"experiment_id": decision.experiment_id, "day": decision.day}, decision.model_dump()
    )
    return {"decision": decision.model_dump()}


def reflection_node(state: GraphState) -> dict:
    """On rejection: verbatim lesson, no LLM. On a demo-flagged conclusion: one LLM-written lesson."""
    decision = Decision.model_validate(state["decision"])
    config = ExperimentConfig.model_validate(state["config"])
    category = _infer_category(config.audience_segment + " " + config.flag_key)
    written: list[str] = []

    if decision.human_verdict == "rejected":
        record = MemoryRecord(
            id=memory.new_id(),
            kind="lesson",
            category=category,
            content=f"Recommendation '{decision.action}' rejected: {decision.human_reason}",
            source_experiment_id=decision.experiment_id,
            created_at=memory.now_iso(),
        )
        memory.write(record)
        written.append(record.id)

    demo_outcome = state.get("demo_outcome")
    if demo_outcome and _llm_configured():
        try:
            prompt = _render(
                _load_prompt("reflection"),
                category=category,
                predicted_action=decision.action,
                actual_outcome=demo_outcome,
                config_summary=f"flag={config.flag_key}, mde={config.mde}, baseline={config.baseline_rate}",
            )
            lesson_text = _call_llm_text(prompt).strip()
            record = MemoryRecord(
                id=memory.new_id(),
                kind="lesson",
                category=category,
                content=lesson_text,
                source_experiment_id=decision.experiment_id,
                created_at=memory.now_iso(),
            )
            memory.write(record)
            written.append(record.id)
        except Exception:
            pass

    _write_agent_run(
        "reflection_node",
        {"experiment_id": decision.experiment_id, "verdict": decision.human_verdict},
        {"written": written},
    )
    return {"reflection_written": written}
