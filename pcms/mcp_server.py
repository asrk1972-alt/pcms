"""PCMS MCP Server — exposes Personal Chat Memory System as tools for Claude Code.

This runs as a stdio MCP server. Claude Code connects to it and gets access to:
  - pcms_search: full-text search across all stored messages
  - pcms_recall: retrieve a specific conversation's full history
  - pcms_topics: list/search knowledge topics
  - pcms_ingest: import a conversation or note into the vault
  - pcms_stats: get vault statistics
  - pcms_add_topic: create a new knowledge topic from current conversation
  - pcms_approve_list: show pending actions awaiting approval
  - pcms_rebuild_md: regenerate CLAUDE.md and memory/ files

Usage:
    python -m pcms.mcp_server
"""

import json
import sys
import os
from pathlib import Path

# MCP protocol over stdio
# We implement the MCP JSON-RPC protocol directly — zero dependencies beyond stdlib + pcms


def _get_db():
    """Get a database connection using PCMS_HOME."""
    from .db import init_db
    return init_db()


# ============================================================
# TOOL DEFINITIONS
# ============================================================

TOOLS = [
    {
        "name": "pcms_search",
        "description": "Search across all stored chat messages from Claude, ChatGPT, Cursor, Gemini, etc. Use this to recall past conversations, decisions, code discussions, or anything discussed previously.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms. Supports AND, OR, NOT, and \"exact phrases\"."},
                "source": {"type": "string", "description": "Filter by source: claude, chatgpt, cursor, gemini, manual", "enum": ["claude", "chatgpt", "cursor", "gemini", "manual"]},
                "after": {"type": "string", "description": "Only results after this ISO date (e.g., 2026-01-01)"},
                "before": {"type": "string", "description": "Only results before this ISO date"},
                "limit": {"type": "integer", "description": "Max results (default 10)", "default": 10},
            },
            "required": ["query"],
        },
    },
    {
        "name": "pcms_recall",
        "description": "Retrieve the full message history of a specific conversation by its ID. Use after pcms_search to get the complete context of a relevant conversation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "conversation_id": {"type": "string", "description": "The conversation ID to retrieve"},
            },
            "required": ["conversation_id"],
        },
    },
    {
        "name": "pcms_topics",
        "description": "List or search knowledge topics (decisions, facts, preferences, insights) extracted from past conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms to filter topics (optional)"},
                "category": {"type": "string", "description": "Filter by category", "enum": ["decision", "fact", "preference", "insight", "task", "reference"]},
                "project": {"type": "string", "description": "Filter by project name"},
                "limit": {"type": "integer", "description": "Max results (default 20)", "default": 20},
            },
        },
    },
    {
        "name": "pcms_add_topic",
        "description": "Save a new knowledge topic to PCMS. Use this to capture decisions, preferences, insights, or facts from the current conversation so they're remembered permanently.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short topic name (e.g., 'Switched to Bun for builds')"},
                "category": {"type": "string", "description": "Topic category", "enum": ["decision", "fact", "preference", "insight", "task", "reference"]},
                "summary": {"type": "string", "description": "Concise summary of the topic"},
                "detail": {"type": "string", "description": "Longer explanation (optional)"},
                "project": {"type": "string", "description": "Associated project (optional)"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
            },
            "required": ["name", "category", "summary"],
        },
    },
    {
        "name": "pcms_ingest_note",
        "description": "Save a note or the current conversation summary into PCMS for permanent storage. Content is stored verbatim and is searchable.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Title for this note/conversation"},
                "content": {"type": "string", "description": "The full text content to store"},
                "source": {"type": "string", "description": "Source identifier (default: 'manual')", "default": "manual"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "pcms_stats",
        "description": "Get PCMS vault statistics — total conversations, messages, topics, pending actions, and breakdowns by source and category.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pcms_pending",
        "description": "List pending actions that need human approval (deletions, archives, merges). Nothing destructive happens without approval.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "pcms_rebuild_md",
        "description": "Regenerate CLAUDE.md and all memory/*.md files from the database. Run this after adding new topics or ingesting new conversations.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
]


# ============================================================
# TOOL HANDLERS
# ============================================================

def handle_pcms_search(args: dict) -> str:
    from .search import search_messages
    conn = _get_db()
    results = search_messages(
        conn,
        query=args["query"],
        source=args.get("source"),
        after=args.get("after"),
        before=args.get("before"),
        limit=args.get("limit", 10),
    )
    if not results:
        return "No results found."

    output = []
    for r in results:
        output.append(f"[{r['source']}] {r['conversation_title']} (conv_id: {r['conversation_id']})")
        output.append(f"  {r['role'].upper()} ({r['timestamp'][:10]}): {r['snippet']}")
        output.append("")
    return "\n".join(output)


def handle_pcms_recall(args: dict) -> str:
    from .search import get_conversation_messages
    conn = _get_db()
    messages = get_conversation_messages(conn, args["conversation_id"])
    if not messages:
        return f"No conversation found with ID: {args['conversation_id']}"

    # Also get conversation metadata
    conv = conn.execute("SELECT * FROM conversations WHERE id = ?", (args["conversation_id"],)).fetchone()
    output = []
    if conv:
        output.append(f"# {conv['title'] or 'Untitled'}")
        output.append(f"Source: {conv['source']} | Started: {conv['started_at']}")
        output.append("")

    for msg in messages:
        output.append(f"**{msg['role'].upper()}** ({msg['timestamp'][:19]}):")
        output.append(msg["content"])
        output.append("")
    return "\n".join(output)


def handle_pcms_topics(args: dict) -> str:
    from .search import search_topics
    conn = _get_db()

    query = args.get("query", "")
    if query:
        results = search_topics(
            conn, query,
            category=args.get("category"),
            project=args.get("project"),
            limit=args.get("limit", 20),
        )
    else:
        # List all active topics
        sql = "SELECT * FROM topics WHERE status = 'active'"
        params = []
        if args.get("category"):
            sql += " AND category = ?"
            params.append(args["category"])
        if args.get("project"):
            sql += " AND project = ?"
            params.append(args["project"])
        sql += " ORDER BY category, created_at DESC LIMIT ?"
        params.append(args.get("limit", 20))
        results = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if not results:
        return "No topics found."

    output = []
    for t in results:
        proj = f" [{t['project']}]" if t.get('project') else ""
        output.append(f"[{t['category']}]{proj} {t['name']}")
        output.append(f"  {t['summary']}")
        output.append(f"  (id: {t['id']}, confidence: {t.get('confidence', 1.0)})")
        output.append("")
    return "\n".join(output)


def handle_pcms_add_topic(args: dict) -> str:
    from .models import Topic
    from .db import write_audit
    conn = _get_db()

    topic = Topic(
        name=args["name"],
        category=args["category"],
        summary=args["summary"],
        detail=args.get("detail"),
        project=args.get("project"),
        tags=args.get("tags", []),
    )

    conn.execute(
        """INSERT INTO topics (id, name, category, summary, detail, source_messages, project, confidence, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (topic.id, topic.name, topic.category, topic.summary, topic.detail,
         topic.source_messages_json(), topic.project, topic.confidence, topic.tags_json())
    )
    conn.commit()
    write_audit(conn, "topic_create", "topics", topic.id, "mcp:claude_code",
                json.dumps({"name": topic.name, "category": topic.category}))

    return f"Topic saved: '{topic.name}' ({topic.category}) — id: {topic.id}"


def handle_pcms_ingest_note(args: dict) -> str:
    from .models import Conversation, Message, new_id, now_iso
    from .db import write_audit
    conn = _get_db()

    conv = Conversation(
        source=args.get("source", "manual"),
        source_id=new_id(),
        title=args["title"],
        started_at=now_iso(),
        tags=args.get("tags", []),
        metadata={"ingested_via": "mcp"},
    )

    msg = Message(
        conversation_id=conv.id,
        role="user",
        content=args["content"],
        timestamp=now_iso(),
        sequence_num=0,
    )

    conn.execute(
        """INSERT INTO conversations (id, source, source_id, title, started_at, imported_at, tags, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (conv.id, conv.source, conv.source_id, conv.title,
         conv.started_at, conv.imported_at, conv.tags_json(), conv.metadata_json())
    )
    conn.execute(
        """INSERT INTO messages (id, conversation_id, role, content, timestamp, metadata, sequence_num)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (msg.id, msg.conversation_id, msg.role, msg.content,
         msg.timestamp, msg.metadata_json(), msg.sequence_num)
    )
    conn.commit()
    write_audit(conn, "import", "conversations", conv.id, "mcp:claude_code",
                json.dumps({"title": conv.title}))

    return f"Note saved: '{conv.title}' — conv_id: {conv.id}"


def handle_pcms_stats(args: dict) -> str:
    from .search import get_stats
    conn = _get_db()
    s = get_stats(conn)

    lines = [
        f"Conversations: {s['conversations']}",
        f"Messages: {s['messages']}",
        f"Active Topics: {s['topics']}",
        f"Pending Actions: {s['pending_actions']}",
    ]
    if s["by_source"]:
        lines.append("\nBy source:")
        for source, count in s["by_source"].items():
            lines.append(f"  {source}: {count}")
    if s["by_category"]:
        lines.append("\nBy category:")
        for cat, count in s["by_category"].items():
            lines.append(f"  {cat}: {count}")
    return "\n".join(lines)


def handle_pcms_pending(args: dict) -> str:
    from .approval import list_pending
    conn = _get_db()
    pending = list_pending(conn)

    if not pending:
        return "No pending actions. Everything is clean."

    output = [f"{len(pending)} pending action(s):\n"]
    for p in pending:
        output.append(f"[{p['id'][:8]}] {p['action_type'].upper()} on {p['target_table']}")
        output.append(f"  Reason: {p['reason'][:150]}")
        output.append(f"  Proposed: {p['proposed_at']}")
        output.append("")
    return "\n".join(output)


def handle_pcms_rebuild_md(args: dict) -> str:
    from .md_builder import rebuild_all
    conn = _get_db()
    rebuild_all(conn)
    from .config import CLAUDE_MD_PATH, MD_OUTPUT_DIR
    return f"Rebuilt CLAUDE.md and memory/ directory.\nCLAUDE.md: {CLAUDE_MD_PATH}\nMemory dir: {MD_OUTPUT_DIR}"


TOOL_HANDLERS = {
    "pcms_search": handle_pcms_search,
    "pcms_recall": handle_pcms_recall,
    "pcms_topics": handle_pcms_topics,
    "pcms_add_topic": handle_pcms_add_topic,
    "pcms_ingest_note": handle_pcms_ingest_note,
    "pcms_stats": handle_pcms_stats,
    "pcms_pending": handle_pcms_pending,
    "pcms_rebuild_md": handle_pcms_rebuild_md,
}


# ============================================================
# MCP JSON-RPC PROTOCOL
# ============================================================

def handle_request(request: dict) -> dict:
    """Handle a JSON-RPC request from Claude Code."""
    method = request.get("method", "")
    req_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "pcms",
                    "version": "1.0.0",
                },
            },
        }

    elif method == "notifications/initialized":
        return None  # no response needed for notifications

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if not handler:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}],
                    "isError": True,
                },
            }

        try:
            result_text = handler(tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result_text}],
                },
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {str(e)}"}],
                    "isError": True,
                },
            }

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    else:
        # Unknown method — return empty for notifications, error for requests
        if req_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None


def main():
    """Run the MCP server over stdio."""
    # Ensure PCMS_HOME is set
    if "PCMS_HOME" not in os.environ:
        os.environ["PCMS_HOME"] = str(Path.home() / ".pcms")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue

        response = handle_request(request)

        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
