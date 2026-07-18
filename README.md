# Sapience

**Human-like memory and a judgment ledger for AI** — an [MCP](https://modelcontextprotocol.io) server for [Claude Code](https://claude.com/claude-code).

An LLM has *intelligence* — it processes and analyzes brilliantly — but it's amnesiac between sessions and never accumulates *your* experience. Humans win on something else: memory that persists and judgment that gets sharper because we keep track of how our past calls turned out. That faculty — the one that makes *Homo sapiens* more than raw brainpower — is what Sapience adds to your AI.

Two halves:

- **A human-like memory** — episodic and semantic memories, ranked by importance, consolidated over time into durable patterns. Not RAG over a scratch file.
- **A judgment ledger** — log a prediction with a probability, resolve it against what actually happened, and get a real calibration read (Brier score, reliability by confidence band, a bias map) so you can see where your judgment is systematically off.

> Sapience gives *one user's* AI a compounding memory + judgment loop. It is not a claim to reproduce human cognition — it's the missing feedback loop that lets an intelligence learn from experience.

## The judgment ledger

This is the part you won't find in other memory tools. Every "AI memory" remembers what you said; Sapience keeps score of whether you were *right*.

1. **Log** a forward-looking call with a probability (0–1) and — crucially — the *reasoning and conditions as they were at the time*. Most retrospectives rewrite history; this preserves the contemporaneous evidence.
2. **Resolve** it when the outcome is known (right / partial / wrong).
3. **Calibrate.** Sapience computes a **Brier score** against a base-rate baseline, breaks accuracy down by confidence band, and flags over/under-confidence. A Claude-written narrative sits *on top of* the numbers — never instead of them.

**Honesty by design:** below a sample threshold (20 resolved by default), Sapience refuses to call anything a "bias" and explicitly labels its output *"reflection, not statistics."* A bias is not a bias at n=3.

## How the memory works

- **Storage** — a local [ChromaDB](https://www.trychroma.com/) vector store; the ledger is local SQLite. No third-party SaaS account.
- **Embeddings** — OpenAI (`text-embedding-3` family) for semantic similarity.
- **Synthesis** — Anthropic Claude for context briefs, consolidation, calibration, and bias maps.
- **Retrieval** — candidates are over-fetched by similarity, then reranked by `similarity × salience`, so an important-but-slightly-less-similar memory can still surface.

Memory types: `episodic` (events/decisions), `semantic` (patterns, written by consolidation), `user` (facts about you), `feedback` (how to work with you), `project` (initiatives), `reference` (external pointers).

### Privacy — read this precisely

Your data is stored **locally** (vector DB + SQLite on your machine; no hosted account). But Sapience is **not** fully local compute: memory **content is sent to OpenAI** to create embeddings, and **selected memories are sent to Anthropic** for briefs, consolidation, and calibration. If that tradeoff doesn't work for your data, don't point Sapience at it.

## Tools

**Memory** — `search_memory`, `save_memory`, `get_context_brief`, `get_related`, `consolidate`, `list_memories`, `memory_stats`

**Memory admin** — `get_memory` (inspect by id), `edit_memory` (fix content/salience/topic/type in place, re-embeds automatically), `delete_memory`, `export_memories` (JSONL backup), `find_duplicate_memories` (report-only — nothing is auto-deleted)

**Judgment ledger** — `log_assessment` (prefer a numeric `probability`), `list_pending_assessments`, `resolve_assessment`, `generate_calibration` (Brier + reliability, gated for sufficiency), `get_bias_map`

## Setup

Requires Python 3.12+.

```bash
git clone https://github.com/allenc84/sapience.git
cd sapience
python3.12 -m venv venv
./venv/bin/pip install -e .
cp .env.example .env   # then edit
```

Configure `.env` (see `.env.example`):

```
MEMORY_USER_CONTEXT="Jane Doe, founder of Acme"   # who the memory serves
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
# Optional:
LEDGER_DOMAINS="predictions,decisions,commitments" # your judgment domains
SAPIENCE_DATA_DIR=/absolute/path/to/data           # defaults to a per-user OS dir
SAPIENCE_NAMESPACE=work                            # memory namespace (default: "default")
```

### Namespaces

Memories are partitioned by **namespace** — set `SAPIENCE_NAMESPACE` per project/workspace (e.g. in a project's `.mcp.json` `env` block) to keep contexts separate inside one database. Reads and writes default to the server's namespace; pass `namespace: "*"` to `search_memory`/`list_memories` to read across all of them, and `memory_stats` shows the per-namespace breakdown. Records created before namespaces existed are stamped `default` automatically on first read. The judgment ledger is deliberately **not** namespaced — your track record is yours, not a project's.

> **macOS Keychain (optional):** the `run_*.sh` scripts read keys from the Keychain if present, falling back to `.env`. Store keys as the `-w` **argument**, never via the interactive prompt — the prompt truncates at 128 chars and silently corrupts longer keys:
> ```
> security add-generic-password -U -s "OPENAI_API_KEY" -a "claude-memory" -w 'sk-proj-...'
> ```

### Install as a Claude Code plugin (easiest)

With [uv](https://docs.astral.sh/uv/) installed and `OPENAI_API_KEY` + `ANTHROPIC_API_KEY` in your environment:

```
/plugin marketplace add allenc84/sapience
/plugin install sapience@sapience
```

This wires up everything below in one step: the MCP server (launched via `uvx`, no manual install), the `/sapience:log` judgment-ledger command, and a session-stop hook that runs the weekly ledger review (self-gated to once every 6 days). Configuration still comes from your environment — set `MEMORY_USER_CONTEXT`, `LEDGER_DOMAINS`, or `SAPIENCE_DATA_DIR` there if you want non-defaults.

### Wire into Claude Code manually

Add to your MCP config (`~/.claude.json` or project `.mcp.json`):

```json
{
  "mcpServers": {
    "sapience": {
      "command": "/absolute/path/to/sapience/run_server.sh"
    }
  }
}
```

Or, with the package installed, point directly at the console script / module:

```json
{ "mcpServers": { "sapience": {
  "command": "/absolute/path/to/sapience/venv/bin/python",
  "args": ["-m", "sapience.server"],
  "env": { "SAPIENCE_DATA_DIR": "/absolute/path/to/data" }
} } }
```

Restart Claude Code. The server reads keys and config at launch — restart after changing either.

### The `/log` command

`.claude/commands/log.md` provides a `/log` slash command for the ledger — logging, reviewing, resolving, and generating calibrations/bias maps in natural language. Copy it into your project's `.claude/commands/`.

## Automation (optional)

- `run_consolidate.sh` — nightly: extract semantic patterns from recent episodes (cron/launchd).
- `run_weekly_review.sh` — weekly ledger review; designed for a Claude Code Stop hook.

## Try it on demo data

Don't want to point Sapience at real data yet? Seed a fictional founder's dataset — 21 memories and a 30-call judgment ledger with a real calibration story for the bias map to find (overconfident on product bets, calibrated on hiring, underconfident on growth):

```bash
OPENAI_API_KEY=... sapience-demo --dir ./sapience-demo-data
```

It prints the MCP config to paste, plus a 4-step demo flow. Everything is fictional; the target directory must be new or empty.

## Migrating existing markdown memories

```bash
MEMORY_MIGRATE_DIR="$HOME/path/to/memory" ./venv/bin/python -m sapience.migrate
```

## License

MIT — see [LICENSE](LICENSE).
