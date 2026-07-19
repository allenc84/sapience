"""Tests for the memory admin surface: update, delete, export, find_duplicates.

Embeddings are faked (deterministic, keyless) so these run in CI without
OPENAI_API_KEY and without network access.
"""

import json

import pytest

from tests.test_memory_store import fake_embed, store, seed  # noqa: F401  (fixture reuse)


def test_update_content_reembeds_and_preserves_identity(store):
    ids = seed(store)
    before = store.get(ids["a"])

    updated = store.update(ids["a"], content="omega memory about the pricing model")
    assert updated is not None
    assert updated.id == ids["a"]
    assert updated.content == "omega memory about the pricing model"
    assert updated.created_at == before.created_at

    # New content must be findable by its own embedding, old content must not win.
    results = store.search("omega memory about the pricing model", top_k=1)
    assert results[0].memory.id == ids["a"]
    assert results[0].memory.content == "omega memory about the pricing model"


def test_update_metadata_only_keeps_content(store):
    ids = seed(store)
    updated = store.update(ids["b"], salience=0.95, topic="retro", memory_type="semantic")
    assert updated.content == "beta memory about the launch retro"
    assert updated.salience == 0.95
    assert updated.topic == "retro"
    assert updated.type == "semantic"

    # Persisted, not just returned.
    again = store.get(ids["b"])
    assert again.salience == 0.95
    assert again.type == "semantic"


def test_update_rejects_bad_input(store):
    ids = seed(store)
    with pytest.raises(ValueError):
        store.update(ids["a"], memory_type="nonsense")
    with pytest.raises(ValueError):
        store.update(ids["a"], salience=1.5)
    assert store.update("no-such-id", salience=0.5) is None


def test_delete_removes_record(store):
    ids = seed(store)
    assert store.delete(ids["c"]) is True
    assert store.get(ids["c"]) is None
    assert store.delete(ids["c"]) is False
    assert store.count() == 2


def test_export_all_roundtrips(store, tmp_path):
    ids = seed(store)
    records = store.export_all()
    assert len(records) == 3
    by_id = {r["id"]: r for r in records}
    assert by_id[ids["a"]]["content"] == "alpha memory about the launch plan"
    assert by_id[ids["a"]]["salience"] == 0.9
    assert "embedding" not in by_id[ids["a"]]

    # JSONL round-trip: every record survives serialization.
    out = tmp_path / "export.jsonl"
    with open(out, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    back = [json.loads(line) for line in out.read_text().splitlines()]
    assert back == records

    only_user = store.export_all(memory_type="user")
    assert [r["id"] for r in only_user] == [ids["c"]]


def test_find_duplicates_flags_only_near_identical(store):
    ids = seed(store)
    # fake_embed keys on the first 12 chars, so this collides with "a" exactly.
    dup = store.save("alpha memory duplicated later", "project", salience=0.4, topic="launch")

    pairs = store.find_duplicates(threshold=0.99)
    assert len(pairs) == 1
    pair_ids = {pairs[0]["a"]["id"], pairs[0]["b"]["id"]}
    assert pair_ids == {ids["a"], dup}
    assert pairs[0]["similarity"] >= 0.99

    # Nothing was deleted — report-only.
    assert store.count() == 4
