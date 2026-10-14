"""Single-writer lock: a second process must refuse to open a held Chroma dir."""

import subprocess
import sys
import textwrap


def _try_lock_in_subprocess(path) -> subprocess.CompletedProcess:
    code = textwrap.dedent(f"""
        from pathlib import Path
        from sapience import memory_store as M
        M.DB_PATH = Path({str(path)!r})
        M.acquire_db_lock()
        print("acquired")
    """)
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)


def test_second_process_is_refused(tmp_path, monkeypatch):
    from sapience import memory_store as M

    monkeypatch.setattr(M, "DB_PATH", tmp_path / "chroma_db")
    monkeypatch.setattr(M, "_lock_fd", None)
    M.acquire_db_lock()
    try:
        result = _try_lock_in_subprocess(tmp_path / "chroma_db")
        assert result.returncode != 0
        assert "Another sapience process already owns" in result.stderr
    finally:
        M._lock_fd.close()


def test_lock_is_released_when_holder_exits(tmp_path):
    first = _try_lock_in_subprocess(tmp_path / "chroma_db")
    second = _try_lock_in_subprocess(tmp_path / "chroma_db")
    assert "acquired" in first.stdout
    assert "acquired" in second.stdout


def test_reacquire_in_same_process_is_noop(tmp_path, monkeypatch):
    from sapience import memory_store as M

    monkeypatch.setattr(M, "DB_PATH", tmp_path / "chroma_db")
    monkeypatch.setattr(M, "_lock_fd", None)
    M.acquire_db_lock()
    held = M._lock_fd
    M.acquire_db_lock()
    assert M._lock_fd is held
    held.close()
