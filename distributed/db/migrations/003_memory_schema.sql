-- Schema: memory — long-term agent memory, pgvector-embedded (§2).
--
-- shared/models.py::MemoryRecord is the pydantic source of truth for the
-- non-vector columns. `embedding` is additive: retrieval falls back to plain
-- category/kind/recency filtering (agents/rag.py's existing behavior, wrapped
-- by distributed/vector/sql_fallback.py) whenever the embedding column is NULL
-- or pgvector isn't installed — the same graceful-degradation pattern used
-- throughout this codebase for optional model providers.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS memory;

-- Dimension matches the embedding model configured for the Tier-1 embedding
-- endpoint (docs/distributed-architecture.md §3: "embeddings served from the
-- same fleet"). 768 is a placeholder for a mid-size sentence embedding model;
-- change to match whatever model is actually deployed before running this
-- migration for real.
CREATE TABLE IF NOT EXISTS memory.records (
    id                      TEXT PRIMARY KEY,
    kind                    TEXT NOT NULL CHECK (kind IN ('episodic', 'lesson', 'exemplar')),
    category                TEXT NOT NULL,
    content                 TEXT NOT NULL,
    source_experiment_id    TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding               vector(768)
);

CREATE INDEX IF NOT EXISTS idx_memory_kind_category ON memory.records (kind, category);

-- Approximate nearest-neighbor index for cosine search. HNSW over IVFFlat: no
-- training step required, and recall stays high without a periodic REINDEX as
-- the table grows — the right tradeoff at the row counts this table is scoped
-- for (tens of thousands, per §2's "agent memory ... likely never" graduates
-- to a dedicated vector engine).
CREATE INDEX IF NOT EXISTS idx_memory_embedding_hnsw
    ON memory.records USING hnsw (embedding vector_cosine_ops)
    WHERE embedding IS NOT NULL;
