"""ChatGPT conversation importer.

Handles the conversations.json from OpenAI data export
(Settings → Data Controls → Export Data).
"""

import json
from pathlib import Path
from ..models import Conversation, Message, new_id, now_iso
from .base import BaseImporter


class ChatGPTImporter(BaseImporter):
    source_name = "chatgpt"

    def parse(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Parse ChatGPT export.

        Expected structure (OpenAI data export → conversations.json):
        [
            {
                "id": "...",
                "title": "...",
                "create_time": 1705000000.0,
                "update_time": 1705000000.0,
                "mapping": {
                    "node-id": {
                        "message": {
                            "author": {"role": "user"|"assistant"|"system"|"tool"},
                            "content": {"parts": ["text"]},
                            "create_time": 1705000000.0
                        },
                        "parent": "parent-node-id",
                        "children": ["child-node-id"]
                    }
                }
            }
        ]
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        results = []

        for conv_data in data:
            create_ts = conv_data.get("create_time")
            update_ts = conv_data.get("update_time")

            conv = Conversation(
                id=new_id(),
                source="chatgpt",
                source_id=conv_data.get("id", ""),
                title=conv_data.get("title", "Untitled"),
                started_at=self._ts_to_iso(create_ts) if create_ts else now_iso(),
                ended_at=self._ts_to_iso(update_ts) if update_ts else None,
                metadata={
                    "model": conv_data.get("default_model_slug"),
                    "plugin_ids": conv_data.get("plugin_ids", []),
                },
            )

            # ChatGPT uses a tree structure — linearize it
            messages = self._linearize_mapping(conv_data.get("mapping", {}), conv.started_at)

            if messages:
                results.append((conv, messages))

        return results

    def _linearize_mapping(self, mapping: dict, fallback_ts: str) -> list[Message]:
        """Walk the ChatGPT message tree and produce a linear sequence."""
        # Find root node (no parent or parent not in mapping)
        ordered = []
        visited = set()

        # Build parent→children lookup
        roots = []
        for node_id, node in mapping.items():
            parent = node.get("parent")
            if not parent or parent not in mapping:
                roots.append(node_id)

        # BFS from roots
        queue = list(roots)
        seq = 0
        while queue:
            node_id = queue.pop(0)
            if node_id in visited:
                continue
            visited.add(node_id)

            node = mapping.get(node_id, {})
            msg_data = node.get("message")

            if msg_data and msg_data.get("content"):
                author = msg_data.get("author", {})
                role = author.get("role", "unknown")

                # Extract text from content.parts
                parts = msg_data.get("content", {}).get("parts", [])
                text_parts = []
                for part in parts:
                    if isinstance(part, str):
                        text_parts.append(part)
                    elif isinstance(part, dict) and "text" in part:
                        text_parts.append(part["text"])

                content = "\n".join(text_parts).strip()

                if content and role in ("user", "assistant", "system", "tool"):
                    create_time = msg_data.get("create_time")
                    msg = Message(
                        id=new_id(),
                        role=role,
                        content=content,
                        timestamp=self._ts_to_iso(create_time) if create_time else fallback_ts,
                        sequence_num=seq,
                        metadata={
                            "model_slug": msg_data.get("metadata", {}).get("model_slug"),
                            "node_id": node_id,
                        },
                    )
                    ordered.append(msg)
                    seq += 1

            # Add children to queue
            for child_id in node.get("children", []):
                queue.append(child_id)

        return ordered

    @staticmethod
    def _ts_to_iso(ts) -> str:
        """Convert Unix timestamp to ISO 8601."""
        from datetime import datetime, timezone
        if ts is None:
            return now_iso()
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
