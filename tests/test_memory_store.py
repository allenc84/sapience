"""Regression tests for memory_store search, including HNSW-corruption fallback.

Embeddings are faked (deterministic, keyless) so these run in CI without
OPENAI_API_KEY and without network access.
"""

import hashlib

import pytest


def fake_embed(text: str) -> list[float]:
    """Deterministic 8-dim embedding: identical prefixes → identical vectors.

    Uses md5, not hash() — built-in hash is randomized per process
    (PYTHONHASHSEED), which made rankings flake across runs. Components are
    centered so unrelated texts get near-zero cosine similarity instead of
    crowding the positive orthant.
    """
    digest = hashlib.md5(text[:12].encode()).digest()
    seed = [b - 127.5 for b in digest[:8]]
    norm = sum(x * x for x in seed) ** 0.5 or 1.0
    return [x / norm for x in seed]


@pytest.fixture
def store(tmp_path, monkeypatch):
    """memory_store pointed at a fresh temp Chroma dir with fake embeddings."""
    from sapience import memory_store as M

    monkeypatch.setattr(M, "DB_PATH", tmp_path / "chroma_db")
    monkeypatch.setattr(M, "embed", fake_embed)
    return M


def seed(store) -> dict:
    ids = {}
    ids["a"] = store.save("alpha memory about the launch plan", "project", salience=0.9, topic="launch")
    ids["b"] = store.save("beta memory about the launch retro", "episodic", salience=0.5, topic="launch")
    ids["c"] = store.save("gamma memory about hiring", "user", salience=0.5, topic="hiring")
    return ids


def test_save_and_search_roundtrip(store):
    ids = seed(store)
    results = store.search("alpha memory about the launch plan", top_k=3)
    assert results, "search returned nothing"
    assert results[0].memory.id == ids["a"]


def test_type_filtered_search(store):
    ids = seed(store)
    results = store.search("launch", top_k=5, types=["episodic"])
    returned_ids = {r.memory.id for r in results}
    assert ids["b"] in returned_ids
    assert ids["a"] not in returned_ids


def test_search_falls_back_when_hnsw_query_fails(store, monkeypatch):
    """When collection.query raises (ghost-index drift), search must still
    return correctly ranked, correctly filtered results via the metadata scan."""
    ids = seed(store)
    real_get_collection = store._get_collection

    class BrokenIndex:
        def __init__(self, inner):
            self._inner = inner

        def query(self, *args, **kwargs):
            raise RuntimeError("Error executing plan: Internal error: Error finding id")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(store, "_get_collection", lambda: BrokenIndex(real_get_collection()))

    results = store.search("alpha memory about the launch plan", top_k=3)
    assert results, "fallback search returned nothing"
    assert results[0].memory.id == ids["a"]

    filtered = store.search("launch", top_k=5, types=["episodic"])
    returned_ids = {r.memory.id for r in filtered}
    assert ids["b"] in returned_ids
    assert ids["a"] not in returned_ids


def test_fallback_respects_min_salience(store, monkeypatch):
    ids = seed(store)
    real_get_collection = store._get_collection

    class BrokenIndex:
        def __init__(self, inner):
            self._inner = inner

        def query(self, *args, **kwargs):
            raise RuntimeError("boom")

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(store, "_get_collection", lambda: BrokenIndex(real_get_collection()))
    results = store.search("launch", top_k=5, min_salience=0.8)
    assert {r.memory.id for r in results} == {ids["a"]}
