# PCMS — Personal Chat Memory System

> Nothing leaves without your permission.

PCMS stores all your AI chat history (Claude, ChatGPT, Cursor, Gemini, manual notes) in a **local** SQLite database with full-text search, an approval queue for any destructive operations, and structured `.md` output that AI agents can read.

**Local-first. Append-only. Zero cloud dependencies.**

---

## ⚠️ Read This First — Public Code, Private Data

This repository contains the PCMS **software only**. It is MIT-licensed and public so anyone can use it.

Your actual chat history and memory live on **your own computer**, in a separate folder (`~/.pcms/` by default, or wherever you point `PCMS_HOME`). **Nothing in that folder is ever pushed to this repo** — the `.gitignore` excludes all database and memory files by design.

### Recommended setup

| Folder | Contains | Where it goes |
|---|---|---|
| `./PCMS/` (this repo) | Code only | Public GitHub — safe to fork/share |
| `~/.pcms/` | **Your** chat data | Stays on your machine. Never auto-synced anywhere. |

### If you want to back up your personal vault

Create a **separate private repo** for your `~/.pcms/` directory. Keep it completely isolated from the public code repo. A typical layout:

- `github.com/yourname/pcms` — public fork of this repo (the code)
- `github.com/yourname/my-pcms-vault` — **private** repo just for your `~/.pcms/` contents

**Never commit your vault contents to a public repository.** Chat history contains personal conversations, API keys you may have pasted, client names, etc.

### Safety checklist before you share anything

- [ ] You are pushing from the code folder, not `~/.pcms/`
- [ ] `git status` shows no `.db`, `.sqlite`, or `memory/` files staged
- [ ] Any repo containing `~/.pcms/` is marked **private** on GitHub
- [ ] You understand that once a commit is public, it's in the git history forever (even if deleted later)

---

## Install

```bash
git clone https://github.com/asrk1972-alt/pcms.git
cd pcms
pip install -e .
```

Verify it works:

```bash
pcms stats
```

You should see an empty vault (0 conversations, 0 messages, 0 topics).

---

## Quick Start

### 1. Import your existing chat history

**Claude (export from claude.ai):**
```bash
pcms ingest claude ~/Downloads/claude-export.json
```

**ChatGPT (export from chat.openai.com):**
```bash
pcms ingest chatgpt ~/Downloads/conversations.json
```

**Manual note:**
```bash
pcms ingest note "Decision: we're going with Postgres over MySQL because of JSON support"
```

### 2. Search across everything

```bash
pcms search "GraphQL migration"
pcms search "auth decision" --limit 20
```

### 3. Save a topic, decision, or preference

```bash
pcms topics add "Always prefer Postgres for new services" --category decision
pcms topics list --category decision
```

### 4. Generate Markdown files for AI agents

```bash
pcms md
```

This regenerates `CLAUDE.md` (a compact index) and the `memory/` directory (detailed per-topic files) inside your `PCMS_HOME`. Point any AI agent at these files for context.

### 5. Review the approval queue (nothing gets deleted without you)

```bash
pcms approve list           # see what the system wants to clean up
pcms approve accept <id>    # approve a proposed action
pcms approve reject <id>    # reject it — nothing happens
```

### 6. Other useful commands

```bash
pcms stats        # vault summary
pcms audit        # full audit log
pcms check        # run staleness detection (proposes cleanup, never executes)
pcms export       # export everything to JSON
```

---

## Claude Code Integration (Optional)

PCMS includes an MCP server so Claude Code can search your memory, recall past conversations, and save new insights directly during a chat session.

Add this to your Claude Code config at `~/.claude.json` (or `%USERPROFILE%\.claude.json` on Windows), inside the global `"mcpServers"` block:

```json
"pcms": {
  "type": "stdio",
  "command": "python",
  "args": ["-m", "pcms.mcp_server"],
  "env": {
    "PCMS_HOME": "/absolute/path/to/your/.pcms"
  }
}
```

On Windows, use double backslashes in the path: `"C:\\Users\\yourname\\.pcms"`.

After editing, validate the JSON and restart Claude Code:

```powershell
# Windows PowerShell
Get-Content $HOME\.claude.json -Raw | ConvertFrom-Json | Out-Null; if ($?) { "JSON is valid" }
```

Once connected, Claude Code gets these 8 tools:

| Tool | What it does |
|---|---|
| `pcms_search` | Full-text search across all stored messages |
| `pcms_recall` | Pull a full conversation by ID |
| `pcms_topics` | List or search saved decisions/preferences/insights |
| `pcms_add_topic` | Save a new decision/preference from the current chat |
| `pcms_ingest_note` | Save a note or conversation summary |
| `pcms_stats` | Vault statistics |
| `pcms_pending` | Show pending approval queue |
| `pcms_rebuild_md` | Regenerate the Markdown memory files |

---

## Safety Guarantees

These are enforced by the system, not just by convention:

- **Append-only database** — SQLite triggers **physically block** any DELETE without an approved pending action. Even buggy code can't destroy your data.
- **Approval queue** — staleness detection and cleanup only *propose* actions. You approve or reject each one individually.
- **Rollback snapshots** — every approved destructive action stores the original state, so you can undo.
- **Immutable audit log** — every mutation is timestamped and logged.
- **No network calls** — PCMS never phones home. Your data never leaves your machine unless you explicitly move it.

---

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PCMS_HOME` | `~/.pcms` | Where your vault lives (database + memory files) |

To use a different location, set `PCMS_HOME` in your shell profile or in the MCP config's `env` block.

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full system design — data model, safety triggers, staleness rules, and MD output structure.

## Project Structure

```
pcms/
├── cli.py            # Click CLI (pcms ingest/search/approve/md/...)
├── mcp_server.py     # MCP server for Claude Code
├── db.py             # SQLite schema + safety triggers
├── approval.py       # Approval queue logic
├── md_builder.py     # CLAUDE.md + memory/ generator
├── search.py         # FTS5 full-text search
├── staleness.py      # Proposes (never executes) cleanup
├── config.py         # PCMS_HOME resolution
└── importers/        # Per-source importers
    ├── base.py
    ├── claude.py
    ├── chatgpt.py
    ├── cursor.py
    ├── gemini.py
    └── manual.py
```

## License

MIT — use it, fork it, modify it. Your data stays yours.
