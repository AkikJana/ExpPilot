-- Schema: exp — experiments, day-level telemetry, decisions, the flag registry,
-- and the agent audit trail. One schema per bounded context (§2), not one
-- database per service: cross-domain joins (decision -> experiment -> memory)
-- are the product here, not an accident.
--
-- Migrates the shape of exppilot.db (data/db.py) 1:1 where the shared pydantic
-- models in shared/models.py define the contract — JSONB replaces the SQLite
-- JSON-as-TEXT columns, everything else is a direct translation.

CREATE SCHEMA IF NOT EXISTS exp;

CREATE TABLE IF NOT EXISTS exp.flags (
    key                     TEXT PRIMARY KEY,
    segment                 TEXT NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN ('free', 'in_use')),
    running_experiment_id   TEXT
);

CREATE TABLE IF NOT EXISTS exp.history (
    id                  TEXT PRIMARY KEY,
    category            TEXT NOT NULL,
    hypothesis_text     TEXT NOT NULL,
    lift_observed       DOUBLE PRECISION NOT NULL,
    outcome             TEXT NOT NULL CHECK (outcome IN ('shipped', 'abandoned', 'rolled_back'))
);

CREATE TABLE IF NOT EXISTS exp.experiments (
    id              TEXT PRIMARY KEY,
    config          JSONB NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('draft', 'validated', 'running', 'paused', 'concluded')),
    ground_truth    JSONB,          -- eval-harness-only; no application code path may SELECT this column
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS exp.day_stats (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id   TEXT NOT NULL REFERENCES exp.experiments(id),
    day             INTEGER NOT NULL,
    data            JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, day)
);
CREATE INDEX IF NOT EXISTS idx_day_stats_experiment_id ON exp.day_stats (experiment_id);

-- Append-only: a decision, once written, is never edited — only superseded by a
-- new row for the same (experiment_id, day) pair on resume. INSERT-only grants
-- for every service role live in 005_roles_and_grants.sql; this table's
-- immutability is a permission, not a convention.
CREATE TABLE IF NOT EXISTS exp.decisions (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id   TEXT NOT NULL REFERENCES exp.experiments(id),
    day             INTEGER NOT NULL,
    data            JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, day)
);
CREATE INDEX IF NOT EXISTS idx_decisions_experiment_id ON exp.decisions (experiment_id);

-- The audit trail of record. Append-only (INSERT-only grant), 7-year retention
-- per the trace-spine policy (docs/distributed-architecture.md §6).
CREATE TABLE IF NOT EXISTS exp.agent_runs (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    node            TEXT NOT NULL,
    thread_id       TEXT,
    trace_id        TEXT,           -- links to the common OTel spine (§6)
    input           JSONB NOT NULL,
    output          JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_thread_id ON exp.agent_runs (thread_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_trace_id ON exp.agent_runs (trace_id);

-- Assignment/exposure events, per §5: "Assignment at the edge ... Exposure events
-- flow through Kafka; the warehouse-side truth is reconstructed from logs." This
-- table IS that reconstructed warehouse-side truth — the durable sink Kafka
-- consumers write into, not the hot assignment path itself.
CREATE TABLE IF NOT EXISTS exp.exposures (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id   TEXT NOT NULL,
    unit_id         TEXT NOT NULL,
    variant         TEXT NOT NULL,
    event_id        TEXT NOT NULL UNIQUE,   -- from distributed/eventbus — idempotent replay
    exposed_at      TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exposures_experiment_id ON exp.exposures (experiment_id);
