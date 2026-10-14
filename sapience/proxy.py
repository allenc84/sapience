"""stdio <-> shared-HTTP-server relay — what each Claude Code session launches.

Chroma's on-disk index is single-writer, so sessions must not each run their
own server against the same DB. Instead every session runs this relay: it
finds the one shared `sapience --http` server on localhost (starting it in
the background if none is up) and forwards MCP JSON-RPC between the
session's stdio and that server. The server outlives the sessions and is the
only process that ever opens the DB.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time

import httpx

from .paths import data_dir

HOST = "127.0.0.1"
PORT = int(os.environ.get("SAPIENCE_PORT", 8765))
BASE = f"http://{HOST}:{PORT}"
START_TIMEOUT_S = 60  # first start imports chromadb and may load the index


def _version() -> str:
    from . import __version__
    return __version__


def _health() -> dict | None:
    try:
        r = httpx.get(f"{BASE}/health", timeout=2)
        r.raise_for_status()
        info = r.json()
        return info if info.get("service") == "sapience" else None
    except Exception:
        return None


def _log(msg: str) -> None:
    print(f"sapience-proxy: {msg}", file=sys.stderr, flush=True)


def _spawn_server() -> None:
    log = open(data_dir() / "server.log", "a")
    subprocess.Popen(
        [sys.executable, "-m", "sapience.server", "--http", "--host", HOST, "--port", str(PORT)],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
        start_new_session=True,  # survive this session's exit
        # The server is shared, so it must not adopt whichever session's
        # namespace happened to start it; relays pass their own per call.
        env={**{k: v for k, v in os.environ.items() if k != "SAPIENCE_NAMESPACE"},
             "SAPIENCE_MANAGED": "1"},
    )


def ensure_server() -> None:
    """Make sure a compatible shared server is answering on PORT."""
    info = _health()
    if info and info.get("managed") and info.get("version") != _version():
        # A server this relay family started, from an older/newer install:
        # replace it so tool schemas match the installed package.
        _log(f"replacing server v{info.get('version')} with v{_version()}")
        try:
            os.kill(int(info["pid"]), signal.SIGTERM)
        except (ProcessLookupError, KeyError, ValueError):
            pass
        deadline = time.monotonic() + 10
        while _health() and time.monotonic() < deadline:
            time.sleep(0.2)
        info = None
    if info:
        return  # user-managed servers (e.g. a LaunchAgent) are used as-is

    # Several sessions may race to start it; the DB lock lets exactly one
    # server win and the rest exit, so everyone just waits for /health.
    _spawn_server()
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _health():
            return
        time.sleep(0.3)
    raise RuntimeError(
        f"shared sapience server did not come up on {BASE}; "
        f"see {data_dir() / 'server.log'}"
    )


async def _relay() -> None:
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
    write_lock = asyncio.Lock()
    # This session's namespace (e.g. from a project .mcp.json env block),
    # applied to every namespace-aware tool call that doesn't name one.
    namespace = os.environ.get("SAPIENCE_NAMESPACE")
    namespaced_tools: set[str] = set()

    async def emit(obj) -> None:
        async with write_lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    async def post(client: httpx.AsyncClient, line: bytes) -> httpx.Response:
        return await client.post(
            f"{BASE}/mcp/", content=line,
            headers={"Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream"},
        )

    async def forward(client: httpx.AsyncClient, line: bytes) -> None:
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            return
        params = msg.get("params") or {}
        if (namespace and msg.get("method") == "tools/call"
                and params.get("name") in namespaced_tools):
            args = params.setdefault("arguments", {})
            if "namespace" not in args:
                args["namespace"] = namespace
                line = json.dumps(msg).encode()
        try:
            try:
                r = await post(client, line)
            except httpx.ConnectError:
                # Server went away mid-session (crash, upgrade): bring it
                # back and retry once. The server is stateless, so nothing
                # about this session is lost.
                await asyncio.to_thread(ensure_server)
                r = await post(client, line)
            if r.status_code == 202 or not r.content:
                return  # notification / response acknowledged
            r.raise_for_status()
            await emit(r.json())
        except Exception as e:
            if "id" in msg:
                await emit({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32603, "message": f"sapience server unreachable: {e}"}})

    tasks: set[asyncio.Task] = set()
    async with httpx.AsyncClient(timeout=httpx.Timeout(10, read=300)) as client:
        if namespace:
            # Learn which tools take a namespace before forwarding anything,
            # so injection doesn't race the session's own tools/list.
            r = await post(client, json.dumps(
                {"jsonrpc": "2.0", "id": "ns-probe", "method": "tools/list"}).encode())
            for tool in r.json().get("result", {}).get("tools", []):
                if "namespace" in (tool.get("inputSchema") or {}).get("properties", {}):
                    namespaced_tools.add(tool["name"])
        while line := await reader.readline():
            if not line.strip():
                continue
            # Concurrent: a slow tool (context briefs take ~15s) must not
            # block other requests from the same session.
            t = asyncio.create_task(forward(client, line))
            tasks.add(t)
            t.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks)


def main() -> None:
    ensure_server()
    asyncio.run(_relay())
