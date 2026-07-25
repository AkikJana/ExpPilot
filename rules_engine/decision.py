"""Modular Decision Rules Engine for ExpPilot.

Evaluates experiment telemetry and statistical metrics to recommend actions:
Scale, Continue, Stop, Rollback, or Pause.
"""

from __future__ import annotations

from typing import Any

from shared.models import (
    EXPECTED_LOSS_EPSILON,
    KILL_PROB_THRESHOLD,
    MIN_RUNTIME_DAYS,
    SHIP_PROB_THRESHOLD,
    DecisionRecommendation,
    ExperimentConfig,
    HypothesisSpec,
    StatsResult,
)


def _calculate_progress(
    stats: StatsResult,
    config: ExperimentConfig | HypothesisSpec | dict[str, Any] | None = None,
    sample_size_sufficient: bool | None = None,
    runtime_days: int | None = None,
) -> tuple[bool, bool, float]:
    """Calculate sample size sufficiency, runtime sufficiency, and progress ratio.

    Returns:
        (sample_sufficient, runtime_sufficient, progress_ratio)
    """
    days = runtime_days if runtime_days is not None else stats.day
    runtime_sufficient = days >= MIN_RUNTIME_DAYS
    runtime_progress = min(1.0, max(0.0, days / float(MIN_RUNTIME_DAYS)))

    req_n: int | None = None
    if config is not None:
        if isinstance(config, ExperimentConfig):
            req_n = config.required_n_per_arm
        elif isinstance(config, dict):
            req_n = config.get("required_n_per_arm")

    if sample_size_sufficient is not None:
        sample_ok = sample_size_sufficient
        if req_n and req_n > 0:
            current_n = min(stats.control_n, stats.treatment_n)
            sample_progress = min(1.0, max(0.0, current_n / float(req_n)))
        else:
            sample_progress = 1.0 if sample_ok else 0.5
    elif req_n is not None and req_n > 0:
        current_n = min(stats.control_n, stats.treatment_n)
        sample_ok = current_n >= req_n
        sample_progress = min(1.0, max(0.0, current_n / float(req_n)))
    else:
        sample_ok = True
        sample_progress = 1.0

    progress_ratio = min(1.0, max(0.0, min(runtime_progress, sample_progress)))
    sample_sufficient = sample_ok

    return sample_sufficient, runtime_sufficient, progress_ratio


def evaluate_decision(
    stats: StatsResult,
    config: ExperimentConfig | HypothesisSpec | dict[str, Any] | None = None,
    *,
    sample_size_sufficient: bool | None = None,
    runtime_days: int | None = None,
) -> DecisionRecommendation:
    """Evaluate experiment telemetry using the 6-step precedence decision hierarchy.

    1. SRM check (`srm_flag == True`) -> recommend "Pause" or "Rollback"
    2. Guardrail breach (`guardrail_breach == True`) -> recommend "Rollback"
    3. Unready (`sample_size_sufficient == False` or `runtime_days < 7`) -> recommend "Continue"
    4. Bayes win (`prob_beats_control >= 0.95` and `expected_loss_ship <= 0.0025`) -> recommend "Scale"
    5. Bayes loss (`prob_beats_control <= 0.05`) -> recommend "Stop"
    6. Otherwise -> recommend "Continue"
    """
    days = runtime_days if runtime_days is not None else stats.day

    # Step 1: SRM Check
    if stats.srm_flag:
        return DecisionRecommendation(
            action="Pause",
            confidence_score=1.0,
            risk_assessment={
                "risk_level": "high",
                "risk_factors": ["Sample Ratio Mismatch (SRM) detected"],
                "srm_info": {
                    "srm_flag": True,
                    "srm_p_value": stats.srm_p_value,
                    "control_n": stats.control_n,
                    "treatment_n": stats.treatment_n,
                },
                "expected_loss": stats.expected_loss_ship,
            },
            explainable_summary=(
                f"Sample Ratio Mismatch (SRM) detected (p-value = {stats.srm_p_value:.4f}). "
                "Traffic distribution between variants is significantly imbalanced, indicating potential "
                "assignment bias. Recommend pausing the experiment to investigate telemetry."
            ),
        )

    # Step 2: Guardrail Breach
    if stats.guardrail_breach:
        affected_metrics: list[str] = []
        if isinstance(config, ExperimentConfig) and config.guardrail_metrics:
            affected_metrics = config.guardrail_metrics
        elif isinstance(config, dict) and config.get("guardrail_metrics"):
            affected_metrics = config.get("guardrail_metrics", [])
        else:
            affected_metrics = ["error_rate"]

        return DecisionRecommendation(
            action="Rollback",
            confidence_score=1.0,
            risk_assessment={
                "risk_level": "high",
                "risk_factors": ["Guardrail metric breach detected"],
                "guardrail_details": {
                    "breach": True,
                    "guardrail_margin": stats.guardrail_margin,
                    "affected_metrics": affected_metrics,
                },
                "affected_metrics": affected_metrics,
                "margin_drop": stats.guardrail_margin,
                "expected_loss": stats.expected_loss_ship,
            },
            explainable_summary=(
                f"Guardrail metric breach detected with a margin drop of {stats.guardrail_margin * 100:.2f}%. "
                f"Affected guardrail metric(s): {', '.join(affected_metrics)}. "
                "Recommend immediate rollback to protect key system guardrails."
            ),
        )

    # Step 3: Readiness Gate
    sample_sufficient, runtime_sufficient, progress_ratio = _calculate_progress(
        stats, config, sample_size_sufficient, runtime_days
    )
    if not (sample_sufficient and runtime_sufficient):
        req_n = config.required_n_per_arm if isinstance(config, ExperimentConfig) else 0
        return DecisionRecommendation(
            action="Continue",
            confidence_score=float(progress_ratio),
            risk_assessment={
                "risk_level": "low",
                "risk_factors": ["Insufficient runtime or sample size to reach statistical confidence"],
                "progress": {
                    "runtime_days": days,
                    "target_runtime_days": MIN_RUNTIME_DAYS,
                    "current_sample_n": min(stats.control_n, stats.treatment_n),
                    "required_sample_n": req_n,
                    "progress_percentage": round(progress_ratio * 100, 2),
                },
                "expected_loss": stats.expected_loss_ship,
            },
            explainable_summary=(
                f"Experiment is still collecting data (Day {days}/{MIN_RUNTIME_DAYS}, "
                f"progress: {progress_ratio * 100:.1f}% towards sample/runtime target). "
                "Recommend continuing the experiment."
            ),
        )

    # Step 4: Bayes Win
    if (
        stats.prob_beats_control >= SHIP_PROB_THRESHOLD
        and stats.expected_loss_ship <= EXPECTED_LOSS_EPSILON
    ):
        return DecisionRecommendation(
            action="Scale",
            confidence_score=float(stats.prob_beats_control),
            risk_assessment={
                "risk_level": "low",
                "risk_factors": [],
                "posterior_win_probability": stats.prob_beats_control,
                "expected_loss": stats.expected_loss_ship,
                "lift_abs": stats.lift_abs,
            },
            explainable_summary=(
                f"Treatment variant demonstrates high probability of beating control "
                f"(win probability: {stats.prob_beats_control * 100:.1f}%, expected loss: {stats.expected_loss_ship:.4f}, "
                f"lift: {stats.lift_abs * 100:.2f}%). Recommend scaling treatment to 100%."
            ),
        )

    # Step 5: Bayes Loss
    if stats.prob_beats_control <= KILL_PROB_THRESHOLD:
        loss_prob = 1.0 - stats.prob_beats_control
        return DecisionRecommendation(
            action="Stop",
            confidence_score=float(loss_prob),
            risk_assessment={
                "risk_level": "medium",
                "risk_factors": ["Treatment underperforming control"],
                "posterior_loss_probability": loss_prob,
                "expected_loss": stats.expected_loss_ship,
                "lift_abs": stats.lift_abs,
            },
            explainable_summary=(
                f"Treatment variant is underperforming control "
                f"(loss probability: {loss_prob * 100:.1f}%, lift: {stats.lift_abs * 100:.2f}%). "
                "Recommend stopping the experiment."
            ),
        )

    # Step 6: Otherwise (Inconclusive)
    return DecisionRecommendation(
        action="Continue",
        confidence_score=float(stats.prob_beats_control),
        risk_assessment={
            "risk_level": "low",
            "risk_factors": ["Inconclusive results, additional telemetry needed"],
            "posterior_win_probability": stats.prob_beats_control,
            "expected_loss": stats.expected_loss_ship,
            "lift_abs": stats.lift_abs,
        },
        explainable_summary=(
            f"Experiment telemetry is inconclusive (win probability: {stats.prob_beats_control * 100:.1f}%, "
            f"lift: {stats.lift_abs * 100:.2f}%). Recommend continuing to observe performance."
        ),
    )
