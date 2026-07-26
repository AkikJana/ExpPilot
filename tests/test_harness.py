"""Unit tests for harness/gitops.py (Harness GitOps Manifest Generation)."""

from __future__ import annotations

import unittest

from harness.gitops import flag_manifest, gitops_proposal
from shared.models import ExperimentConfig


class HarnessGitOpsTests(unittest.TestCase):
    def _make_config(self) -> ExperimentConfig:
        return ExperimentConfig(
            id="exp_gitops_123",
            hypothesis_id="hyp_gitops_123",
            flag_key="checkout_one_page",
            audience_segment="mobile_users",
            traffic_split={"control": 0.5, "treatment": 0.5},
            baseline_rate=0.10,
            mde=0.01,
            required_n_per_arm=10000,
            estimated_days=14,
            guardrail_metrics=["checkout_abandon_rate"],
            daily_traffic=2000,
            status="running",
        )

    def test_flag_manifest_structure_and_state(self) -> None:
        config = self._make_config()

        # Scale -> state: true
        manifest_scale = flag_manifest(config, "scale")
        self.assertIn("kind: feature_flag", manifest_scale)
        self.assertIn("identifier: checkout_one_page", manifest_scale)
        self.assertIn("state: true", manifest_scale)
        self.assertIn("segment: mobile_users", manifest_scale)
        self.assertIn("experiment_id: exp_gitops_123", manifest_scale)

        # Rollback -> state: false
        manifest_rollback = flag_manifest(config, "rollback")
        self.assertIn("state: false", manifest_rollback)

        # Custom segment override
        manifest_custom = flag_manifest(config, "scale", segment="desktop_users")
        self.assertIn("segment: desktop_users", manifest_custom)

    def test_gitops_proposal_terminal_actions(self) -> None:
        config = self._make_config()
        for action in ("scale", "stop", "rollback", "pause"):
            prop = gitops_proposal(config, action)
            self.assertEqual(prop["filename"], "flags/checkout_one_page.yaml")
            self.assertEqual(prop["branch"], f"exppilot/exp_gitops_123-{action}")
            self.assertIn(action, prop["title"])
            self.assertEqual(prop["requires_review"], "true")
            self.assertIn("kind: feature_flag", prop["manifest"])

    def test_gitops_proposal_non_terminal_action_raises_value_error(self) -> None:
        config = self._make_config()
        with self.assertRaises(ValueError) as ctx:
            gitops_proposal(config, "continue")
        self.assertIn("terminal actions", str(ctx.exception))

        with self.assertRaises(ValueError):
            gitops_proposal(config, "draft")

    def test_gitops_yaml_syntax_and_structure_validation(self) -> None:
        """Adversarial test validating generated Harness GitOps manifests against YAML syntax and structural schema."""
        import yaml

        config = self._make_config()

        # Test each terminal action
        for action in ("scale", "stop", "rollback", "pause"):
            manifest_str = flag_manifest(config, action)

            # 1. Parse YAML - raises yaml.YAMLError if syntax is invalid
            parsed = yaml.safe_load(manifest_str)
            self.assertIsInstance(parsed, dict)

            # 2. Schema structure check
            expected_root_keys = {"kind", "identifier", "name", "state", "target", "metadata"}
            self.assertEqual(set(parsed.keys()), expected_root_keys)

            # 3. Field assertions
            self.assertEqual(parsed["kind"], "feature_flag")
            self.assertEqual(parsed["identifier"], config.flag_key)
            self.assertEqual(parsed["name"], f"ExpPilot {config.id}")

            # State check: 'scale' is True; 'stop'/'rollback'/'pause' are False
            expected_state = True if action == "scale" else False
            self.assertEqual(parsed["state"], expected_state)

            # Target structure
            self.assertIn("segment", parsed["target"])
            self.assertEqual(parsed["target"]["segment"], config.audience_segment)

            # Metadata structure
            metadata = parsed["metadata"]
            self.assertEqual(metadata["experiment_id"], config.id)
            self.assertEqual(metadata["action"], action)
            self.assertEqual(metadata["managed_by"], "exppilot-gitops")

        # 4. Custom segment override YAML validation
        manifest_custom = flag_manifest(config, "scale", segment="custom_segment_xyz")
        parsed_custom = yaml.safe_load(manifest_custom)
        self.assertEqual(parsed_custom["target"]["segment"], "custom_segment_xyz")


if __name__ == "__main__":
    unittest.main()

