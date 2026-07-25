# Original User Request

## Initial Request — 2026-07-25T01:04:57Z

# Teamwork Project Prompt — AI Experiment Copilot & Decision Intelligence

Build an AI-powered Experiment Copilot & Decision Intelligence system (ExpPilot) that guides product teams throughout the entire experimentation lifecycle—from hypothesis generation, feature flag and target audience configuration, pre-launch validation, and real-time monitoring to statistical analysis, explainable result summaries, and actionable decision recommendations (Scale, Continue, Stop, Rollback).

Working directory: /Users/akikjana/documents/TheTalentHack/ExpPilot
Integrity mode: development

## Requirements

### R1. Hypothesis Generation & Pre-Launch Configuration Validation
The AI assistant must generate structured experiment hypotheses tied to business goals, recommend Feature Flags, target audiences, and success/guardrail metrics, and automatically validate experiment setups before launch (detecting invalid schemas, traffic allocation errors, sample size issues, and overlapping/conflicting experiments).

### R2. Continuous Performance Monitoring & Statistical Engine
The engine must process incoming experiment telemetry, run rigorous statistical tests (Frequentist sequential testing / Bayesian inference for p-values, confidence intervals, sample size reach, and guardrail metric degradation), and generate plain-language explainable summaries detailing why a specific variant is winning or underperforming.

### R3. Decision Recommendation Engine (Scale / Continue / Stop / Rollback)
The assistant must analyze real-time performance and historical experiment data to recommend next best actions—specifically classifying experiments into `Scale`, `Continue`, `Stop`, or `Rollback`—accompanied by risk assessments, confidence scores, and business-friendly explainability.

### R4. Automated Evaluation Framework (Evals Suite)
The system must include an automated evaluation suite testing all key metrics:
- Creation & analysis time reduction
- Configuration acceptance rate
- Recommendation accuracy against expert decision benchmarks
- Statistical significance detection accuracy
- Adoption readiness of AI recommendations

## Acceptance Criteria

### Experiment Setup & Pre-Launch Guardrails
- [ ] Hypothesis generator accepts high-level business goals and outputs structured hypothesis specs with primary/guardrail metrics and audience criteria.
- [ ] Pre-launch validator detects sample size shortfalls, audience overlap conflicts, and missing feature flag keys before launch.

### Statistical & Decision Intelligence
- [ ] Statistical analysis module computes accurate p-values, confidence bounds, and sample size sufficiency for A/B/n test variants.
- [ ] Decision engine outputs explicit `Scale`, `Continue`, `Stop`, or `Rollback` recommendations with structured rationale and confidence scores.
- [ ] Result summarizer converts complex statistical metrics into clear, non-jargon executive summaries explaining variant performance.

### Evaluation Suite & System Alignment
- [ ] Automated eval scripts in `evals/` measure recommendation precision against ground-truth benchmarks and evaluate statistical significance accuracy.
- [ ] Test suites in `tests/` pass cleanly without errors across all modules (agents, stats, rules_engine, api/ui).
