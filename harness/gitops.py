"""Generate reviewable Harness Feature Flag GitOps proposals.

The application never calls Harness with an API key. A human-approved pull request
can apply the returned manifest in the organization's existing Harness GitOps repo.
"""

from __future__ import annotations

from shared.models import ExperimentConfig


def flag_manifest(config: ExperimentConfig, action: str, segment: str | None = None) -> str:
    """Return a minimal, reviewable flag manifest for a terminal decision."""
    target = segment or config.audience_segment
    enabled = "true" if action == "scale" else "false"
    return f"""kind: feature_flag
identifier: {config.flag_key}
name: ExpPilot {config.id}
state: {enabled}
target:
  segment: {target}
metadata:
  experiment_id: {config.id}
  action: {action}
  managed_by: exppilot-gitops
"""


def gitops_proposal(config: ExperimentConfig, action: str, segment: str | None = None) -> dict[str, str]:
    if action not in {"scale", "stop", "rollback", "pause"}:
        raise ValueError(f"GitOps proposals are only valid for terminal actions, got {action}")
    filename = f"flags/{config.flag_key}.yaml"
    return {
        "filename": filename,
        "branch": f"exppilot/{config.id}-{action}",
        "title": f"ExpPilot: {action} {config.flag_key}",
        "manifest": flag_manifest(config, action, segment),
        "requires_review": "true",
    }
