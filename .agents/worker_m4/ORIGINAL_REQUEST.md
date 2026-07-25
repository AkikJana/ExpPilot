## 2026-07-25T06:47:42Z
You are a Worker subagent for the ExpPilot project.
Your working directory: /Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m4
Project root: /Users/akikjana/documents/TheTalentHack/ExpPilot

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task: Milestone 4 — R2: Statistical Engine & Continuous Performance Monitoring Implementation

1. **CRITICAL FIX — Guardrail Metric Directionality (`stats/core.py`)**:
   - In `stats/core.py`, fix `guardrail_check`:
     Look up or take `direction` (`increase_good` vs `decrease_good`) from metrics catalog / parameter.
     - For `decrease_good` (e.g. `checkout_abandon_rate`, `error_rate`): breach when `t_rate - c_rate > margin` (treatment increase is BAD).
     - For `increase_good` (e.g. `crash_free_rate`, `app_rating`): breach when `c_rate - t_rate > margin` (treatment drop is BAD).
   - Ensure `guardrail_check` never falsely flags an improvement on `increase_good` guardrails as a breach.

2. **Multi-Guardrail & Continuous Telemetry (`shared/models.py`, `stats/core.py`)**:
   - Expand `DayStats` in `shared/models.py` to support multi-guardrail telemetry (e.g. `guardrail_metrics_data: dict[str, dict[str, float]]` or list of per-guardrail metrics).
   - Update `compute_day_stats` / `guardrail_check` in `stats/core.py` to evaluate all configured guardrail metrics independently.

3. **Sequential Testing Corrections (mSPRT / Always Valid P-Values)**:
   - Implement `msprt_test` or Always Valid P-Values in `stats/core.py` for continuous daily peeking to control Type I error inflation across multi-day monitoring.

4. **Continuous Metric Models & Multiple Testing Corrections**:
   - Support continuous metrics (Normal/Log-Normal model for revenue, ARPU, latency) in `freq_test` / `bayes_decision`.
   - Add Benjamini-Hochberg (FDR) / Bonferroni correction helper for multi-variant and multi-metric evaluations.

5. **Verification**:
   - Run `pytest tests/test_stats.py` to confirm all statistical tests pass cleanly.

6. Deliver handoff report in `/Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m4/handoff.md` with build/test execution results and send a message to parent.
