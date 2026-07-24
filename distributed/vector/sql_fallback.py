"""The SQL/keyword VectorIndex implementation — wraps agents/rag.py's existing,
already-tested lexical retrieval behind the VectorIndex port.

This is not a placeholder: docs/distributed-architecture.md §2 explicitly scopes
agent-memory retrieval (tens of thousands of rows at most) as a case where
pgvector — or even this keyword fallback — is sufficient, and reserves a
dedicated vector engine for the PS5 product-catalog index, which is a different
collection at a different scale. Swapping this for pgvector_index.py is a
config change; nothing that calls VectorIndex.search() needs to know which one
is active.
"""
from __future__ import annotations

from agents.rag import search_past_experiments


class SqlFallbackIndex:
    """Adapts agents.rag.search_past_experiments to the VectorIndex port."""

    def search(self, query: str, category: str | None, k: int):
        from distributed.vector.interface import RetrievedItem

        rows = search_past_experiments(query, category=category, k=k)
        return [
            RetrievedItem(
                id=row["id"],
                content=row["hypothesis_text"],
                category=row["category"],
                score=float(row["score"]),
                metadata={"lift_observed": row["lift_observed"], "outcome": row["outcome"]},
            )
            for row in rows
        ]

    def upsert(self, item_id: str, content: str, category: str, metadata: dict) -> None:
        """The SQL fallback has no separate embedding store to update — the
        `history` table it reads from is written by data/seed.py. This method
        exists to satisfy the VectorIndex port for callers that don't care which
        backend is active; it is intentionally a documented no-op here."""
        return None
