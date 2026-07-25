# Handoff Report: Requirement R2 Audit (Continuous Performance Monitoring & Statistical Engine)

**Agent ID**: explorer_m1_2  
**Role**: Explorer Subagent  
**Project**: ExpPilot (`/Users/akikjana/documents/TheTalentHack/ExpPilot`)  
**Date**: 2026-07-25  

---

## 1. Observation

### 1.1 Project Structure Overview & Subsystem State
An exhaustive filesystem inspection of `/Users/akikjana/documents/TheTalentHack/ExpPilot` revealed the following module states:

- **`stats/`**: Fully implemented deterministic statistical core (`stats/core.py`) and driver diagnostics (`stats/diagnostics.py`).
- **`data/`**: Fully implemented dual SQLite/PostgreSQL database layer (`data/db.py`) and CSV catalog loader (`data/seed.py`) with seed data in `data/seeds/` (`feature_flags.csv`, `segments.csv`, `metrics_catalog.csv`, `historical_experiments.csv`). Note: `data/synth.py` does not exist on disk.
- **`synthgen/`**: Empty directory containing only `__pycache__` compiled `.pyc` files. No `.py` source files exist in `synthgen/` despite references in `README.md`.
- **`ontology/`**: Implemented serializable hypothesis tree and branching logic (`ontology/tree.py`).
- **`distributed/`**: Subdirectories (`decision_service`, `eventbus`, `gateway`, `tracing`, `vector`), `decision_audit.db`, and `__pycache__` `.pyc` files exist, but zero `.py` source files exist in `distributed/`.
- **`rules_engine/` & `evals/`**: Empty directories containing only `__pycache__`.
- **`agents/`**: Implemented LangGraph orchestration (`graph.py`), LLM adapter (`llm.py`), anti-hallucination narrative guard (`narrator.py`), SQL-grounded recommender (`recommender.py`), and pre-launch validator (`validator.py`).
- **`api/`**: FastAPI main router (`main.py`) and application service layer (`service.py`).
- **`ui/`**: Streamlit workspace (`ui/app.py`) with CSV upload, column mapping auto-detection, single-day monitoring, and batch timeline evaluation.
- **`tests/`**: Unit and integration test suite (`tests/test_lifecycle.py`).

---

### 1.2 Verbatim Code & Mathematical Formulas in `stats/`

#### 1. Sample Size & Power Analysis (`stats/core.py:23-36`)
```python
def power_analysis(
    baseline_rate: float,
    mde: float,
    alpha: float = 0.05,
    power: float = 0.8,
) -> int:
    """Return required sample size per arm for a two-sided proportions test."""
    p1 = baseline_rate
    p2 = baseline_rate + mde
    pbar = (p1 + p2) / 2
    z_a = sp_stats.norm.ppf(1 - alpha / 2)
    z_b = sp_stats.norm.ppf(power)
    n = (z_a + z_b) ** 2 * 2 * pbar * (1 - pbar) / mde**2
    return math.ceil(n)
```
**Formula**: $N = \left\lceil \frac{(z_{\alpha/2} + z_\beta)^2 \cdot 2 \bar{p}(1-\bar{p})}{MDE^2} \right\rceil$ where $\bar{p} = \frac{p_1 + p_2}{2}$.

#### 2. Sample Ratio Mismatch (SRM) Check (`stats/core.py:39-45`)
```python
def srm_check(
    control_n: int, treatment_n: int, expected_ratio: float = 0.5
) -> tuple[float, bool]:
    total = control_n + treatment_n
    expected = [total * expected_ratio, total * (1 - expected_ratio)]
    _, p_value = sp_stats.chisquare([control_n, treatment_n], f_exp=expected)
    return float(p_value), bool(p_value < SRM_ALPHA)
```
**Formula**: $\chi^2 = \sum \frac{(O_i - E_i)^2}{E_i}$ with 1 degree of freedom, $\alpha_{SRM} = 0.001$ (`SRM_ALPHA` from `shared/models.py`).

#### 3. Frequentist Two-Sample Proportions Z-Test (`stats/core.py:48-63`)
```python
def freq_test(c_conv: int, c_n: int, t_conv: int, t_n: int) -> dict[str, float]:
    c_rate = c_conv / c_n
    t_rate = t_conv / t_n
    p_pool = (c_conv + t_conv) / (c_n + t_n)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / c_n + 1 / t_n))
    z_stat = (t_rate - c_rate) / se if se > 0 else 0.0
    p_value = 2 * (1 - sp_stats.norm.cdf(abs(z_stat))) if se > 0 else 1.0
    diff = t_rate - c_rate
    se_ci = math.sqrt(c_rate * (1 - c_rate) / c_n + t_rate * (1 - t_rate) / t_n)
    return {
        "z_stat": float(z_stat),
        "p_value": float(p_value),
        "lift_abs": float(diff),
        "ci_low": float(diff - 1.96 * se_ci),
        "ci_high": float(diff + 1.96 * se_ci),
    }
```
**Formulas**:
- Pooled variance SE for hypothesis test: $SE_{pool} = \sqrt{p_{pool}(1 - p_{pool}) \left(\frac{1}{c_n} + \frac{1}{t_n}\right)}$
- Test statistic: $Z = \frac{\hat{p}_T - \hat{p}_C}{SE_{pool}}$
- P-value (two-sided): $p = 2 \cdot (1 - \Phi(|Z|))$
- Unpooled SE for Confidence Interval: $SE_{CI} = \sqrt{\frac{\hat{p}_C(1-\hat{p}_C)}{c_n} + \frac{\hat{p}_T(1-\hat{p}_T)}{t_n}}$
- 95% Confidence Interval: $(\hat{p}_T - \hat{p}_C) \pm 1.96 \cdot SE_{CI}$

#### 4. Bayesian Posterior Decision Engine (`stats/core.py:66-81`)
```python
def bayes_decision(
    c_conv: int,
    c_n: int,
    t_conv: int,
    t_n: int,
    seed: int = 0,
    draws: int = 50000,
) -> dict[str, float]:
    rng = np.random.default_rng(seed)
    control = rng.beta(1 + c_conv, 1 + c_n - c_conv, draws)
    treatment = rng.beta(1 + t_conv, 1 + t_n - t_conv, draws)
    return {
        "prob_beats_control": float(np.mean(treatment > control)),
        "expected_loss_ship": float(np.mean(np.maximum(control - treatment, 0))),
        "expected_loss_keep": float(np.mean(np.maximum(treatment - control, 0))),
    }
```
**Formulas & Model**:
- Priors: $\theta_C \sim \text{Beta}(1, 1)$, $\theta_T \sim \text{Beta}(1, 1)$ (Uniform default priors).
- Posteriors: $\theta_C | D \sim \text{Beta}(1 + c_{conv}, 1 + c_n - c_{conv})$, $\theta_T | D \sim \text{Beta}(1 + t_{conv}, 1 + t_n - t_{conv})$.
- Monte Carlo Sampling: $D = 50,000$ draws.
- $P(\text{treatment} > \text{control}) = \frac{1}{D} \sum_{i=1}^D \mathbb{I}(\theta_T^{(i)} > \theta_C^{(i)})$
- Expected Loss (Ship Treatment): $\mathbb{E}[\max(\theta_C - \theta_T, 0)] = \frac{1}{D} \sum_{i=1}^D \max(\theta_C^{(i)} - \theta_T^{(i)}, 0)$
- Expected Loss (Keep Control): $\mathbb{E}[\max(\theta_T - \theta_C, 0)] = \frac{1}{D} \sum_{i=1}^D \max(\theta_T^{(i)} - \theta_C^{(i)}, 0)$

#### 5. Guardrail Metric Degradation Check (`stats/core.py:84-88`)
```python
def guardrail_check(
    c_rate: float, t_rate: float, margin: float = GUARDRAIL_MARGIN
) -> tuple[bool, float]:
    observed_margin = t_rate - c_rate
    return observed_margin > margin, observed_margin
```
Threshold: `GUARDRAIL_MARGIN = 0.01` (from `shared/models.py:13`).

#### 6. Precedence Decision Tree (`stats/core.py:91-113`)
```python
def _ready_to_call(stats: StatsResult, config: ExperimentConfig) -> bool:
    if stats.day < MIN_RUNTIME_DAYS:
        return False
    return min(stats.control_n, stats.treatment_n) >= config.required_n_per_arm

def decide(stats: StatsResult, config: ExperimentConfig) -> str:
    """Return the deterministic, precedence-ordered experiment action."""
    if stats.srm_flag:
        return "pause"
    if stats.guardrail_breach:
        return "rollback"
    if not _ready_to_call(stats, config):
        return "continue"
    if (
        stats.prob_beats_control >= SHIP_PROB_THRESHOLD
        and stats.expected_loss_ship <= EXPECTED_LOSS_EPSILON
    ):
        return "scale"
    if stats.prob_beats_control <= KILL_PROB_THRESHOLD:
        return "stop"
    return "continue"
```
**Threshold Constants (`shared/models.py:9-14`)**:
- `SRM_ALPHA = 0.001`
- `SHIP_PROB_THRESHOLD = 0.95`
- `KILL_PROB_THRESHOLD = 0.05`
- `EXPECTED_LOSS_EPSILON = 0.0025`
- `GUARDRAIL_MARGIN = 0.01`
- `MIN_RUNTIME_DAYS = 7`

---

### 1.3 Segment Driver Diagnostics (`stats/diagnostics.py`)

Decomposes aggregate conversion rate lift into per-segment slices (`SegmentDayStats`):
```python
MIN_SEGMENT_N = 200
_RELATIVE_BAND = 0.30
_ABSOLUTE_BAND_FLOOR = 0.01

def _band(overall_lift_abs: float) -> float:
    return max(abs(overall_lift_abs) * _RELATIVE_BAND, _ABSOLUTE_BAND_FLOOR)
```
- Excludes segment slices where $\min(c_n, t_n) < 200$ (flagged `"inconclusive"`).
- Calculates segment deviation: $\delta_{segment} = \text{lift}_{segment} - \text{lift}_{overall}$.
- Classification rules:
  - $|\delta_{segment}| \le \text{band} \implies \text{"in\_line"}$
  - $\delta_{segment} > \text{band} \implies \text{"driving"}$
  - $\delta_{segment} < -\text{band} \implies \text{"dragging"}$

---

### 1.4 Plain-Language Business Narrative & Numeric Guard (`agents/narrator.py`)

```python
_TOLERANCE = 0.02  # 2% relative tolerance
_SAFE_INTEGER_RANGE = (0, 60)  # safe day/segment count range

def verify_numeric_grounding(text: str, stats: StatsResult, driver_analysis: DriverAnalysis | None = None) -> bool:
    ground_truth = _ground_truth_numbers(stats, driver_analysis)
    return all(_is_grounded(value, is_percent, ground_truth) for value, is_percent in _extract_numbers(text))
```
- Extracts all numeric tokens in LLM prose via regex (`-?\d+\.?\d*(%)?`).
- Ensures that every floating-point number or percentage matches a computed fact from `StatsResult` or `DriverAnalysis` within 2% relative tolerance.
- Carves out non-percentage small integers (0 to 60) for safe day count references.
- Rejects ungrounded LLM narratives and falls back to `_template_narrative`.

---

### 1.5 Monitoring & Timeline APIs (`api/service.py`, `api/main.py`)

- `POST /monitor` (`analyze_day`): Evaluates day stats, executes `decide`, logs results in `day_stats` and `decisions` DB tables, and records an audit log entry in `agent_runs`.
- `GET /experiments/{experiment_id}/timeline` (`get_timeline`): Fetches day-by-day telemetry and decision history for continuous trend monitoring over time.

---

## 2. Logic Chain

1. **Deterministic Core Separation**:
   - *Observation*: `stats/core.py:1` states `"Deterministic statistics core. No LLM calls are allowed here."`
   - *Reasoning*: All p-values, z-stats, Bayesian probabilities, SRM flags, guardrail margin checks, and `decide()` action selection are calculated deterministically via `scipy.stats` and `numpy`. The LLM in `agents/narrator.py` only narrates pre-computed metrics under a strict numeric validation guard (`verify_numeric_grounding`).

2. **Continuous Peeking & Alpha Inflation Gap**:
   - *Observation*: `freq_test` (lines 48-63) computes a standard fixed-sample Z-test ($z = 1.96, \alpha = 0.05$). `api/service.py:216` calls `compute_day_stats` every day as new telemetry arrives.
   - *Reasoning*: Standard Frequentist hypothesis tests assume single-shot evaluation at sample size completion ($N = \text{required\_n}$). Evaluating $p$-values daily without sequential corrections (such as mSPRT / Always Valid P-Values or Group Sequential Pocock / O'Brien-Fleming alpha-spending functions) inflates the overall false positive rate (Type I error).

3. **Readiness Gate Protection**:
   - *Observation*: `decide()` enforces `_ready_to_call()` (`stats/core.py:91-95`), requiring `day >= 7` AND `min(c_n, t_n) >= required_n_per_arm` before allowing `scale` or `stop`.
   - *Reasoning*: While the readiness gate mitigates premature stopping under fixed-horizon rules by forcing `"continue"` until sample size target and 7-day minimum runtime are reached, it does not provide true sequential testing boundaries during intermediate daily peeks.

4. **Bayesian Posterior Scope & Metric Limitations**:
   - *Observation*: `bayes_decision` (lines 66-81) models conversion events using Beta-Binomial sampling (`Beta(1 + conv, 1 + n - conv)`).
   - *Reasoning*: While optimal for binary conversion rates (e.g., checkout conversion), it cannot handle continuous metrics (e.g., `arpu`, `latency_p95_ms`) or revenue metrics requiring Log-Normal, Normal, or Gamma-Poisson posterior models.

5. **SRM & Guardrail Precedence Enforcement**:
   - *Observation*: `decide()` evaluates `stats.srm_flag` first (`"pause"`) and `stats.guardrail_breach` second (`"rollback"`).
   - *Reasoning*: This strict precedence hierarchy guarantees that data integrity issues (SRM) or business harm (guardrail degradation) immediately halt or roll back an experiment regardless of positive primary metric performance.

6. **Subsystem Completeness Gaps**:
   - *Observation*: `synthgen/` contains only `__pycache__` `.pyc` files, but no `.py` source files. `distributed/` subdirectories (`decision_service`, `eventbus`, `gateway`, `tracing`, `vector`) lack `.py` source files. `data/synth.py` is missing.
   - *Reasoning*: Live telemetry simulation relies on `data/seeds/` CSV files or user-uploaded CSVs in Streamlit (`ui/app.py`). The differentiable GPU-aware synthetic generator described in `README.md` is currently missing source files in the local repository.

---

## 3. Caveats

1. **Terminal Command Permission Timeout**: Interactive command execution via `run_command` timed out due to zsh permissions. Inspection was performed using static code analysis, `find_by_name`, `list_dir`, `view_file`, and `grep_search`.
2. **Missing Source Files in `synthgen/` & `distributed/`**: `.pyc` files in `__pycache__` indicate compiled bytecode exists from prior builds, but source files are not tracked or present in the current working directory.
3. **Priors in Bayesian Analysis**: The Bayesian engine uses uninformative uniform priors (`Beta(1,1)`). Empirical or informative historical priors (e.g., from `historical_experiments` table) are not currently utilized in posterior computation.

---

## 4. Conclusion

ExpPilot's implementation of Requirement R2 (Continuous performance monitoring & statistical engine) is **structurally solid, deterministic, and highly explainable**.

### Key Strengths:
- **Strict Hard Separation**: Core math and decisioning (`stats/core.py`) are 100% LLM-free.
- **Robust Anti-Hallucination Guard**: `agents/narrator.py` verifies every number in LLM prose against computed facts with 2% relative tolerance.
- **Deterministic Precedence Hierarchy**: SRM (`pause`) $\rightarrow$ Guardrail breach (`rollback`) $\rightarrow$ Unready (`continue`) $\rightarrow$ Scale / Stop based on Bayesian probability of superiority and expected loss.
- **Segment Driver Diagnostics**: `stats/diagnostics.py` accurately identifies segment drivers/drags using relative deviation bands.
- **Full Monitoring APIs & UI**: `POST /monitor`, `GET /experiments/{id}/timeline`, and Streamlit UI support single-day and batch multi-day evaluation.

### Existing Gaps & Missing Capabilities for R2:
1. **Lack of Sequential Testing (mSPRT / Alpha-Spending)**: Fixed-horizon Z-test is used continuously without peeking corrections.
2. **Binary-Only Bayesian Engine**: Beta-Binomial model is restricted to proportion metrics. Continuous (ARPU, latency) metric posteriors are unmodeled.
3. **Multiple Testing Correction**: No Bonferroni / FDR (Benjamini-Hochberg) adjustment when evaluating multiple guardrail metrics or segment slices.
4. **CUPED (Variance Reduction)**: No pre-experiment covariate adjustment to reduce metric variance and accelerate runtime.
5. **Missing `synthgen/` & `distributed/` Source Code**: Differentiable synthetic telemetry engine code is missing on disk.

---

## 5. Verification Method

To independently verify the findings in this report:

1. **Inspect Core Files**:
   - Review statistics formulas and decision logic: `view_file` on `/Users/akikjana/documents/TheTalentHack/ExpPilot/stats/core.py`.
   - Review driver classification logic: `view_file` on `/Users/akikjana/documents/TheTalentHack/ExpPilot/stats/diagnostics.py`.
   - Review numeric grounding guard: `view_file` on `/Users/akikjana/documents/TheTalentHack/ExpPilot/agents/narrator.py`.
   - Review monitoring service methods: `view_file` on `/Users/akikjana/documents/TheTalentHack/ExpPilot/api/service.py`.

2. **Execute Test Suite**:
   Run unit tests from project root:
   ```bash
   python -m unittest tests/test_lifecycle.py -v
   ```
   *Expected outcome*: All tests pass, validating positive scale decisions, SRM pause precedence, driver diagnostics, and numeric anti-hallucination grounding.

3. **Verify API Endpoints**:
   Start uvicorn server and test endpoints:
   - `POST /experiments`
   - `POST /experiments/{id}/start`
   - `POST /monitor`
   - `GET /experiments/{id}/timeline`

4. **Conditions for Invalidation**:
   - Any modification in `decide()` that allows LLM output to dictate actions.
   - Failure of `verify_numeric_grounding()` to reject ungrounded metric claims in `narrator.py`.
   - Discrepancy between `StatsResult` values and `decide()` precedence order.
