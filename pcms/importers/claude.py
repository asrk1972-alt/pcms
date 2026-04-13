"""Claude conversation importer.

Handles JSON exports from claude.ai (Settings → Export Data).
The export contains a conversations.json with all chat sessions.
"""

import json
from pathlib import Path
from ..models import Conversation, Message, new_id, now_iso
from .base import BaseImporter


class ClaudeImporter(BaseImporter):
    source_name = "claude"

    def parse(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Parse Claude export JSON.

        Expected structure (claude.ai export):
        [
            {
                "uuid": "...",
                "name": "conversation title",
                "created_at": "2026-01-15T...",
                "updated_at": "2026-01-15T...",
                "chat_messages": [
                    {
                        "uuid": "...",
                        "sender": "human" | "assistant",
                        "text": "...",
                        "created_at": "2026-01-15T..."
                    }
                ]
            }
        ]
        """
        data = json.loads(path.read_text(encoding="utf-8"))

        # Handle both single-file and directory exports
        if isinstance(data, dict) and "conversations" in data:
            conversations_data = data["conversations"]
        elif isinstance(data, list):
            conversations_data = data
        else:
            raise ValueError(f"Unexpected Claude export format in {path}")

        results = []

        for conv_data in conversations_data:
            conv = Conversation(
                id=new_id(),
                source="claude",
                source_id=conv_data.get("uuid", conv_data.get("id", "")),
                title=conv_data.get("name", conv_data.get("title", "Untitled")),
                started_at=conv_data.get("created_at", now_iso()),
                ended_at=conv_data.get("updated_at"),
                metadata={
                    "model": conv_data.get("model"),
                    "project": conv_data.get("project"),
                },
            )

            messages = []
            raw_messages = conv_data.get("chat_messages", conv_data.get("messages", []))

            for i, msg_data in enumerate(raw_messages):
                # Map Claude's role names to PCMS's standard roles
                sender = msg_data.get("sender", msg_data.get("role", "unknown"))
                role_map = {"human": "user", "assistant": "assistant", "system": "system"}
                role = role_map.get(sender, sender)

                # Handle content that might be a string or structured
                content = msg_data.get("text", "")
                if not content:
                    content_parts = msg_data.get("content", [])
                    if isinstance(content_parts, list):
                        content = "\n".join(
                            p.get("text", "") for p in content_parts
                            if isinstance(p, dict) and p.get("type") == "text"
                        )
                    elif isinstance(content_parts, str):
                        content = content_parts

                msg = Message(
                    id=new_id(),
                    role=role,
                    content=content,
                    timestamp=msg_data.get("created_at", conv.started_at),
                    sequence_num=i,
                    metadata={
                        "original_uuid": msg_data.get("uuid", ""),
                        "attachments": msg_data.get("attachments", []),
                    },
                )
                messages.append(msg)

            if messages:  # only import conversations with actual content
                results.append((conv, messages))

        return results
