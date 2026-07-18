"""
Consolidation job — the biological equivalent of sleep.

Reads recent episodic memories, groups them by topic, asks Claude to extract
semantic patterns, and writes those back as semantic memories. Run nightly.
"""

import os
import json
import uuid
from datetime import datetime, timezone, timedelta

import anthropic

from . import memory_store
from .schema import Memory, USER_CONTEXT

# Cap on accumulated source-episode ids carried on a summary; oldest drop first.
MAX_RELATED_IDS = 60


def _summary_id(namespace: str, topic: str) -> str:
    """Deterministic id for a topic's consolidated summary, so repeated runs
    upsert the same record instead of delete-and-recreate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"sapience-consolidation:{namespace}:{topic}"))


def _prior_summaries(topic: str) -> list[Memory]:
    """Existing consolidation summaries for a topic in this namespace, newest
    first. More than one (or a non-deterministic id) means records from the
    old delete-then-save era that should be folded in and cleaned up."""
    collection = memory_store._get_collection()
    got = collection.get(
        where={"$and": [
            {"namespace": {"$eq": memory_store.DEFAULT_NAMESPACE}},
            {"type": {"$eq": "semantic"}},
            {"source": {"$eq": "consolidation"}},
            {"topic": {"$eq": topic}},
        ]},
        include=["documents", "metadatas"],
    )
    summaries = [
        memory_store._metadata_to_memory(doc, meta, mid)
        for doc, meta, mid in zip(got["documents"], got["metadatas"], got["ids"])
        if doc is not None and meta is not None
    ]
    summaries.sort(key=lambda m: m.metadata.get("last_consolidated", m.created_at), reverse=True)
    return summaries


def _get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


def _group_by_topic(memories: list[Memory]) -> dict[str, list[Memory]]:
    groups: dict[str, list[Memory]] = {}
    for mem in memories:
        key = mem.topic or "general"
        groups.setdefault(key, []).append(mem)
    return groups


def _extract_patterns(topic: str, memories: list[Memory], prior_summary: str | None = None) -> dict:
    client = _get_client()

    memory_text = "\n\n".join(
        f"[{m.created_at[:10]} | salience={m.salience:.1f}]\n{m.content}"
        for m in memories
    )

    prior_block = ""
    if prior_summary:
        prior_block = f"""

PREVIOUS CONSOLIDATED SUMMARY (distilled from earlier episodes, possibly older than the ones below — this is accumulated knowledge, not stale data):
{prior_summary}

Integrate the new episodes INTO this accumulated knowledge: carry forward every durable insight that is still true, update or replace what the new episodes supersede, and resolve open questions the new episodes answer. Do not drop an insight merely because the new episodes don't mention it."""

    prompt = f"""You are analyzing a set of episodic memories about the topic "{topic}" from {USER_CONTEXT}.

Your job is to extract durable semantic knowledge from these episodes — patterns, principles, evolved thinking, and open questions that will be useful in future conversations. Think like a trusted advisor who has been paying close attention.{prior_block}

NEW MEMORIES:
{memory_text}

Return a JSON object with these fields:
- "patterns": list of strings — durable patterns or principles extracted across episodes
- "evolved_thinking": list of strings — how the user's thinking has changed or matured on this topic
- "open_questions": list of strings — unresolved questions or tensions that keep appearing
- "key_facts": list of strings — specific facts, decisions, or outcomes worth remembering
- "summary": string — 2-3 sentence narrative summary of where things stand on this topic

Be specific and non-generic. These will be used to challenge and develop the user's thinking in future sessions, not just remind them of what happened."""

    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=4000,
        tools=[{
            "name": "extract_patterns",
            "description": "Extract semantic patterns from episodic memories",
            "input_schema": {
                "type": "object",
                "properties": {
                    "patterns": {"type": "array", "items": {"type": "string"}},
                    "evolved_thinking": {"type": "array", "items": {"type": "string"}},
                    "open_questions": {"type": "array", "items": {"type": "string"}},
                    "key_facts": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["patterns", "evolved_thinking", "open_questions", "key_facts", "summary"],
            },
        }],
        tool_choice={"type": "tool", "name": "extract_patterns"},
        messages=[{"role": "user", "content": prompt}],
    )

    # A truncated tool call parses as a dict with fields silently missing —
    # writing that would replace a good summary with a degraded one. Fail
    # loudly instead; the atomic upsert path then leaves the prior intact.
    if response.stop_reason == "max_tokens":
        raise RuntimeError(f"extraction for '{topic}' truncated at max_tokens; keeping prior summary")
    extracted = response.content[0].input
    if "summary" not in extracted:
        raise RuntimeError(f"extraction for '{topic}' returned no summary; keeping prior summary")
    return extracted


def run(days_back: int = 7) -> dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_back)
    cutoff_str = cutoff.isoformat()

    recent = memory_store.list_recent(memory_type="episodic", limit=200)
    recent = [m for m in recent if m.created_at >= cutoff_str]

    if not recent:
        return {"status": "no_recent_memories", "extracted": 0}

    groups = _group_by_topic(recent)
    total_extracted = 0
    results = []

    for topic, memories in groups.items():
        if len(memories) < 2:
            continue

        try:
            # Incremental: feed the accumulated summary back in, so insights
            # from episodes older than this window survive re-consolidation.
            priors = _prior_summaries(topic)
            prior = priors[0] if priors else None
            extracted = _extract_patterns(
                topic, memories, prior_summary=prior.content if prior else None
            )

            content_parts = [
                f"TOPIC: {topic}",
                f"SUMMARY: {extracted.get('summary', '')}",
            ]
            if extracted.get("patterns"):
                content_parts.append("PATTERNS:\n" + "\n".join(f"- {p}" for p in extracted["patterns"]))
            if extracted.get("evolved_thinking"):
                content_parts.append("EVOLVED THINKING:\n" + "\n".join(f"- {p}" for p in extracted["evolved_thinking"]))
            if extracted.get("open_questions"):
                content_parts.append("OPEN QUESTIONS:\n" + "\n".join(f"- {q}" for q in extracted["open_questions"]))
            if extracted.get("key_facts"):
                content_parts.append("KEY FACTS:\n" + "\n".join(f"- {f}" for f in extracted["key_facts"]))

            semantic_content = "\n\n".join(content_parts)

            source_ids = [m.id for m in memories]
            carried = prior.related_ids if prior else []
            all_related = list(dict.fromkeys(carried + source_ids))[-MAX_RELATED_IDS:]
            # Windows overlap between runs, so only episodes not already
            # carried on the summary count as new.
            prior_total = (
                int(prior.metadata.get("episodes_total", prior.metadata.get("episode_count", 0)))
                if prior else 0
            )
            episodes_total = prior_total + len([i for i in source_ids if i not in set(carried)])

            # Atomic: upsert at a deterministic per-topic id. A failure before
            # or during the write leaves the previous summary untouched —
            # there is no delete-then-save window.
            sid = _summary_id(memory_store.DEFAULT_NAMESPACE, topic)
            memory_store.upsert(
                content=semantic_content,
                memory_type="semantic",
                memory_id=sid,
                salience=0.8,
                source="consolidation",
                topic=topic,
                related_ids=all_related,
                metadata={
                    "days_back": days_back,
                    "episode_count": len(memories),
                    "episodes_total": episodes_total,
                    "last_consolidated": datetime.now(timezone.utc).isoformat(),
                },
            )
            # Only after the new summary is safely written: retire summaries
            # from the old delete-then-save era (random ids). If this cleanup
            # fails, the harmless direction is a leftover duplicate.
            for stale in priors:
                if stale.id != sid:
                    memory_store.delete(stale.id)

            total_extracted += 1
            results.append({"topic": topic, "episodes": len(memories), "status": "ok"})

        except Exception as e:
            results.append({"topic": topic, "episodes": len(memories), "status": f"error: {e}"})

    return {
        "status": "complete",
        "episodes_processed": len(recent),
        "topics_consolidated": total_extracted,
        "results": results,
    }


def main():
    """Console-script entry point (`sapience-consolidate`)."""
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
