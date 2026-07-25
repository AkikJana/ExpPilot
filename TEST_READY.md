# ExpPilot Test & Evaluation Suite (Milestone 2)

## Overview
This document specifies the E2E Testing Suite (Tiers 1-4) and Evaluation Suite setup created for ExpPilot. All tests and evals are fully deterministic, genuine, and grounded in real application logic and SQLite/PostgreSQL seed catalogs.

---

## Test Execution Commands

### 1. Run Complete Test Suite via pytest
```bash
pytest
```
*Note: Configured via `pytest.ini` with `pythonpath = .` and `testpaths = tests evals`.*

### 2. Run Complete Test Suite via unittest
```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

### 3. Run Evaluation Suite CLI Runner
```bash
python3 evals/run_evals.py
```

### 4. Run Evaluation Suite as JSON Output
```bash
python3 evals/run_evals.py --json
```

---

## Test Tier Breakdown

### Tier 1: Core Unit Tests
- `tests/test_stats.py`: Frequentist Z-test, Chi-square Sample Ratio Mismatch (SRM), Bayesian Beta-Binomial decisioning, Power analysis sample sizing, Readiness gate logic (MIN_RUNTIME_DAYS=7, required N), Precedence decision hierarchy, Guardrail margin breach checking.
- `tests/test_validator.py`: Pre-launch validation rules including feature flag availability, audience segment overlap detection, traffic split validation, power horizon feasibility (<= 30 days planning horizon), segment traffic capacity checks, and guardrail metric classification.
- `tests/test_recommender.py`: Category inference via keyword mapping, precedent-driven segment selection, non-busy segment fallback, feature flag allocation, data-driven primary metric selection, curated guardrails catalog validation, precedent search.

### Tier 2: API Integration Tests
- `tests/test_api.py`: FastAPI HTTP routes (`/health`, `/experiments`, `/copilot/run`, `/experiments/{id}/start`, `/experiments/{id}/conclude`, `/monitor`, `/timeline`, `/experiments/{id}/harness-gitops`, `/experiments/{id}/ontology`, `/reset`). Includes error handling (404 Not Found, 409 Conflict, 422 Unprocessable Entity).

### Tier 3: State Machine & Harness GitOps Tests
- `tests/test_graph.py`: LangGraph state machine execution, graph node routing (`START` -> `configure` -> `monitor` / `end` -> `END`), state dictionary contracts.
- `tests/test_harness.py`: Harness GitOps YAML manifest generation, state flag toggles (`state: true` on `scale`, `state: false` on `stop`/`rollback`/`pause`), non-terminal action handling (`ValueError` on `continue`).

### Tier 4: Evaluation & Alignment Suite
- `evals/benchmarks/gold_recommendations.json`: 20 benchmark scenarios covering product categories, audience segments, feature flags, primary metrics, and guardrails.
- `evals/benchmarks/telemetry_scenarios.json`: 30 synthetic experiment telemetry scenarios covering True Positives (`scale`), True Negatives (`stop`), SRM Imbalances (`pause`), Guardrail Breaches (`rollback`), and Underpowered Horizons (`continue`).
- `evals/evaluator.py`: Benchmark evaluator computing Recommendation Precision/Recall, Statistical Detection Accuracy (TPR, TNR, SRM rate, Guardrail rate), Pre-Launch Configuration Acceptance Rate, Creation/Analysis Time Reduction Metrics, and Composite Adoption Readiness Score.
- `evals/run_evals.py`: CLI evaluation runner.
- `tests/test_evals.py`: Automated tests validating clean execution of the evaluation suite.

---

## Feature & Test Coverage Checklist

| Component / Module | Test Suite File | Coverage Scope | Status |
|-------------------|-----------------|----------------|--------|
| Statistical Engine (`stats/core.py`) | `tests/test_stats.py` | Z-test, SRM, Bayes, Power, Readiness, Decisions, Guardrails | ✅ PASSED |
| Pre-Launch Validator (`agents/validator.py`) | `tests/test_validator.py` | Flags, Overlap, Split, Horizon, Segment Traffic, Guardrails | ✅ PASSED |
| Recommender Engine (`agents/recommender.py`) | `tests/test_recommender.py` | Category Inference, Precedents, Segment/Flag Allocation, Metrics | ✅ PASSED |
| FastAPI Application (`api/main.py`) | `tests/test_api.py` | Endpoints, HTTP Status Codes, Data Payload Mapping | ✅ PASSED |
| LangGraph State Machine (`agents/graph.py`) | `tests/test_graph.py` | Node compilation, edge routing, state graph invocation | ✅ PASSED |
| Harness GitOps (`harness/gitops.py`) | `tests/test_harness.py` | Manifest YAML structure, terminal vs non-terminal actions | ✅ PASSED |
| Evals Runner & Evaluator (`evals/`) | `tests/test_evals.py` | Evals execution, 20 gold + 30 telemetry scenarios, readiness score | ✅ PASSED |
