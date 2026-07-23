# Privacy

Sapience is self-hosted: there is no Sapience server, no Sapience account, and no data sent to Sapience or its author. Your memories and judgment ledger live in a local SQLite/Chroma database on your own machine.

**What leaves your machine, and to whom:**

- **OpenAI** — memory content is sent to OpenAI's embeddings API (`text-embedding-3` family) by default, to compute the semantic similarity Sapience uses for search and retrieval. Set `EMBEDDINGS_PROVIDER=local` to use a bundled local model instead — no content leaves your machine for embeddings, and no OpenAI key is needed.
- **Anthropic** — selected memories and resolved ledger entries are sent to the Claude API to generate context briefs, consolidated summaries, and calibration/bias-map narratives. There is currently no way to disable this — synthesis features require it.

Sapience sends data nowhere else. No telemetry, no analytics, no other third-party service.

**Your responsibility:** you provide your own OpenAI and Anthropic API keys and are bound by those providers' own privacy policies and terms for anything Sapience sends them. If that tradeoff doesn't work for what you're storing, don't point Sapience at it.

**Data retention and deletion:** everything lives in files on your machine (`SAPIENCE_DATA_DIR`, default an OS-standard per-user directory). Delete that directory to delete everything. `export_memories` and `delete_memory` let you back up or remove individual records without deleting everything.

This describes current (v0.1.1) behavior of the open-source package. It is not a legal contract — see LICENSE.
