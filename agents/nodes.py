"""Agent node logic. Each node is a pure function: state-in -> partial-state-out.

LLMs draft prose only. Every metric, sample size, p-value, and action is produced
by stats.core or the deterministic tools and passed to the LLM as ground truth.
"""
from __future__ import annotations

from shared.models import (
    EXPECTED_LOSS_EPSILON,
    KILL_PROB_THRESHOLD,
    SHIP_PROB_THRESHOLD,
    Alert,
    Decision,
    ExperimentConfig,
    Hypothesis,
    StatsResult,
)
from agents import tools
from agents.llm import narrate
from agents.rag import infer_category, search_past_experiments

_DEFAULT_BASELINE = 0.10
_DEFAULT_DAILY_TRAFFIC = 6000
_DEFAULT_MDE = 0.02
_ACTION_LABEL = {
    "scale": "SCALE",
    "continue": "CONTINUE",
    "stop": "STOP",
    "rollback": "ROLLBACK",
    "pause": "PAUSE",
}


# --------------------------------------------------------------------------- #
# Create pipeline
# --------------------------------------------------------------------------- #
def retrieve_node(state: dict) -> dict:
    """Ground the goal in a category and the most relevant past experiments."""
    goal = state["goal"]
    category = infer_category(goal)
    precedents = search_past_experiments(goal, category=category, k=5)
    return {"category": category, "precedents": precedents}


def hypothesis_node(state: dict) -> dict:
    """Propose 3 falsifiable hypotheses, each grounded in cited precedents."""
    goal = state["goal"]
    category = state.get("category") or infer_category(goal)
    precedents = state.get("precedents") or search_past_experiments(goal, category=category, k=5)
    segment = tools.CATEGORY_SEGMENT.get(category, "mobile_users")

    shipped = [p for p in precedents if p["outcome"] == "shipped"] or precedents
    hypotheses: list[dict] = []
    for i in range(3):
        seed_precs = precedents[i : i + 2] if precedents else []
        cite = [p["id"] for p in seed_precs] or [p["id"] for p in precedents[:1]]
        anchor = seed_precs[0] if seed_precs else (precedents[0] if precedents else None)
        if anchor:
            statement = (
                f"Applying the '{anchor['hypothesis_text']}' pattern to \"{goal}\" will "
                f"increase conversion_rate for {segment}."
            )
            rationale = (
                f"Closest precedents in the {category} category "
                f"({', '.join(cite)}) observed lifts around "
                f"{anchor['lift_observed'] * 100:.1f}% and were {anchor['outcome']}."
            )
        else:
            statement = f"The proposed change for \"{goal}\" will increase conversion_rate for {segment}."
            rationale = "No close precedent found; treat prior as uninformative."
        # Vary the MDE across candidates so the PM sees a risk/speed tradeoff.
        expected_mde = round(_DEFAULT_MDE * (0.75 + 0.25 * i) + 0.005, 4)
        h = Hypothesis(
            id=f"hyp_{category}_{i+1}",
            goal=goal,
            statement=statement,
            primary_metric="conversion_rate",
            expected_direction="increase",
            expected_mde=expected_mde,
            segment=segment,
            rationale=rationale,
            precedent_ids=cite,
        )
        hypotheses.append(h.model_dump())

    # Optional LLM polish of statements only (numbers are fixed & verified).
    allowed = [h["expected_mde"] for h in hypotheses] + [
        p["lift_observed"] for p in precedents
    ]
    for h in hypotheses:
        polished, source = narrate(
            system=(
                "You are an experimentation coach. Rewrite the hypothesis statement to be crisp, "
                "testable, and business-friendly. Do NOT introduce any statistic or number that is "
                "not already present. One sentence."
            ),
            prompt=f"Goal: {h['goal']}\nDraft: {h['statement']}",
            allowed_values=allowed,
            fallback=h["statement"],
        )
        h["statement"] = polished
        h["_narration_source"] = source
    return {"hypotheses": hypotheses}


def config_node(state: dict) -> dict:
    """Turn the chosen hypothesis into a fully specified, power-analyzed config."""
    hyp = state["chosen_hypothesis"]
    category = state.get("category") or infer_category(hyp["goal"])
    flag_key, segment = tools.pick_free_flag(category)

    baseline = _DEFAULT_BASELINE
    mde = float(hyp.get("expected_mde", _DEFAULT_MDE))
    daily_traffic = _DEFAULT_DAILY_TRAFFIC
    required_n = tools.estimate_sample_size(baseline, mde)
    import math

    estimated_days = math.ceil(required_n * 2 / daily_traffic)

    config = ExperimentConfig(
        id=f"exp_{flag_key}",
        hypothesis_id=hyp["id"],
        flag_key=flag_key,
        audience_segment=segment,
        traffic_split={"control": 0.5, "treatment": 0.5},
        baseline_rate=baseline,
        mde=mde,
        required_n_per_arm=required_n,
        estimated_days=estimated_days,
        guardrail_metrics=["latency_breach_rate"],
        daily_traffic=daily_traffic,
        status="draft",
    )
    return {"config": config.model_dump()}


def validation_node(state: dict) -> dict:
    """Gate before launch: overlap, metric conflicts, and power feasibility."""
    config = state["config"]
    overlap = tools.check_overlap(config["flag_key"], config["audience_segment"])

    issues: list[dict] = []
    blocked = False
    for c in overlap["conflicts"]:
        blocked = True
        issues.append(
            {
                "severity": "critical",
                "kind": "flag_conflict",
                "detail": f"Flag '{c['flag']}' is already running experiment {c['experiment_id']}.",
            }
        )
    for o in overlap["overlaps"]:
        issues.append(
            {
                "severity": "warning",
                "kind": "audience_overlap",
                "detail": (
                    f"Experiment {o['experiment_id']} (flag '{o['flag']}') is already live on "
                    f"segment '{o['segment']}' — interaction risk. Recommend mutual exclusion."
                ),
            }
        )
    # Metric conflict: a guardrail must not equal the primary metric.
    if config["guardrail_metrics"] and "conversion_rate" in config["guardrail_metrics"]:
        blocked = True
        issues.append(
            {
                "severity": "critical",
                "kind": "metric_conflict",
                "detail": "Primary metric is also listed as a guardrail.",
            }
        )
    # Power feasibility within a 14-day window.
    if config["estimated_days"] > 14:
        issues.append(
            {
                "severity": "warning",
                "kind": "underpowered",
                "detail": (
                    f"Estimated {config['estimated_days']} days to reach "
                    f"{config['required_n_per_arm']} per arm exceeds the 14-day window. "
                    "Raise MDE or traffic allocation."
                ),
            }
        )

    ok = not blocked
    validation = {
        "ok": ok,
        "blocked": blocked,
        "issues": issues,
        "overlap": overlap,
    }
    new_status = "validated" if ok else "draft"
    updated = {**config, "status": new_status}
    return {"validation": validation, "config": updated}


# --------------------------------------------------------------------------- #
# Analyze pipeline
# --------------------------------------------------------------------------- #
def stats_node(state: dict) -> dict:
    """Compute the day's StatsResult and the deterministic action."""
    config = ExperimentConfig(**state["config"])
    stats, action = tools.analyze(state["scenario"], state["seed"], state["day"], config)
    return {"stats": stats.model_dump(), "action": action}


def monitor_node(state: dict) -> dict:
    """Derive typed alerts from the StatsResult (SRM / guardrail / underpowered)."""
    stats = StatsResult(**state["stats"])
    config = ExperimentConfig(**state["config"])
    alerts: list[dict] = []
    if stats.srm_flag:
        alerts.append(
            Alert(
                experiment_id=stats.experiment_id,
                day=stats.day,
                kind="srm",
                severity="critical",
                detail=f"Sample-ratio mismatch (chi-square p={stats.srm_p_value:.4g} < 0.001). "
                "Results are not trustworthy until randomization is fixed.",
            ).model_dump()
        )
    if stats.guardrail_breach:
        alerts.append(
            Alert(
                experiment_id=stats.experiment_id,
                day=stats.day,
                kind="guardrail",
                severity="critical",
                detail=f"Guardrail breach: treatment worse than control by "
                f"{stats.guardrail_margin * 100:.2f}pp.",
            ).model_dump()
        )
    # underpowered heuristic: still inconclusive and not near target sample
    if not stats.srm_flag and not stats.guardrail_breach:
        if KILL_PROB_THRESHOLD < stats.prob_beats_control < SHIP_PROB_THRESHOLD:
            alerts.append(
                Alert(
                    experiment_id=stats.experiment_id,
                    day=stats.day,
                    kind="underpowered",
                    severity="info",
                    detail="Result still inconclusive; continue collecting data.",
                ).model_dump()
            )
    if not alerts:
        alerts.append(
            Alert(
                experiment_id=stats.experiment_id,
                day=stats.day,
                kind="none",
                severity="info",
                detail="No quality issues detected.",
            ).model_dump()
        )
    return {"alerts": alerts}


def _confidence(stats: StatsResult, action: str) -> float:
    """Map the deterministic stats to a bounded confidence for display."""
    if action == "pause":
        return 0.99
    if action == "rollback":
        return min(0.99, 0.85 + abs(stats.guardrail_margin) * 5)
    if action == "scale":
        return round(stats.prob_beats_control, 4)
    if action == "stop":
        return round(1 - stats.prob_beats_control, 4)
    # continue: distance from the nearest decision boundary
    return round(1 - 2 * abs(stats.prob_beats_control - 0.5), 4)


def _deterministic_narrative(stats: StatsResult, config: ExperimentConfig, action: str, precedents: list[dict]) -> str:
    """Always-grounded business-language summary built purely from computed facts."""
    label = _ACTION_LABEL[action]
    lift_pp = stats.lift_abs * 100
    ci = f"[{stats.ci_low * 100:.2f}pp, {stats.ci_high * 100:.2f}pp]"
    prob = stats.prob_beats_control * 100
    base = (
        f"Recommendation: {label}. On day {stats.day}, the treatment shows an absolute lift of "
        f"{lift_pp:.2f}pp (95% CI {ci}) with a {prob:.1f}% posterior probability of beating control."
    )
    if action == "pause":
        return (
            f"Recommendation: {label}. A sample-ratio mismatch was detected "
            f"(chi-square p={stats.srm_p_value:.4g}). The traffic split is broken, so no lift can be "
            "trusted. Fix randomization and resume before reading any result."
        )
    if action == "rollback":
        return (
            f"Recommendation: {label}. A guardrail regressed: the treatment is worse than control by "
            f"{stats.guardrail_margin * 100:.2f}pp, which exceeds the safety margin. Roll back now; "
            "the primary lift does not justify the harm."
        )
    if action == "scale":
        cite = precedents[0]["id"] if precedents else None
        extra = (
            f" This is consistent with precedent {cite}, which shipped a similar change."
            if cite
            else ""
        )
        return base + f" The result clears the ship bar (>= {SHIP_PROB_THRESHOLD:.0%} and low ship-loss)." + extra
    if action == "stop":
        return base + " The change is very unlikely to help; stop and reallocate traffic."
    return base + " The result is not yet conclusive; keep the experiment running."


def decision_node(state: dict) -> dict:
    """Assemble the Decision, then let the LLM narrate the *already-computed* action."""
    stats = StatsResult(**state["stats"])
    config = ExperimentConfig(**state["config"])
    action = state["action"]
    precedents = state.get("precedents") or search_past_experiments(
        config.flag_key, category=None, k=2
    )

    fallback = _deterministic_narrative(stats, config, action, precedents)
    allowed = [
        stats.lift_abs, stats.ci_low, stats.ci_high, stats.prob_beats_control,
        stats.srm_p_value, stats.p_value, stats.z_stat, stats.guardrail_margin,
        stats.expected_loss_ship, stats.expected_loss_keep,
        SHIP_PROB_THRESHOLD, KILL_PROB_THRESHOLD, EXPECTED_LOSS_EPSILON,
    ] + [p["lift_observed"] for p in precedents]
    narrative, source = narrate(
        system=(
            "You are a product experimentation analyst writing for a non-technical PM. "
            "Explain the given recommendation in 2-3 sentences. You MUST NOT invent, alter, or add "
            "any number, percentage, or p-value beyond those in the facts. Do not change the "
            "recommendation."
        ),
        prompt=(
            f"Recommendation (fixed): {_ACTION_LABEL[action]}\n"
            f"Facts: lift={stats.lift_abs:.4f}, CI=[{stats.ci_low:.4f},{stats.ci_high:.4f}], "
            f"prob_beats_control={stats.prob_beats_control:.4f}, srm_p={stats.srm_p_value:.4g}, "
            f"guardrail_margin={stats.guardrail_margin:.4f}\n"
            f"Deterministic draft: {fallback}"
        ),
        allowed_values=allowed,
        fallback=fallback,
    )

    decision = Decision(
        experiment_id=stats.experiment_id,
        day=stats.day,
        action=action,
        confidence=_confidence(stats, action),
        reasoning_stats=stats,
        narrative=narrative,
        requires_human=action in {"scale", "rollback"} or stats.srm_flag,
        human_verdict="pending" if (action in {"scale", "rollback"} or stats.srm_flag) else None,
        human_reason=None,
    )
    out = decision.model_dump()
    out["_narration_source"] = source
    out["_citations"] = [
        {"id": p["id"], "hypothesis_text": p["hypothesis_text"], "outcome": p["outcome"]}
        for p in precedents
    ]
    return {"decision": out}
