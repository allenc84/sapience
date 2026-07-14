"""
One-time migration: import existing flat memory files into the vector store.

Reads all .md files from the memory directory, parses frontmatter,
classifies type, and saves into ChromaDB with embeddings.
"""

import os
import sys
from pathlib import Path

import frontmatter

from . import memory_store

# OpenAI text-embedding-3-small limit is 8192 tokens (~6000 words). We chunk conservatively.
MAX_CHARS = 12000


def _chunk(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks = []
    while text:
        chunk = text[:max_chars]
        # Try to split on a paragraph boundary
        split = chunk.rfind("\n\n")
        if split > max_chars // 2:
            chunk = text[:split]
        chunks.append(chunk.strip())
        text = text[len(chunk):].strip()
    return [c for c in chunks if c]

# Directory of legacy flat-file memories to import. Set MEMORY_MIGRATE_DIR to
# point at your own memory folder, e.g.
#   export MEMORY_MIGRATE_DIR="$HOME/.claude/projects/<project>/memory"
_migrate_dir = os.environ.get("MEMORY_MIGRATE_DIR")
if not _migrate_dir:
    sys.exit("Set MEMORY_MIGRATE_DIR to the folder of .md memory files to import.")
MEMORY_DIR = Path(_migrate_dir).expanduser()

TYPE_MAP = {
    "user": "user",
    "feedback": "feedback",
    "project": "project",
    "reference": "reference",
}

SALIENCE_MAP = {
    "feedback": 0.85,
    "user": 0.7,
    "project": 0.75,
    "reference": 0.5,
}


def _infer_topic(filename: str, content: str) -> str:
    name = filename.replace(".md", "").lower()
    # Map filename keywords to topic tags. Customize for your own memory files.
    topic_hints = {
        "active": "initiatives",
        "growth": "growth",
        "memory": "meta",
    }
    for hint, topic in topic_hints.items():
        if hint in name:
            return topic
    return name.replace("_", "-").replace(" ", "-")


def migrate(dry_run: bool = False) -> dict:
    if not MEMORY_DIR.exists():
        return {"error": f"Memory directory not found: {MEMORY_DIR}"}

    files = [f for f in MEMORY_DIR.glob("*.md") if f.name != "MEMORY.md"]
    results = {"migrated": [], "skipped": [], "errors": []}

    for filepath in sorted(files):
        try:
            post = frontmatter.load(str(filepath))
            content = post.content.strip()
            meta = post.metadata

            if not content or len(content) < 20:
                results["skipped"].append({"file": filepath.name, "reason": "too short"})
                continue

            raw_type = str(meta.get("metadata", {}).get("type", "")).lower() if isinstance(meta.get("metadata"), dict) else ""
            memory_type = TYPE_MAP.get(raw_type, "project")
            salience = SALIENCE_MAP.get(memory_type, 0.6)
            topic = _infer_topic(filepath.name, content)
            name = meta.get("name", filepath.stem)

            full_content = f"{name}\n\n{content}" if name else content
            chunks = _chunk(full_content)

            if not dry_run:
                for i, chunk in enumerate(chunks):
                    chunk_label = f" (part {i+1}/{len(chunks)})" if len(chunks) > 1 else ""
                    mid = memory_store.save(
                        content=chunk,
                        memory_type=memory_type,
                        salience=salience,
                        source="migration",
                        topic=topic,
                        metadata={"original_file": filepath.name, "chunk": i, "total_chunks": len(chunks)},
                    )
                    results["migrated"].append({"file": filepath.name + chunk_label, "id": mid, "type": memory_type, "topic": topic})
            else:
                results["migrated"].append({"file": filepath.name, "type": memory_type, "topic": topic, "chunks": len(chunks), "dry_run": True})

            suffix = f" ({len(chunks)} chunks)" if len(chunks) > 1 else ""
            print(f"  ✓ {filepath.name} → {memory_type}/{topic}{suffix}")

        except Exception as e:
            results["errors"].append({"file": filepath.name, "error": str(e)})
            print(f"  ✗ {filepath.name}: {e}")

    return results


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    print(f"{'DRY RUN — ' if dry else ''}Migrating memory files from {MEMORY_DIR}\n")
    result = migrate(dry_run=dry)
    print(f"\nDone: {len(result['migrated'])} migrated, {len(result['skipped'])} skipped, {len(result['errors'])} errors")
    if result.get("errors"):
        for e in result["errors"]:
            print(f"  ERROR: {e}")
