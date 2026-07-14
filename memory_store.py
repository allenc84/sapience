import os
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from schema import Memory, SearchResult, MEMORY_TYPES
from embeddings import embed, embed_batch

DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", Path(__file__).parent / "chroma_db"))


def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(
        path=str(DB_PATH),
        settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name="memories",
        metadata={"hnsw:space": "cosine"}
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metadata_to_memory(doc: str, meta: dict, id: str) -> Memory:
    return Memory(
        id=id,
        content=doc,
        type=meta.get("type", "episodic"),
        created_at=meta.get("created_at", _now()),
        salience=float(meta.get("salience", 0.5)),
        source=meta.get("source", "unknown"),
        topic=meta.get("topic", ""),
        access_count=int(meta.get("access_count", 0)),
        last_accessed=meta.get("last_accessed"),
        related_ids=json.loads(meta.get("related_ids", "[]")),
        metadata=json.loads(meta.get("extra_metadata", "{}")),
    )


def save(
    content: str,
    memory_type: str,
    salience: float = 0.5,
    source: str = "manual",
    topic: str = "",
    related_ids: list[str] | None = None,
    metadata: dict | None = None,
    memory_id: str | None = None,
) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"Invalid memory type '{memory_type}'. Must be one of: {MEMORY_TYPES}")

    collection = _get_collection()
    mid = memory_id or str(uuid.uuid4())
    embedding = embed(content)

    collection.add(
        ids=[mid],
        embeddings=[embedding],
        documents=[content],
        metadatas=[{
            "type": memory_type,
            "salience": salience,
            "source": source,
            "topic": topic,
            "created_at": _now(),
            "access_count": 0,
            "related_ids": json.dumps(related_ids or []),
            "extra_metadata": json.dumps(metadata or {}),
        }]
    )
    return mid


def delete(memory_id: str) -> bool:
    """Delete a single memory by id. Returns True if it existed."""
    collection = _get_collection()
    existing = collection.get(ids=[memory_id])
    if not existing["ids"]:
        return False
    collection.delete(ids=[memory_id])
    return True


def delete_where(where: dict) -> int:
    """Delete every memory matching a metadata filter. Returns the number deleted.

    Used to keep regenerated memories (consolidation summaries, calibration
    feedback) idempotent: delete the prior version for a key before writing the
    new one, so repeated runs replace rather than accumulate duplicates.
    """
    collection = _get_collection()
    existing = collection.get(where=where)
    ids = existing.get("ids", [])
    if ids:
        collection.delete(ids=ids)
    return len(ids)


def search(
    query: str,
    top_k: int = 5,
    types: list[str] | None = None,
    min_salience: float = 0.0,
) -> list[SearchResult]:
    collection = _get_collection()
    if collection.count() == 0:
        return []

    query_embedding = embed(query)

    where = None
    if types:
        valid = [t for t in types if t in MEMORY_TYPES]
        if valid:
            where = {"type": {"$in": valid}}

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for doc, meta, dist, mid in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
        results["ids"][0],
    ):
        score = 1.0 - dist   # cosine distance → similarity
        mem = _metadata_to_memory(doc, meta, mid)
        if mem.salience >= min_salience:
            output.append(SearchResult(memory=mem, score=score))
            _increment_access(collection, mid)

    return sorted(output, key=lambda r: r.score * (0.5 + r.memory.salience), reverse=True)


def get(memory_id: str) -> Optional[Memory]:
    collection = _get_collection()
    try:
        result = collection.get(
            ids=[memory_id],
            include=["documents", "metadatas"]
        )
        if not result["ids"]:
            return None
        return _metadata_to_memory(result["documents"][0], result["metadatas"][0], memory_id)
    except Exception:
        return None


def get_related(memory_id: str, top_k: int = 5) -> list[SearchResult]:
    mem = get(memory_id)
    if not mem:
        return []
    # Spread activation: search using the memory's own content as query
    results = search(mem.content, top_k=top_k + 1)
    return [r for r in results if r.memory.id != memory_id][:top_k]


def list_recent(memory_type: str | None = None, limit: int = 20) -> list[Memory]:
    collection = _get_collection()
    if collection.count() == 0:
        return []

    where = {"type": memory_type} if memory_type else None
    result = collection.get(
        where=where,
        limit=limit,
        include=["documents", "metadatas"],
    )
    memories = [
        _metadata_to_memory(doc, meta, mid)
        for doc, meta, mid in zip(result["documents"], result["metadatas"], result["ids"])
    ]
    return sorted(memories, key=lambda m: m.created_at, reverse=True)


def _increment_access(collection: chromadb.Collection, memory_id: str) -> None:
    try:
        result = collection.get(ids=[memory_id], include=["metadatas"])
        if result["metadatas"]:
            meta = result["metadatas"][0].copy()
            meta["access_count"] = int(meta.get("access_count", 0)) + 1
            meta["last_accessed"] = _now()
            collection.update(ids=[memory_id], metadatas=[meta])
    except Exception:
        pass


def count() -> int:
    return _get_collection().count()
