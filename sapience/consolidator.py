"""
Consolidation job — the biological equivalent of sleep.

Reads recent episodic memories, groups them by topic, asks Claude to extract
semantic patterns, and writes those back as semantic memories. Run nightly.
"""

import os
import json
from datetime import datetime, timezone, timedelta

import anthropic

from . import memory_store
from .schema import Memory, USER_CONTEXT


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


def _extract_patterns(topic: str, memories: list[Memory]) -> dict:
    client = _get_client()

    memory_text = "\n\n".join(
        f"[{m.created_at[:10]} | salience={m.salience:.1f}]\n{m.content}"
        for m in memories
    )

    prompt = f"""You are analyzing a set of episodic memories about the topic "{topic}" from {USER_CONTEXT}.

Your job is to extract durable semantic knowledge from these episodes — patterns, principles, evolved thinking, and open questions that will be useful in future conversations. Think like a trusted advisor who has been paying close attention.

MEMORIES:
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
        max_tokens=1500,
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

    return response.content[0].input


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
            extracted = _extract_patterns(topic, memories)

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

            # Idempotency: replace any prior consolidation summary for this topic
            # so repeated runs over the same window don't accumulate duplicates.
            # Scoped to this namespace — another workspace's summary for the same
            # topic name is a different memory.
            memory_store.delete_where({
                "$and": [
                    {"namespace": {"$eq": memory_store.DEFAULT_NAMESPACE}},
                    {"type": {"$eq": "semantic"}},
                    {"source": {"$eq": "consolidation"}},
                    {"topic": {"$eq": topic}},
                ]
            })

            memory_store.save(
                content=semantic_content,
                memory_type="semantic",
                salience=0.8,
                source="consolidation",
                topic=topic,
                related_ids=source_ids,
                metadata={"days_back": days_back, "episode_count": len(memories)},
            )
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
