"""SQLite database initialization and connection management for PCMS.

Key safety features:
- WAL mode for concurrent reads during writes
- DELETE triggers that block direct deletion without approved pending_action
- FTS5 virtual table for full-text search across messages
"""

import sqlite3
from pathlib import Path
from .config import DB_PATH, ensure_dirs


SCHEMA_SQL = """
-- ============================================================
-- CORE TABLES
-- ============================================================

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    source_id       TEXT,
    title           TEXT,
    started_at      TEXT NOT NULL,
    ended_at        TEXT,
    imported_at     TEXT NOT NULL DEFAULT (datetime('now')),
    tags            TEXT DEFAULT '[]',
    metadata        TEXT DEFAULT '{}',
    is_archived     INTEGER DEFAULT 0,
    archived_at     TEXT,
    archive_reason  TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    token_count     INTEGER,
    metadata        TEXT DEFAULT '{}',
    sequence_num    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, sequence_num);
CREATE INDEX IF NOT EXISTS idx_messages_ts ON messages(timestamp);

CREATE TABLE IF NOT EXISTS topics (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    summary         TEXT NOT NULL,
    detail          TEXT,
    source_messages TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT,
    confidence      REAL DEFAULT 1.0,
    status          TEXT DEFAULT 'active',
    superseded_by   TEXT REFERENCES topics(id),
    project         TEXT,
    tags            TEXT DEFAULT '[]'
);

CREATE INDEX IF NOT EXISTS idx_topics_category ON topics(category);
CREATE INDEX IF NOT EXISTS idx_topics_project ON topics(project);
CREATE INDEX IF NOT EXISTS idx_topics_status ON topics(status);

CREATE TABLE IF NOT EXISTS pending_actions (
    id              TEXT PRIMARY KEY,
    action_type     TEXT NOT NULL,
    target_table    TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    proposed_by     TEXT NOT NULL,
    reason          TEXT NOT NULL,
    proposed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status          TEXT DEFAULT 'pending',
    reviewed_at     TEXT,
    reviewer_note   TEXT,
    rollback_data   TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    action      TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    record_id   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(timestamp);

-- ============================================================
-- FULL-TEXT SEARCH
-- ============================================================

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content,
    content=messages,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
END;

-- ============================================================
-- SAFETY TRIGGERS — THE HARD LOCK
-- No DELETE on core tables without an approved pending_action
-- ============================================================

CREATE TRIGGER IF NOT EXISTS prevent_conversation_delete
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

CREATE TRIGGER IF NOT EXISTS prevent_message_delete
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

CREATE TRIGGER IF NOT EXISTS prevent_topic_delete
BEFORE DELETE ON topics
BEGIN
    SELECT RAISE(ABORT, 'BLOCKED: Cannot delete topics directly. Use pcms approve.')
    WHERE NOT EXISTS (
        SELECT 1 FROM pending_actions
        WHERE target_table = 'topics'
        AND target_id = OLD.id
        AND status = 'approved'
    );
END;
"""


def get_connection(db_path: Path = None) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and safety features."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = None) -> sqlite3.Connection:
    """Initialize the database with full schema and safety triggers."""
    ensure_dirs()
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def write_audit(conn: sqlite3.Connection, action: str, table_name: str,
                record_id: str, actor: str, detail: str = None):
    """Write an immutable audit log entry."""
    conn.execute(
        "INSERT INTO audit_log (action, table_name, record_id, actor, detail) VALUES (?, ?, ?, ?, ?)",
        (action, table_name, record_id, actor, detail)
    )
    conn.commit()
