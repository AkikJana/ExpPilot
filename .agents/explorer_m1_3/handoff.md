# Handoff Report: ExpPilot Test Harness, Dependencies, & R4 Evals Suite Audit

**Agent**: Explorer (`explorer_m1_3`)  
**Milestone**: M1_3 (Exploration & Architecture Audit)  
**Date**: 2026-07-25  

---

## 1. Observation

### 1.1 Filesystem & Layout Findings
- **`tests/` Directory**: Contains exactly **one** test file: `tests/test_lifecycle.py` (274 lines).
  - Total test count: 15 unit/integration tests split across 3 test classes (`LifecycleTests`, `RecommendationAndDiagnosticsTests`, `NarratorNumericGuardTests`).
  - No subdirectories or additional test files exist in `tests/`.
- **`evals/` Directory**: Contains **0 source files**.
  - Only contains a `.pycache` directory (`evals/__pycache__`) with compiled `.pyc` artifacts (`gold.cpython-313.pyc`, `harness.cpython-313.pyc`, `run_evals.cpython-313.pyc`, `demo_dryrun.cpython-313.pyc`).
  - The actual Python source files (`gold.py`, `harness.py`, `run_evals.py`, `demo_dryrun.py`) are missing/absent.
- **`harness/` Directory**: Contains `harness/__init__.py` and `harness/gitops.py` (40 lines).
  - Implements `flag_manifest()` and `gitops_proposal()`.
  - Architecture constraint enforced: GitOps manifest proposals only (`requires_review: "true"`), no direct Harness API key usage or live flag mutation.
- **`requirements.txt`**:
  - Contains 11 runtime packages: `fastapi>=0.115,<1`, `uvicorn[standard]>=0.30,<1`, `streamlit>=1.38,<2`, `pydantic>=2.8,<3`, `numpy>=2.0,<3`, `scipy>=1.13,<2`, `requests>=2.32,<3`, `pandas>=2.2,<3`, `langgraph>=0.2,<1`, `psycopg[binary]>=3.2,<4`, `neo4j>=5.25,<6`.
  - **Missing**: Testing and evaluation dependencies (`pytest`, `pytest-cov`, `httpx` for FastAPI testing, `pytest-asyncio`, `coverage`).
- **Pytest Configuration**:
  - **No configuration file** (`pytest.ini`, `pyproject.toml`, `.pytest.ini`, or `setup.cfg`) exists anywhere in the project root or subdirectories.
  - No configured `pythonpath`, test discovery pattern, test markers (`@pytest.mark.unit`, `@pytest.mark.eval`), or coverage settings.
- **Other Directories**:
  - `rules_engine/`: Empty (0 `.py` files, only `__pycache__` with `evaluator.cpython-313.pyc`).
  - `synthgen/`: Empty (0 `.py` files).
  - `distributed/`: Subdirectories present but 0 `.py` source files. Stale pycache contains `test_evaluator.cpython-313-pytest-8.3.4.pyc`.

### 1.2 Module Test Coverage Summary

| Module / Component | Target Files | Existing Tests | Estimated Unit Coverage | Primary Uncovered Scope |
|---|---|---|---|---|
| **`stats/`** | `core.py`, `diagnostics.py` | Indirect via `analyze_day` in `test_lifecycle.py` | ~40% | Direct math unit tests for `power_analysis`, `srm_check` chi-square, `freq_test` z-stat/p-value bounds, `bayes_decision` Beta simulation stability, `guardrail_check`, `analyze_drivers` band thresholds |
| **`agents/`** | `llm.py`, `narrator.py`, `recommender.py`, `validator.py`, `graph.py` | 5 narrator numeric guard tests; 2 recommender/validator tests in `test_lifecycle.py` | ~25% | `graph.py` state machine graph execution; `llm.py` Gemini API & Cursor CLI fallbacks and prompt formatting; standalone edge-cases for validator & recommender |
| **`api/`** | `main.py`, `service.py` | 2 service methods tested (`get_timeline`, `branch_ontology`) | ~15% | All FastAPI HTTP endpoints (`/experiments`, `/copilot/run`, `/monitor`, `/harness-gitops`, `/reset`); HTTP 404/409/422 status code validation |
| **`harness/`** | `gitops.py` | 1 test in `test_lifecycle.py` (`test_branching_is_queued_and_harness_is_gitops_only`) | ~50% | Error handling for non-terminal actions (`ValueError`), custom segment manifest generation, YAML syntax validation |
| **`data/`** | `db.py`, `seed.py` | Indirect DB creation via temp SQLite files | ~20% | SQLite schema creation (`init_db`), CSV seed parsing error handling, `ensure_seeded()` thread safety |
| **`ontology/`** | `tree.py` | Indirect tree branching | ~20% | `initial_tree()` layout, invalid parent node handling, tree serialization |
| **`ui/`** | `app.py` (40KB) | 0 tests | 0% | Streamlit app rendering, state transitions, API client calls |
| **`evals/`** | missing source files | 0 tests | 0% | Automated evaluation suite completely unbuilt |

### 1.3 Audit of Requirement R4 (Automated Evaluation Framework)
The ExpPilot specification defines R4 as an automated evaluation framework to measure system performance and alignment across six key dimensions:

1. **Creation Time Reduction**: Intended to benchmark time saved generating hypothesis specs and pre-launch configs vs manual setups.
   - *Current State*: **NOT IMPLEMENTED**. No timing benchmarks or baseline comparison scripts exist.
2. **Analysis Time Reduction**: Intended to benchmark time saved computing stats and decisions vs manual statistical analysis.
   - *Current State*: **NOT IMPLEMENTED**. No telemetry processing latency benchmark exists.
3. **Configuration Acceptance Rate**: Intended to measure the percentage of copilot-generated experiment specs passing pre-launch validation without human intervention.
   - *Current State*: **NOT IMPLEMENTED**. No batch generation or validation acceptance script exists.
4. **Recommendation Precision against Benchmarks**: Intended to evaluate recommender key selections (flag, segment, primary/guardrail metrics) against gold standard datasets.
   - *Current State*: **NOT IMPLEMENTED**. No benchmark datasets or precision/recall evaluators exist in `evals/`.
5. **Statistical Significance Detection Accuracy**: Intended to evaluate detection accuracy of SRM anomalies, Frequentist p-value thresholds, and Bayesian scale/stop/rollback boundaries across synthetic experiment telemetry scenarios.
   - *Current State*: **NOT IMPLEMENTED**. No synthetic telemetry dataset generator or statistical evaluator exists.
6. **Adoption Readiness**: Intended as a composite metric measuring overall test pass rate, validation pass rate, hallucination block rate, and metric precision.
   - *Current State*: **NOT IMPLEMENTED**.

---

## 2. Logic Chain

1. **Test Infrastructure Deficiency**:
   - `requirements.txt` specifies runtime libraries (`fastapi`, `pydantic`, `scipy`, `pandas`) but excludes test tools (`pytest`, `pytest-cov`, `httpx`).
   - Without a `pytest.ini` file, running `pytest` without setting `PYTHONPATH=.` causes import errors (`ModuleNotFoundError: No module named 'shared'`).
2. **Incomplete Test Coverage**:
   - The current test suite (`tests/test_lifecycle.py`) only tests happy paths for high-level service functions (`create_experiment`, `analyze_day`, `get_timeline`, `branch_ontology`, `propose_harness_gitops`) and narrator numeric guards.
   - Crucial core logic (e.g. direct statistical functions in `stats/core.py`, edge cases in `agents/validator.py`, API endpoint response schemas and error codes in `api/main.py`) lacks direct unit tests.
3. **Absence of Evaluation Framework (R4 Gap)**:
   - Stale `.pyc` files in `evals/__pycache__` show that `.py` scripts (`gold.py`, `harness.py`, `run_evals.py`) existed at some point, but no `.py` files currently exist in `evals/`.
   - Without ground-truth datasets (`evals/benchmarks/`) and evaluation scripts (`evals/run_evals.py`), R4 metrics (creation/analysis time reduction, recommendation precision, statistical detection accuracy, configuration acceptance rate, adoption readiness) cannot be measured.

---

## 3. Caveats

- **Read-Only Scope**: This audit was strictly read-only. No missing test files, configuration files, or evals scripts were created or modified during this inspection.
- **Pytest Command Execution**: Running `.venv/bin/python -m pytest` required interactive permission prompts which timed out; static code analysis was used to inspect `tests/test_lifecycle.py` and verify all test definitions.
- **Stale `.pyc` Artifacts**: The presence of compiled `.pyc` files in `evals/__pycache__` and `distributed/` indicates previous code existed. However, source files in `evals/` must be authored from scratch or restored in subsequent milestones.

---

## 4. Conclusion

ExpPilot has a working, well-structured core application (`agents/`, `api/`, `stats/`, `harness/`, `shared/`), but its testing and evaluation infrastructure has major gaps:
1. **Test Configuration**: Pytest is unconfigured, and test dependencies are missing from `requirements.txt`.
2. **Test Suite Layout**: Only 1 test file (`tests/test_lifecycle.py` with 15 tests) exists. Unit tests for `stats/core.py`, `agents/validator.py`, `agents/recommender.py`, `agents/graph.py`, and `api/main.py` REST routes are missing.
3. **R4 Evals Suite**: `evals/` is currently empty of Python source code and benchmark datasets. None of the 6 R4 requirement metrics are currently implemented.

---

## 5. Verification Method

To independently verify the observations in this report:

1. **Inspect Test Suite Layout**:
   ```bash
   ls -la /Users/akikjana/documents/TheTalentHack/ExpPilot/tests/
   # Output will show only test_lifecycle.py
   ```
2. **Inspect Evals Directory**:
   ```bash
   ls -la /Users/akikjana/documents/TheTalentHack/ExpPilot/evals/
   # Output will show 0 .py files (only __pycache__)
   ```
3. **Check Dependencies & Pytest Config**:
   ```bash
   cat /Users/akikjana/documents/TheTalentHack/ExpPilot/requirements.txt
   # Observe missing pytest, pytest-cov, httpx
   ls -la /Users/akikjana/documents/TheTalentHack/ExpPilot/pytest.ini
   # Observe file does not exist
   ```
4. **Execute Existing Test Suite**:
   ```bash
   PYTHONPATH=. python3 -m unittest discover tests
   # Executes 15 tests in tests/test_lifecycle.py
   ```

---

## 6. Concrete Recommendations for Future Milestones

### Milestone 2 (E2E Testing Track Suite Creation):
1. **Environment & Configuration**:
   - Add `pytest>=8.0.0`, `pytest-cov>=5.0.0`, `httpx>=0.27.0` to `requirements.txt`.
   - Create `pytest.ini` in project root with `pythonpath = .` and `testpaths = tests`.
2. **Test Suite Expansion**:
   - Add `tests/test_stats.py`: Unit tests for Frequentist z-test/p-values, Chi-Square SRM check, Bayesian Beta simulation, and `decide()` rule hierarchy.
   - Add `tests/test_validator.py`: Unit tests for pre-launch validation rules (flag availability, audience overlap, traffic split, power horizon, guardrails).
   - Add `tests/test_recommender.py`: Category keyword matching, precedent ranking, flag allocation.
   - Add `tests/test_api.py`: FastAPI `TestClient` route tests for POST `/experiments`, POST `/monitor`, GET `/timeline`, POST `/harness-gitops`, error status codes (404, 409, 422).
   - Add `tests/test_graph.py`: LangGraph state machine execution and conditional routing tests.
   - Add `tests/test_harness.py`: Harness GitOps manifest generation, non-terminal action handling (`ValueError`), YAML syntax validation.

### Milestone 6 (R4 Evals Suite & System Alignment):
1. **Benchmark Datasets (`evals/benchmarks/`)**:
   - Create `gold_recommendations.json`: 20+ business goals mapped to expert-validated category, segment, flag, and metric choices.
   - Create `telemetry_scenarios.json`: 30+ synthetic experiment telemetry series covering true positive lift, true negative, SRM imbalance, guardrail breach, underpowered sample size.
2. **Eval Framework Scripts (`evals/`)**:
   - Create `evals/evaluator.py`: Modules to compute:
     - Recommendation Precision & Recall vs gold dataset.
     - Statistical Significance Detection Accuracy (True Positive / False Positive rates on synthetic telemetry).
     - Configuration Acceptance Rate (% of specs passing validator without manual edits).
     - Creation & Analysis Time Reduction metrics (execution latency benchmarking).
     - Adoption Readiness Score (composite alignment score).
   - Create `evals/run_evals.py`: CLI harness to run evals suite, aggregate scores, and print structured eval reports.
