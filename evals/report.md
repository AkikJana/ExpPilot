# ExpPilot Eval Report

Experiments scored: **36** (zero LLM calls — the decision path is deterministic)

## Headline metrics

| Metric | Value | Target | Status |
|---|---|---|---|
| Overall accuracy | 0.917 | ≥ 0.85 | PASS |
| A/A false-positive rate | 0.000 | ≤ 0.10 | PASS |
| SRM detection rate | 1.000 | ≥ 1.00 | PASS |
| Mean days to decision | 5.05 | – | – |

## Per-scenario accuracy

| Scenario | Accuracy |
|---|---|
| true_lift | 1.000 |
| aa_null | 1.000 |
| srm | 1.000 |
| guardrail_breach | 1.000 |
| underpowered | 0.500 |

## Individual runs

| Scenario | Seed | Predicted | Expected | Day | Correct |
|---|---|---|---|---|---|
| true_lift | 100 | scale | scale | 7 | yes |
| true_lift | 101 | scale | scale | 7 | yes |
| true_lift | 102 | scale | scale | 7 | yes |
| true_lift | 103 | scale | scale | 7 | yes |
| true_lift | 104 | scale | scale | 7 | yes |
| true_lift | 105 | scale | scale | 7 | yes |
| aa_null | 100 | continue | continue | 14 | yes |
| aa_null | 101 | continue | continue | 14 | yes |
| aa_null | 102 | continue | continue | 14 | yes |
| aa_null | 103 | continue | continue | 14 | yes |
| aa_null | 104 | continue | continue | 14 | yes |
| aa_null | 105 | continue | continue | 14 | yes |
| aa_null | 106 | continue | continue | 14 | yes |
| aa_null | 107 | continue | continue | 14 | yes |
| aa_null | 108 | continue | continue | 14 | yes |
| aa_null | 109 | continue | continue | 14 | yes |
| aa_null | 110 | continue | continue | 14 | yes |
| aa_null | 111 | continue | continue | 14 | yes |
| srm | 100 | pause | pause | 4 | yes |
| srm | 101 | pause | pause | 5 | yes |
| srm | 102 | pause | pause | 4 | yes |
| srm | 103 | pause | pause | 5 | yes |
| srm | 104 | pause | pause | 4 | yes |
| srm | 105 | pause | pause | 5 | yes |
| guardrail_breach | 100 | rollback | rollback | 1 | yes |
| guardrail_breach | 101 | rollback | rollback | 1 | yes |
| guardrail_breach | 102 | rollback | rollback | 1 | yes |
| guardrail_breach | 103 | rollback | rollback | 1 | yes |
| guardrail_breach | 104 | rollback | rollback | 1 | yes |
| guardrail_breach | 105 | rollback | rollback | 1 | yes |
| underpowered | 100 | scale | continue | 13 | NO |
| underpowered | 101 | continue | continue | 14 | yes |
| underpowered | 102 | scale | continue | 12 | NO |
| underpowered | 103 | rollback | continue | 6 | NO |
| underpowered | 104 | continue | continue | 14 | yes |
| underpowered | 105 | continue | continue | 14 | yes |

Full run history in the MLflow UI: `mlflow ui` (experiment `exppilot-evals`).
