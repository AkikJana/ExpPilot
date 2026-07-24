"""Production VectorIndex backend: Postgres + pgvector.

docs/distributed-architecture.md §2: colocating the embedding column with the
memory rows makes hybrid retrieval (semantic + category + recency) one indexed
SQL statement instead of a fan-out to a separate vector service.

Honest scope note: this class is reviewed, type-checked code — there is no live
Postgres+pgvector instance in this development environment to test it against.
It implements the same VectorIndex port sql_fallback.py implements (which *is*
tested), so the swap between them is the only risk surface, and it is a thin
one: standard psycopg3 + pgvector cosine search behind three methods.

Embedding generation is deliberately out of scope for this class — it accepts a
pre-computed embedding vector or an `embed_fn` callable (wired to the Model
Gateway's local Tier-1 embedding endpoint in production, per §3: "embeddings
served from the same [vLLM] fleet"). Keeping embedding generation external keeps
this class testable with a fake embed_fn even before the gateway exists.
"""
from __future__ import annotations

from typing import Callable

EmbedFn = Callable[[str], list[float]]


class PgVectorUnavailableError(RuntimeError):
    """Raised when PgVectorIndex is constructed but psycopg or pgvector isn't
    installed, or the configured database can't be reached at construction time."""


class PgVectorIndex:
    """pgvector-backed VectorIndex. Table shape (see distributed/db/migrations/
    003_memory_schema.sql):

        memory.records(id, content, category, kind, source_experiment_id,
                        created_at, embedding vector(EMBED_DIM))

    with an HNSW index on `embedding` for approximate cosine search.
    """

    def __init__(self, dsn: str, embed_fn: EmbedFn, embed_dim: int = 768, table: str = "memory.records"):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise PgVectorUnavailableError(
                "psycopg[binary] and pgvector are not installed. "
                "pip install -r requirements-platform.txt"
            ) from exc

        self._embed_fn = embed_fn
        self._embed_dim = embed_dim
        self._table = table
        try:
            self._conn = psycopg.connect(dsn, autocommit=True)
            register_vector(self._conn)
        except Exception as exc:
            raise PgVectorUnavailableError(f"cannot connect to {dsn}: {exc}") from exc

    def search(self, query: str, category: str | None, k: int):
        from distributed.vector.interface import RetrievedItem

        query_embedding = self._embed_fn(query)
        sql = (
            f"SELECT id, content, category, "
            f"1 - (embedding <=> %(embedding)s) AS score "
            f"FROM {self._table} "
            f"WHERE (%(category)s IS NULL OR category = %(category)s) "
            f"ORDER BY embedding <=> %(embedding)s "
            f"LIMIT %(k)s"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, {"embedding": query_embedding, "category": category, "k": k})
            rows = cur.fetchall()
        return [
            RetrievedItem(id=row[0], content=row[1], category=row[2], score=float(row[3]), metadata={})
            for row in rows
        ]

    def upsert(self, item_id: str, content: str, category: str, metadata: dict) -> None:
        embedding = self._embed_fn(content)
        sql = (
            f"INSERT INTO {self._table} (id, content, category, embedding) "
            f"VALUES (%(id)s, %(content)s, %(category)s, %(embedding)s) "
            f"ON CONFLICT (id) DO UPDATE SET "
            f"content = EXCLUDED.content, category = EXCLUDED.category, embedding = EXCLUDED.embedding"
        )
        with self._conn.cursor() as cur:
            cur.execute(sql, {"id": item_id, "content": content, "category": category, "embedding": embedding})
