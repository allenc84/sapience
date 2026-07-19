"""Consolidation tests: incremental carry-forward and atomic upsert.

The Anthropic extraction call is monkeypatched; embeddings are faked. These
guard the two failure modes of the old delete-then-save design: losing the
summary when a write fails, and forgetting insights older than the window.
"""

import pytest

from tests.test_memory_store import fake_embed, store  # noqa: F401  (fixture reuse)


@pytest.fixture
def consolidator(store, monkeypatch):  # noqa: F811
    from sapience import consolidator as C

    # consolidator holds its own reference to the memory_store module; the
    # store fixture already patched DB_PATH/embed on that same module object.
    return C


def fake_extraction(summary="things stand here"):
    return {
        "patterns": ["pattern one"],
        "evolved_thinking": [],
        "open_questions": [],
        "key_facts": ["fact one"],
        "summary": summary,
    }


def seed_episodes(store, n=2, topic="launch", offset=0):  # noqa: F811
    return [
        store.save(f"episode {offset + i} about {topic} details", "episodic",
                   salience=0.6, topic=topic)
        for i in range(n)
    ]


def summaries(store, topic="launch"):  # noqa: F811
    return [m for m in store.list_recent(memory_type="semantic", limit=50)
            if m.source == "consolidation" and m.topic == topic]


def test_first_run_creates_deterministic_summary(consolidator, store, monkeypatch):  # noqa: F811
    seed_episodes(store)
    monkeypatch.setattr(consolidator, "_extract_patterns",
                        lambda topic, memories, prior_summary=None: fake_extraction())

    result = consolidator.run()
    assert result["topics_consolidated"] == 1

    found = summaries(store)
    assert len(found) == 1
    assert found[0].id == consolidator._summary_id("default", "launch")
    assert found[0].metadata["episodes_total"] == 2


def test_second_run_feeds_prior_and_upserts_same_id(consolidator, store, monkeypatch):  # noqa: F811
    seed_episodes(store)
    monkeypatch.setattr(consolidator, "_extract_patterns",
                        lambda topic, memories, prior_summary=None: fake_extraction("v1"))
    consolidator.run()
    first = summaries(store)[0]

    seen_priors = []

    def second_extract(topic, memories, prior_summary=None):
        seen_priors.append(prior_summary)
        return fake_extraction("v2")

    seed_episodes(store, offset=10)
    monkeypatch.setattr(consolidator, "_extract_patterns", second_extract)
    consolidator.run()

    # Prior summary content was fed into the second extraction.
    assert seen_priors and "v1" in seen_priors[0]

    found = summaries(store)
    assert len(found) == 1, "re-consolidation must not accumulate duplicates"
    assert found[0].id == first.id
    assert "v2" in found[0].content
    assert found[0].created_at == first.created_at, "created_at survives upsert"
    assert found[0].metadata["episodes_total"] == 4
    # Source-episode ids accumulate across runs.
    assert len(found[0].related_ids) == 4


def test_failed_extraction_leaves_prior_summary_intact(consolidator, store, monkeypatch):  # noqa: F811
    seed_episodes(store)
    monkeypatch.setattr(consolidator, "_extract_patterns",
                        lambda topic, memories, prior_summary=None: fake_extraction("v1"))
    consolidator.run()

    def boom(topic, memories, prior_summary=None):
        raise RuntimeError("api down")

    seed_episodes(store, offset=10)
    monkeypatch.setattr(consolidator, "_extract_patterns", boom)
    result = consolidator.run()

    assert "error" in result["results"][0]["status"]
    found = summaries(store)
    assert len(found) == 1 and "v1" in found[0].content, \
        "a failed run must never delete the existing summary"


def test_failed_embedding_leaves_prior_summary_intact(consolidator, store, monkeypatch):  # noqa: F811
    seed_episodes(store)
    monkeypatch.setattr(consolidator, "_extract_patterns",
                        lambda topic, memories, prior_summary=None: fake_extraction("v1"))
    consolidator.run()

    seed_episodes(store, offset=10)
    monkeypatch.setattr(consolidator, "_extract_patterns",
                        lambda topic, memories, prior_summary=None: fake_extraction("v2"))

    real_embed = store.embed

    def embed_fails_on_summary(text):
        if text.startswith("TOPIC:"):
            raise RuntimeError("embedding api down")
        return real_embed(text)

    monkeypatch.setattr(store, "embed", embed_fails_on_summary)
    result = consolidator.run()

    assert "error" in result["results"][0]["status"]
    found = summaries(store)
    assert len(found) == 1 and "v1" in found[0].content


def test_migrates_old_random_id_summaries(consolidator, store, monkeypatch):  # noqa: F811
    # A summary from the delete-then-save era: same topic, random id.
    old = store.save("TOPIC: launch\n\nSUMMARY: legacy insights", "semantic",
                     salience=0.8, source="consolidation", topic="launch")

    seen_priors = []

    def extract(topic, memories, prior_summary=None):
        seen_priors.append(prior_summary)
        return fake_extraction("merged")

    seed_episodes(store)
    monkeypatch.setattr(consolidator, "_extract_patterns", extract)
    consolidator.run()

    assert seen_priors and "legacy insights" in seen_priors[0]
    found = summaries(store)
    assert len(found) == 1
    assert found[0].id == consolidator._summary_id("default", "launch")
    assert store.get(old) is None, "legacy summary retired after successful upsert"
