"""Search engine for PCMS — FTS5 full-text search across all stored messages."""

import sqlite3
from typing import Optional


def search_messages(conn: sqlite3.Connection, query: str, source: Optional[str] = None,
                    after: Optional[str] = None, before: Optional[str] = None,
                    limit: int = 20) -> list[dict]:
    """Full-text search across all messages.

    Args:
        query: search terms (FTS5 syntax supported: AND, OR, NOT, "exact phrase")
        source: filter by source ('claude', 'chatgpt', etc.)
        after: ISO date — only messages after this date
        before: ISO date — only messages before this date
        limit: max results to return
    """
    # Build the query joining FTS results with message + conversation metadata
    sql = """
        SELECT
            m.id,
            m.conversation_id,
            m.role,
            snippet(messages_fts, 0, '>>>','<<<', '...', 40) as snippet,
            m.timestamp,
            c.title as conversation_title,
            c.source,
            m.sequence_num
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
        JOIN conversations c ON c.id = m.conversation_id
        WHERE messages_fts MATCH ?
    """
    params: list = [query]

    if source:
        sql += " AND c.source = ?"
        params.append(source)
    if after:
        sql += " AND m.timestamp > ?"
        params.append(after)
    if before:
        sql += " AND m.timestamp < ?"
        params.append(before)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def search_topics(conn: sqlite3.Connection, query: str,
                  category: Optional[str] = None,
                  project: Optional[str] = None,
                  status: str = "active",
                  limit: int = 20) -> list[dict]:
    """Search across topics by name/summary with optional filters."""
    sql = """
        SELECT * FROM topics
        WHERE status = ?
        AND (name LIKE ? OR summary LIKE ? OR detail LIKE ?)
    """
    like_q = f"%{query}%"
    params: list = [status, like_q, like_q, like_q]

    if category:
        sql += " AND category = ?"
        params.append(category)
    if project:
        sql += " AND project = ?"
        params.append(project)

    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def get_conversation_messages(conn: sqlite3.Connection, conversation_id: str) -> list[dict]:
    """Retrieve all messages in a conversation, in order."""
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY sequence_num",
        (conversation_id,)
    ).fetchall()
    return [dict(row) for row in rows]


def get_stats(conn: sqlite3.Connection) -> dict:
    """Get pcms-wide statistics."""
    stats = {}
    stats["conversations"] = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    stats["messages"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    stats["topics"] = conn.execute("SELECT COUNT(*) FROM topics WHERE status = 'active'").fetchone()[0]
    stats["pending_actions"] = conn.execute("SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'").fetchone()[0]

    # Source breakdown
    rows = conn.execute("SELECT source, COUNT(*) as count FROM conversations GROUP BY source").fetchall()
    stats["by_source"] = {row["source"]: row["count"] for row in rows}

    # Topic category breakdown
    rows = conn.execute(
        "SELECT category, COUNT(*) as count FROM topics WHERE status = 'active' GROUP BY category"
    ).fetchall()
    stats["by_category"] = {row["category"]: row["count"] for row in rows}

    return stats
