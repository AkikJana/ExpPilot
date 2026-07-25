## 2026-07-25T01:30:11Z
You are a Worker subagent for the ExpPilot project.
Your working directory: /Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m5
Project root: /Users/akikjana/documents/TheTalentHack/ExpPilot

MANDATORY INTEGRITY WARNING:
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A Forensic Auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

Task: Milestone 5 — R3: Decision Recommendation Engine Implementation

1. **Modular Decision Rules Engine (`rules_engine/decision.py`)**:
   - Create `rules_engine/decision.py`.
   - Implement `evaluate_decision` with the 6-step precedence decision evaluation hierarchy:
     1. SRM check (`srm_flag == True`) -> recommend `"Rollback"` or `"Pause"` with high severity SRM risk assessment.
     2. Guardrail breach (`guardrail_breach == True`) -> recommend `"Rollback"` with guardrail metric breach details, affected metrics, and margin drop.
     3. Unready (`sample_size_sufficient == False` or `runtime_days < 7`) -> recommend `"Continue"` with progress percentage towards target sample size / runtime.
     4. Bayes win (`prob_beats_control >= 0.95` and `expected_loss_ship <= 0.0025`) -> recommend `"Scale"` with posterior win probability, expected loss, and lift.
     5. Bayes loss (`prob_beats_control <= 0.05`) -> recommend `"Stop"` with posterior loss probability.
     6. Otherwise -> recommend `"Continue"`.

2. **Schema & Model Contracts (`shared/models.py`)**:
   - Add `DecisionRecommendation` model matching `PROJECT.md` contracts:
     - `action`: Literal["Scale", "Continue", "Stop", "Rollback"] (title-cased strings).
     - `confidence_score`: float (0.0 to 1.0, accurately calculated: e.g. `prob_beats_control` for Scale, `1 - prob_beats_control` for Stop, 1.0 for SRM/Guardrail breaches, progress ratio for Continue).
     - `risk_assessment`: dict (risk level, risk factors, guardrail details, SRM info, expected loss).
     - `explainable_summary`: str (plain-language non-jargon executive summary of why the action was recommended).
   - Ensure backward compatibility aliases for existing API/UI code using `Decision` or lowercase strings (`action_code`, `action.title()`, or model validator).

3. **Subsystem Integration**:
   - Update `stats/core.py:decide` to delegate to `rules_engine/decision.py`.
   - Update `agents/recommender.py`, `agents/narrator.py`, `agents/graph.py`, and `api/service.py` to use `DecisionRecommendation` and `evaluate_decision`.

4. **Verification**:
   - Run `pytest tests/test_stats.py tests/test_recommender.py tests/test_api.py tests/test_lifecycle.py` to confirm all tests pass cleanly.

5. Deliver handoff report in `/Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/worker_m5/handoff.md` with build/test execution results and send a message to parent.
