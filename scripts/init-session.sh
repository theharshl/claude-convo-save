#!/bin/bash
# Initialise a Claude Code logging session.
# Called by Claude when the user names a new (or previously un-logged) session.
#
# Usage: init-session.sh "Session Name" "script|app"

set -euo pipefail

SESSION_NAME="${1:-}"
PROJECT_TYPE="${2:-app}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "$SESSION_NAME" ]]; then
    echo "Usage: init-session.sh \"Session Name\" \"script|app\"" >&2
    exit 1
fi

python3 "$SCRIPT_DIR/convert_session.py" init "$SESSION_NAME" "$PROJECT_TYPE"
