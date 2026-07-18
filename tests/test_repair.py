"""Repair-tool tests: rebuild preserves every intact record and swaps safely."""

import pytest

from tests.test_memory_store import fake_embed


@pytest.fixture
def db(tmp_path, monkeypatch):
    from sapience import memory_store as M
    from sapience import repair as R

    path = tmp_path / "chroma_db"
    monkeypatch.setattr(M, "DB_PATH", path)
    monkeypatch.setattr(M, "embed", fake_embed)
    return M, R, path


def test_check_reports_healthy(db):
    M, R, path = db
    M.save("first memory", "episodic", salience=0.7, topic="t")
    M.save("second memory", "project", salience=0.4, topic="t")
    report = R.check(path)
    assert report["reported_count"] == 2
    assert report["intact_records"] == 2
    assert report["unreadable_records"] == 0
    assert report["hnsw_query_ok"] and report["filtered_query_ok"]
    assert not report["needs_rebuild"]


def test_rebuild_preserves_records_and_keeps_original(db):
    M, R, path = db
    saved = {
        M.save("first memory about launches", "episodic", salience=0.7, topic="t"),
        M.save("second memory about hiring", "project", salience=0.4, topic="t"),
        M.save("third memory about metrics", "semantic", salience=0.9, topic="m"),
    }
    result = R.rebuild(path)
    assert result["restored_records"] == 3
    assert result["dropped_unreadable"] == 0

    # Old dir preserved for forensics
    from pathlib import Path
    assert Path(result["old_dir"]).exists()

    # Rebuilt collection contains exactly the saved records, searchable
    rebuilt = R._open(path)
    got = rebuilt.get()
    assert set(got["ids"]) == saved
    results = M.search("first memory about launches", top_k=3)
    assert results and results[0].memory.content == "first memory about launches"


def test_rebuild_refuses_empty(db):
    M, R, path = db
    R._open(path)  # create empty collection
    with pytest.raises(RuntimeError, match="refusing"):
        R.rebuild(path)
