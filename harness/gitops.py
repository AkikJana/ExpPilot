"""Generate reviewable Harness Feature Flag GitOps proposals.

The application never calls Harness with an API key, and never pushes a branch.
It produces the *artifact* a human would review: a flag manifest plus the branch
name, PR title and commit message to carry it. Applying it stays a deliberate
human act in the organisation's existing GitOps repo.

That boundary is the point. The decision engine can recommend Scale, but rolling
a flag out to all production traffic is not something an automated recommendation
should be able to do by itself.

On shape: the top-level keys and the boolean `state` are a fixed contract (see
tests/test_harness.py). Traffic percentages, intent and decision provenance
therefore live under `metadata`, which the schema leaves open -- `state` alone
cannot distinguish "ship to everyone" from "turn it off", nor express a pause.
"""

from __future__ import annotations

from shared.models import Decision, ExperimentConfig

# What each terminal action means for traffic, beyond the on/off bit.
_ROLLOUT: dict[str, dict[str, object]] = {
    "scale": {
        "enabled": True,
        "treatment_pct": 100,
        "freeze": False,
        "intent": "Ship the treatment to all traffic in the target segment.",
    },
    "rollback": {
        "enabled": False,
        "treatment_pct": 0,
        "freeze": False,
        "intent": "Return all traffic to control immediately; a guardrail was breached.",
    },
    "stop": {
        "enabled": False,
        "treatment_pct": 0,
        "freeze": False,
        "intent": "End the experiment; the treatment did not win. Traffic returns to control.",
    },
    "pause": {
        # A pause is not a rollback. The measurement is untrustworthy (typically a
        # sample ratio mismatch), so the split is recorded for restoration and the
        # change is marked resumable -- the experiment is suspended pending
        # investigation, not abandoned on its merits.
        "enabled": False,
        "treatment_pct": 0,
        "freeze": True,
        "intent": (
            "Suspend assignment pending sample-ratio-mismatch investigation. "
            "Not a verdict on the treatment; restore previous_split to resume."
        ),
    },
}

TERMINAL_ACTIONS = frozenset(_ROLLOUT)


def _require_terminal(action: str) -> dict[str, object]:
    if action not in _ROLLOUT:
        raise ValueError(
            f"GitOps proposals are only valid for terminal actions "
            f"({', '.join(sorted(TERMINAL_ACTIONS))}), got {action}"
        )
    return _ROLLOUT[action]


def flag_manifest(
    config: ExperimentConfig,
    action: str,
    segment: str | None = None,
    decision: Decision | None = None,
) -> str:
    """Return a reviewable flag manifest for a terminal decision.

    `decision`, when supplied, embeds the provenance of the change: which day and
    which numbers produced this recommendation, so a reviewer never has to take
    the action on trust.
    """
    plan = _require_terminal(action)
    target = segment or config.audience_segment

    current_treatment_pct = round(float(config.traffic_split.get("treatment", 0.5)) * 100)
    treatment_pct = int(plan["treatment_pct"])
    control_pct = 100 - treatment_pct

    lines = [
        "kind: feature_flag",
        f"identifier: {config.flag_key}",
        f"name: ExpPilot {config.id}",
        f"state: {'true' if plan['enabled'] else 'false'}",
        "target:",
        f"  segment: {target}",
        "metadata:",
        f"  experiment_id: {config.id}",
        f"  action: {action}",
        f"  intent: {plan['intent']}",
        "  rollout:",
        f"    control_pct: {control_pct}",
        f"    treatment_pct: {treatment_pct}",
        "  previous_split:",
        f"    control_pct: {100 - current_treatment_pct}",
        f"    treatment_pct: {current_treatment_pct}",
        f"  resumable: {'true' if plan['freeze'] else 'false'}",
        "  managed_by: exppilot-gitops",
        "  requires_human_review: true",
    ]

    if decision is not None:
        stats = decision.reasoning_stats
        lines += [
            "  decision_provenance:",
            f"    day: {decision.day}",
            f"    confidence: {decision.confidence:.4f}",
            f"    lift_abs: {stats.lift_abs:.6f}",
            f"    prob_beats_control: {stats.prob_beats_control:.4f}",
            f"    p_value: {stats.p_value:.6f}",
            f"    srm_flag: {'true' if stats.srm_flag else 'false'}",
            f"    guardrail_breach: {'true' if stats.guardrail_breach else 'false'}",
        ]

    return "\n".join(lines) + "\n"


def gitops_proposal(
    config: ExperimentConfig,
    action: str,
    segment: str | None = None,
    decision: Decision | None = None,
) -> dict[str, str]:
    """The full reviewable change: manifest plus everything needed to raise a PR.

    Nothing is applied and no branch is pushed -- the caller receives text.
    """
    plan = _require_terminal(action)
    provenance = ""
    if decision is not None:
        provenance = (
            f"\n\nDecision: day {decision.day}, confidence {decision.confidence:.1%}, "
            f"lift {decision.reasoning_stats.lift_abs:+.2%}, "
            f"P(beats control) {decision.reasoning_stats.prob_beats_control:.1%}."
        )

    return {
        "filename": f"flags/{config.flag_key}.yaml",
        "branch": f"exppilot/{config.id}-{action}",
        "title": f"ExpPilot: {action} {config.flag_key}",
        "commit_message": (
            f"{action}: {config.flag_key} for {segment or config.audience_segment}\n\n"
            f"{plan['intent']}{provenance}\n\n"
            "Generated by ExpPilot. Review before merging; ExpPilot does not apply flag changes."
        ),
        "manifest": flag_manifest(config, action, segment, decision),
        "requires_review": "true",
    }
