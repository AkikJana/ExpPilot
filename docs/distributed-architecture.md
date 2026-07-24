# Unified Decision Intelligence Platform — Production Distributed Architecture

Combining PS3 (Experiment Copilot), PS4 (Configurable Decision Automation), and PS5
(Omnichannel Consumer AI) into one production system, with local Gemma inference served
by a distributed vLLM fleet.

Design patterns are borrowed deliberately from Netflix's published engineering:
[In-House LLM Serving](https://netflixtechblog.com/in-house-llm-serving-at-netflix-a5a8e799ea2c)
(vLLM engine selection, control-plane/data-plane split, deployment strategies, cold-start
elimination, constrained decoding) and
[Edgar / Distributed Tracing](https://netflixtechblog.com/building-netflixs-distributed-tracing-infrastructure-bb856c319304)
(instrument everything, keep 100% of *interesting* traces, tier the storage).

The invariant survives every layer of scale-out:

> **Generation is where models are free; acceptance is where they are forbidden.**

No model — local Gemma, cloud frontier, or fine-tuned adapter — ever sits on the accept
path. Scale changes *where* things run, never *who is allowed to decide*.

---

## 1. Topology

```
                       ┌───────────────────────────────────────────────┐
                       │            EDGE / EXPERIENCE (PS5)            │
                       │   Web · Mobile · Conversational assistant     │
                       │   Assignment SDK (deterministic hash, local)  │
                       └──────────┬────────────────────────────────────┘
                                  │ HTTPS / gRPC        traceparent →
                       ┌──────────┴────────────────────────────────────┐
                       │                API GATEWAY                    │
                       │  authn/z · rate limits · trace root · routing │
                       └───┬──────────────┬──────────────┬─────────────┘
                           │              │              │
            ┌──────────────┴──┐  ┌────────┴───────┐  ┌───┴──────────────┐
            │ PERSONALIZATION │  │  CONVERSATION  │  │  EXPERIMENTATION │
            │  SVC (PS5)      │  │  SVC (PS5)     │  │  SVC (PS3)       │
            │ recs · NBA ·    │  │ chat sessions  │  │ design · monitor │
            │ cart optimizer  │  │ tool calls     │  │ verdicts · evals │
            └───────┬─────────┘  └───────┬────────┘  └───┬──────────────┘
                    │ candidates         │ drafts        │ configs
                    ▼                    ▼               ▼
            ┌──────────────────────────────────────────────────────────┐
            │              DECISION SERVICE (PS4) — sync, stateless    │
            │  rule-pack evaluator (deterministic, versioned, no LLM)  │
            │  every eval → immutable decision record + explanation    │
            │  packs: experiment-lifecycle · commerce-guardrails ·     │
            │         compliance · eligibility                         │
            └──────────────────────────┬───────────────────────────────┘
                                       │ approved actions only
                                       ▼
     ┌──────────────┐   ┌─────────────────────────────┐   ┌───────────────────┐
     │ AGENT WORKERS│   │      INFERENCE PLATFORM     │   │  STREAM WORKERS   │
     │ (LangGraph,  │──►│  Model Gateway (OpenAI API) │   │ SRM monitor ·     │
     │  async queue)│   │  ┌───────────────────────┐  │   │ metric aggregation│
     │ hypothesis · │   │  │ vLLM fleet:           │  │   │ exposure logging ·│
     │ designer ·   │   │  │  Gemma base + LoRA    │  │   │ guardrail alerts  │
     │ analyst ·    │   │  │  adapters, embeddings │  │   │ (Kafka consumers) │
     │ reflection · │   │  └───────────────────────┘  │   └─────────┬─────────┘
     │ PS5 rec/chat │   │  escalation: frontier API   │             │
     └──────┬───────┘   │  fallback: template tier    │             │
            │           └─────────────────────────────┘             │
            ▼                                                       ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │                        DATA PLANE                                      │
   │                                                                        │
   │  POSTGRES (system of record, one cluster, schema-per-domain)           │
   │    exp.*        experiments, day_stats, decisions, assignments         │
   │    rules.*      rule_packs, rule_versions, evaluations (append-only)   │
   │    memory.*     records + pgvector embeddings (one JOIN away)          │
   │    commerce.*   catalog, sessions, carts (PII boundary lives here)     │
   │                                                                        │
   │  KAFKA event backbone   exposures · conversions · decisions · outbox   │
   │  REDIS                  session cache · feature cache · rate state     │
   │  OBJECT STORE           model weights · artifacts · MLflow             │
   │  VECTOR (pgvector → dedicated engine when scale demands)               │
   └────────────────────────────────────────────────────────────────────────┘
   ┌────────────────────────────────────────────────────────────────────────┐
   │                     OBSERVABILITY SPINE (§6)                           │
   │  OTel SDK everywhere → OTel Collector → routed, tiered backends        │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Postgres as the shared system of record

One physical cluster (HA pair + read replicas), **schema-per-bounded-context**, not
database-per-service. At this system's scale the operational win of one backup/failover/
migration story outweighs textbook microservice purity — and cross-domain joins
(decision → experiment → memory) are the *product*, not an accident.

Segregation is enforced logically, which is what actually matters:

- **One role per service**, grants limited to its schema. The personalization service
  physically cannot read `exp.assignments`; the experimentation service cannot read
  `commerce.sessions` (PII).
- **Append-only audit tables** (`rules.evaluations`, `exp.decisions`) — `INSERT`-only
  grants, no `UPDATE`/`DELETE` for any service role. The audit trail is a table
  *constraint*, not a convention.
- **Outbox pattern + CDC** (Debezium) → Kafka. Services never dual-write; every state
  change that other domains care about is emitted transactionally.

**The memory store is the flagship case for "common Postgres + vector in one place."**
Agent memory retrieval is a hybrid query — semantic similarity AND category AND recency
AND kind — and colocating pgvector embeddings with the memory rows makes that one indexed
SQL statement instead of a fan-out to a separate vector service with a consistency
problem:

```sql
SELECT id, content, kind, created_at
FROM memory.records
WHERE category = $1 AND kind = ANY($2)
ORDER BY embedding <=> $3        -- pgvector cosine, HNSW index
       + recency_decay(created_at)
LIMIT 5;
```

Everything embedded goes through the same offline pipeline: Kafka event → embedding
worker (vLLM embedding endpoint) → `UPDATE ... SET embedding` — so the write path of
record never blocks on an embedding model.

**When to graduate to a dedicated vector engine** (Qdrant/Milvus): only when a
collection crosses ~50–100M vectors or needs >1k vector QPS with heavy filtering —
in practice the PS5 product-catalog index hits that first; agent memory and precedent
search likely never do. The interface is a thin `VectorIndex` port so the swap is a
config change, not a rewrite.

What each domain uses vectors for:

| Domain | Collection | Replaces |
|---|---|---|
| PS3 | historical experiments + lessons | today's keyword/category SQL retrieval |
| PS4 | rule-pack descriptions + past evaluations | "which rule fired for situations like this" |
| PS5 | product catalog, conversation turns | cold-start recs, contextual assistance |

---

## 3. Distributed inference platform (the Netflix lessons, applied)

Netflix's core finding transfers directly: **pick the engine for operational fit, then
invest in the control plane around it.** They chose vLLM over faster compiled stacks for
debuggability, custom-architecture loading, and extensibility — and put the engineering
into deployment, versioning, and observability instead.

### Data plane

- **vLLM fleet** serving a **Gemma base model with LoRA adapter multiplexing** — one set
  of base weights resident per GPU, per-domain adapters (analyst-narrative, chat-commerce,
  rule-explanation, hypothesis-design) hot-loaded per request. Tens of "specialists" for
  the GPU cost of one generalist.
- **Embeddings served from the same fleet** (prefill-only workloads) — one platform, not
  a parallel embedding stack.
- **Guided decoding as a platform guarantee.** Every agent output in this system is
  pydantic-validated JSON. vLLM's structured-output support (grammar/JSON-schema
  constrained decoding) moves "retry on parse failure" from application code into the
  engine — a malformed JSON response becomes *impossible* rather than *handled*. Netflix
  runs constraint state machines per request via the logits-processor interface and
  rewrote the hot path batch-level when per-request CPU processing became the bottleneck;
  we inherit that lesson by using batch-level structured outputs from day one.
- **Prefix caching on.** Agent system prompts are long, shared, and stable per adapter —
  KV prefix reuse is nearly free latency.

### Control plane

Small (and boring, on purpose) deployment controller, separate from the serving path:

- **Model registry**: every deployable = (base weights, adapter, engine version, prompt
  pack version) — pinned together. Netflix's hard-won gotcha: engine/frontend version
  mismatches cause silent failures (their `response_format` was schema-accepted and
  silently dropped); pinning is not optional, and model authors must not override engine
  versions at packaging time.
- **Red-black deploys** for stable interfaces (adapter updates): new fleet up, health +
  golden-prompt checks, traffic shift, atomic rollback. **Versioned deploys** (both
  versions serving simultaneously) only when the interface breaks — expensive in GPU,
  so interfaces are designed not to break.
- **Cold-start elimination**: weights pre-materialized on fast local/NVMe-backed storage
  at *announce* time, never pulled from object storage or a hub during pod start.
  Netflix uses FSx for exactly this; autoscaling is only real if a new replica is serving
  in seconds.
- **Autoscaling on engine signals** — queue depth and KV-cache utilization, not CPU.
  A vLLM worker at 95% KV utilization is saturated at 30% CPU.
- **Unified metrics endpoint**: vLLM's own metrics (token throughput, KV utilization,
  prefix-cache hit rate) merged with server metrics into one scrape target — Netflix had
  to build a proxy because the off-the-shelf bridge surfaced 9 of 40+ engine metrics;
  budget for the same glue.

### The routing ladder

The model gateway (OpenAI-compatible, so every client is provider-agnostic) routes by
task class:

```
TIER 0  deterministic template      always available, no model      (exists today)
TIER 1  Gemma + LoRA on vLLM       high-volume, low-latency, PII-safe (on-prem)
TIER 2  frontier cloud API          low-volume, high-stakes generation
```

Escalation is policy, not vibes: task class + token budget + PII flag → tier. PII-tagged
context can *only* route to Tier ≤ 1 (weights we run, boxes we own). Tier 2 failure falls
back to Tier 1; Tier 1 failure falls back to Tier 0 — which is the keyless degradation
ladder ExpPilot already ships, promoted to platform policy. **The system stays up, making
deterministic decisions, with zero models available.**

---

## 4. Decision Service (PS4) — the acceptance layer as a product

The extraction of `stats/core.py::decide()` into infrastructure:

- **Rule packs**: versioned JSON documents (thresholds, predicates, precedence order),
  authored via PR + CI, stored in `rules.rule_packs` with full history. Today's
  `SHIP_PROB_THRESHOLD = 0.95` becomes a governed parameter with an owner and a diff.
- **Evaluator**: stateless, deterministic, pure — same inputs, same verdict, forever.
  Sync gRPC/HTTP, p99 in single-digit ms, horizontally scaled, **no model calls,
  no network calls** during evaluation. It is the one service that must never be down
  and never be interesting.
- **Every evaluation appends an immutable record**: inputs digest, pack version, fired
  rules in order, verdict, `trace_id`. This is `agent_runs` + `Decision.reasoning_stats`
  generalized into the platform's audit spine.
- **Explanation ≠ evaluation**: LLM-written plain-language explanations of a decision
  are generated *after* the verdict, from the trace, via Tier 1 — and regex/traceability
  checked exactly like ExpPilot's analyst narratives. Prose never feeds back into a verdict.
- **Rule changes are experiments.** A challenger pack shadow-evaluates against live
  traffic (verdicts logged, not enforced); divergence report + eval-harness regression
  gate promotion. The platform cannot "configure" its way into a looser significance bar
  silently — the harness that caught our day-one peeking bug is the same gate here.

## 5. Experimentation everywhere (PS3 at platform scale)

- **Assignment at the edge**: deterministic `hash(unit_id, experiment_salt)` in an SDK —
  no assignment service round-trip, no assignment DB on the hot path. Exposure events
  flow through Kafka; the warehouse-side truth is reconstructed from logs.
- **The day-tick monitor becomes a stream job**: SRM checks, guardrail checks, and
  interim Bayesian reads run as Kafka consumers over exposure/conversion streams,
  emitting alerts and verdict-requests to the Decision Service on schedule or on trigger.
  `compute_day_stats` is unchanged math — only its feeding changes.
- **Every PS5 policy ships as an experiment.** Recommender variant, NBA policy, chat
  prompt pack — all behind flags, all measured, all subject to the same
  scale/rollback human gate. "% of AI recommendations adopted" stops being a demo metric
  and becomes the production feedback signal into agent memory.

---

## 6. Traces: common spine, segregated destinations

Edgar's two governing ideas, applied:

**(1) One trace, end to end.** OTel SDK in every service, W3C `traceparent` propagated
from the edge click through gateway → agent graph → model gateway → vLLM (span per
inference: adapter, tokens in/out, KV hit rate, tier) → decision evaluator (span per
pack: version, fired rules) → Postgres. One `trace_id` answers the platform's defining
question: *"Why did this user see this?"* — because rule pack v12 approved a Tier-1
Gemma candidate under experiment `exp_7f3a` variant B, posterior 0.97, guardrails clean.

**(2) Sample by interestingness, not uniformly.** Netflix keeps 100% of traces that
matter and lets the firehose go. Our policy, enforced by **tail-based sampling at the
OTel Collector**:

| Trace class | Sampling | Retention | Backend |
|---|---|---|---|
| Decision evaluations (PS4) | **100%** | 7 years, immutable | Postgres append-only (audit) |
| Experiment assignment/exposure | **100%** | 2 years | warehouse via Kafka |
| Agent graph runs | 100% | 90 days | MLflow (traces + lineage) |
| Inference spans | 10%, **but 100% when parent trace contains a decision** | 30 days | Tempo/Jaeger + ClickHouse rollups |
| Consumer chat/browse | 1–5%, PII-scrubbed **at the collector** | 14 days | Tempo/Jaeger |

"Common and segregated" is exactly this: **one SDK, one propagation standard, one
collector — many destinations by policy.** The collector routes on span attributes
(`domain=decision|experiment|inference|consumer`), scrubs PII attributes before anything
leaves the trusted zone, and applies per-class retention. Ops debugging, ML lineage, and
regulatory audit each get the view they need without three instrumentation stacks —
and cost tiering falls out of TTLs instead of arguments.

MLflow keeps its current role (agent traces, eval runs, prompt/adapter lineage) but is
*fed by* the common spine rather than being a parallel system: the MLflow trace carries
the platform `trace_id`, so an eval regression links to the exact inference spans and
rule evaluations that produced it.

---

## 7. Failure domains and the degradation ladder

Ranked by what is allowed to fail:

| Component | If it's down | Blast radius |
|---|---|---|
| Decision Service | **Nothing else matters — this must not go down.** Stateless + replicated + no external calls makes that credible. | Everything |
| Postgres primary | Decisions still evaluate against cached rule packs; writes buffer in outbox; failover in seconds | Minutes of write lag |
| vLLM fleet | Tier 0 templates serve; UX degrades to deterministic; **no decision quality change** | Narratives, chat, recs |
| Frontier API | Tier 1 absorbs; escalation queue drains later | Hypothesis quality |
| Kafka | Exposure logging buffers at the edge; monitoring blind but decisions frozen safe (no data → `continue`, never `scale`) | Measurement lag |
| Vector index | Retrieval falls back to keyword/category SQL (today's code path, kept alive) | Recall quality |

The pattern is uniform: **every intelligent component degrades to a deterministic one,
and the deterministic core degrades to "safe hold," never to "guess."** The keyless
constraint ExpPilot was built under turns out to be the production degradation design.

## 8. Security & tenancy boundaries

- PII lives in `commerce.*` only; agents receive pseudonymized context keyed by
  surrogate IDs. Re-identification requires a grant no agent role has.
- PII-tagged inference routes to Tier ≤ 1 only — consumer text never leaves owned
  hardware. This is the *architectural* argument for the local Gemma fleet, beyond cost.
- Rule packs and prompt packs deploy via the same path: PR → CI (eval harness) →
  registry → staged rollout. Nobody hot-edits a threshold or a prompt in production.
- Service-to-service: mTLS + per-service Postgres roles + per-schema grants (§2).

## 9. Migration path from the current repo

| Today (ExpPilot) | Becomes | Step |
|---|---|---|
| `stats/core.py::decide` + constants | Decision Service + `rules.*` packs | 1 |
| SQLite files | Postgres schemas (`exp.*`, `memory.*`) — models unchanged, pydantic contracts survive | 1 |
| `agents/memory.py` keyword fetch | hybrid pgvector retrieval (SQL fallback kept) | 2 |
| `_get_llm()` + keyless fallback | Model Gateway + routing ladder (Tier 0 already exists) | 2 |
| `agents/graph.py` in-process | queue-fed LangGraph workers, checkpoints in Postgres | 3 |
| `/advance` day ticks | Kafka stream monitor workers | 3 |
| `agent_runs` table | OTel spine + decision-audit store | parallel from 1 |
| `evals/run_evals.py` | CI gate for rule packs, adapters, and prompt packs | parallel from 1 |

Order matters: the decision extraction (1) is the highest-leverage, lowest-risk move —
it is pure-function code that already has its own test suite and its own eval gate.

---

## Sources

- [In-House LLM Serving at Netflix](https://netflixtechblog.com/in-house-llm-serving-at-netflix-a5a8e799ea2c) — Netflix AI Platform (Model Runtime & Inference)
- [Building Netflix's Distributed Tracing Infrastructure](https://netflixtechblog.com/building-netflixs-distributed-tracing-infrastructure-bb856c319304)
- [Edgar: Solving Mysteries Faster with Observability](https://netflixtechblog.com/edgar-solving-mysteries-faster-with-observability-e1a76302c71f)
- [Netflix LLMOps case study (ZenML database)](https://www.zenml.io/llmops-database/in-house-llm-serving-infrastructure-at-scale)
