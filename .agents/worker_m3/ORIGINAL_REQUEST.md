## 2026-07-25T01:17:42Z

You are a Worker subagent for the ExpPilot project.
Your working directory: /Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m3
Project root: /Users/akikjana/documents/TheTalentHack/ExpPilot

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task: Milestone 3 — R1: Hypothesis Generation & Pre-Launch Validation Implementation

1. **Modularize Rules Engine (`rules_engine/validator.py`)**:
   - Create `rules_engine/__init__.py` and `rules_engine/validator.py`.
   - Move/refactor validation logic from `agents/validator.py` into `rules_engine/validator.py`.
   - Implement 6 comprehensive pre-launch validation passes:
     1. Flag availability pass (`_check_flag_availability`): verifies flags are free/cataloged, checks multi-flag lists.
     2. Audience overlap pass (`_check_audience_overlap`): checks for running, scheduled, and draft experiment collisions on exact or overlapping segments.
     3. Traffic split pass (`_check_traffic_split`): ensures total traffic allocation equals 100% (1.0).
     4. Power feasibility & sample size capacity pass (`_check_power_feasibility`): checks if required sample size exceeds segment daily traffic capacity or 30-day horizon limit.
     5. Segment traffic capacity pass (`_check_segment_traffic`).
     6. Guardrail metrics pass (`_check_guardrail_metrics`): verifies guardrails are cataloged, not identical to primary metric, and directionally configured.

2. **Schema & Model Contracts (`shared/models.py`)**:
   - Add/update `HypothesisSpec` schema: `hypothesis: str`, `primary_metric: str` (allow any string key in metrics catalog, not just `"conversion_rate"`), `guardrail_metrics: list[str]`, `feature_flag_keys: list[str]`, `target_audience: dict`.
   - Add/update `ValidationResult` schema: `is_valid: bool`, `errors: list[str]`, `warnings: list[str]`.
   - Ensure backward compatibility aliases for existing API / UI dependencies where needed (`ValidationReport` alias / mapping).

3. **Agent Integration (`agents/validator.py`, `agents/recommender.py`)**:
   - Update `agents/validator.py` to delegate to `rules_engine/validator.py`.
   - Update `agents/recommender.py` to produce valid `HypothesisSpec` and support multi-flag specs and non-conversion primary metrics.

4. **Verification**:
   - Run `pytest tests/test_validator.py tests/test_recommender.py` to confirm all tests pass cleanly.

5. Deliver handoff report in `/Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m3/handoff.md` with build/test execution results and send a message to parent.
