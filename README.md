# ExpPilot — AI Experiment Copilot & Decision Intelligence

DTDL Talent Hack · Problem Statement 3. An AI copilot that guides a PM through the
full experimentation lifecycle — hypothesis → config → validation → monitoring →
**Scale / Continue / Stop / Rollback** — with a hard separation between what the
**LLM does** (explain) and what **deterministic code does** (compute & decide).

> Core promise: the LLM never invents a p-value or a decision. Every number comes
> from `stats/core.py`; every action comes from `stats.core.decide`; the LLM only
> narrates the already-computed result, and any narration that introduces an
> ungrounded number is rejected.

## Architecture

```
UI (Streamlit)  ──┐
                  ├──►  api/service.py  ──►  agents/graph.py (LangGraph)
API (FastAPI) ────┘         │                    │  retrieve → hypothesize
                            │                    │  config → validate (gate)
                            │                    │  stats → monitor → decide
                            ▼                    ▼
                     data/ (SQLite registry,   stats/core.py  (z-test, Welch t,
                     synthetic telemetry,      Bayesian posterior, SRM chi-square,
                     history for RAG)          power, guardrails, decide)  ← LLM-free
                            ▲
                     agents/llm.py  (Anthropic, optional; deterministic fallback)
```

| Layer | Module | Responsibility |
|-------|--------|----------------|
| Deterministic core | `stats/core.py` | z/t tests, Bayesian posterior, SRM, power, **the decision** |
| Data | `data/{db,synth,seed}.py` | registry, ground-truth synthetic telemetry, seeds |
| Tools | `agents/tools.py` | sample size, overlap detection, cumulative sim, analyze |
| RAG | `agents/rag.py` | lexical retrieval over 30 historical experiments |
| LLM | `agents/llm.py` | narration only + numeric anti-hallucination guard |
| Orchestration | `agents/graph.py`, `nodes.py` | LangGraph pipelines (create / analyze) |
| Service | `api/service.py` | persistence + lifecycle glue (shared by API & UI) |
| API | `api/main.py` | FastAPI surface |
| Evals | `evals/{gold,harness}.py` | recommendation accuracy vs expert gold set |
| UI | `ui/app.py` | Streamlit workspace + decision card + eval dashboard |

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) seed the registry + demo experiments (idempotent)
python -m data.seed

# 2) run the eval harness (recommendation accuracy vs expert labels)
python -m evals.harness

# 3) launch the UI (single-process demo; imports the service layer directly)
streamlit run ui/app.py

# ...or run the API
uvicorn api.main:app --reload
```

### Optional LLM
Set `ANTHROPIC_API_KEY` (and optionally `ANTHROPIC_MODEL`) to enable LLM narration.
**Without a key the product runs fully offline** using deterministic templates —
ideal for a stable on-site demo. LangGraph and MLflow are also optional: the graph
falls back to a sequential runner and MLflow logging is best-effort.

## Demo flow (matches the 5 PS3 objectives)

1. **Create** — type a business goal → 3 grounded hypotheses (cite past experiments).
2. **Configure** — pick one → flag/audience/metrics + computed sample size & runtime.
3. **Validate** — overlap detection blocks/warns on colliding live experiments.
4. **Monitor** — advance days; SRM (`demo_bundle_srm`) blocks analysis with a banner.
5. **Decide** — Decision Card shows SCALE/CONTINUE/STOP/ROLLBACK with confidence,
   evidence citations, and an audit trail; guardrail breach (`demo_paywall_guardrail`)
   forces ROLLBACK.
6. **Eval Dashboard** — recommendation accuracy vs expert (~93%), significance
   detection (100%), confusion matrix, and impact metrics.

## Decision policy (deterministic, precedence-ordered)

1. SRM detected → **PAUSE** (analysis blocked; not trustworthy)
2. Guardrail breach → **ROLLBACK**
3. `P(beats control) ≥ 0.95` and low ship-loss → **SCALE**
4. `P(beats control) ≤ 0.05` → **STOP**
5. otherwise → **CONTINUE**

## Tests

```bash
pytest -q          # stats core unit tests
python scripts/smoke.py   # end-to-end lifecycle + eval smoke test
```
