# PCMS — Personal Chat Memory System

This is the source code for PCMS, a personal AI memory system that stores all chat history from Claude, ChatGPT, Cursor, Gemini, and manual notes.

## Key Design Principles
- **Append-only**: Nothing is ever deleted without explicit human approval via the approval queue
- **SQLite + flat files**: Zero cloud dependencies, everything local
- **Two-layer .md output**: Compact CLAUDE.md index + detailed memory/ directory for agent consumption
- **Safety triggers**: Database-level DELETE triggers block any unapproved destructive operations

## Project Structure
- `pcms/cli.py` — Click-based CLI (`pcms ingest`, `pcms search`, `pcms approve`, `pcms md`, etc.)
- `pcms/mcp_server.py` — MCP server for Claude Code integration (8 tools)
- `pcms/db.py` — SQLite schema, WAL mode, safety triggers
- `pcms/importers/` — Pluggable importers for Claude, ChatGPT, Cursor, Gemini, manual
- `pcms/approval.py` — Approval queue (propose → review → approve/reject → execute)
- `pcms/md_builder.py` — Generates CLAUDE.md + memory/*.md from database
- `pcms/search.py` — FTS5 full-text search
- `pcms/staleness.py` — Proposes (never auto-executes) cleanup of old/unused content

## MCP Tools Available
When connected via MCP, Claude Code gets these tools:
- `pcms_search` — search all stored messages
- `pcms_recall` — get full conversation history by ID
- `pcms_topics` — list/search knowledge topics
- `pcms_add_topic` — save a decision/preference/insight from current conversation
- `pcms_ingest_note` — save a note or conversation summary
- `pcms_stats` — vault statistics
- `pcms_pending` — show pending approval queue
- `pcms_rebuild_md` — regenerate .md files

## CLI Quick Reference
```bash
pcms ingest claude ~/export.json    # import Claude chat export
pcms ingest chatgpt ~/export.json   # import ChatGPT export
pcms search "GraphQL migration"     # full-text search
pcms topics list --category decision
pcms approve list                   # review pending actions
pcms approve accept <id>            # approve a destructive action
pcms md                             # rebuild .md files
pcms stats                          # vault statistics
pcms check                          # run staleness detection
```
