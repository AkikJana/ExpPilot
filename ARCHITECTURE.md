# ExpPilot — System Architecture

> **AI Experiment Copilot & Decision Intelligence**
> Full-lifecycle experimentation platform: hypothesis → configuration → monitoring → decision → deployment

---

## High-Level System Overview

```mermaid
graph TB
    subgraph USER["👤 User / Product Team"]
        UI["Streamlit UI<br/>(ui/app.py)"]
        API_CLIENT["API Client / CLI"]
    end

    subgraph API_LAYER["🌐 API Layer (FastAPI)"]
        MAIN["api/main.py<br/>FastAPI App v0.2.0"]
    end

    subgraph SERVICE["⚙️ Service Orchestrator"]
        SVC["api/service.py<br/>Application Service"]
    end

    subgraph AI_AGENTS["🤖 AI Agents Layer"]
        GRAPH["agents/graph.py<br/>LangGraph State Machine"]
        LLM["agents/llm.py<br/>Gemini / Cursor Adapter"]
        REC["agents/recommender.py<br/>Deterministic Recommender"]
        NAR["agents/narrator.py<br/>Business Narrative Generator"]
        AVAL["agents/validator.py<br/>Validation Agent Wrapper"]
    end

    subgraph RULES["📏 Rules Engine"]
        RVAL["rules_engine/validator.py<br/>Pre-Launch Validator"]
        RDEC["rules_engine/decision.py<br/>Decision Rules Engine"]
    end

    subgraph STATS_ENGINE["📊 Statistical Engine"]
        SCORE["stats/core.py<br/>Frequentist + Bayesian Engine"]
        DIAG["stats/diagnostics.py<br/>Segment Driver Analysis"]
    end

    subgraph KNOWLEDGE["🧠 Knowledge & Ontology"]
        ONT["ontology/tree.py<br/>Hypothesis Tree"]
        HARNESS["harness/gitops.py<br/>Feature Flag Manifests"]
    end

    subgraph DATA_LAYER["🗄️ Data Layer"]
        DB["data/db.py<br/>Dual-Engine DB<br/>(SQLite / PostgreSQL)"]
        SEED["data/seed.py<br/>CSV Catalog Loader"]
        DERIVE["data/derive.py<br/>Audience Derivation"]
        SYNTH["data/synth.py<br/>Synthetic Telemetry"]
        SEEDS["data/seeds/<br/>CSV Catalogs"]
    end

    subgraph EVAL_SUITE["✅ Evaluation Suite"]
        ERUN["evals/run_evals.py<br/>CLI Runner"]
        EEVAL["evals/evaluator.py<br/>Benchmark Evaluator"]
        BENCH["evals/benchmarks/<br/>Gold Standards"]
    end

    subgraph SHARED["📦 Shared Contracts"]
        MODELS["shared/models.py<br/>Pydantic v2 Models"]
    end

    UI --> MAIN
    API_CLIENT --> MAIN
    MAIN --> SVC
    MAIN --> GRAPH

    SVC --> LLM
    SVC --> REC
    SVC --> NAR
    SVC --> AVAL
    SVC --> SCORE
    SVC --> DIAG
    SVC --> DB
    SVC --> SEED
    SVC --> DERIVE
    SVC --> SYNTH
    SVC --> ONT
    SVC --> HARNESS
    SVC --> RDEC

    GRAPH --> SVC

    LLM -.->|"Gemini API / Cursor CLI"| EXTERNAL["☁️ External LLM"]
    REC --> DB
    REC --> RDEC
    AVAL --> RVAL
    RVAL --> DB
    NAR --> LLM
    SCORE --> RDEC
    DIAG --> SCORE

    SEED --> DB
    SEED --> SEEDS
    DERIVE --> DB

    ERUN --> EEVAL
    EEVAL --> REC
    EEVAL --> SCORE
    EEVAL --> RVAL
    EEVAL --> BENCH

    MODELS -.->|"shared across all"| SVC
    MODELS -.-> SCORE
    MODELS -.-> RDEC
    MODELS -.-> RVAL
    MODELS -.-> REC
    MODELS -.-> NAR
    MODELS -.-> HARNESS

    style USER fill:#1a1a2e,stroke:#e94560,color:#fff
    style API_LAYER fill:#16213e,stroke:#0f3460,color:#fff
    style SERVICE fill:#0f3460,stroke:#533483,color:#fff
    style AI_AGENTS fill:#533483,stroke:#e94560,color:#fff
    style RULES fill:#e94560,stroke:#fff,color:#fff
    style STATS_ENGINE fill:#0f3460,stroke:#53a8b6,color:#fff
    style KNOWLEDGE fill:#2c3e50,stroke:#e67e22,color:#fff
    style DATA_LAYER fill:#1b4332,stroke:#2d6a4f,color:#fff
    style EVAL_SUITE fill:#7b2cbf,stroke:#c77dff,color:#fff
    style SHARED fill:#343a40,stroke:#adb5bd,color:#fff
```

---

## Experiment Lifecycle Flow

```mermaid
sequenceDiagram
    participant U as User / UI
    participant API as FastAPI (main.py)
    participant SVC as Service (service.py)
    participant REC as Recommender
    participant LLM as LLM Adapter (Gemini)
    participant VAL as Pre-Launch Validator
    participant DB as Database (SQLite/PG)
    participant STATS as Statistical Engine
    participant DEC as Decision Engine
    participant NAR as Narrator
    participant GIT as Harness GitOps

    Note over U, GIT: Phase 1 — Experiment Creation & Configuration

    U->>API: POST /experiments {goal}
    API->>SVC: create_experiment(goal)
    SVC->>REC: infer_category(goal)
    REC->>DB: Query segments, flags, metrics catalogs
    DB-->>REC: Catalog rows
    REC-->>SVC: Recommendation (flag, segment, metrics)
    SVC->>LLM: hypotheses_for_goal(goal, recommendation)
    LLM-->>SVC: Hypothesis[] (with numeric guard)
    SVC->>VAL: validate_experiment(config)
    VAL->>DB: Check flag status, audience overlap, traffic
    DB-->>VAL: Validation data
    VAL-->>SVC: ValidationReport (pass/block)
    SVC->>DB: INSERT experiment + config
    SVC-->>API: Experiment proposal + validation
    API-->>U: JSON response

    Note over U, GIT: Phase 2 — Monitoring & Analysis

    U->>API: POST /monitor {DayStats}
    API->>SVC: analyze_day(day_stats)
    SVC->>STATS: compute_day_stats(day, config)
    STATS->>STATS: Z-test, SRM, Bayesian posterior
    STATS-->>SVC: StatsResult
    SVC->>DEC: evaluate_decision(stats, config)
    DEC-->>SVC: DecisionRecommendation (Scale/Continue/Stop/Rollback)
    SVC->>NAR: narrate_decision(action, stats)
    NAR->>LLM: Generate business prose
    LLM-->>NAR: Narrative (numerically guarded)
    NAR-->>SVC: Business-friendly summary
    SVC->>DB: Persist decision + timeline
    SVC-->>API: Decision object
    API-->>U: JSON response

    Note over U, GIT: Phase 3 — Action & Deployment

    U->>API: POST /experiments/{id}/harness-gitops {action: "scale"}
    API->>SVC: propose_harness_gitops(id, "scale")
    SVC->>GIT: gitops_proposal(config, "scale")
    GIT-->>SVC: YAML manifest + PR metadata
    SVC-->>API: GitOps proposal
    API-->>U: Reviewable flag manifest
```

---

## API Endpoints Map

```mermaid
graph LR
    subgraph ENDPOINTS["FastAPI Routes"]
        H["/health"]
        LE["/experiments GET"]
        CE["/experiments POST"]
        SE["/experiments/{id}/start"]
        CO["/experiments/{id}/conclude"]
        SIM["/experiments/{id}/simulate"]
        MON["/monitor POST"]
        TL["/experiments/{id}/timeline"]
        ONT["/experiments/{id}/ontology"]
        BR["/experiments/{id}/ontology/branches"]
        HG["/experiments/{id}/harness-gitops"]
        COP["/copilot/run POST"]
        SEG["/segments GET"]
        SD["/segments/derived POST"]
        RST["/reset POST"]
    end

    subgraph LIFECYCLE["Lifecycle Phase"]
        P1["🔵 Setup"]
        P2["🟢 Running"]
        P3["🟠 Analysis"]
        P4["🔴 Action"]
    end

    CE --> P1
    SE --> P1
    COP --> P1
    SEG --> P1
    SD --> P1

    MON --> P2
    TL --> P2
    SIM --> P2

    ONT --> P3
    BR --> P3

    HG --> P4
    CO --> P4

    style P1 fill:#3498db,stroke:#2980b9,color:#fff
    style P2 fill:#2ecc71,stroke:#27ae60,color:#fff
    style P3 fill:#e67e22,stroke:#d35400,color:#fff
    style P4 fill:#e74c3c,stroke:#c0392b,color:#fff
```

---

## Data Model & Pydantic Contracts

```mermaid
classDiagram
    class Hypothesis {
        +str id
        +str goal
        +str statement
        +str primary_metric
        +Literal expected_direction
        +float expected_mde
        +str segment
        +str rationale
        +list~str~ precedent_ids
    }

    class HypothesisSpec {
        +str hypothesis
        +str primary_metric
        +list~str~ guardrail_metrics
        +list~str~ feature_flag_keys
        +dict target_audience
    }

    class ExperimentConfig {
        +str id
        +str hypothesis_id
        +str flag_key
        +str audience_segment
        +dict traffic_split
        +float baseline_rate
        +float mde
        +int required_n_per_arm
        +int estimated_days
        +list~str~ guardrail_metrics
        +int daily_traffic
        +Literal status
    }

    class DayStats {
        +str experiment_id
        +int day
        +int control_n / control_conversions
        +int treatment_n / treatment_conversions
        +float guardrail_control_rate
        +float guardrail_treatment_rate
        +list~SegmentDayStats~ segments
    }

    class StatsResult {
        +str experiment_id
        +int day
        +float srm_p_value / z_stat / p_value
        +float lift_abs / ci_low / ci_high
        +float prob_beats_control
        +float expected_loss_ship / keep
        +bool guardrail_breach
    }

    class DecisionRecommendation {
        +Literal action
        +float confidence_score
        +dict risk_assessment
        +str explainable_summary
    }

    class Decision {
        +str experiment_id
        +int day
        +Literal action
        +float confidence
        +StatsResult reasoning_stats
        +str narrative
        +bool requires_human
        +DecisionRecommendation recommendation
    }

    class ValidationIssue {
        +Literal severity
        +str code
        +str message
    }

    class ValidationReport {
        +list~ValidationIssue~ issues
        +blocking()
        +warnings()
        +passed()
    }

    Hypothesis --> ExperimentConfig : configures
    ExperimentConfig --> DayStats : receives telemetry
    DayStats --> StatsResult : analyzed by stats engine
    StatsResult --> DecisionRecommendation : fed to decision engine
    DecisionRecommendation --> Decision : wrapped in
    ExperimentConfig --> ValidationReport : validated by
    ValidationReport --> ValidationIssue : contains
```

---

## Statistical & Decision Engine Pipeline

```mermaid
flowchart TD
    TEL["📥 Incoming Telemetry<br/>(DayStats)"] --> SRM{"🔍 SRM Check<br/>Chi-square test<br/>α = 0.001"}

    SRM -->|"p < 0.001<br/>Traffic split broken"| PAUSE["⏸️ PAUSE<br/>Investigate assignment"]
    SRM -->|"p ≥ 0.001<br/>Split healthy"| GUARD{"🛡️ Guardrail Check<br/>margin ≥ 0.01?"}

    GUARD -->|"Breach detected<br/>Metric degrading"| ROLLBACK["⏪ ROLLBACK<br/>Immediate harm"]
    GUARD -->|"No breach"| READY{"⏱️ Readiness Gate<br/>day ≥ 7?<br/>N ≥ required?"}

    READY -->|"Not ready"| CONTINUE["▶️ CONTINUE<br/>Collect more data"]
    READY -->|"Ready"| BAYES{"📈 Bayesian Decisioning<br/>Beta-Binomial posterior"}

    BAYES -->|"P(B>A) ≥ 0.95<br/>E[loss|ship] < 0.0025"| SCALE["🚀 SCALE<br/>Ship treatment"]
    BAYES -->|"P(B>A) ≤ 0.05"| STOP["🛑 STOP<br/>Treatment lost"]
    BAYES -->|"Inconclusive"| CONTINUE

    style PAUSE fill:#f39c12,stroke:#e67e22,color:#fff
    style ROLLBACK fill:#e74c3c,stroke:#c0392b,color:#fff
    style CONTINUE fill:#3498db,stroke:#2980b9,color:#fff
    style SCALE fill:#2ecc71,stroke:#27ae60,color:#fff
    style STOP fill:#95a5a6,stroke:#7f8c8d,color:#fff
```

---

## Data Layer & Catalog Schema

```mermaid
erDiagram
    SEGMENTS {
        text segment_key PK
        text display_name
        int population
        int daily_traffic
        float baseline_conversion_rate
        text description
    }

    FEATURE_FLAGS {
        text flag_key PK
        text display_name
        text status
        text category
        text owner
    }

    METRICS_CATALOG {
        text metric_key PK
        text display_name
        text direction
        text category
        text unit
    }

    HISTORICAL_EXPERIMENTS {
        text experiment_id PK
        text category
        text segment_key
        text flag_key
        text primary_metric
        float lift
        text outcome
    }

    EXPERIMENTS {
        text id PK
        text config JSON
        text status
        text created_at
    }

    DECISIONS {
        text experiment_id FK
        int day
        text decision JSON
    }

    SEGMENTS ||--o{ HISTORICAL_EXPERIMENTS : "audience for"
    FEATURE_FLAGS ||--o{ HISTORICAL_EXPERIMENTS : "tested with"
    METRICS_CATALOG ||--o{ HISTORICAL_EXPERIMENTS : "measured by"
    EXPERIMENTS ||--o{ DECISIONS : "produces"
```

---

## Module Dependency Graph

```mermaid
graph TD
    MAIN["api/main.py"] --> SVC["api/service.py"]
    MAIN --> GRAPH["agents/graph.py"]
    MAIN --> DBMOD["data/db.py"]
    MAIN --> SEEDMOD["data/seed.py"]
    MAIN --> MODELS["shared/models.py"]

    GRAPH --> SVC

    SVC --> LLM["agents/llm.py"]
    SVC --> REC["agents/recommender.py"]
    SVC --> NAR["agents/narrator.py"]
    SVC --> AVAL["agents/validator.py"]
    SVC --> SCORE["stats/core.py"]
    SVC --> DIAGMOD["stats/diagnostics.py"]
    SVC --> DBMOD
    SVC --> SEEDMOD
    SVC --> DERIVE["data/derive.py"]
    SVC --> SYNTHMOD["data/synth.py"]
    SVC --> ONT["ontology/tree.py"]
    SVC --> HARNESS["harness/gitops.py"]
    SVC --> RDEC["rules_engine/decision.py"]
    SVC --> MODELS

    REC --> DBMOD
    REC --> RDEC
    REC --> MODELS

    AVAL --> RVAL["rules_engine/validator.py"]

    RVAL --> DBMOD
    RVAL --> MODELS

    NAR --> LLM
    NAR --> MODELS
    NAR --> DIAGMOD

    SCORE --> RDEC
    SCORE --> MODELS

    DIAGMOD --> SCORE
    DIAGMOD --> MODELS

    RDEC --> MODELS

    HARNESS --> MODELS

    SEEDMOD --> DBMOD
    DERIVE --> DBMOD
    SYNTHMOD --> MODELS

    ERUN["evals/run_evals.py"] --> EEVAL["evals/evaluator.py"]
    EEVAL --> REC
    EEVAL --> SCORE
    EEVAL --> RVAL
    EEVAL --> DBMOD

    UI["ui/app.py<br/>(Streamlit)"] --> MAIN

    style MAIN fill:#e74c3c,color:#fff
    style SVC fill:#3498db,color:#fff
    style GRAPH fill:#9b59b6,color:#fff
    style MODELS fill:#2c3e50,color:#fff
    style SCORE fill:#1abc9c,color:#fff
    style RDEC fill:#e67e22,color:#fff
    style RVAL fill:#e67e22,color:#fff
    style DBMOD fill:#27ae60,color:#fff
    style LLM fill:#8e44ad,color:#fff
    style ERUN fill:#7b2cbf,color:#fff
    style EEVAL fill:#7b2cbf,color:#fff
```

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Frontend** | Streamlit (`ui/app.py`, 66KB) | Interactive dashboard & copilot UI |
| **API** | FastAPI v0.2.0 | REST endpoints, request validation |
| **Orchestration** | LangGraph (`agents/graph.py`) | State machine: configure → monitor → end |
| **AI / LLM** | Gemini Flash API + Cursor CLI fallback | Hypothesis prose & narrative generation |
| **Statistics** | SciPy + NumPy | Z-tests, Chi-square SRM, Beta-Binomial Bayes |
| **Data** | SQLite (local) / PostgreSQL (prod via Supabase) | Dual-engine transparent DB layer |
| **Catalogs** | CSV seed files (4 catalogs) | Feature flags, segments, metrics, history |
| **Deployment** | Docker + Railway | Containerized deployment |
| **Evals** | Custom benchmark suite | 20 gold + 30 telemetry scenarios |
| **Models** | Pydantic v2 | Typed contracts shared across all modules |

---

## File Tree Summary

```
ExpPilot/
├── api/
│   ├── main.py              # FastAPI app, 15 routes
│   └── service.py           # Central orchestrator (569 lines)
├── agents/
│   ├── graph.py             # LangGraph state machine
│   ├── llm.py               # Gemini/Cursor LLM adapter
│   ├── recommender.py       # Deterministic recommendation engine
│   ├── narrator.py          # Business narrative with numeric guard
│   └── validator.py         # Validation agent wrapper
├── rules_engine/
│   ├── validator.py          # 6 pre-launch validation checks
│   └── decision.py           # Scale/Continue/Stop/Rollback/Pause rules
├── stats/
│   ├── core.py               # Frequentist + Bayesian statistical engine
│   └── diagnostics.py        # Segment-level driver analysis
├── shared/
│   └── models.py             # 12 Pydantic v2 models + constants
├── data/
│   ├── db.py                 # Dual SQLite/PostgreSQL engine
│   ├── seed.py               # CSV → DB catalog loader
│   ├── derive.py             # Transaction log → audience segments
│   ├── synth.py              # Synthetic telemetry generator
│   └── seeds/                # 4 CSV catalog files
├── ontology/
│   └── tree.py               # Hypothesis tree (branch/invalidate)
├── harness/
│   └── gitops.py             # Harness feature flag YAML manifests
├── evals/
│   ├── evaluator.py          # Benchmark evaluator (6 metric families)
│   ├── run_evals.py          # CLI runner (text + JSON output)
│   └── benchmarks/           # 20 gold + 30 telemetry scenarios
├── ui/
│   └── app.py                # Streamlit dashboard (66KB)
├── distributed/              # Microservices scaffolding (future)
│   ├── decision_service/
│   ├── eventbus/
│   ├── gateway/
│   ├── tracing/
│   └── vector/
└── tests/                    # 50+ unit & integration tests
```
