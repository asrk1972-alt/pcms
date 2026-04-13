"""Manual importer for raw .md and .json files.

Use this for meeting notes, ad-hoc conversations, or any
content you want to manually add to the pcms store.
"""

import json
from pathlib import Path
from ..models import Conversation, Message, new_id, now_iso
from .base import BaseImporter


class ManualImporter(BaseImporter):
    source_name = "manual"

    def parse(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Parse a manual file (.md, .txt, or .json).

        For .md/.txt: the entire file becomes a single-message conversation.
        For .json: expects {"title": "...", "messages": [{"role": "...", "content": "..."}]}
        """
        if path.suffix == ".json":
            return self._parse_json(path)
        else:
            return self._parse_text(path)

    def _parse_text(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Import a text/markdown file as a single conversation."""
        content = path.read_text(encoding="utf-8").strip()
        if not content:
            return []

        # Use filename as title, file modified time as timestamp
        import os
        stat = os.stat(path)
        from datetime import datetime, timezone
        file_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

        conv = Conversation(
            id=new_id(),
            source="manual",
            source_id=str(path.resolve()),
            title=path.stem.replace("-", " ").replace("_", " ").title(),
            started_at=file_time,
            metadata={"original_path": str(path), "file_type": path.suffix},
        )

        msg = Message(
            id=new_id(),
            role="user",
            content=content,
            timestamp=file_time,
            sequence_num=0,
        )

        return [(conv, [msg])]

    def _parse_json(self, path: Path) -> list[tuple[Conversation, list[Message]]]:
        """Import a structured JSON file."""
        data = json.loads(path.read_text(encoding="utf-8"))

        conv = Conversation(
            id=new_id(),
            source="manual",
            source_id=data.get("id", str(path.resolve())),
            title=data.get("title", path.stem),
            started_at=data.get("started_at", now_iso()),
            ended_at=data.get("ended_at"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

        messages = []
        for i, msg_data in enumerate(data.get("messages", [])):
            msg = Message(
                id=new_id(),
                role=msg_data.get("role", "user"),
                content=msg_data.get("content", ""),
                timestamp=msg_data.get("timestamp", conv.started_at),
                sequence_num=i,
            )
            messages.append(msg)

        if not messages:
            return []

        return [(conv, messages)]
