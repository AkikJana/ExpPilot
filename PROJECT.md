# Project: ExpPilot (AI Experiment Copilot & Decision Intelligence)

## Architecture
ExpPilot consists of four core modular subsystems integrated into a unified workflow:
1. **Hypothesis Generation & Pre-Launch Validation (`agents/`, `rules_engine/`)**: Takes business goals, produces structured hypothesis specs (`HypothesisSpec`), and validates flag keys, traffic allocation, sample sizes, and overlap conflicts via `rules_engine/validator.py`.
2. **Statistical Engine & Performance Monitoring (`stats/`)**: Processes experiment telemetry, calculates Frequentist / Bayesian metrics (p-values, confidence intervals, sample size reach, guardrail degradation, mSPRT sequential corrections) and outputs natural-language summaries via `agents/narrator.py`.
3. **Decision Recommendation Engine (`rules_engine/`, `agents/`)**: Evaluates real-time telemetry and statistical metrics via `rules_engine/decision.py` to recommend action (`Scale`, `Continue`, `Stop`, `Rollback`) with confidence scores and risk rationale.
4. **Automated Evaluation & Testing Suite (`evals/`, `tests/`)**: Benchmarks system performance, recommendation precision against benchmarks, statistical detection accuracy, creation/analysis time reduction, and system alignment.

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Exploration & Architecture Audit | Codebase inspection & gap analysis | None | DONE |
| 2 | E2E Testing Track Suite Creation | Tiers 1-4 requirement-driven tests & evals in `tests/` & `evals/` | M1 | DONE |
| 3 | R1: Hypothesis Gen & Validation | `rules_engine/validator.py`, `HypothesisSpec`, `ValidationResult` | M1 | DONE |
| 4 | R2: Statistical Engine & Monitoring | Guardrail direction fix, mSPRT, multi-guardrail telemetry | M1 | DONE |
| 5 | R3: Decision Recommendation Engine | `rules_engine/decision.py`, `DecisionRecommendation` ("Scale"/"Continue"/"Stop"/"Rollback") | M3, M4 | DONE |
| 6 | R4: Evals Suite & System Alignment | Evals scripts in `evals/`, 100% E2E test pass, Tier 5 adversarial hardening | M2, M5 | IN_PROGRESS |

## Interface Contracts
### Hypothesis Generator ↔ Pre-Launch Validator
- Inputs: Business goals string, target domain, optional parameters.
- Output: `HypothesisSpec` schema (hypothesis: str, primary_metric: str, guardrail_metrics: list[str], feature_flag_keys: list[str], target_audience: dict).
- Validator Input: `HypothesisSpec` + Experiment setup config.
- Validator Output: `ValidationResult` schema (is_valid: bool, errors: list[str], warnings: list[str]).

### Statistical Engine ↔ Decision Engine
- Inputs: Experiment telemetry data (variants, sample sizes, conversions/metrics, time series, guardrail directions).
- Output: `StatsResult` schema (variant_stats: dict, p_values: dict, confidence_intervals: dict, sample_size_sufficient: bool, guardrail_degraded: bool, srm_flag: bool).
- Decision Engine Input: `StatsResult` + `HypothesisSpec`.
- Decision Engine Output: `DecisionRecommendation` schema (action: "Scale" | "Continue" | "Stop" | "Rollback", confidence_score: float, risk_assessment: dict, explainable_summary: str).

## Code Layout
- `agents/`: AI copilot agent implementations (`graph.py`, `llm.py`, `narrator.py`, `recommender.py`, `validator.py`).
- `rules_engine/`: Pre-launch validation (`validator.py`) and decision recommendation rules (`decision.py`).
- `stats/`: Frequentist, Bayesian, mSPRT sequential testing, and driver diagnostics (`core.py`, `diagnostics.py`).
- `evals/`: Automated evaluation benchmarks (`evals/benchmarks/`) and evaluation metrics runner (`evaluator.py`, `run_evals.py`).
- `tests/`: Complete unit, integration, and E2E test suites (`test_lifecycle.py`, `test_stats.py`, `test_validator.py`, `test_recommender.py`, `test_api.py`, `test_graph.py`, `test_harness.py`, `test_evals.py`, `test_decision.py`).
- `api/` / `ui/`: FastAPI endpoints and Streamlit interactive workspace.
