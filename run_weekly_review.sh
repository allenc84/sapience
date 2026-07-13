#!/bin/bash
# Weekly judgment ledger review — called from Stop hook, runs in background.
source ~/.zshrc 2>/dev/null || true
export ANTHROPIC_API_KEY=$(security find-generic-password -s "ANTHROPIC_API_KEY" -a "claude-memory" -w 2>/dev/null)
if [ -z "$ANTHROPIC_API_KEY" ]; then
    exit 0
fi
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$DIR/.env" ] && set -a && . "$DIR/.env" && set +a
cd "$DIR"
"$DIR/venv/bin/python" "$DIR/weekly_review.py" >> /tmp/weekly_review.log 2>&1 || exit 0
