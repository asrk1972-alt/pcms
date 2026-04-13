# PCMS — Personal Chat Memory System

> Nothing leaves without your permission.

PCMS stores all your AI chat history (Claude, ChatGPT, Cursor, Gemini) in a local SQLite database with full-text search, an approval queue for any destructive operations, and structured `.md` output that AI agents can read.

## Quick Start

```bash
pip install -e .
pcms ingest claude ~/exports/claude-export.json
pcms ingest chatgpt ~/exports/conversations.json
pcms search "that thing we discussed about auth"
pcms md
```

## Claude Code Integration

Add this to your Claude Code MCP settings (`~/.claude/claude_code_config.json`):

```json
{
  "mcpServers": {
    "pcms": {
      "command": "python",
      "args": ["-m", "pcms.mcp_server"],
      "env": {
        "PCMS_HOME": "C:\\Users\\asrk1\\.pcms"
      }
    }
  }
}
```

Once connected, Claude Code gets 8 tools: `pcms_search`, `pcms_recall`, `pcms_topics`, `pcms_add_topic`, `pcms_ingest_note`, `pcms_stats`, `pcms_pending`, `pcms_rebuild_md`.

## Safety Guarantees

- **Append-only database** — SQLite triggers physically block DELETE without an approved pending action
- **Approval queue** — staleness detection only *proposes*; you approve or reject
- **Rollback snapshots** — every approved action stores the original state for undo
- **Audit log** — every mutation is logged with timestamp, actor, and detail

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design.

## License

MIT
