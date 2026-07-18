import os
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import chromadb
from chromadb.config import Settings

from .schema import Memory, SearchResult, MEMORY_TYPES
from .embeddings import embed, embed_batch

from .paths import data_dir

DB_PATH = Path(os.environ.get("MEMORY_DB_PATH") or (data_dir() / "chroma_db"))

# Namespace this process reads and writes by default. Namespaces partition
# memories (e.g. per project or workspace) inside one DB; the judgment ledger
# is deliberately NOT namespaced — a person's track record is global.
# Pass namespace="*" to read across all namespaces.
DEFAULT_NAMESPACE = os.environ.get("SAPIENCE_NAMESPACE", "default")

_namespace_backfilled = False


def _ensure_namespace_backfill(collection: chromadb.Collection) -> None:
    """Stamp namespace='default' onto records created before namespaces existed.

    Runs once per process, metadata-only (never touches the vector segment).
    Without this, pre-namespace records would be invisible to every
    namespace-filtered query.
    """
    global _namespace_backfilled
    if _namespace_backfilled:
        return
    _namespace_backfilled = True
    try:
        got = collection.get(include=["metadatas"])
        ids, metas = [], []
        for mid, meta in zip(got["ids"], got["metadatas"]):
            if meta is not None and "namespace" not in meta:
                m = meta.copy()
                m["namespace"] = "default"
                ids.append(mid)
                metas.append(m)
        if ids:
            collection.update(ids=ids, metadatas=metas)
    except Exception:
        _namespace_backfilled = False


def _namespace_clause(namespace: str | None) -> dict | None:
    """Chroma where-clause for a namespace; None for the all-namespaces wildcard."""
    ns = namespace or DEFAULT_NAMESPACE
    if ns == "*":
        return None
    return {"namespace": {"$eq": ns}}


def _combine_where(*clauses: dict | None) -> dict | None:
    present = [c for c in clauses if c]
    if not present:
        return None
    if len(present) == 1:
        return present[0]
    return {"$and": present}


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
        namespace=meta.get("namespace", "default"),
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
    namespace: str | None = None,
) -> str:
    if memory_type not in MEMORY_TYPES:
        raise ValueError(f"Invalid memory type '{memory_type}'. Must be one of: {MEMORY_TYPES}")
    ns = namespace or DEFAULT_NAMESPACE
    if ns == "*":
        raise ValueError("namespace '*' is a read-side wildcard; saves need a concrete namespace")

    collection = _get_collection()
    mid = memory_id or str(uuid.uuid4())
    embedding = embed(content)

    collection.add(
        ids=[mid],
        embeddings=[embedding],
        documents=[content],
        metadatas=[{
            "namespace": ns,
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


def update(
    memory_id: str,
    content: str | None = None,
    memory_type: str | None = None,
    salience: float | None = None,
    topic: str | None = None,
) -> Optional[Memory]:
    """Edit a memory in place, preserving id, created_at, and access history.

    Only the fields passed are changed; content changes trigger re-embedding.
    Returns the updated Memory, or None if the id doesn't exist.
    """
    if memory_type is not None and memory_type not in MEMORY_TYPES:
        raise ValueError(f"Invalid memory type '{memory_type}'. Must be one of: {MEMORY_TYPES}")
    if salience is not None and not 0.0 <= salience <= 1.0:
        raise ValueError(f"salience must be between 0.0 and 1.0, got {salience}")

    collection = _get_collection()
    existing = collection.get(ids=[memory_id], include=["documents", "metadatas"])
    if not existing["ids"]:
        return None

    meta = existing["metadatas"][0].copy()
    if memory_type is not None:
        meta["type"] = memory_type
    if salience is not None:
        meta["salience"] = salience
    if topic is not None:
        meta["topic"] = topic
    meta["updated_at"] = _now()

    kwargs = {"ids": [memory_id], "metadatas": [meta]}
    new_doc = existing["documents"][0]
    if content is not None:
        new_doc = content
        kwargs["documents"] = [content]
        kwargs["embeddings"] = [embed(content)]
    collection.update(**kwargs)
    return _metadata_to_memory(new_doc, meta, memory_id)


def export_all(memory_type: str | None = None, namespace: str | None = "*") -> list[dict]:
    """Return every memory (optionally one type/namespace) as plain dicts for export.

    Defaults to ALL namespaces — an export is a backup, and a backup that
    silently drops other namespaces' records is a data-loss trap.

    Embeddings are deliberately excluded — they are recomputable, and their
    bulk read path is the one that fails on a corrupted vector segment.
    """
    collection = _get_collection()
    _ensure_namespace_backfill(collection)
    where = _combine_where(
        _namespace_clause(namespace),
        {"type": {"$eq": memory_type}} if memory_type else None,
    )
    got = collection.get(where=where, include=["documents", "metadatas"])
    records = []
    for doc, meta, mid in zip(got["documents"], got["metadatas"], got["ids"]):
        if doc is None or meta is None:
            continue
        m = _metadata_to_memory(doc, meta, mid)
        records.append({
            "id": m.id,
            "namespace": m.namespace,
            "content": m.content,
            "type": m.type,
            "created_at": m.created_at,
            "salience": m.salience,
            "source": m.source,
            "topic": m.topic,
            "access_count": m.access_count,
            "last_accessed": m.last_accessed,
            "related_ids": m.related_ids,
            "metadata": m.metadata,
        })
    return sorted(records, key=lambda r: r["created_at"])


def find_duplicates(
    threshold: float = 0.92,
    limit: int = 50,
    namespace: str | None = None,
) -> list[dict]:
    """Report near-duplicate memory pairs by embedding cosine similarity,
    within one namespace (the same content in two namespaces is usually a
    deliberate copy, not a duplicate).

    Report-only by design: the 2026-07-13 out-of-process dedup deleted
    high-salience memories, so candidates are surfaced for explicit per-id
    deletion rather than removed automatically.
    """
    collection = _get_collection()
    _ensure_namespace_backfill(collection)
    got = collection.get(where=_namespace_clause(namespace), include=["documents", "metadatas"])
    ids = got["ids"]
    if len(ids) < 2:
        return []
    embeddings = _get_embeddings_by_id(collection, ids)
    by_id = {
        mid: _metadata_to_memory(doc, meta, mid)
        for doc, meta, mid in zip(got["documents"], got["metadatas"], ids)
        if doc is not None and meta is not None and mid in embeddings
    }

    import numpy as np
    kept = list(by_id)
    mat = np.array([embeddings[mid] for mid in kept], dtype=np.float64)
    norms = np.linalg.norm(mat, axis=1)
    norms[norms == 0] = 1.0
    mat /= norms[:, None]
    sims = mat @ mat.T

    pairs = []
    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            if sims[i, j] >= threshold:
                pairs.append((float(sims[i, j]), kept[i], kept[j]))
    pairs.sort(reverse=True)

    def _summary(m: Memory) -> dict:
        return {
            "id": m.id,
            "type": m.type,
            "topic": m.topic,
            "salience": m.salience,
            "created_at": m.created_at[:10],
            "content_preview": m.content[:200],
        }

    return [
        {"similarity": round(sim, 4), "a": _summary(by_id[a]), "b": _summary(by_id[b])}
        for sim, a, b in pairs[:limit]
    ]


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


def _get_embeddings_by_id(collection: chromadb.Collection, ids: list[str]) -> dict:
    """Fetch embeddings for ids, tolerating vector-segment corruption.

    Tries one batched get; if the segment is drifted that can fail wholesale
    ("Error finding id"), so fall back to per-id gets and skip only the ids
    that are individually unreadable."""
    try:
        got = collection.get(ids=ids, include=["embeddings"])
        embs = got.get("embeddings")
        if embs is not None:
            return {
                mid: list(emb)
                for mid, emb in zip(got["ids"], embs)
                if emb is not None
            }
    except Exception:
        pass
    out = {}
    for mid in ids:
        try:
            got = collection.get(ids=[mid], include=["embeddings"])
            embs = got.get("embeddings")
            if got["ids"] and embs is not None and embs[0] is not None:
                out[mid] = list(embs[0])
        except Exception:
            continue
    return out


def _scan_all(
    collection: chromadb.Collection,
    query_embedding: list[float],
    where: dict | None,
) -> list[tuple]:
    """HNSW-independent search: read every record from the metadata store and
    rank by cosine similarity computed client-side. documents/metadatas come
    from sqlite and survive index corruption; embeddings are salvaged per-id."""
    got = collection.get(where=where, include=["documents", "metadatas"])
    if not got["ids"]:
        return []
    embeddings = _get_embeddings_by_id(collection, got["ids"])
    q_norm = sum(x * x for x in query_embedding) ** 0.5 or 1.0
    candidates = []
    for doc, meta, mid in zip(got["documents"], got["metadatas"], got["ids"]):
        emb = embeddings.get(mid)
        if doc is None or meta is None or emb is None:
            continue
        dot = sum(a * b for a, b in zip(query_embedding, emb))
        e_norm = sum(x * x for x in emb) ** 0.5 or 1.0
        candidates.append((doc, meta, dot / (q_norm * e_norm), mid))
    return candidates


def search(
    query: str,
    top_k: int = 5,
    types: list[str] | None = None,
    min_salience: float = 0.0,
    namespace: str | None = None,
) -> list[SearchResult]:
    collection = _get_collection()
    if collection.count() == 0:
        return []
    _ensure_namespace_backfill(collection)

    query_embedding = embed(query)

    type_clause = None
    if types:
        valid = [t for t in types if t in MEMORY_TYPES]
        if valid:
            type_clause = {"type": {"$in": valid}}
    where = _combine_where(_namespace_clause(namespace), type_clause)

    # Over-fetch semantic candidates so the salience reranking below can promote
    # an important-but-slightly-less-similar memory above a top_k cutoff. Without
    # this, salience could only reorder within the first top_k by pure similarity.
    fetch_n = min(max(top_k * 5, top_k), collection.count())
    try:
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=fetch_n,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        candidates = [
            (doc, meta, 1.0 - dist, mid)   # cosine distance → similarity
            for doc, meta, dist, mid in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
                results["ids"][0],
            )
        ]
    except Exception:
        # The HNSW index can drift from the metadata store when the DB dir is
        # mutated by a second process (2026-07-14 ghost-index incident); Chroma
        # then fails internally ("Error finding id"), especially on where-filtered
        # queries. The metadata store itself survives, so fall back to scanning it
        # and ranking client-side. Personal-scale collections make this cheap.
        candidates = _scan_all(collection, query_embedding, where)

    output = []
    for doc, meta, score, mid in candidates:
        # A deleted record can linger in the HNSW index (e.g. deletes issued from
        # another process) and come back with None doc/metadata — skip it rather
        # than crash the whole search.
        if doc is None or meta is None:
            continue
        mem = _metadata_to_memory(doc, meta, mid)
        if mem.salience >= min_salience:
            output.append(SearchResult(memory=mem, score=score))

    ranked = sorted(output, key=lambda r: r.score * (0.5 + r.memory.salience), reverse=True)[:top_k]
    # Only count an access for memories actually returned, not every candidate fetched.
    for r in ranked:
        _increment_access(collection, r.memory.id)
    return ranked


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
    # Spread activation: search using the memory's own content as query,
    # within the memory's own namespace (not necessarily this process's).
    results = search(mem.content, top_k=top_k + 1, namespace=mem.namespace)
    return [r for r in results if r.memory.id != memory_id][:top_k]


def list_recent(
    memory_type: str | None = None,
    limit: int = 20,
    namespace: str | None = None,
) -> list[Memory]:
    collection = _get_collection()
    if collection.count() == 0:
        return []
    _ensure_namespace_backfill(collection)

    where = _combine_where(
        _namespace_clause(namespace),
        {"type": {"$eq": memory_type}} if memory_type else None,
    )
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


def count(namespace: str | None = "*") -> int:
    collection = _get_collection()
    clause = _namespace_clause(namespace)
    if clause is None:
        return collection.count()
    _ensure_namespace_backfill(collection)
    return len(collection.get(where=clause, include=[])["ids"])


def list_namespaces() -> dict[str, int]:
    """All namespaces present in the DB, with record counts."""
    collection = _get_collection()
    _ensure_namespace_backfill(collection)
    got = collection.get(include=["metadatas"])
    counts: dict[str, int] = {}
    for meta in got["metadatas"]:
        if meta is None:
            continue
        ns = meta.get("namespace", "default")
        counts[ns] = counts.get(ns, 0) + 1
    return dict(sorted(counts.items()))
