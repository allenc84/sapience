# claude-memory

A persistent, semantic memory system for [Claude Code](https://claude.com/claude-code), exposed as an [MCP](https://modelcontextprotocol.io) server. Instead of re-reading flat markdown files each session, Claude searches a vector database by meaning — surfacing prior decisions, evolved thinking, and relevant context automatically.

It also includes a **judgment ledger**: log predictions and assessments, resolve them later against reality, and generate calibration patterns and bias maps so the assistant learns where your judgment is systematically off.

## How it works

- **Storage** — [ChromaDB](https://www.trychroma.com/) vector store on disk (`./chroma_db`).
- **Embeddings** — OpenAI (`text-embedding-3` family) for semantic similarity.
- **Synthesis** — Anthropic Claude for context briefs, consolidation, calibration, and bias maps.
- **Retrieval** — memories are ranked by relevance × salience, so important context surfaces first.

Memories are typed: `episodic` (events/decisions), `semantic` (extracted patterns, usually written by consolidation), `user` (facts about you), `feedback` (how to work with you), `project` (ongoing initiatives), `reference` (pointers to external systems).

## Tools

**Memory**
- `search_memory` — semantic search over all memories
- `save_memory` — persist a decision, insight, or piece of context
- `get_context_brief` — Claude-synthesized brief on a topic (what's known, how thinking evolved, what to challenge)
- `get_related` — memories related to a given one
- `consolidate` — extract durable semantic patterns from recent episodes
- `list_memories`, `memory_stats` — inspect the store

**Judgment ledger**
- `log_assessment` — record a prediction with confidence, horizon, and reasoning
- `list_pending_assessments` — assessments awaiting resolution
- `resolve_assessment` — score what actually happened (right / partial / wrong)
- `generate_calibration` — extract a calibration pattern for a domain (needs 3+ resolved)
- `get_bias_map` — cross-domain map of where judgment is strong vs. poor

## Setup

Requires Python 3.12+.

```bash
git clone https://github.com/allenc84/claude-memory.git
cd claude-memory
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env   # then edit .env
```

Set your keys and persona in `.env` (see `.env.example`):

```
MEMORY_USER_CONTEXT="Jane Doe, founder of Acme"
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
```

> On macOS, `run_server.sh` reads the API keys from the Keychain if present, falling back to `.env`:
> ```
> security add-generic-password -U -s "OPENAI_API_KEY"    -a "claude-memory" -w 'sk-proj-...'
> security add-generic-password -U -s "ANTHROPIC_API_KEY" -a "claude-memory" -w 'sk-ant-...'
> ```
> Pass the key as the `-w` argument, not via the interactive prompt — the prompt truncates at 128 characters and silently corrupts longer keys.

### Wire into Claude Code

Add to your MCP config (e.g. `~/.claude.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "claude-memory": {
      "command": "/absolute/path/to/claude-memory/run_server.sh"
    }
  }
}
```

Restart Claude Code. The server caches keys and config at launch, so restart after changing either.

### The `/log` command

`.claude/commands/log.md` provides a `/log` slash command for the judgment ledger — logging, reviewing, resolving, and generating calibrations/bias maps in natural language. Copy it into your project's `.claude/commands/` to use it.

## Automation (optional)

- `run_consolidate.sh` — nightly: extract semantic patterns from recent episodes. Schedule via cron/launchd.
- `run_weekly_review.sh` — weekly judgment-ledger review; designed to be triggered from a Claude Code Stop hook.

## Migrating existing markdown memories

To import legacy flat-file memories into the vector store:

```bash
MEMORY_MIGRATE_DIR="$HOME/path/to/memory" ./venv/bin/python migrate.py
```

## License

MIT — see [LICENSE](LICENSE).
