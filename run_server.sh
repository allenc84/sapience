#!/bin/bash
# Launch the claude-memory MCP server using the bundled venv (Python 3.12).
# Reads OPENAI_API_KEY (required, for embeddings) and ANTHROPIC_API_KEY (for context briefs) from the macOS Keychain.
export OPENAI_API_KEY=$(security find-generic-password -s "OPENAI_API_KEY" -a "claude-memory" -w 2>/dev/null)
export ANTHROPIC_API_KEY=$(security find-generic-password -s "ANTHROPIC_API_KEY" -a "claude-memory" -w 2>/dev/null)
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Load local config (e.g. MEMORY_USER_CONTEXT); also a fallback source for API keys.
[ -f "$DIR/.env" ] && set -a && . "$DIR/.env" && set +a
# Kill only orphaned server instances (parent exited, PID 1 adopted them)
ps -eo ppid,pid | awk 'NR>1 && $1==1 {print $2}' | while read pid; do
    cmd=$(ps -p "$pid" -o command= 2>/dev/null)
    [[ "$cmd" == *"sapience.server"* ]] && kill "$pid" 2>/dev/null
done
cd "$DIR"
exec "$DIR/venv/bin/python" -m sapience.server
