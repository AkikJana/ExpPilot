-- Per-service Postgres roles and schema-scoped grants (§2, §8).
--
-- Segregation is enforced logically, at the grant level, not by splitting into
-- separate physical databases: "the personalization service physically cannot
-- read exp.assignments; the experimentation service cannot read
-- commerce.sessions (PII)." This file is that enforcement, made real.
--
-- Run this AFTER 001-004. Passwords below are placeholders — a real deployment
-- injects them via a secrets manager at provisioning time, never commits them.

-- ---------------------------------------------------------------------------
-- exp_service — owns experiment lifecycle: api/main.py, agents/graph.py.
-- ---------------------------------------------------------------------------
CREATE ROLE exp_service WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA exp TO exp_service;
GRANT SELECT, INSERT, UPDATE ON exp.flags, exp.history, exp.experiments, exp.day_stats
    TO exp_service;
-- Audit tables: INSERT-only. A service that could UPDATE or DELETE its own
-- audit trail could also falsify it — the grant removes that possibility
-- structurally rather than relying on application-code discipline.
GRANT SELECT, INSERT ON exp.decisions, exp.agent_runs, exp.exposures TO exp_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA exp TO exp_service;

-- ---------------------------------------------------------------------------
-- decision_service — the evaluator (distributed/decision_service). Reads
-- packs, writes evaluations, INSERT-only on both — a rule pack it can UPDATE
-- in place is a rule pack that isn't actually versioned.
-- ---------------------------------------------------------------------------
CREATE ROLE decision_service WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA rules TO decision_service;
GRANT SELECT, INSERT ON rules.rule_packs, rules.evaluations TO decision_service;
GRANT SELECT ON rules.proposed_diffs TO decision_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rules TO decision_service;

-- ---------------------------------------------------------------------------
-- policy_learner — Loop 2 (§10): reads evaluations + realized outcomes, writes
-- proposed diffs. Deliberately CANNOT write to rules.rule_packs directly —
-- promotion requires the human-approval path in api/main.py's own role.
-- ---------------------------------------------------------------------------
CREATE ROLE policy_learner WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA rules TO policy_learner;
GRANT SELECT ON rules.evaluations, rules.rule_packs TO policy_learner;
GRANT SELECT, INSERT ON rules.proposed_diffs TO policy_learner;
GRANT UPDATE (realized_correct) ON rules.evaluations TO policy_learner;  -- the one mutable column
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rules TO policy_learner;

-- ---------------------------------------------------------------------------
-- memory_service — agents/memory.py's production backend.
-- ---------------------------------------------------------------------------
CREATE ROLE memory_service WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA memory TO memory_service;
GRANT SELECT, INSERT, UPDATE ON memory.records TO memory_service;

-- ---------------------------------------------------------------------------
-- commerce_service — PS5's surface. The only role with any grant on `commerce`
-- at all; every other role below has explicitly zero access to this schema —
-- there is no GRANT statement for them here because the absence of a grant
-- *is* the enforcement.
-- ---------------------------------------------------------------------------
CREATE ROLE commerce_service WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA commerce TO commerce_service;
GRANT SELECT, INSERT, UPDATE ON commerce.users, commerce.catalog, commerce.sessions, commerce.carts
    TO commerce_service;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA commerce TO commerce_service;

-- ---------------------------------------------------------------------------
-- readonly_analyst — dashboards, ad-hoc SQL, the eval harness's read side.
-- SELECT everywhere except commerce (PII) and everywhere except the mutable
-- realized_correct column path.
-- ---------------------------------------------------------------------------
CREATE ROLE readonly_analyst WITH LOGIN PASSWORD 'CHANGE_ME_VIA_SECRETS_MANAGER';
GRANT USAGE ON SCHEMA exp, rules, memory TO readonly_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA exp TO readonly_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA rules TO readonly_analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA memory TO readonly_analyst;
-- No REVOKE statements needed for commerce: readonly_analyst was never granted
-- USAGE on that schema, so every table in it is already unreachable.
