"""Offline check and repair for a drifted Chroma DB.

The HNSW vector index and the sqlite metadata store live side by side in the
Chroma directory. If the directory is mutated by a second process while a
server holds it open, the two drift apart: queries fail internally
("Error finding id") or return ghost rows with None payloads. The metadata
store is the durable source of truth, so repair means: read every intact
record out of it and rebuild a fresh collection.

Usage (with the MCP server STOPPED — a live server will silently recorrupt
the rebuilt index):

    python -m sapience.repair --check
    python -m sapience.repair --rebuild --server-stopped

--check is read-only and safe anytime. --rebuild refuses to run without
--server-stopped, writes the new collection next to the old one, then swaps:
the old directory is kept as chroma_db.corrupt-<timestamp>.
"""

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .memory_store import DB_PATH

COLLECTION = "memories"
BATCH = 100


def _open(path: Path) -> chromadb.Collection:
    client = chromadb.PersistentClient(
        path=str(path), settings=Settings(anonymized_telemetry=False)
    )
    return client.get_or_create_collection(
        name=COLLECTION, metadata={"hnsw:space": "cosine"}
    )


def _read_all(collection: chromadb.Collection) -> tuple[list[dict], int]:
    """Read every record out of a possibly-corrupted collection.

    documents/metadatas live in sqlite and survive vector-index drift, so they
    are read first, in batches. Embeddings live in the vector segment and are
    salvaged per-id (see memory_store._get_embeddings_by_id); a record whose
    embedding is unrecoverable is returned with embedding=None so the caller
    can re-embed or quarantine it.

    Returns (records, unreadable_count) where unreadable means document or
    metadata itself was missing.
    """
    from .memory_store import _get_embeddings_by_id

    records, dropped, offset = [], 0, 0
    while True:
        got = collection.get(
            limit=BATCH,
            offset=offset,
            include=["documents", "metadatas"],
        )
        ids = got["ids"]
        if not ids:
            break
        embeddings = _get_embeddings_by_id(collection, ids)
        for i, mid in enumerate(ids):
            doc = got["documents"][i]
            meta = got["metadatas"][i]
            if doc is None or meta is None:
                dropped += 1
                continue
            records.append(
                {"id": mid, "document": doc, "metadata": meta,
                 "embedding": embeddings.get(mid)}
            )
        offset += len(ids)
    return records, dropped


def check(db_path: Path = DB_PATH) -> dict:
    """Read-only health report: metadata-store contents vs HNSW query behavior."""
    collection = _open(db_path)
    count = collection.count()
    records, dropped = _read_all(collection)

    probe = next((r["embedding"] for r in records if r["embedding"] is not None), None)

    query_ok, query_error = True, ""
    if probe is not None:
        try:
            collection.query(
                query_embeddings=[probe],
                n_results=min(10, count),
                include=["documents"],
            )
        except Exception as e:
            query_ok, query_error = False, str(e)

    filtered_ok, filtered_error = True, ""
    if probe is not None:
        try:
            collection.query(
                query_embeddings=[probe],
                n_results=min(10, count),
                where={"type": {"$in": ["episodic", "semantic"]}},
                include=["documents"],
            )
        except Exception as e:
            filtered_ok, filtered_error = False, str(e)

    missing_embeddings = sum(1 for r in records if r["embedding"] is None)
    return {
        "db_path": str(db_path),
        "reported_count": count,
        "intact_records": len(records),
        "unreadable_records": dropped,
        "missing_embeddings": missing_embeddings,
        "hnsw_query_ok": query_ok,
        "hnsw_query_error": query_error,
        "filtered_query_ok": filtered_ok,
        "filtered_query_error": filtered_error,
        "needs_rebuild": bool(
            dropped or missing_embeddings or not query_ok or not filtered_ok
            or count != len(records)
        ),
    }


def rebuild(db_path: Path = DB_PATH, re_embed_all: bool = False) -> dict:
    """Rebuild the collection from intact metadata-store records and swap dirs.

    The old directory survives as <db_path>.corrupt-<timestamp>; nothing is
    deleted. Aborts (removing only its own partial output) if verification of
    the rebuilt collection fails.

    re_embed_all discards every salvaged embedding and regenerates them with
    the CURRENT provider — the migration path when switching
    EMBEDDINGS_PROVIDER/EMBEDDINGS_MODEL, since dimensions differ between
    providers and a collection can't mix them.
    """
    source = _open(db_path)
    records, dropped = _read_all(source)
    if not records:
        raise RuntimeError("No intact records found — refusing to rebuild to empty.")

    if re_embed_all:
        for r in records:
            r["embedding"] = None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    # Records whose embeddings were unrecoverable: re-embed when a key is
    # available, otherwise quarantine to JSONL next to the DB — never silently drop.
    missing = [r for r in records if r["embedding"] is None]
    re_embedded, quarantined = 0, 0
    if missing:
        try:
            from .embeddings import embed
            for r in missing:
                r["embedding"] = embed(r["document"])
                re_embedded += 1
        except Exception:
            still_missing = [r for r in missing if r["embedding"] is None]
            quarantine_path = db_path.parent / f"{db_path.name}.quarantine-{stamp}.jsonl"
            with open(quarantine_path, "w") as f:
                for r in still_missing:
                    f.write(json.dumps({"id": r["id"], "document": r["document"],
                                        "metadata": r["metadata"]}) + "\n")
            quarantined = len(still_missing)
            records = [r for r in records if r["embedding"] is not None]
            if not records:
                raise RuntimeError(
                    f"All embeddings unrecoverable and re-embedding failed; "
                    f"records preserved in {quarantine_path}."
                )

    fresh_path = db_path.parent / f"{db_path.name}.rebuild-{stamp}"
    fresh = _open(fresh_path)
    for i in range(0, len(records), BATCH):
        chunk = records[i : i + BATCH]
        fresh.add(
            ids=[r["id"] for r in chunk],
            documents=[r["document"] for r in chunk],
            metadatas=[r["metadata"] for r in chunk],
            embeddings=[r["embedding"] for r in chunk],
        )

    # Verify before swapping: counts match and both query paths work.
    ok = fresh.count() == len(records)
    if ok:
        try:
            fresh.query(query_embeddings=[records[0]["embedding"]], n_results=5)
            fresh.query(
                query_embeddings=[records[0]["embedding"]],
                n_results=5,
                where={"type": {"$in": ["episodic", "semantic", "project"]}},
            )
        except Exception:
            ok = False
    if not ok:
        shutil.rmtree(fresh_path, ignore_errors=True)
        raise RuntimeError("Rebuilt collection failed verification — original left untouched.")

    corrupt_path = db_path.parent / f"{db_path.name}.corrupt-{stamp}"
    db_path.rename(corrupt_path)
    fresh_path.rename(db_path)
    return {
        "restored_records": len(records),
        "dropped_unreadable": dropped,
        "re_embedded": re_embedded,
        "quarantined": quarantined,
        "old_dir": str(corrupt_path),
        "db_path": str(db_path),
    }


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="read-only health report")
    action.add_argument("--rebuild", action="store_true", help="rebuild collection and swap")
    parser.add_argument(
        "--server-stopped",
        action="store_true",
        help="assert the MCP server is not running (required for --rebuild)",
    )
    parser.add_argument(
        "--re-embed",
        action="store_true",
        help="regenerate ALL embeddings with the current provider "
             "(use when switching EMBEDDINGS_PROVIDER/EMBEDDINGS_MODEL)",
    )
    args = parser.parse_args()

    if args.check:
        report = check()
        for k, v in report.items():
            print(f"{k}: {v}")
        sys.exit(0 if not report["needs_rebuild"] else 1)

    if not args.server_stopped:
        parser.error(
            "--rebuild mutates the live DB dir and must not run while the MCP "
            "server is up. Stop the server (quit Claude Code sessions using it), "
            "then re-run with --server-stopped."
        )
    result = rebuild(re_embed_all=args.re_embed)
    for k, v in result.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    cli()
