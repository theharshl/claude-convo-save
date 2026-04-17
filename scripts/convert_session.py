#!/usr/bin/env python3
"""
Claude Code session logger — converts JSONL transcripts to Obsidian markdown.

Commands:
  init <name> <type>     Create a new session folder and register it as pending.
  save --from-stdin      Read Stop-hook JSON from stdin and append new entries.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

OBSIDIAN_BASE = Path("/Users/lharsh/Obsidian/Landon/AI Managed/Conversations")
STATE_FILE = Path.home() / ".claude" / "session-logger" / "state.json"

# Tool names whose output we include for "script" project type
SCRIPT_TOOLS_INCLUDE = {"Bash", "Write", "Edit"}

# Tool names we always skip in output (internal/noisy)
TOOLS_SKIP = {"Read", "Glob", "Grep", "Agent", "TodoWrite", "TodoRead",
              "ToolSearch", "TaskCreate", "TaskUpdate", "TaskGet", "TaskList",
              "ListMcpResourcesTool", "ReadMcpResourceTool"}


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"sessions": {}, "pending_session": None}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def fmt_ts(ts_str: str) -> str:
    """Format an ISO timestamp into a readable local string."""
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts_str


def frontmatter(name: str, project_type: str, started: str) -> str:
    return (
        f"---\n"
        f"title: {name}\n"
        f"type: {project_type}\n"
        f"started: {started}\n"
        f"---\n\n"
        f"# {name}\n"
        f"*Started: {started}*\n"
    )


def ensure_md_file(session_info: dict) -> Path:
    """Create the markdown file (and parent folder) if either is missing."""
    md_path = Path(session_info["markdown_path"])
    md_path.parent.mkdir(parents=True, exist_ok=True)
    if not md_path.exists():
        started = session_info.get("created_at", datetime.now().isoformat())
        try:
            started = fmt_ts(started)
        except Exception:
            pass
        md_path.write_text(frontmatter(
            session_info["name"],
            session_info.get("project_type", "app"),
            started,
        ))
    return md_path


# ---------------------------------------------------------------------------
# JSONL → markdown conversion
# ---------------------------------------------------------------------------

def preprocess_entries(entries: list) -> list:
    """
    Merge queued_command attachments into their adjacent empty user entries.

    When the user sends a message while Claude is mid-response, Claude Code
    stores it as an empty ``user`` entry immediately followed by an
    ``attachment`` entry whose ``attachment.type`` is ``queued_command``.
    The actual text lives only in the attachment; the user entry is blank.
    This function fills that text back into the user entry and drops the
    now-redundant attachment.
    """
    result = []
    skip = set()
    for i, entry in enumerate(entries):
        if i in skip:
            continue
        if entry.get("type") == "user":
            msg = entry.get("message", {})
            content = msg.get("content")
            is_empty = not content or (isinstance(content, str) and not content.strip())
            if is_empty and i + 1 < len(entries):
                nxt = entries[i + 1]
                if (nxt.get("type") == "attachment"
                        and nxt.get("attachment", {}).get("type") == "queued_command"):
                    prompt = nxt["attachment"].get("prompt", "")
                    if prompt:
                        entry = dict(entry)
                        entry["message"] = dict(msg)
                        entry["message"]["content"] = prompt
                        skip.add(i + 1)
        result.append(entry)
    return result


def build_tool_id_map(entries: list) -> dict:
    """Return a mapping of tool_use_id → tool_name from all entries."""
    id_map = {}
    for entry in entries:
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message", {})
        for block in msg.get("content", []):
            if isinstance(block, dict) and block.get("type") == "tool_use":
                id_map[block["id"]] = block.get("name", "")
    return id_map


PREVIEW_LINES = 5


def _summarise_output(raw: str) -> str:
    """Return a fenced-code-block summary of tool output.

    Inline backtick spans break Obsidian rendering when the content itself
    contains backticks or spans multiple lines (CommonMark can misparse the
    closing backtick).  A fenced block is always safe.
    """
    lines = raw.splitlines()
    total = len(lines)
    preview = "\n".join(lines[:PREVIEW_LINES])
    suffix = f"\n*… ({total - PREVIEW_LINES} more lines)*" if total > PREVIEW_LINES else ""
    return f"\n```\n{preview}\n```{suffix}"


def _tables_to_lists(text: str) -> str:
    """Convert markdown pipe tables to structured key-value blocks."""

    def parse_row(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    def is_separator(line: str) -> bool:
        inner = line.strip().strip("|").strip()
        return bool(re.match(r'^[\s\-:|]+$', inner)) and '-' in inner

    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # A header row has pipes and the next line is a separator
        if ("|" in line and line.strip().startswith("|")
                and i + 1 < len(lines) and is_separator(lines[i + 1])):
            headers = parse_row(line)
            i += 2  # skip header and separator
            blocks: list[str] = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip().startswith("|"):
                cells = parse_row(lines[i])
                pairs = [
                    f"**{h}:** {v}"
                    for h, v in zip(headers, cells)
                    if h.strip() and v.strip()
                ]
                if pairs:
                    blocks.append("\n".join(pairs))
                i += 1
            out.append("\n\n".join(blocks))
        else:
            out.append(line)
            i += 1
    return "\n".join(out)


def entry_to_md(entry: dict, project_type: str, tool_id_map: dict) -> str | None:
    """
    Convert one JSONL entry to markdown.  Returns None if the entry should
    be silently skipped (e.g. metadata entries, empty messages).
    """
    etype = entry.get("type")

    # ---- user turn --------------------------------------------------------
    if etype == "user":
        msg = entry.get("message", {})
        content = msg.get("content")
        ts = fmt_ts(entry.get("timestamp", ""))

        # Plain text message — skip local-command noise injected by Claude Code UI
        if isinstance(content, str) and content.strip():
            stripped = content.strip()
            if stripped.startswith(("<local-command-caveat>", "<command-name>", "<local-command-stdout>")):
                return None
            return f"\n---\n\n## Human\n*{ts}*\n\n{stripped}\n"

        # Tool-result message
        if isinstance(content, list):
            parts = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_result":
                    continue

                tool_name = tool_id_map.get(block.get("tool_use_id", ""), "")

                # For app projects skip all tool output
                if project_type == "app":
                    continue
                # For script projects skip internal/noisy tools
                if tool_name in TOOLS_SKIP:
                    continue

                raw = block.get("content", "")
                if isinstance(raw, list):
                    raw = "\n".join(
                        b.get("text", "") if isinstance(b, dict) else str(b)
                        for b in raw
                    )
                raw = raw.strip()
                if raw:
                    parts.append(
                        f"\n**Tool output ({tool_name}):** {_summarise_output(raw)}"
                    )

            if parts:
                return f"\n---\n\n## Tool Results\n*{ts}*\n" + "\n".join(parts) + "\n"

        return None

    # ---- assistant turn ---------------------------------------------------
    if etype == "assistant":
        msg = entry.get("message", {})
        content = msg.get("content", [])
        ts = fmt_ts(entry.get("timestamp", ""))

        text_parts = []
        tool_parts = []

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")

            if btype == "text":
                t = block.get("text", "").strip()
                if t:
                    text_parts.append(t)

            elif btype == "tool_use" and project_type == "script":
                tool_name = block.get("name", "")
                if tool_name in TOOLS_SKIP:
                    continue
                inp = block.get("input", {})
                if tool_name == "Bash":
                    cmd = inp.get("command", "").strip()
                    desc = inp.get("description", "")
                    desc_str = f" — {desc}" if desc else ""
                    tool_parts.append(
                        f"\n**Bash{desc_str}**\n```bash\n{cmd}\n```"
                    )
                elif tool_name in ("Write", "Edit"):
                    fp = inp.get("file_path", "")
                    tool_parts.append(f"\n**{tool_name}** → `{fp}`")
                else:
                    # Other tool (e.g. WebSearch)
                    tool_parts.append(f"\n**Tool: {tool_name}**")

        if not text_parts and not tool_parts:
            return None

        lines = [f"\n---\n\n## Assistant\n*{ts}*\n"]
        if text_parts:
            lines.append(_tables_to_lists("\n\n".join(text_parts)))
        if tool_parts:
            lines.extend(tool_parts)

        return "\n".join(lines) + "\n"

    # Everything else (metadata, system, etc.) is skipped
    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_init(name: str, project_type: str) -> None:
    state = load_state()

    ts = datetime.now()
    ts_folder = ts.strftime("%Y-%m-%d_%H-%M")
    ts_human = ts.strftime("%Y-%m-%d %H:%M:%S")

    # Sanitise name for filesystem use
    safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip()
    folder_path = OBSIDIAN_BASE / safe_name
    md_file = folder_path / f"conversation_{ts_folder}.md"

    folder_path.mkdir(parents=True, exist_ok=True)
    md_file.write_text(frontmatter(name, project_type, ts_human))

    state["pending_session"] = {
        "name": name,
        "project_type": project_type,
        "folder_path": str(folder_path),
        "markdown_path": str(md_file),
        "last_written_index": 0,
        "created_at": ts.isoformat(),
    }
    save_state(state)
    print(f"Session '{name}' initialised → {folder_path}")


def _inject_custom_title(transcript_path: str, session_id: str, name: str) -> None:
    """Append a custom-title entry to the JSONL so `claude --resume` shows the name."""
    try:
        entry = json.dumps({"type": "custom-title", "customTitle": name, "sessionId": session_id})
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def cmd_save(session_id: str, transcript_path: str) -> None:
    state = load_state()
    sessions = state.setdefault("sessions", {})

    # Look up known session or claim a pending one
    session_info = sessions.get(session_id)

    if not session_info:
        pending = state.get("pending_session")
        if pending:
            try:
                age = (datetime.now() - datetime.fromisoformat(pending["created_at"])).total_seconds()
                if age <= 3600:  # claim if created within the last hour
                    session_info = pending
                    sessions[session_id] = session_info
                    state["pending_session"] = None
                    save_state(state)
                    # Inject a custom-title entry so `claude --resume` shows the name
                    _inject_custom_title(transcript_path, session_id, session_info["name"])
            except Exception:
                pass

    if not session_info:
        return  # session not named yet — nothing to write

    # Read JSONL
    jfile = Path(transcript_path)
    if not jfile.exists():
        return

    entries = []
    try:
        with open(jfile) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except Exception:
        return

    entries = preprocess_entries(entries)

    last_idx = session_info.get("last_written_index", 0)
    new_entries = entries[last_idx:]
    if not new_entries:
        return

    project_type = session_info.get("project_type", "app")
    tool_id_map = build_tool_id_map(entries)  # build from full history

    md_path = ensure_md_file(session_info)

    chunks = []
    for entry in new_entries:
        md = entry_to_md(entry, project_type, tool_id_map)
        if md:
            chunks.append(md)

    if chunks:
        with open(md_path, "a", encoding="utf-8") as f:
            f.write("".join(chunks))

    session_info["last_written_index"] = len(entries)
    sessions[session_id] = session_info
    save_state(state)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Claude Code → Obsidian logger")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Initialise a new session")
    p_init.add_argument("name", help="Session name")
    p_init.add_argument("type", choices=["script", "app"], help="Project type")

    p_save = sub.add_parser("save", help="Append new entries to markdown")
    p_save.add_argument("--from-stdin", action="store_true",
                        help="Read Stop-hook JSON payload from stdin")
    p_save.add_argument("--session-id", help="Session ID (alternative to --from-stdin)")
    p_save.add_argument("--transcript", help="JSONL path (alternative to --from-stdin)")

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args.name, args.type)

    elif args.command == "save":
        if args.from_stdin:
            try:
                payload = json.loads(sys.stdin.read())
                sid = payload.get("session_id")
                tp = payload.get("transcript_path")
            except Exception:
                return
        else:
            sid = args.session_id
            tp = args.transcript

        if sid and tp:
            cmd_save(sid, tp)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
