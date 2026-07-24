"""The VectorIndex port (docs/distributed-architecture.md §2).

"A thin VectorIndex port so the swap is a config change, not a rewrite." Two
implementations: sql_fallback.py (wraps agents/rag.py's existing keyword search,
fully tested, zero new infra) and pgvector_index.py (production, requires a live
Postgres with the pgvector extension — reviewed code, not integration-tested
here; see distributed/README.md for scope notes).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RetrievedItem:
    """One retrieval hit, backend-agnostic. `score` is cosine similarity for a
    vector backend or a lexical overlap score for the SQL fallback — callers
    should treat it as "higher is more relevant," not compare it across backends."""

    id: str
    content: str
    category: str
    score: float
    metadata: dict


class VectorIndex(Protocol):
    """Retrieval port. search() is the only method every backend must provide;
    upsert() is required for backends that own their own embedding store."""

    def search(self, query: str, category: str | None, k: int) -> list[RetrievedItem]: ...

    def upsert(self, item_id: str, content: str, category: str, metadata: dict) -> None: ...
