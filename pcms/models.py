"""Data models for PCMS entities."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import json
import uuid


def new_id() -> str:
    return str(uuid.uuid4())


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class Conversation:
    id: str = field(default_factory=new_id)
    source: str = ""              # claude | chatgpt | cursor | gemini | manual
    source_id: Optional[str] = None
    title: Optional[str] = None
    started_at: str = ""
    ended_at: Optional[str] = None
    imported_at: str = field(default_factory=now_iso)
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    is_archived: bool = False
    archived_at: Optional[str] = None
    archive_reason: Optional[str] = None

    def tags_json(self) -> str:
        return json.dumps(self.tags)

    def metadata_json(self) -> str:
        return json.dumps(self.metadata)


@dataclass
class Message:
    id: str = field(default_factory=new_id)
    conversation_id: str = ""
    role: str = ""                # user | assistant | system | tool
    content: str = ""
    timestamp: str = ""
    token_count: Optional[int] = None
    metadata: dict = field(default_factory=dict)
    sequence_num: int = 0

    def metadata_json(self) -> str:
        return json.dumps(self.metadata)


@dataclass
class Topic:
    id: str = field(default_factory=new_id)
    name: str = ""
    category: str = ""            # decision | fact | preference | insight | task | reference
    summary: str = ""
    detail: Optional[str] = None
    source_messages: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: Optional[str] = None
    confidence: float = 1.0
    status: str = "active"        # active | superseded | pending_review
    superseded_by: Optional[str] = None
    project: Optional[str] = None
    tags: list[str] = field(default_factory=list)

    def source_messages_json(self) -> str:
        return json.dumps(self.source_messages)

    def tags_json(self) -> str:
        return json.dumps(self.tags)


@dataclass
class PendingAction:
    id: str = field(default_factory=new_id)
    action_type: str = ""         # delete | archive | supersede | merge | update
    target_table: str = ""        # conversations | messages | topics
    target_id: str = ""
    proposed_by: str = ""         # system:staleness_check | system:dedup | user:manual
    reason: str = ""
    proposed_at: str = field(default_factory=now_iso)
    status: str = "pending"       # pending | approved | rejected
    reviewed_at: Optional[str] = None
    reviewer_note: Optional[str] = None
    rollback_data: Optional[dict] = None

    def rollback_json(self) -> str:
        return json.dumps(self.rollback_data) if self.rollback_data else None
