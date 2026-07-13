#!/bin/bash
# Nightly consolidation job — retrieves API key from keychain at runtime
source ~/.zshrc 2>/dev/null || true
export ANTHROPIC_API_KEY=$(security find-generic-password -s "ANTHROPIC_API_KEY" -a "claude-memory" -w 2>/dev/null)
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo "ERROR: ANTHROPIC_API_KEY not found in keychain" >&2
    exit 1
fi
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$DIR/.env" ] && set -a && . "$DIR/.env" && set +a
cd "$DIR"
exec "$DIR/venv/bin/python" "$DIR/consolidator.py"
