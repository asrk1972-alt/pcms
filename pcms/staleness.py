"""Staleness detection — proposes (never executes) cleanup actions.

Scans topics and conversations against configurable rules and
creates pending_action entries for human review.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

from .config import STALENESS_RULES
from .approval import propose_action


def run_staleness_check(conn: sqlite3.Connection) -> dict:
    """Run all staleness rules and create pending actions for matches.

    Returns a summary of what was proposed.
    """
    results = {
        "stale_topics": 0,
        "old_conversations": 0,
        "total_proposed": 0,
    }

    results["stale_topics"] = _check_unreferenced_topics(conn)
    results["old_conversations"] = _check_old_conversations(conn)
    results["total_proposed"] = results["stale_topics"] + results["old_conversations"]

    return results


def _check_unreferenced_topics(conn: sqlite3.Connection) -> int:
    """Find topics not referenced or updated in N days."""
    threshold = STALENESS_RULES["no_reference_days"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold)).isoformat()

    # Find active topics that haven't been updated since cutoff
    # and don't already have a pending action
    stale = conn.execute("""
        SELECT t.id, t.name, t.category, t.created_at, t.updated_at
        FROM topics t
        WHERE t.status = 'active'
        AND COALESCE(t.updated_at, t.created_at) < ?
        AND NOT EXISTS (
            SELECT 1 FROM pending_actions pa
            WHERE pa.target_table = 'topics'
            AND pa.target_id = t.id
            AND pa.status = 'pending'
        )
    """, (cutoff,)).fetchall()

    count = 0
    for topic in stale:
        last_touch = topic["updated_at"] or topic["created_at"]
        propose_action(
            conn,
            action_type="archive",
            target_table="topics",
            target_id=topic["id"],
            proposed_by="system:staleness_check",
            reason=f"Topic '{topic['name']}' ({topic['category']}) last updated {last_touch[:10]}, "
                   f"exceeds {threshold}-day threshold. No recent references found.",
            rollback_data=dict(topic),
        )
        count += 1

    return count


def _check_old_conversations(conn: sqlite3.Connection) -> int:
    """Find conversations older than N days that could be archived."""
    threshold = STALENESS_RULES["conversation_age_days"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=threshold)).isoformat()

    old = conn.execute("""
        SELECT c.id, c.title, c.source, c.started_at
        FROM conversations c
        WHERE c.is_archived = 0
        AND c.started_at < ?
        AND NOT EXISTS (
            SELECT 1 FROM pending_actions pa
            WHERE pa.target_table = 'conversations'
            AND pa.target_id = c.id
            AND pa.status = 'pending'
        )
    """, (cutoff,)).fetchall()

    count = 0
    for conv in old:
        propose_action(
            conn,
            action_type="archive",
            target_table="conversations",
            target_id=conv["id"],
            proposed_by="system:staleness_check",
            reason=f"Conversation '{conv['title']}' from {conv['source']} started {conv['started_at'][:10]}, "
                   f"exceeds {threshold}-day archive threshold. Content preserved, just deprioritized in search.",
            rollback_data=dict(conv),
        )
        count += 1

    return count
