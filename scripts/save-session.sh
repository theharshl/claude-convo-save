#!/bin/bash
# Claude Code Stop hook — appends new conversation entries to Obsidian markdown.
# Receives a JSON payload on stdin from Claude Code (contains session_id and
# transcript_path).  Exits silently on any error so it never disrupts the session.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Brief pause so Claude Code finishes flushing the final assistant entry before we read.
sleep 2

python3 "$SCRIPT_DIR/convert_session.py" save --from-stdin 2>/dev/null || true
