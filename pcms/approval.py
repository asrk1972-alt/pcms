"""Approval queue — the gatekeeper for all destructive operations.

Nothing is deleted, archived, superseded, or merged without passing through
this queue and receiving explicit human approval.
"""

import json
import sqlite3
from typing import Optional
from .models import PendingAction, now_iso, new_id
from .db import write_audit


def propose_action(conn: sqlite3.Connection, action_type: str, target_table: str,
                   target_id: str, proposed_by: str, reason: str,
                   rollback_data: dict = None) -> PendingAction:
    """Create a new pending action for human review.

    Args:
        action_type: 'delete' | 'archive' | 'supersede' | 'merge' | 'update'
        target_table: 'conversations' | 'messages' | 'topics'
        target_id: ID of the record to act on
        proposed_by: who/what proposed this (e.g., 'system:staleness_check')
        reason: human-readable explanation
        rollback_data: snapshot of current state for undo capability
    """
    action = PendingAction(
        id=new_id(),
        action_type=action_type,
        target_table=target_table,
        target_id=target_id,
        proposed_by=proposed_by,
        reason=reason,
        rollback_data=rollback_data,
    )

    conn.execute(
        """INSERT INTO pending_actions
           (id, action_type, target_table, target_id, proposed_by, reason, rollback_data)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (action.id, action.action_type, action.target_table, action.target_id,
         action.proposed_by, action.reason, action.rollback_json())
    )
    conn.commit()

    write_audit(conn, "propose", target_table, target_id, proposed_by,
                json.dumps({"action_id": action.id, "type": action_type, "reason": reason}))

    return action


def list_pending(conn: sqlite3.Connection, status: str = "pending") -> list[dict]:
    """List all pending actions awaiting review."""
    rows = conn.execute(
        "SELECT * FROM pending_actions WHERE status = ? ORDER BY proposed_at DESC",
        (status,)
    ).fetchall()
    return [dict(row) for row in rows]


def approve_action(conn: sqlite3.Connection, action_id: str,
                   reviewer_note: Optional[str] = None) -> bool:
    """Approve a pending action — this is the ONLY way to enable destructive ops.

    After approval, the calling code must still execute the actual operation.
    The SQLite safety triggers will now allow the DELETE/UPDATE because the
    pending_action status is 'approved'.
    """
    row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise ValueError(f"No pending action with id: {action_id}")
    if row["status"] != "pending":
        raise ValueError(f"Action {action_id} is already {row['status']}")

    # Capture rollback data if not already present
    if not row["rollback_data"]:
        snapshot = _capture_snapshot(conn, row["target_table"], row["target_id"])
        conn.execute(
            "UPDATE pending_actions SET rollback_data = ? WHERE id = ?",
            (json.dumps(snapshot), action_id)
        )

    conn.execute(
        """UPDATE pending_actions
           SET status = 'approved', reviewed_at = ?, reviewer_note = ?
           WHERE id = ?""",
        (now_iso(), reviewer_note, action_id)
    )
    conn.commit()

    write_audit(conn, "approve", row["target_table"], row["target_id"], "user",
                json.dumps({"action_id": action_id, "note": reviewer_note}))

    return True


def reject_action(conn: sqlite3.Connection, action_id: str,
                  reviewer_note: Optional[str] = None) -> bool:
    """Reject a pending action — the target remains completely untouched."""
    row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise ValueError(f"No pending action with id: {action_id}")
    if row["status"] != "pending":
        raise ValueError(f"Action {action_id} is already {row['status']}")

    conn.execute(
        """UPDATE pending_actions
           SET status = 'rejected', reviewed_at = ?, reviewer_note = ?
           WHERE id = ?""",
        (now_iso(), reviewer_note, action_id)
    )
    conn.commit()

    write_audit(conn, "reject", row["target_table"], row["target_id"], "user",
                json.dumps({"action_id": action_id, "note": reviewer_note}))

    return True


def execute_approved_action(conn: sqlite3.Connection, action_id: str) -> bool:
    """Execute an already-approved action.

    This performs the actual destructive operation (delete, archive, etc.)
    The safety triggers will check that the action is approved before allowing it.
    """
    row = conn.execute("SELECT * FROM pending_actions WHERE id = ?", (action_id,)).fetchone()
    if not row:
        raise ValueError(f"No pending action with id: {action_id}")
    if row["status"] != "approved":
        raise ValueError(f"Action {action_id} must be approved first (current: {row['status']})")

    action_type = row["action_type"]
    target_table = row["target_table"]
    target_id = row["target_id"]

    if action_type == "delete":
        conn.execute(f"DELETE FROM {target_table} WHERE id = ?", (target_id,))
    elif action_type == "archive":
        conn.execute(
            f"UPDATE {target_table} SET is_archived = 1, archived_at = ?, archive_reason = ? WHERE id = ?",
            (now_iso(), row["reason"], target_id)
        )
    elif action_type == "supersede":
        conn.execute(
            "UPDATE topics SET status = 'superseded', updated_at = ? WHERE id = ?",
            (now_iso(), target_id)
        )

    conn.commit()
    write_audit(conn, f"execute_{action_type}", target_table, target_id, "system:approval",
                json.dumps({"action_id": action_id}))

    return True


def _capture_snapshot(conn: sqlite3.Connection, table: str, record_id: str) -> dict:
    """Capture a full snapshot of a record for rollback purposes."""
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (record_id,)).fetchone()
    if row:
        return dict(row)
    return {}
