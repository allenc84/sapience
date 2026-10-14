#!/bin/bash
# Launch Sapience using the bundled venv (Python 3.12). With no args this is the
# per-session stdio relay, which starts the shared server on demand; pass
# --http to run the shared server itself.
# Reads OPENAI_API_KEY (required, for embeddings) and ANTHROPIC_API_KEY (for context briefs) from the macOS Keychain.
export OPENAI_API_KEY=$(security find-generic-password -s "OPENAI_API_KEY" -a "claude-memory" -w 2>/dev/null)
export ANTHROPIC_API_KEY=$(security find-generic-password -s "ANTHROPIC_API_KEY" -a "claude-memory" -w 2>/dev/null)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Load local config (e.g. MEMORY_USER_CONTEXT); also a fallback source for API keys.
[ -f "$DIR/.env" ] && set -a && . "$DIR/.env" && set +a
cd "$DIR"
exec "$DIR/venv/bin/python" -m sapience.server "$@"
