-- Schema: rules — the Decision Service's governed policy store (§4).
--
-- distributed/decision_service/schemas.py::RulePack and ::EvaluationResult are
-- the pydantic source of truth for these shapes; this table is where a real
-- deployment persists what decision_service/audit.py's SqliteAuditSink and the
-- in-memory pack registry in decision_service/main.py stand in for locally.

CREATE SCHEMA IF NOT EXISTS rules;

-- Every version of every pack, forever. A pack is never UPDATEd — a change is a
-- new row with a new version, so "what was the policy on 2026-06-01" is always
-- answerable by a plain SELECT, no point-in-time recovery required.
CREATE TABLE IF NOT EXISTS rules.rule_packs (
    id              TEXT NOT NULL,
    version         TEXT NOT NULL,
    owner           TEXT NOT NULL,
    description     TEXT NOT NULL,
    definition      JSONB NOT NULL,     -- the full RulePack, serialized
    status          TEXT NOT NULL DEFAULT 'shadow'
                        CHECK (status IN ('shadow', 'active', 'retired')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);
-- At most one ACTIVE version per pack id at any time — the constraint that makes
-- "which pack version is live" a query, not tribal knowledge.
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_version_per_pack
    ON rules.rule_packs (id) WHERE status = 'active';

-- Append-only (INSERT-only grant — see 005_roles_and_grants.sql). This is the
-- table the calibration monitor (docs/distributed-architecture.md §10, Loop 2)
-- streams over: joining evaluations to realized outcomes is how a rule-pack
-- diff gets proposed, evidence attached, never how the pack mutates itself.
CREATE TABLE IF NOT EXISTS rules.evaluations (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id       TEXT NOT NULL,
    day                 INTEGER NOT NULL,
    pack_id             TEXT NOT NULL,
    pack_version        TEXT NOT NULL,
    action              TEXT NOT NULL CHECK (action IN ('scale', 'continue', 'stop', 'rollback', 'pause')),
    fired_checks        JSONB NOT NULL,
    inputs_digest       TEXT NOT NULL,
    trace_id            TEXT,
    evaluated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Filled in later by the calibration monitor once ground truth is known —
    -- the ONE mutable column on an otherwise append-only row, and even this is
    -- a single well-known UPDATE path, not an arbitrary one.
    realized_correct    BOOLEAN
);
CREATE INDEX IF NOT EXISTS idx_evaluations_experiment_id ON rules.evaluations (experiment_id);
CREATE INDEX IF NOT EXISTS idx_evaluations_pack ON rules.evaluations (pack_id, pack_version);

-- Loop 2's output: a policy-learner-drafted diff with evidence attached, on the
-- road to shadow -> human sign-off -> versioned deploy. Never auto-applied.
CREATE TABLE IF NOT EXISTS rules.proposed_diffs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    base_pack_id        TEXT NOT NULL,
    base_pack_version   TEXT NOT NULL,
    proposed_definition JSONB NOT NULL,
    evidence            JSONB NOT NULL,   -- the calibration-drift bundle that motivated this diff
    status              TEXT NOT NULL DEFAULT 'proposed'
                            CHECK (status IN ('proposed', 'shadow', 'approved', 'rejected')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by         TEXT,
    reviewed_at         TIMESTAMPTZ
);
