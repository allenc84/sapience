"""Tests for memory namespaces: isolation, wildcard reads, and the lazy
backfill that stamps pre-namespace records as 'default'.

Embeddings are faked (deterministic, keyless) so these run in CI without
OPENAI_API_KEY and without network access.
"""

import pytest

from tests.test_memory_store import fake_embed, store  # noqa: F401  (fixture reuse)


@pytest.fixture
def ns_store(store, monkeypatch):  # noqa: F811
    monkeypatch.setattr(store, "_namespace_backfilled", False)
    return store


def test_namespace_isolation(ns_store):
    a = ns_store.save("alpha memory about the launch plan", "project", namespace="work")
    b = ns_store.save("alpha memory about the launch plan", "project")  # default ns

    default_hits = {r.memory.id for r in ns_store.search("alpha memory about the launch", top_k=5)}
    assert default_hits == {b}

    work_hits = {r.memory.id for r in ns_store.search("alpha memory about the launch", top_k=5, namespace="work")}
    assert work_hits == {a}

    all_hits = {r.memory.id for r in ns_store.search("alpha memory about the launch", top_k=5, namespace="*")}
    assert all_hits == {a, b}


def test_env_namespace_is_process_default(ns_store, monkeypatch):
    monkeypatch.setattr(ns_store, "DEFAULT_NAMESPACE", "sideproject")
    mid = ns_store.save("gamma memory about hiring", "user")
    mem = ns_store.get(mid)
    assert mem.namespace == "sideproject"
    assert [r.memory.id for r in ns_store.search("gamma memory about hiring", top_k=3)] == [mid]
    assert ns_store.search("gamma memory about hiring", top_k=3, namespace="default") == []


def test_save_rejects_wildcard_namespace(ns_store):
    with pytest.raises(ValueError):
        ns_store.save("text", "user", namespace="*")


def test_backfill_makes_legacy_records_default(ns_store):
    # Simulate a record created before namespaces existed: no namespace key.
    collection = ns_store._get_collection()
    collection.add(
        ids=["legacy-1"],
        embeddings=[fake_embed("legacy memory about budget")],
        documents=["legacy memory about budget"],
        metadatas=[{"type": "project", "salience": 0.5, "created_at": "2026-01-01T00:00:00+00:00"}],
    )

    hits = ns_store.search("legacy memory about budget", top_k=3)
    assert [r.memory.id for r in hits] == ["legacy-1"]
    assert hits[0].memory.namespace == "default"
    assert ns_store.list_namespaces() == {"default": 1}


def test_list_recent_and_count_scope_by_namespace(ns_store):
    ns_store.save("alpha memory about the launch plan", "project", namespace="work")
    ns_store.save("beta memory about the launch retro", "episodic")

    assert {m.namespace for m in ns_store.list_recent()} == {"default"}
    assert {m.namespace for m in ns_store.list_recent(namespace="work")} == {"work"}
    assert len(ns_store.list_recent(namespace="*")) == 2
    assert ns_store.count() == 2
    assert ns_store.count(namespace="work") == 1
    assert ns_store.list_namespaces() == {"default": 1, "work": 1}


def test_export_defaults_to_all_namespaces(ns_store):
    ns_store.save("alpha memory about the launch plan", "project", namespace="work")
    ns_store.save("beta memory about the launch retro", "episodic")

    assert {r["namespace"] for r in ns_store.export_all()} == {"default", "work"}
    assert {r["namespace"] for r in ns_store.export_all(namespace="default")} == {"default"}


def test_duplicates_not_flagged_across_namespaces(ns_store):
    # Identical fake embedding (same 12-char prefix) in two namespaces:
    # a deliberate copy, not a duplicate.
    ns_store.save("alpha memory about the launch plan", "project", namespace="work")
    ns_store.save("alpha memory about the launch plan", "project")

    assert ns_store.find_duplicates(threshold=0.99) == []
    assert ns_store.find_duplicates(threshold=0.99, namespace="work") == []

    # But within one namespace they are flagged.
    ns_store.save("alpha memory duplicated in work", "project", namespace="work")
    pairs = ns_store.find_duplicates(threshold=0.99, namespace="work")
    assert len(pairs) == 1
