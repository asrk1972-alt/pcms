"""Base importer interface.

Every source-specific importer must subclass this and implement parse().
The base class handles dedup, storage, and audit logging uniformly.
"""

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import Conversation, Message, new_id, now_iso
from ..db import write_audit
from ..config import DEDUP_ENABLED


class BaseImporter(ABC):
    """Abstract base for all chat importers."""

    source_name: str = ""  # override in subclass: 'claude', 'chatgpt', etc.

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.stats = {"imported": 0, "skipped_duplicate": 0, "errors": 0}

    @abstractmethod
    def parse(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Parse a source file/directory into (Conversation, [Message]) pairs.

        Each importer must handle its own source format and return
        standardized PCMS data models.
        """
        ...

    def run(self, path: Path) -> dict:
        """Full import pipeline: parse → dedup → store → audit."""
        parsed = self.parse(path)

        for conversation, messages in parsed:
            try:
                if DEDUP_ENABLED and self._is_duplicate(conversation, messages):
                    self.stats["skipped_duplicate"] += 1
                    continue

                self._store_conversation(conversation)
                for msg in messages:
                    msg.conversation_id = conversation.id
                    self._store_message(msg)

                write_audit(self.conn, "import", "conversations", conversation.id,
                            f"system:importer:{self.source_name}",
                            json.dumps({"title": conversation.title, "message_count": len(messages)}))

                self.stats["imported"] += 1

            except Exception as e:
                self.stats["errors"] += 1
                write_audit(self.conn, "import_error", "conversations",
                            conversation.id or "unknown", f"system:importer:{self.source_name}",
                            json.dumps({"error": str(e)}))

        self.conn.commit()
        return self.stats

    def _dedup_hash(self, conversation: Conversation, messages: list[Message]) -> str:
        """Compute a deterministic hash for dedup.

        Hash = SHA-256(source + source_id + first_message_content)
        """
        first_content = messages[0].content if messages else ""
        raw = f"{conversation.source}|{conversation.source_id}|{first_content}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _is_duplicate(self, conversation: Conversation, messages: list[Message]) -> bool:
        """Check if this conversation already exists in the pcms store."""
        # Check by source_id first (fast path)
        if conversation.source_id:
            existing = self.conn.execute(
                "SELECT id FROM conversations WHERE source = ? AND source_id = ?",
                (conversation.source, conversation.source_id)
            ).fetchone()
            if existing:
                return True

        # Check by content hash (slow path, catches re-exports)
        content_hash = self._dedup_hash(conversation, messages)
        # Store hash in metadata for future lookups
        conversation.metadata["_dedup_hash"] = content_hash

        existing = self.conn.execute(
            "SELECT id FROM conversations WHERE json_extract(metadata, '$._dedup_hash') = ?",
            (content_hash,)
        ).fetchone()
        return existing is not None

    def _store_conversation(self, conv: Conversation):
        """Insert a conversation into the pcms store."""
        self.conn.execute(
            """INSERT INTO conversations
               (id, source, source_id, title, started_at, ended_at, imported_at, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conv.id, conv.source, conv.source_id, conv.title,
             conv.started_at, conv.ended_at, conv.imported_at,
             conv.tags_json(), conv.metadata_json())
        )

    def _store_message(self, msg: Message):
        """Insert a message into the pcms store."""
        self.conn.execute(
            """INSERT INTO messages
               (id, conversation_id, role, content, timestamp, token_count, metadata, sequence_num)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg.id, msg.conversation_id, msg.role, msg.content,
             msg.timestamp, msg.token_count, msg.metadata_json(), msg.sequence_num)
        )
