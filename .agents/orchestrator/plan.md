# Master Plan: ExpPilot (AI Experiment Copilot & Decision Intelligence)

## Executive Summary
ExpPilot is an AI-powered Experiment Copilot & Decision Intelligence system that guides product teams throughout the experimentation lifecycle.
This plan orchestrates the full development, testing, evaluation, and verification of ExpPilot across four main requirement areas:
- **R1**: Hypothesis Generation & Pre-Launch Configuration Validation
- **R2**: Continuous Performance Monitoring & Statistical Engine
- **R3**: Decision Recommendation Engine (Scale / Continue / Stop / Rollback)
- **R4**: Automated Evaluation Framework & System Alignment

## Architecture & Workflows

```
                           [Project Orchestrator]
                                     │
           ┌─────────────────────────┴─────────────────────────┐
           ▼                                                   ▼
[Implementation Track]                               [E2E Testing Track]
  │                                                    │
  ├── Phase 1: Explorer Audit & Gap Analysis           ├── Define E2E Test Infra
  ├── Phase 2: R1 - Hypothesis Gen & Validation        ├── Build Tier 1-4 Test Suites
  ├── Phase 3: R2 - Statistical Engine & Monitoring    └── Publish `TEST_READY.md`
  ├── Phase 4: R3 - Decision Engine & Summarizer               │
  └── Phase 5: R4 Integration & Tier 5 Hardening <─────────────┘
```

## Milestone Decomposition

### Milestone 1: Exploration & Codebase Audit
- **Objective**: Audit existing project structure, module boundaries, dependencies, existing tests/evals, and create initial `PROJECT.md`.
- **Worker**: 3 `teamwork_preview_explorer` instances.

### Milestone 2: E2E Testing Suite Creation (Dual Track)
- **Objective**: Build comprehensive, opaque-box, requirement-driven test suite in `tests/` covering Tiers 1-4.
- **Worker**: Specialist worker / testing sub-orchestrator. Output `TEST_READY.md`.

### Milestone 3: R1 — Hypothesis Generation & Pre-Launch Validation
- **Objective**: Implement/verify hypothesis generator (business goal to hypothesis spec with primary/guardrail metrics & audience criteria) and pre-launch validator (detect sample size shortfalls, audience overlap conflicts, missing flag keys).
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.

### Milestone 4: R2 — Continuous Performance Monitoring & Statistical Engine
- **Objective**: Implement/verify statistical analysis engine (Frequentist sequential testing / Bayesian inference, p-values, confidence bounds, sample size sufficiency, guardrail metric degradation) and result summarizer (explainable executive summaries).
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.

### Milestone 5: R3 — Decision Recommendation Engine
- **Objective**: Implement/verify decision engine classifying experiments into `Scale`, `Continue`, `Stop`, `Rollback` with risk assessment, confidence scores, and business-friendly rationale.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.

### Milestone 6: R4 — Automated Evaluation Framework & Tier 5 Adversarial Hardening
- **Objective**: Implement/verify evaluation suite in `evals/` measuring creation & analysis time reduction, configuration acceptance rate, recommendation precision against benchmarks, statistical significance detection accuracy, and adoption readiness. Conduct Tier 5 white-box adversarial testing.
- **Subagents**: Explorer -> Worker -> Reviewer -> Challenger -> Auditor.

## Verification & Audit Strategy
- Every milestone requires passing unit/integration/E2E tests.
- Every milestone requires 2 independent Reviewers and 2 Challengers.
- Every milestone requires a Forensic Auditor (`teamwork_preview_auditor`). Violation verdict = BINARY VETO.
