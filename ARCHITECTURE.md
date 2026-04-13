# PCMS — Personal Chat Memory System
## Architecture Specification v1.0

> **Design principle:** Every byte of chat history is immutable by default. Nothing is deleted, overridden, or decayed without explicit human approval. Agents read structured `.md` files; the truth lives in SQLite.

---

## 1. Core Requirements

| # | Requirement | How PCMS Delivers |
|---|---|---|
| R1 | Never lose chat history | Append-only SQLite store. No DELETE without approval. |
| R2 | Nothing becomes stale/irrelevant silently | Staleness is *proposed*, never auto-applied. Approval queue. |
| R3 | Pre-approved before override or deletion | `pending_actions.json` queue — human reviews, approves/rejects. |
| R4 | Multi-source ingestion | Pluggable importers: Claude, ChatGPT, Cursor, Gemini, raw .md/.json. |
| R5 | .md output for agents | Two-layer: `CLAUDE.md` index + `memory/` directory of detailed files. |

---

## 2. System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      PCMS CLI                          │
│  pcms ingest | pcms search | pcms approve | pcms md │
└────────┬──────────┬───────────┬──────────────┬──────────┘
         │          │           │              │
    ┌────▼────┐ ┌───▼───┐ ┌────▼─────┐  ┌─────▼─────┐
    │Importers│ │Search │ │Approval  │  │MD Builder │
    │  Layer  │ │Engine │ │  Queue   │  │  (Agent   │
    │         │ │       │ │          │  │  Output)  │
    └────┬────┘ └───┬───┘ └────┬─────┘  └─────┬─────┘
         │          │          │               │
    ┌────▼──────────▼──────────▼───────────────▼──────┐
    │              STORAGE LAYER                       │
    │  pcms.db (SQLite)  +  memory/ (flat .md files)  │
    │  pending_actions.json  +  CLAUDE.md (index)      │
    └──────────────────────────────────────────────────┘
```

---

## 3. Data Model (SQLite)

### 3.1 `conversations` — The immutable ledger

```sql
CREATE TABLE conversations (
    id            TEXT PRIMARY KEY,  -- UUID
    source        TEXT NOT NULL,     -- 'claude' | 'chatgpt' | 'cursor' | 'gemini' | 'manual'
    source_id     TEXT,              -- original conversation ID from the platform
    title         TEXT,              -- conversation title or first-line summary
    started_at    TEXT NOT NULL,     -- ISO 8601
    ended_at      TEXT,              -- ISO 8601
    imported_at   TEXT NOT NULL DEFAULT (datetime('now')),
    tags          TEXT,              -- JSON array: ["python", "architecture", "project-x"]
    metadata      TEXT,              -- JSON blob for source-specific data
    is_archived   INTEGER DEFAULT 0, -- soft archive (still searchable, just deprioritized)
    archived_at   TEXT,
    archive_reason TEXT
);
```

### 3.2 `messages` — Every single message, verbatim

```sql
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,      -- 'user' | 'assistant' | 'system' | 'tool'
    content         TEXT NOT NULL,      -- full verbatim content, never truncated
    timestamp       TEXT NOT NULL,
    token_count     INTEGER,
    metadata        TEXT,               -- JSON: model version, tool calls, attachments
    sequence_num    INTEGER NOT NULL    -- ordering within conversation
);
CREATE INDEX idx_messages_conv ON messages(conversation_id, sequence_num);
CREATE INDEX idx_messages_ts ON messages(timestamp);
```

### 3.3 `topics` — Extracted knowledge units

```sql
CREATE TABLE topics (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,          -- "GraphQL Migration", "Auth Architecture"
    category        TEXT NOT NULL,          -- 'decision' | 'fact' | 'preference' | 'insight' | 'task' | 'reference'
    summary         TEXT NOT NULL,          -- concise description
    detail          TEXT,                   -- longer explanation if needed
    source_messages TEXT NOT NULL,          -- JSON array of message IDs that produced this
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT,
    confidence      REAL DEFAULT 1.0,      -- 0.0-1.0 how certain we are
    status          TEXT DEFAULT 'active',  -- 'active' | 'superseded' | 'pending_review'
    superseded_by   TEXT REFERENCES topics(id),
    project         TEXT,                   -- project association
    tags            TEXT                    -- JSON array
);
CREATE INDEX idx_topics_category ON topics(category);
CREATE INDEX idx_topics_project ON topics(project);
```

### 3.4 `pending_actions` — The approval queue

```sql
CREATE TABLE pending_actions (
    id              TEXT PRIMARY KEY,
    action_type     TEXT NOT NULL,     -- 'delete' | 'archive' | 'supersede' | 'merge' | 'update'
    target_table    TEXT NOT NULL,     -- 'conversations' | 'messages' | 'topics'
    target_id       TEXT NOT NULL,
    proposed_by     TEXT NOT NULL,     -- 'system:staleness_check' | 'system:dedup' | 'user:manual'
    reason          TEXT NOT NULL,
    proposed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected'
    reviewed_at     TEXT,
    reviewer_note   TEXT,
    rollback_data   TEXT              -- JSON snapshot of original state before action
);
```

### 3.5 `audit_log` — Immutable record of every mutation

```sql
CREATE TABLE audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    action      TEXT NOT NULL,      -- 'import' | 'approve' | 'reject' | 'archive' | 'tag' | 'topic_create'
    table_name  TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    actor       TEXT NOT NULL,      -- 'user' | 'system:importer' | 'system:staleness'
    detail      TEXT                -- JSON with before/after or context
);
```

---

## 4. Importers

Each importer converts a source format into PCMS's `conversations` + `messages` schema.

### 4.1 Supported Sources

| Source | Input Format | Importer |
|--------|-------------|----------|
| **Claude** | JSON export from claude.ai | `importers/claude.py` |
| **ChatGPT** | `conversations.json` from OpenAI data export | `importers/chatgpt.py` |
| **Cursor** | `.cursor/` workspace conversation logs | `importers/cursor.py` |
| **Gemini** | Google Takeout JSON | `importers/gemini.py` |
| **Manual** | Raw `.md` or `.json` files | `importers/manual.py` |

### 4.2 Dedup Strategy

- On import, compute SHA-256 of `(source + source_id + first_message_content)`.
- If hash exists → skip (log to audit as `import_skipped_duplicate`).
- If source_id matches but content differs → create `pending_action` of type `merge` for human review.

---

## 5. Approval Queue Workflow

```
  System proposes action (staleness, dedup, supersede)
                    │
                    ▼
        ┌─── pending_actions ───┐
        │  action_type: archive │
        │  reason: "No refs in  │
        │   180 days"           │
        │  status: pending      │
        └───────────┬───────────┘
                    │
        pcms approve --list    ← human reviews
                    │
          ┌─────────┴─────────┐
          │                   │
    pcms approve <id>   pcms reject <id>
          │                   │
          ▼                   ▼
   Execute action        Mark rejected
   Log to audit_log      Log to audit_log
   Snapshot in           (original untouched)
   rollback_data
```

**Critical rule:** The system NEVER executes destructive actions autonomously. It only *proposes*. The `pcms approve` command is the sole gateway.

---

## 6. MD Output Layer (Agent-Readable)

### 6.1 Two-Layer Structure

```
pcms-data/
├── CLAUDE.md                  ← Agent startup file (~200 lines max)
├── memory/
│   ├── _index.md              ← Master topic index with links
│   ├── projects/
│   │   ├── project-x.md
│   │   └── project-y.md
│   ├── decisions/
│   │   ├── 2026-01-graphql-migration.md
│   │   └── 2026-03-auth-redesign.md
│   ├── preferences/
│   │   └── coding-style.md
│   ├── people/
│   │   └── team-contacts.md
│   ├── insights/
│   │   └── performance-learnings.md
│   └── conversations/
│       ├── 2026-04-12-claude-pcms-design.md    ← full conversation digests
│       └── ...
```

### 6.2 CLAUDE.md Format

```markdown
# PCMS Memory Index
> Last rebuilt: 2026-04-12T14:30:00Z | Topics: 147 | Conversations: 892

## Active Projects
- **Project X** — API redesign, GraphQL migration. [Details](memory/projects/project-x.md)
- **Project Y** — Mobile app v2. [Details](memory/projects/project-y.md)

## Recent Decisions (last 30 days)
- Switched to Bun for build tooling (2026-04-01) [→](memory/decisions/2026-04-bun-migration.md)
- Adopted tRPC over REST for internal APIs (2026-03-28) [→](memory/decisions/2026-03-trpc.md)

## Key Preferences
- TypeScript strict mode always on
- Prefer composition over inheritance
- Test with Vitest, not Jest
- [Full list →](memory/preferences/coding-style.md)

## Top Insights
- Connection pooling reduced p99 latency by 40% [→](memory/insights/performance-learnings.md)

## Pending Reviews
- 3 items awaiting approval → run `pcms approve --list`
```

### 6.3 Rebuild Rules

- `pcms md` regenerates all `.md` files from SQLite truth.
- CLAUDE.md is always regenerated fresh (never edited manually).
- Individual `memory/*.md` files are rebuilt only when their source topics change.
- Rebuild is idempotent — safe to run anytime.

---

## 7. Search

### 7.1 Full-Text Search (SQLite FTS5)

```sql
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=rowid
);
```

- Fast keyword search across all messages.
- No external dependencies — FTS5 is built into SQLite.

### 7.2 Semantic Search (Optional Enhancement)

If you later want vector similarity:
- Use `sqlite-vss` extension (SQLite native vector search).
- Or export embeddings to a sidecar ChromaDB.
- Architecture is designed so this is additive, not a rewrite.

---

## 8. Staleness Detection

The system proposes (never auto-executes) staleness reviews:

```python
STALENESS_RULES = {
    "no_reference_days": 180,     # topic not referenced in 6 months
    "superseded_threshold": 0.85,  # new topic overlaps >85% with existing
    "conversation_age_days": 365,  # conversations older than 1 year → suggest archive
}
```

Every `pcms check` run:
1. Scans topics against rules.
2. Creates `pending_action` entries for anything that triggers.
3. Prints a summary: "3 topics proposed for archive. Run `pcms approve --list`."

**Nothing is touched without your say-so.**

---

## 9. CLI Interface

```bash
# Ingestion
pcms ingest claude ~/exports/claude-export.json
pcms ingest chatgpt ~/exports/conversations.json
pcms ingest cursor ~/.cursor/
pcms ingest gemini ~/exports/gemini-takeout/
pcms ingest manual ~/notes/meeting-2026-04-12.md

# Search
pcms search "GraphQL migration decision"
pcms search --source claude --after 2026-01-01 "authentication"
pcms search --category decision --project project-x

# Topic management
pcms topics list
pcms topics list --category decision --project project-x
pcms topics create --category insight --project project-x "Connection pooling insight"
pcms topics tag <topic-id> "performance,database"

# Approval queue
pcms approve --list                    # show all pending
pcms approve <action-id>              # approve specific action
pcms approve --all                    # approve all (still confirms)
pcms reject <action-id> --note "Still relevant, keep it"

# MD generation
pcms md                               # rebuild all .md files
pcms md --force                       # full rebuild even if unchanged

# Maintenance
pcms check                            # run staleness detection
pcms stats                            # counts, sizes, source breakdown
pcms export --format json             # full export for backup
pcms audit                            # show recent audit log entries
```

---

## 10. Project Structure

```
pcms/
├── pcms/
│   ├── __init__.py
│   ├── cli.py                  # Click-based CLI entry point
│   ├── config.py               # Paths, settings, staleness rules
│   ├── db.py                   # SQLite connection, schema init, migrations
│   ├── models.py               # Dataclasses for Conversation, Message, Topic, etc.
│   ├── importers/
│   │   ├── __init__.py
│   │   ├── base.py             # Abstract importer interface
│   │   ├── claude.py
│   │   ├── chatgpt.py
│   │   ├── cursor.py
│   │   ├── gemini.py
│   │   └── manual.py
│   ├── search.py               # FTS5 search interface
│   ├── topics.py               # Topic extraction and management
│   ├── approval.py             # Pending actions queue logic
│   ├── md_builder.py           # CLAUDE.md + memory/ generator
│   ├── staleness.py            # Staleness detection rules
│   └── audit.py                # Audit log writer
├── pcms-data/                 # Default data directory
│   ├── pcms.db
│   ├── CLAUDE.md
│   └── memory/
├── tests/
│   ├── test_importers.py
│   ├── test_approval.py
│   ├── test_search.py
│   └── test_md_builder.py
├── pyproject.toml
└── README.md
```

---

## 11. Safety Guarantees

| Threat | Mitigation |
|--------|-----------|
| Accidental deletion | No SQL DELETE without approved `pending_action`. DB-level trigger enforces this. |
| Data corruption | WAL mode enabled. Daily backup via `pcms export`. |
| Silent staleness | Staleness only proposes. `pcms check` is manual or cron — never auto-applied. |
| Import duplicates | SHA-256 dedup on ingest. Conflicts go to approval queue. |
| Lost context | `rollback_data` stored in every `pending_action` — any approved action is reversible. |
| Agent overwrites | Agents read `.md` files only. They never touch `pcms.db` directly. |

### SQLite Trigger — The Hard Lock

```sql
-- Prevent any DELETE on conversations without an approved pending_action
CREATE TRIGGER prevent_conversation_delete
BEFORE DELETE ON conversations
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED: Cannot delete conversations directly. Use pcms approve.')
    WHERE NOT EXISTS (
        SELECT 1 FROM pending_actions
        WHERE target_table = 'conversations'
        AND target_id = OLD.id
        AND status = 'approved'
    );
END;

-- Same for messages
CREATE TRIGGER prevent_message_delete
BEFORE DELETE ON messages
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED: Cannot delete messages directly. Use pcms approve.')
    WHERE NOT EXISTS (
        SELECT 1 FROM pending_actions
        WHERE target_table = 'messages'
        AND target_id = OLD.id
        AND status = 'approved'
    );
END;
```

---

## 12. Future Enhancements (Designed For, Not Built Yet)

- **Vector search** via sqlite-vss or ChromaDB sidecar
- **Knowledge graph** with entity-relationship extraction
- **Auto-topic extraction** using local LLM (Ollama)
- **MCP server** so Claude/agents can query PCMS directly as a tool
- **Web UI** for browsing and approving (Flask/FastAPI)
- **Cross-reference map** between conversations and topics (which chat produced which insight)

---

## 13. Dependencies

```
Python >= 3.10
click          # CLI framework
sqlite3        # stdlib, no install needed
python-dateutil
uuid           # stdlib
hashlib        # stdlib
json           # stdlib
```

Zero external services. Zero cloud. Everything on your machine.
