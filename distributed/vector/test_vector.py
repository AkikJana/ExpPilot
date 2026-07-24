"""Vector index tests. Only sql_fallback.py is exercised — it wraps the existing,
already-tested agents/rag.py retrieval. pgvector_index.py has no live Postgres to
test against in this environment (see distributed/README.md)."""
from __future__ import annotations

import pytest

from data.db import init_db
from data.seed import main as seed_main
from distributed.vector.interface import RetrievedItem
from distributed.vector.pgvector_index import PgVectorIndex, PgVectorUnavailableError
from distributed.vector.sql_fallback import SqlFallbackIndex


@pytest.fixture(scope="module", autouse=True)
def _seeded_db():
    init_db()
    seed_main()


@pytest.fixture()
def index() -> SqlFallbackIndex:
    return SqlFallbackIndex()


def test_search_returns_retrieved_items(index: SqlFallbackIndex):
    results = index.search("checkout conversion mobile", category="checkout", k=3)
    assert results
    assert all(isinstance(r, RetrievedItem) for r in results)
    assert all(r.category == "checkout" for r in results)


def test_search_respects_k(index: SqlFallbackIndex):
    results = index.search("checkout", category="checkout", k=2)
    assert len(results) <= 2


def test_upsert_is_a_documented_noop_not_a_crash(index: SqlFallbackIndex):
    """The SQL fallback has nowhere to write a new embedding; upsert must not
    raise — callers using VectorIndex generically should not need a special case."""
    index.upsert("new_item", "some content", "checkout", {})


def test_pgvector_backend_degrades_predictably_without_the_optional_dependency():
    """psycopg/pgvector are not in base requirements.txt; on a machine without
    them, constructing PgVectorIndex must raise a clear, typed error."""
    try:
        import psycopg  # noqa: F401

        pytest.skip("psycopg is installed in this environment; degrade-path not exercised")
    except ImportError:
        pass

    with pytest.raises(PgVectorUnavailableError, match="not installed"):
        PgVectorIndex(dsn="postgresql://localhost/nonexistent", embed_fn=lambda text: [0.0])
