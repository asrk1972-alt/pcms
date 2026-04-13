"""MD Builder — generates the two-layer .md output for agent consumption.

Layer 1: CLAUDE.md — compact index (~200 lines), always rebuilt fresh.
Layer 2: memory/ directory — detailed .md files per topic/project/category.

Agents read CLAUDE.md on startup, then follow links to deeper files as needed.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import (
    CLAUDE_MD_PATH, MD_OUTPUT_DIR,
    RECENT_DECISIONS_DAYS, RECENT_CONVERSATIONS_DAYS,
)


def rebuild_all(conn: sqlite3.Connection):
    """Full rebuild of CLAUDE.md and all memory/*.md files."""
    _build_claude_md(conn)
    _build_index_md(conn)
    _build_project_files(conn)
    _build_decision_files(conn)
    _build_preference_files(conn)
    _build_insight_files(conn)
    _build_conversation_digests(conn)


def _build_claude_md(conn: sqlite3.Connection):
    """Build the top-level CLAUDE.md agent index."""
    now = datetime.now(timezone.utc)
    stats = _get_counts(conn)
    recent_cutoff = (now - timedelta(days=RECENT_DECISIONS_DAYS)).isoformat()

    lines = [
        "# PCMS Memory Index",
        f"> Last rebuilt: {now.isoformat()} | Topics: {stats['topics']} | Conversations: {stats['conversations']}",
        "",
    ]

    # Active Projects
    projects = conn.execute(
        "SELECT DISTINCT project FROM topics WHERE project IS NOT NULL AND status = 'active' ORDER BY project"
    ).fetchall()
    if projects:
        lines.append("## Active Projects")
        for row in projects:
            p = row["project"]
            topic_count = conn.execute(
                "SELECT COUNT(*) FROM topics WHERE project = ? AND status = 'active'", (p,)
            ).fetchone()[0]
            lines.append(f"- **{p}** — {topic_count} active topics. [Details](memory/projects/{_slugify(p)}.md)")
        lines.append("")

    # Recent Decisions
    decisions = conn.execute(
        "SELECT name, created_at, project FROM topics WHERE category = 'decision' AND status = 'active' AND created_at > ? ORDER BY created_at DESC LIMIT 10",
        (recent_cutoff,)
    ).fetchall()
    if decisions:
        lines.append(f"## Recent Decisions (last {RECENT_DECISIONS_DAYS} days)")
        for d in decisions:
            date_str = d["created_at"][:10]
            proj = f" ({d['project']})" if d["project"] else ""
            lines.append(f"- {d['name']}{proj} — {date_str} [→](memory/decisions/{_slugify(d['name'])}.md)")
        lines.append("")

    # Key Preferences
    prefs = conn.execute(
        "SELECT name, summary FROM topics WHERE category = 'preference' AND status = 'active' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()
    if prefs:
        lines.append("## Key Preferences")
        for p in prefs:
            lines.append(f"- {p['summary']}")
        lines.append(f"- [Full list →](memory/preferences/all-preferences.md)")
        lines.append("")

    # Top Insights
    insights = conn.execute(
        "SELECT name, summary FROM topics WHERE category = 'insight' AND status = 'active' ORDER BY confidence DESC, created_at DESC LIMIT 5"
    ).fetchall()
    if insights:
        lines.append("## Top Insights")
        for ins in insights:
            lines.append(f"- {ins['summary']} [→](memory/insights/{_slugify(ins['name'])}.md)")
        lines.append("")

    # Pending Reviews
    pending_count = conn.execute("SELECT COUNT(*) FROM pending_actions WHERE status = 'pending'").fetchone()[0]
    if pending_count > 0:
        lines.append("## Pending Reviews")
        lines.append(f"- {pending_count} items awaiting approval → run `pcms approve --list`")
        lines.append("")

    # Source breakdown
    lines.append("## Sources")
    sources = conn.execute(
        "SELECT source, COUNT(*) as cnt FROM conversations GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    for s in sources:
        lines.append(f"- {s['source']}: {s['cnt']} conversations")
    lines.append("")

    CLAUDE_MD_PATH.write_text("\n".join(lines), encoding="utf-8")


def _build_index_md(conn: sqlite3.Connection):
    """Build memory/_index.md — master topic index."""
    topics = conn.execute(
        "SELECT id, name, category, project, summary, created_at FROM topics WHERE status = 'active' ORDER BY category, name"
    ).fetchall()

    lines = ["# PCMS Topic Index", ""]
    current_cat = None

    for t in topics:
        if t["category"] != current_cat:
            current_cat = t["category"]
            lines.append(f"## {current_cat.title()}")

        proj = f" [{t['project']}]" if t["project"] else ""
        lines.append(f"- **{t['name']}**{proj} — {t['summary'][:100]}")

    lines.append("")
    (MD_OUTPUT_DIR / "_index.md").write_text("\n".join(lines), encoding="utf-8")


def _build_project_files(conn: sqlite3.Connection):
    """Build one .md per project with all its topics."""
    projects = conn.execute(
        "SELECT DISTINCT project FROM topics WHERE project IS NOT NULL AND status = 'active'"
    ).fetchall()

    for row in projects:
        project = row["project"]
        topics = conn.execute(
            "SELECT * FROM topics WHERE project = ? AND status = 'active' ORDER BY category, created_at",
            (project,)
        ).fetchall()

        lines = [f"# Project: {project}", ""]
        current_cat = None
        for t in topics:
            if t["category"] != current_cat:
                current_cat = t["category"]
                lines.append(f"## {current_cat.title()}")
            lines.append(f"### {t['name']}")
            lines.append(f"{t['summary']}")
            if t["detail"]:
                lines.append(f"\n{t['detail']}")
            lines.append(f"\n*Created: {t['created_at'][:10]} | Confidence: {t['confidence']}*")
            lines.append("")

        filepath = MD_OUTPUT_DIR / "projects" / f"{_slugify(project)}.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")


def _build_decision_files(conn: sqlite3.Connection):
    """Build one .md per decision topic."""
    decisions = conn.execute(
        "SELECT * FROM topics WHERE category = 'decision' AND status = 'active' ORDER BY created_at DESC"
    ).fetchall()

    for d in decisions:
        lines = [
            f"# Decision: {d['name']}",
            "",
            f"**Date:** {d['created_at'][:10]}",
            f"**Project:** {d['project'] or 'General'}",
            f"**Confidence:** {d['confidence']}",
            "",
            "## Summary",
            d["summary"],
        ]
        if d["detail"]:
            lines.extend(["", "## Detail", d["detail"]])

        # Link to source messages
        source_msgs = json.loads(d["source_messages"]) if d["source_messages"] else []
        if source_msgs:
            lines.extend(["", f"## Source", f"Derived from {len(source_msgs)} message(s)."])

        lines.append("")
        filepath = MD_OUTPUT_DIR / "decisions" / f"{_slugify(d['name'])}.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")


def _build_preference_files(conn: sqlite3.Connection):
    """Build a consolidated preferences file."""
    prefs = conn.execute(
        "SELECT * FROM topics WHERE category = 'preference' AND status = 'active' ORDER BY name"
    ).fetchall()

    lines = ["# Preferences", ""]
    for p in prefs:
        lines.append(f"## {p['name']}")
        lines.append(p["summary"])
        if p["detail"]:
            lines.append(f"\n{p['detail']}")
        lines.append("")

    (MD_OUTPUT_DIR / "preferences" / "all-preferences.md").write_text("\n".join(lines), encoding="utf-8")


def _build_insight_files(conn: sqlite3.Connection):
    """Build one .md per insight topic."""
    insights = conn.execute(
        "SELECT * FROM topics WHERE category = 'insight' AND status = 'active' ORDER BY confidence DESC"
    ).fetchall()

    for ins in insights:
        lines = [
            f"# Insight: {ins['name']}",
            "",
            f"**Confidence:** {ins['confidence']}",
            f"**Project:** {ins['project'] or 'General'}",
            "",
            ins["summary"],
        ]
        if ins["detail"]:
            lines.extend(["", ins["detail"]])

        lines.append("")
        filepath = MD_OUTPUT_DIR / "insights" / f"{_slugify(ins['name'])}.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")


def _build_conversation_digests(conn: sqlite3.Connection):
    """Build recent conversation digest .md files."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=RECENT_CONVERSATIONS_DAYS)).isoformat()
    convs = conn.execute(
        "SELECT * FROM conversations WHERE started_at > ? AND is_archived = 0 ORDER BY started_at DESC LIMIT 50",
        (cutoff,)
    ).fetchall()

    for c in convs:
        messages = conn.execute(
            "SELECT role, content, timestamp FROM messages WHERE conversation_id = ? ORDER BY sequence_num",
            (c["id"],)
        ).fetchall()

        lines = [
            f"# {c['title'] or 'Untitled Conversation'}",
            f"**Source:** {c['source']} | **Date:** {c['started_at'][:10]}",
            "",
        ]

        for msg in messages:
            role_label = msg["role"].upper()
            # Truncate very long messages in digests
            content = msg["content"]
            if len(content) > 500:
                content = content[:500] + "... [truncated in digest]"
            lines.append(f"**{role_label}:** {content}")
            lines.append("")

        date_str = c["started_at"][:10]
        slug = _slugify(c["title"] or "untitled")
        filepath = MD_OUTPUT_DIR / "conversations" / f"{date_str}-{slug}.md"
        filepath.write_text("\n".join(lines), encoding="utf-8")


def _get_counts(conn: sqlite3.Connection) -> dict:
    return {
        "conversations": conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0],
        "topics": conn.execute("SELECT COUNT(*) FROM topics WHERE status = 'active'").fetchone()[0],
    }


def _slugify(text: str) -> str:
    """Convert text to a safe filename slug."""
    import re
    slug = text.lower().strip()
    slug = re.sub(r'[^\w\s-]', '', slug)
    slug = re.sub(r'[\s_]+', '-', slug)
    slug = re.sub(r'-+', '-', slug)
    return slug[:80]  # cap length
