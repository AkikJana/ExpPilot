# Codebase Audit and Handoff Report: R1 & R3 Subsystems

**Agent:** Explorer (`explorer_m1_1`)  
**Working Directory:** `/Users/akikjana/documents/TheTalentHack/ExpPilot/.agents/explorer_m1_1`  
**Project Root:** `/Users/akikjana/documents/TheTalentHack/ExpPilot`  
**Date:** 2026-07-25  

---

## 1. Observation

### 1.1 Directory & File Layout
A comprehensive read-only audit was conducted across the codebase. Below is the file mapping for key subsystems:

| Directory | File Path | Line Count | Primary Role / Description |
|-----------|-----------|------------|----------------------------|
| `rules_engine/` | `rules_engine/` | 0 lines | **Empty directory** (contains only `__pycache__`). No `.py` source files present. |
| `agents/` | `agents/__init__.py` | 0 lines | Package marker. |
| | `agents/graph.py` | 61 lines | LangGraph state graph (`CopilotState`, `_configure`, `_monitor`, `run_copilot`). |
| | `agents/llm.py` | 241 lines | Gemini REST API + Cursor CLI wrapper (`ask_llm`), precedent-grounded hypothesis generation, deterministic fallback. |
| | `agents/narrator.py` | 165 lines | Business narrative generator (`narrate_decision`) + numeric anti-hallucination guard (`verify_numeric_grounding`). |
| | `agents/recommender.py` | 218 lines | Category inference, data-driven segment/flag/metric/precedent recommender (`recommend`). |
| | `agents/validator.py` | 214 lines | Pre-launch validation checks (`validate_experiment`). |
| `api/` | `api/main.py` | 144 lines | FastAPI surface (`/experiments`, `/copilot/run`, `/monitor`, `/experiments/{id}/timeline`, etc.). |
| | `api/service.py` | 369 lines | Application service glue for lifecycle operations, persistence, and audit logging. |
| `ui/` | `ui/app.py` | 948 lines | Streamlit interactive workspace (Design, Monitor & Analyze CSV/Manual, Harness GitOps tabs). |
| `stats/` | `stats/core.py` | 153 lines | Power analysis, chi-square SRM check, Z-test, Bayesian Beta-Binomial decisioning (`decide`). |
| | `stats/diagnostics.py` | 133 lines | Segment-level driver diagnostics (`analyze_drivers`). |
| `shared/` | `shared/models.py` | 140 lines | Pydantic v2 data models (`Hypothesis`, `ExperimentConfig`, `DayStats`, `StatsResult`, `Decision`, etc.). |
| `data/` | `data/db.py` | 219 lines | Dual SQLite/PostgreSQL database layer and table schema initialization (`init_db`). |
| | `data/seed.py` | 160 lines | Idempotent CSV seed loader (`segments.csv`, `metrics_catalog.csv`, `feature_flags.csv`, `historical_experiments.csv`). |
| `ontology/` | `ontology/tree.py` | 51 lines | Serializable hypothesis tree (`HypothesisNode`, `initial_tree`). |
| `harness/` | `harness/gitops.py` | 40 lines | Harness Feature Flag GitOps manifest generator (`gitops_proposal`). |
| `tests/` | `tests/test_lifecycle.py` | 274 lines | Unit test suite covering basic lifecycle, recommendations, validator blocking, and narrator numeric guard. |

---

### 1.2 Detailed Audit of R1: Hypothesis Generation & Pre-Launch Validation

#### Existing Functionality
1. **Hypothesis Generation (`agents/recommender.py` & `agents/llm.py`)**:
   - `infer_category(goal)` maps free text to categories via keyword matching (`checkout`, `cart`, `churn`, etc.) (`agents/recommender.py:48-56`).
   - `recommend(goal)` selects segment, free flag, primary metric, guardrail metrics, and historical precedents from seeded catalog (`agents/recommender.py:195-217`).
   - `hypotheses_for_goal(goal, precedents)` calls Gemini REST API or Cursor CLI (`cursor-agent --mode ask`), falling back to `_deterministic_fallback` when LLM is unavailable (`agents/llm.py:225-240`).
   - `power_analysis(baseline_rate, mde)` computes required sample size per arm (`stats/core.py:23-36`).
2. **Pre-Launch Validation (`agents/validator.py`)**:
   - `validate_experiment(config, primary_metric_key)` executes 6 validation passes (`agents/validator.py:57-74`):
     - `_check_flag_availability`: Flags if flag status != `'free'` (blocking if occupied, warning if missing from catalog).
     - `_check_audience_overlap`: Checks if segment already has a running experiment in `flags` table (blocking).
     - `_check_traffic_split`: Verifies `traffic_split` sums to 1.0 (blocking).
     - `_check_power_feasibility`: Warns if `estimated_days > 30` (`MAX_HORIZON_DAYS`) (warning).
     - `_check_segment_traffic`: Warns if requested daily traffic exceeds segment daily traffic (warning).
     - `_check_guardrail_metrics`: Warns if no guardrails, blocks if guardrail == primary metric, warns if metric missing/wrong kind in catalog.

#### Code & Architecture Gaps in R1
1. **Empty `rules_engine/` Directory**:
   - The directory `/Users/akikjana/documents/TheTalentHack/ExpPilot/rules_engine` contains no python files.
   - Validation logic is currently placed inside `agents/validator.py` and `agents/recommender.py` instead of a modular `rules_engine/` package.
2. **Schema Mismatches vs `PROJECT.md` Contract**:
   - `PROJECT.md` specifies output contract `HypothesisSpec` (with `hypothesis`, `primary_metric`, `guardrail_metrics: list`, `feature_flag_keys: list`, `target_audience: dict`).
   - Implementation uses `Hypothesis` (`shared/models.py:17-29`) and `ExperimentConfig` (`shared/models.py:31-47`).
   - `Hypothesis.primary_metric` is hardcoded as `Literal["conversion_rate"]` in Pydantic schema, preventing non-conversion primary metrics (e.g. latency, revenue).
   - `ExperimentConfig` only supports a single `flag_key: str` instead of `feature_flag_keys: list[str]`.
   - `ExperimentConfig.audience_segment` is a flat string key instead of a structured `target_audience: dict`.
   - `PROJECT.md` specifies validator output `ValidationResult` (`is_valid: bool`, `errors: list`, `warnings: list`), whereas implementation uses `ValidationReport` (`passed: bool`, `blocking: list`, `warnings: list`).
3. **Validation Logic Shortfalls**:
   - **Sample size shortfall detection**: Checks only `estimated_days > 30`. It does NOT check whether total required sample size exceeds maximum segment population or whether baseline conversion rate is unreasonably low.
   - **Audience overlap detection**: `_check_audience_overlap` (`agents/validator.py:103-120`) only checks exact segment string matches on experiments with `status = 'running'`. It does not detect overlaps on `draft` or `validated` scheduled experiments, nor does it detect sub-segment/overlapping segments (e.g., `mobile_users` vs `mobile_checkout_users`).
   - **Missing flag key enforcement**: If a flag key is not in `feature_flags` catalog, it produces a warning (`flag_not_cataloged`) and constructs a synthetic key `{exp_id}_flag` rather than failing or requiring explicit registration.

---

### 1.3 Detailed Audit of R3: Decision Recommendation Engine

#### Existing Functionality
1. **Decision Rules Engine (`stats/core.py`)**:
   - `decide(stats, config)` (`stats/core.py:97-113`) implements a 5-step precedence evaluation:
     1. `stats.srm_flag` -> `"pause"` (Analysis blocked)
     2. `stats.guardrail_breach` -> `"rollback"`
     3. `not _ready_to_call(stats, config)` (`day < 7` or `n < required_n_per_arm`) -> `"continue"`
     4. `prob_beats_control >= 0.95` AND `expected_loss_ship <= 0.0025` -> `"scale"`
     5. `prob_beats_control <= 0.05` -> `"stop"`
     6. Otherwise -> `"continue"`
2. **Statistical Core (`stats/core.py`)**:
   - `srm_check`: Chi-square goodness-of-fit test (`p_value < 0.001` triggers SRM flag) (`stats/core.py:39-45`).
   - `freq_test`: Two-sample proportion Z-test (`z_stat`, `p_value`, `lift_abs`, `ci_low`, `ci_high`) (`stats/core.py:48-63`).
   - `bayes_decision`: Monte Carlo Beta-Binomial posterior sampling (50,000 draws) (`stats/core.py:66-81`).
3. **Driver Diagnostics & Narrative (`stats/diagnostics.py` & `agents/narrator.py`)**:
   - `analyze_drivers`: Decomposes overall lift into segment slices (`driving`, `in_line`, `dragging`, `inconclusive`) (`stats/diagnostics.py:80-132`).
   - `verify_numeric_grounding`: Anti-hallucination guard that checks every number in the LLM response against computed facts (`agents/narrator.py:90-95`).

#### Code & Logic Bugs / Gaps in R3
1. **Empty `rules_engine/` Directory**:
   - Decision rule logic is placed in `stats/core.py:decide` rather than in a dedicated `rules_engine/` module.
2. **CRITICAL BUG — Guardrail Metric Directionality**:
   - In `stats/core.py:84-88`:
     ```python
     def guardrail_check(c_rate: float, t_rate: float, margin: float = GUARDRAIL_MARGIN) -> tuple[bool, float]:
         observed_margin = t_rate - c_rate
         return observed_margin > margin, observed_margin
     ```
   - **Bug Analysis**: The check evaluates `t_rate - c_rate > 0.01` assuming higher treatment rate is ALWAYS bad. However, `metrics_catalog.csv` specifies a `direction` column (`increase_good` vs `decrease_good`). For guardrail metrics like `crash_free_rate` or `app_rating` (where `direction = 'increase_good'`), an INCREASE in treatment rate is good, but `guardrail_check` incorrectly flags `t_rate > c_rate` as a guardrail breach!
3. **Single Guardrail Pair Telemetry Limitation**:
   - `DayStats` schema (`shared/models.py:79-92`) contains only `guardrail_control_rate: float` and `guardrail_treatment_rate: float`.
   - Experiments with multiple guardrail metrics (e.g. `guardrail_metrics = ["checkout_abandon_rate", "error_rate"]`) cannot supply or monitor per-metric guardrail telemetry.
4. **Schema & Action Case Mismatches vs `PROJECT.md`**:
   - `PROJECT.md` specifies output contract `DecisionRecommendation` (`action`: `"Scale"` \| `"Continue"` \| `"Stop"` \| `"Rollback"`, `confidence_score`: float, `risk_assessment`: dict, `explainable_summary`: str).
   - Implementation uses `Decision` (`shared/models.py:121-130`) with lowercase actions (`"scale"`, `"continue"`, `"stop"`, `"rollback"`, `"pause"`).
   - `risk_assessment` is not exposed as a distinct dictionary field; risk values are embedded inside `reasoning_stats`.
5. **Confidence Calculation Inaccuracy**:
   - `api/service.py:218`: `confidence = result.prob_beats_control if action == "scale" else 1 - result.prob_beats_control`.
   - For `rollback` (triggered by guardrail breach) or `pause` (triggered by SRM), setting `confidence = 1 - prob_beats_control` is mathematically meaningless (it reflects conversion probability, not SRM/guardrail certainty).

---

## 2. Logic Chain

1. **Observation**: Directory listing confirms `/Users/akikjana/documents/TheTalentHack/ExpPilot/rules_engine` is empty (0 `.py` files).
   - **Reasoning**: Pre-launch validation and decision recommendation rules currently live in `agents/validator.py`, `agents/recommender.py`, and `stats/core.py`.
   - **Step Conclusion**: Refactoring rule logic into `rules_engine/` is required to align with `PROJECT.md` architectural specifications.

2. **Observation**: `stats/core.py:85` calculates `observed_margin = t_rate - c_rate` and triggers breach when `observed_margin > GUARDRAIL_MARGIN`.
   - **Reasoning**: `data/seeds/metrics_catalog.csv` defines metrics with `direction IN ('increase_good', 'decrease_good')`. `guardrail_check` ignores this direction field. For `increase_good` guardrails, `t_rate > c_rate` means improvement, yet the code flags it as a breach.
   - **Step Conclusion**: Guardrail check logic must look up metric direction from `metrics_catalog` or config to evaluate breaches correctly.

3. **Observation**: `DayStats` in `shared/models.py:86-87` has only scalar `guardrail_control_rate` and `guardrail_treatment_rate`.
   - **Reasoning**: `ExperimentConfig.guardrail_metrics` is a list of strings. Multiple guardrail metrics cannot be evaluated independently per day without expanding telemetry schema.
   - **Step Conclusion**: `DayStats` schema needs a dictionary or list representation for multi-guardrail telemetry inputs (e.g. `guardrail_rates: dict[str, dict[str, float]]`).

4. **Observation**: `shared/models.py:23` defines `primary_metric: Literal["conversion_rate"]`.
   - **Reasoning**: `metrics_catalog.csv` lists primary metrics such as `purchase_conversion`, `signup_rate`, `revenue_per_user`. Restricting Pydantic validation to literal `"conversion_rate"` prevents testing hypotheses on other primary metrics.
   - **Step Conclusion**: `primary_metric` field should accept any string key validated against `metrics_catalog`.

5. **Observation**: Inspection of `tests/test_lifecycle.py` shows 16 test cases.
   - **Reasoning**: The test suite covers `scale`, `pause` (SRM), basic recommendations, validator audience overlap, and narrator grounding. It lacks test coverage for `rollback`, `stop`, traffic split validation failures, underpowered horizon warnings, multi-guardrail evaluation, and API endpoint integration.
   - **Step Conclusion**: Missing unit and integration tests must be created in `tests/` under M2 and M6.

---

## 3. Caveats

- **No modifications made**: This investigation was strictly read-only. No application code, schemas, or tests were altered.
- **External LLM Execution**: `agents/llm.py` attempts Gemini REST API when `GEMINI_API_KEY` is set, and Cursor CLI (`cursor-agent`) locally. In automated test environments without API keys or CLI access, it gracefully falls back to deterministic precedent-grounded templates.
- **`rules_engine/` pycache**: The presence of `__pycache__` in `rules_engine/` suggests python previously scanned or built files in that path, but no `.py` source files currently exist there.

---

## 4. Conclusion

### Summary Assessment
ExpPilot features a solid deterministic foundation (`stats/core.py`, `agents/validator.py`, `agents/narrator.py`) with strict anti-hallucination guarantees. However, there are notable structural and logic gaps that need to be addressed in subsequent implementation milestones (M3 and M5):

1. **Architecture Layout**: `rules_engine/` is completely unpopulated. Validation and decision rule modules should be relocated/refactored into `rules_engine/`.
2. **Data Model Alignment**: Align `Hypothesis`, `ExperimentConfig`, `ValidationReport`, and `Decision` model names and fields with `PROJECT.md` contracts (`HypothesisSpec`, `ValidationResult`, `DecisionRecommendation`).
3. **R1 Shortfalls**: Unbind `primary_metric` from literal `"conversion_rate"`, add support for multi-flag specs (`feature_flag_keys`), expand sample size & audience overlap checks to cover draft/scheduled experiments and sub-segments.
4. **R3 Shortfalls & Critical Bug**: Fix guardrail directionality in `guardrail_check`, extend `DayStats` to support multi-guardrail telemetry, align decision action string casing with spec (`"Scale"`, `"Continue"`, `"Stop"`, `"Rollback"`), and refine confidence score calculations for terminal actions.
5. **Testing Gaps**: Build targeted unit tests for `rollback`, `stop`, traffic split validation, underpowered horizons, and FastAPI endpoints.

---

## 5. Verification Method

To independently verify the observations and findings in this audit report:

1. **Verify Empty `rules_engine/` Directory**:
   - Inspect contents of `rules_engine/`:
     ```bash
     ls -la rules_engine/
     ```
   - Confirm only `__pycache__` exists and no `.py` source files are present.

2. **Inspect Guardrail Directionality Bug**:
   - View `stats/core.py` lines 84-88:
     ```python
     def guardrail_check(c_rate: float, t_rate: float, margin: float = GUARDRAIL_MARGIN) -> tuple[bool, float]:
         observed_margin = t_rate - c_rate
         return observed_margin > margin, observed_margin
     ```
   - Compare with `data/seeds/metrics_catalog.csv` column `direction` (e.g. `increase_good` vs `decrease_good`).

3. **Inspect Pydantic Schema Constraints**:
   - View `shared/models.py` lines 17-29 (`Hypothesis.primary_metric`) and lines 86-87 (`DayStats`).

4. **Run Existing Test Suite**:
   - Execute unit tests to confirm current baseline:
     ```bash
     python -m unittest tests/test_lifecycle.py -v
     ```
   - Confirm 16 tests execute cleanly.

---
