"""End-to-end: stdio relays start one shared server on demand and share it."""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "test", "version": "1"}}}
INITIALIZED = {"jsonrpc": "2.0", "method": "notifications/initialized"}
STATS = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "memory_stats", "arguments": {}}}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def env(tmp_path):
    port = _free_port()
    e = {**os.environ, "SAPIENCE_DATA_DIR": str(tmp_path), "SAPIENCE_PORT": str(port),
         "EMBEDDINGS_PROVIDER": "local"}
    e.pop("MEMORY_DB_PATH", None)
    yield e, port
    try:
        pid = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).json()["pid"]
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def _session(env: dict, *messages: dict) -> list[dict]:
    """Run one relay like Claude Code would; return the responses it emits."""
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    out = subprocess.run(
        [sys.executable, "-m", "sapience.server"], input=stdin, env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    return [json.loads(line) for line in out.stdout.splitlines()]


def _health(port: int) -> dict:
    return httpx.get(f"http://127.0.0.1:{port}/health", timeout=2).json()


def test_relay_starts_server_and_sessions_share_it(env):
    e, port = env
    first = _session(e, INIT, INITIALIZED, STATS)
    assert [r["id"] for r in first] == [1, 2]
    stats = json.loads(first[1]["result"]["content"][0]["text"])
    assert stats["total"] == 0
    pid = _health(port)["pid"]
    assert _health(port)["managed"] is True

    second = _session(e, INIT, STATS)
    assert [r["id"] for r in second] == [1, 2]
    assert _health(port)["pid"] == pid  # reused, not a second writer


def test_each_relay_keeps_its_own_namespace(env):
    e, port = env
    list_tools = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}

    def save(i, text):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": "save_memory",
                           "arguments": {"content": text, "memory_type": "project"}}}

    _session({**e, "SAPIENCE_NAMESPACE": "alpha"}, INIT, list_tools, save(3, "alpha note"))
    _session({**e, "SAPIENCE_NAMESPACE": "beta"}, INIT, list_tools, save(3, "beta note"))
    out = _session(e, INIT, STATS)
    stats = json.loads(out[1]["result"]["content"][0]["text"])
    assert stats["by_namespace"] == {"alpha": 1, "beta": 1}
    assert stats["active_namespace"] == "default"  # server didn't adopt a session's


def test_relay_restarts_a_dead_server(env):
    e, port = env
    _session(e, INIT, STATS)
    old = _health(port)["pid"]
    os.kill(old, signal.SIGKILL)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            _health(port)
            time.sleep(0.2)
        except Exception:
            break
    again = _session(e, INIT, STATS)
    assert [r["id"] for r in again] == [1, 2]
    assert _health(port)["pid"] != old
