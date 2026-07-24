-- Schema: commerce — the PII boundary (§8), scaffolded for PS5 (Omnichannel
-- Consumer AI Engine). PS5 is design-only in this repo today (see
-- docs/distributed-architecture.md); this migration exists so the boundary is
-- real from the first day PS5 code lands, not retrofitted after PII has
-- already leaked into a shared table.
--
-- The rule this schema exists to enforce: PII lives here ONLY. Every other
-- schema (exp, rules, memory) receives pseudonymized surrogate ids, never a
-- real user identifier — re-identification requires a grant no agent role has
-- (see 005_roles_and_grants.sql).

CREATE SCHEMA IF NOT EXISTS commerce;

CREATE TABLE IF NOT EXISTS commerce.users (
    surrogate_id    TEXT PRIMARY KEY,      -- the only id ever exposed outside this schema
    external_id     TEXT NOT NULL UNIQUE,  -- real account identifier; PII by association
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS commerce.catalog (
    sku             TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    title           TEXT NOT NULL,
    price_cents     INTEGER NOT NULL,
    attributes      JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS commerce.sessions (
    id              TEXT PRIMARY KEY,
    surrogate_id    TEXT NOT NULL REFERENCES commerce.users(surrogate_id),
    channel         TEXT NOT NULL CHECK (channel IN ('web', 'mobile', 'assistant')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_sessions_surrogate_id ON commerce.sessions (surrogate_id);

CREATE TABLE IF NOT EXISTS commerce.carts (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES commerce.sessions(id),
    items           JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
