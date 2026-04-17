# claude-convo-save

Automatically logs every Claude Code session to an Obsidian vault as clean Markdown files.

## How it works

- **At session start**, Claude asks you to name the session and choose a project type (`script` or `app`).
- **After every response**, a Stop hook reads the raw JSONL transcript Claude Code maintains and appends only new content to the Obsidian note.
- **On resume**, if the session was never logged, Claude prompts for a name and backfills the full conversation.

## Project types

| Type | What gets logged |
|------|-----------------|
| `app` | Human and assistant messages only |
| `script` | Messages + Bash commands, their output (5-line preview), and file edits |

## File layout

```
~/.claude/session-logger/state.json          # session registry
/path/to/Obsidian/Conversations/
└── <session-name>/
    └── conversation_<YYYY-MM-DD_HH-MM>.md
```

## Setup

### 1. Clone this repo

```bash
git clone https://github.com/theharshl/claude-convo-save.git ~/projects/claude-convo-save
```

### 2. Make scripts executable

```bash
chmod +x ~/projects/claude-convo-save/scripts/*.sh
```

### 3. Add the Stop hook to `~/.claude/settings.json`

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/claude-convo-save/scripts/save-session.sh"
          }
        ]
      }
    ]
  }
}
```

### 4. Add session-naming instructions to `~/.claude/CLAUDE.md`

```markdown
## Session Logging (Obsidian Vault)

Every Claude Code conversation is logged to an Obsidian vault at
`/path/to/your/Obsidian/Conversations/`.

At the very start of every conversation, check whether a session has already
been initialised (look for a prior confirmation or `init-session.sh` call).

If not, your FIRST response must ask:
> **What would you like to name this session?**
> Also: script/tool project or application project? Reply `skip` to skip.

After the user replies, run:
`bash /path/to/claude-convo-save/scripts/init-session.sh "<name>" "<type>"`
then confirm the folder path before addressing any other task.

The Stop hook saves the conversation automatically after each response.
```

### 5. Edit the Obsidian vault path

In `scripts/convert_session.py`, update `OBSIDIAN_BASE` to your vault path:

```python
OBSIDIAN_BASE = Path("/path/to/your/Obsidian/Conversations")
```

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/init-session.sh` | Called by Claude to create the Obsidian folder and register the session |
| `scripts/save-session.sh` | Stop hook target — waits 2 s then calls the converter |
| `scripts/convert_session.py` | Core logic: parses JSONL, converts to Markdown, manages state |
