"""Provider dispatch tests for embeddings, and the --re-embed migration path.

The local ONNX model is faked (no ~80MB download in CI); what's under test is
the routing, the env override, and that repair --re-embed regenerates every
vector with the current provider.
"""

import pytest

from tests.test_memory_store import fake_embed, store  # noqa: F401  (fixture reuse)


class FakeLocalEF:
    """Stands in for chromadb's ONNXMiniLM_L6_V2: callable, fixed 4-dim."""

    def __call__(self, texts):
        return [[float(len(t)), 1.0, 0.0, 0.0] for t in texts]


@pytest.fixture
def embeddings(monkeypatch):
    from sapience import embeddings as E

    monkeypatch.setattr(E, "_get_local_ef", lambda: FakeLocalEF())
    return E


def test_default_provider_is_openai(embeddings, monkeypatch):
    monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)
    assert embeddings.provider() == "openai"


def test_local_provider_routes_to_local_ef(embeddings, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "local")
    v = embeddings.embed("hello world")
    assert v == [11.0, 1.0, 0.0, 0.0]
    batch = embeddings.embed_batch(["a", "bcd"])
    assert batch == [[1.0, 1.0, 0.0, 0.0], [3.0, 1.0, 0.0, 0.0]]


def test_provider_read_per_call(embeddings, monkeypatch):
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "LOCAL")  # case-insensitive
    assert embeddings.provider() == "local"
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "openai")
    assert embeddings.provider() == "openai"


def test_rebuild_re_embed_all_switches_dimensions(store, monkeypatch, tmp_path):  # noqa: F811
    from sapience import repair as R

    ids = [
        store.save("first memory about launches", "episodic", salience=0.7, topic="t"),
        store.save("second memory about hiring", "project", salience=0.4, topic="t"),
    ]
    path = store.DB_PATH

    # Records currently carry 8-dim fake_embed vectors. Re-embed with a
    # "new provider" producing 4-dim vectors.
    from sapience import embeddings as E
    monkeypatch.setattr(E, "embed", lambda text: [1.0, 2.0, 3.0, 4.0])

    result = R.rebuild(path, re_embed_all=True)
    assert result["restored_records"] == 2
    assert result["re_embedded"] == 2
    assert result["quarantined"] == 0

    # chromadb caches clients per path within a process, and that cache still
    # holds pre-swap directory handles — copy the swapped dir to a fresh path
    # to read what is actually on disk.
    import shutil
    verify_copy = tmp_path / "verify_copy"
    shutil.copytree(path, verify_copy)
    rebuilt = R._open(verify_copy)
    got = rebuilt.get(ids=ids, include=["embeddings"])
    assert all(len(e) == 4 for e in got["embeddings"]), "all vectors must use the new provider"
