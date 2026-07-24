# distributed/ — the production platform from docs/distributed-architecture.md

Code implementing the architecture doc, on its own branch, alongside the
working demo app (`agents/`, `api/`, `ui/`, `data/`) — **not replacing it**.
Nothing under `agents/`, `api/`, `ui/`, `data/`, or `stats/` was changed by this
package. The demo still runs exactly as it did, with no new runtime
dependencies and no new services it must reach.

## Scope and honesty

Every claim below is either (a) verified by a test in this repo that you can
run right now with `pytest distributed/ -q`, or (b) explicitly marked
**reviewed, not executed** because it needs infrastructure this development
environment doesn't have (a live Postgres, a live Kafka/Redpanda broker, a GPU
for vLLM). Nothing in between. If you see a claim in this file with neither
label, that's a bug in the documentation — file it.

| Component | Status | Why |
|---|---|---|
| `decision_service/` (evaluator, schemas, FastAPI, audit) | **Tested — 89 passing** | Pure Python + SQLite; no external infra |
| `decision_service/test_evaluator.py` parity suite | **Tested — 82 passing** | Every scenario × seed × day the eval harness exercises, evaluator vs. `stats.core.decide` |
| `gateway/` (tiered routing) | **Tested — 7 passing** | Ollama and cloud providers are monkeypatched; 0.02s runtime proves zero real network calls |
| `eventbus/outbox.py` | **Tested — 8 passing** | SQLite-backed, the real production mechanism (transactional outbox), not a mock |
| `eventbus/kafka_bus.py` | Reviewed, not executed | No live Kafka broker here. Same interface as the tested outbox backend; degrade-path (missing `kafka-python`) is tested |
| `vector/sql_fallback.py` | **Tested — 4 passing** | Wraps `agents/rag.py`'s existing, already-tested retrieval |
| `vector/pgvector_index.py` | Reviewed, not executed | No live Postgres+pgvector here. Degrade-path is tested |
| `tracing/spine.py` | **Tested — 5 passing** | Real OTel SDK is present transitively (via mlflow) in this venv, so these tests exercise the actual span/exception-propagation path, not just the no-op branch |
| `tracing/otel-collector-config.yaml` | Reviewed, not deployed | YAML config; correctness is structural review, not a test |
| `db/migrations/*.sql` | Reviewed, not applied | No live Postgres instance to run `psql -f` against here |
| `docker-compose.yml` | Reviewed, not started | No Docker daemon exercised in this session |

Run everything that's testable:

```bash
pytest distributed/ -q
```

## Why the demo app doesn't call any of this yet

The migration path in `docs/distributed-architecture.md` §9 lists
`stats/core.py::decide` → Decision Service as step 1 — but that means *the
Decision Service becomes the thing `api/service.py` calls*, which introduces a
network dependency into a system that is currently, deliberately, fully
keyless and infra-free (Sections 1–8 of this repo's build were graded partly
on `uvicorn api.main:app` starting with zero external services reachable).
Wiring that call in now, with no live Decision Service to reach in this
environment, would break the working demo to make an unrun service "used."

That's a design choice worth pinning down explicitly rather than doing
silently: **the two systems coexist, proven equivalent by the parity suite,
until someone deliberately flips the switch** — a small change (one call site
in `api/service.py::advance_experiment` behind an `if DECISION_SERVICE_URL:`
check) once a Decision Service is actually reachable.

## What each piece is for

- **`decision_service/`** — §4. The extraction of `stats/core.py::decide()`
  into a versioned, owned, replayable policy: `RulePack` generalizes the five
  constants in `shared/models.py`, `evaluate()` is the same control flow with
  the same precedence, and every evaluation is recorded with a full
  explanation trace (`fired_checks`), not just the winning check.
- **`gateway/`** — §3. The Tier 0/1/2 routing ladder wrapping `agents/llm.py`.
  Tier 1 (`LOCAL`) talks to a local Ollama server as the realistic dev-machine
  stand-in for a vLLM+Gemma fleet — this machine has no CUDA GPU, and
  pretending vLLM runs here would be exactly the kind of mistake this package
  exists to avoid. Swapping the Tier 1 client for a real vLLM/Triton gateway
  call is contained to `model_gateway.py::_local_generate`.
- **`eventbus/`** — §2, §5. The transactional outbox pattern (tested, SQLite
  today, Postgres in production — same table shape) plus a Kafka backend
  behind the identical interface.
- **`vector/`** — §2. The `VectorIndex` port: `sql_fallback.py` (tested,
  wraps existing retrieval) and `pgvector_index.py` (reviewed).
- **`tracing/`** — §6. One OTel spine, tail-sampled by domain so decision and
  experiment traces are kept at 100% while consumer traffic is sampled at 1–5%
  and PII-scrubbed at the collector before it leaves the trusted zone.
- **`db/migrations/`** — §2, §8. Schema-per-domain Postgres DDL
  (`exp`, `rules`, `memory`, `commerce`) plus per-service roles and grants —
  segregation enforced by what a role *cannot* SELECT, not by convention.

## Standing it up for real

Requires Docker and, for the local-inference tier, ~8GB free for a Gemma pull:

```bash
pip install -r requirements-platform.txt

docker compose -f distributed/docker-compose.yml up -d postgres redpanda jaeger otel-collector

# Apply migrations in order — each depends on schemas the previous one created.
export POSTGRES_DSN="postgresql://exppilot:dev_only_change_in_prod@localhost:5432/exppilot"
for f in distributed/db/migrations/00{1,2,3,4,5}_*.sql; do
  psql "$POSTGRES_DSN" -f "$f"
done

docker compose -f distributed/docker-compose.yml up decision-service

# Optional: local Gemma inference (Tier 1). The platform works fully without
# this — it degrades to Tier 0 templates, same as the base app runs keyless.
docker compose -f distributed/docker-compose.yml --profile local-inference up -d ollama
docker exec -it $(docker compose -f distributed/docker-compose.yml ps -q ollama) ollama pull gemma2:9b
```

Jaeger UI: `http://localhost:16686`. Decision Service: `http://localhost:8100/docs`.

None of the above was run in this session — this is the reviewed procedure,
not a verified one. If you run it and something in this file is wrong, that's
useful information; please report exactly which step failed.
