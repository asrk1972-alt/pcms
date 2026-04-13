"""PCMS CLI — the primary interface for all pcms operations.

Usage:
    pcms ingest claude ~/exports/claude.json
    pcms search "GraphQL migration"
    pcms topics list --category decision
    pcms approve --list
    pcms md
    pcms check
    pcms stats
"""

import json
import sys
from pathlib import Path

import click

from .db import init_db
from .config import ensure_dirs


@click.group()
@click.pass_context
def cli(ctx):
    """PCMS — Personal AI Memory System. Nothing leaves without your permission."""
    ensure_dirs()
    ctx.ensure_object(dict)
    ctx.obj["conn"] = init_db()


# ============================================================
# INGEST
# ============================================================

@cli.command()
@click.argument("source", type=click.Choice(["claude", "chatgpt", "cursor", "gemini", "manual"]))
@click.argument("path", type=click.Path(exists=True))
@click.pass_context
def ingest(ctx, source: str, path: str):
    """Import chat history from a source."""
    conn = ctx.obj["conn"]
    filepath = Path(path)

    importer = _get_importer(source, conn)
    stats = importer.run(filepath)

    click.echo(f"\n✓ Import complete ({source})")
    click.echo(f"  Imported:   {stats['imported']} conversations")
    click.echo(f"  Duplicates: {stats['skipped_duplicate']} skipped")
    click.echo(f"  Errors:     {stats['errors']}")


def _get_importer(source: str, conn):
    if source == "claude":
        from .importers.claude import ClaudeImporter
        return ClaudeImporter(conn)
    elif source == "chatgpt":
        from .importers.chatgpt import ChatGPTImporter
        return ChatGPTImporter(conn)
    elif source == "manual":
        from .importers.manual import ManualImporter
        return ManualImporter(conn)
    else:
        click.echo(f"Importer for '{source}' not yet implemented. Use 'manual' for now.")
        sys.exit(1)


# ============================================================
# SEARCH
# ============================================================

@cli.command()
@click.argument("query")
@click.option("--source", type=str, default=None, help="Filter by source")
@click.option("--after", type=str, default=None, help="After date (ISO)")
@click.option("--before", type=str, default=None, help="Before date (ISO)")
@click.option("--limit", type=int, default=20)
@click.pass_context
def search(ctx, query: str, source: str, after: str, before: str, limit: int):
    """Search across all stored messages."""
    from .search import search_messages
    conn = ctx.obj["conn"]

    results = search_messages(conn, query, source=source, after=after, before=before, limit=limit)

    if not results:
        click.echo("No results found.")
        return

    for r in results:
        click.echo(f"\n[{r['source']}] {r['conversation_title']}")
        click.echo(f"  {r['role'].upper()} ({r['timestamp'][:10]}):")
        click.echo(f"  {r['snippet']}")


# ============================================================
# TOPICS
# ============================================================

@cli.group()
def topics():
    """Manage knowledge topics."""
    pass


@topics.command("list")
@click.option("--category", type=str, default=None)
@click.option("--project", type=str, default=None)
@click.pass_context
def topics_list(ctx, category: str, project: str):
    """List active topics."""
    conn = ctx.obj["conn"]
    sql = "SELECT * FROM topics WHERE status = 'active'"
    params = []

    if category:
        sql += " AND category = ?"
        params.append(category)
    if project:
        sql += " AND project = ?"
        params.append(project)

    sql += " ORDER BY category, name"
    rows = conn.execute(sql, params).fetchall()

    current_cat = None
    for r in rows:
        if r["category"] != current_cat:
            current_cat = r["category"]
            click.echo(f"\n── {current_cat.upper()} ──")
        proj = f" [{r['project']}]" if r['project'] else ""
        click.echo(f"  {r['name']}{proj} — {r['summary'][:80]}")


@topics.command("create")
@click.option("--category", required=True, type=click.Choice(["decision", "fact", "preference", "insight", "task", "reference"]))
@click.option("--project", type=str, default=None)
@click.option("--confidence", type=float, default=1.0)
@click.argument("name")
@click.pass_context
def topics_create(ctx, category: str, project: str, confidence: float, name: str):
    """Create a new topic manually."""
    from .models import Topic
    conn = ctx.obj["conn"]
    from .db import write_audit

    summary = click.prompt("Summary")
    detail = click.prompt("Detail (optional, press Enter to skip)", default="", show_default=False)

    topic = Topic(
        name=name,
        category=category,
        summary=summary,
        detail=detail or None,
        project=project,
        confidence=confidence,
    )

    conn.execute(
        """INSERT INTO topics (id, name, category, summary, detail, source_messages, project, confidence, tags)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (topic.id, topic.name, topic.category, topic.summary, topic.detail,
         topic.source_messages_json(), topic.project, topic.confidence, topic.tags_json())
    )
    conn.commit()
    write_audit(conn, "topic_create", "topics", topic.id, "user", json.dumps({"name": name}))
    click.echo(f"✓ Created topic: {name} ({category})")


# ============================================================
# APPROVE
# ============================================================

@cli.group()
def approve():
    """Review and approve/reject pending actions."""
    pass


@approve.command("list")
@click.pass_context
def approve_list(ctx):
    """Show all pending actions."""
    from .approval import list_pending
    conn = ctx.obj["conn"]

    pending = list_pending(conn)
    if not pending:
        click.echo("✓ No pending actions. Everything is clean.")
        return

    click.echo(f"\n{len(pending)} pending action(s):\n")
    for p in pending:
        click.echo(f"  [{p['id'][:8]}] {p['action_type'].upper()} on {p['target_table']}")
        click.echo(f"    Proposed by: {p['proposed_by']}")
        click.echo(f"    Reason: {p['reason'][:120]}")
        click.echo(f"    Date: {p['proposed_at']}")
        click.echo()


@approve.command("accept")
@click.argument("action_id")
@click.option("--note", type=str, default=None, help="Optional reviewer note")
@click.pass_context
def approve_accept(ctx, action_id: str, note: str):
    """Approve a pending action."""
    from .approval import approve_action, execute_approved_action
    conn = ctx.obj["conn"]

    # Find by partial ID match
    row = conn.execute(
        "SELECT id FROM pending_actions WHERE id LIKE ? AND status = 'pending'",
        (f"{action_id}%",)
    ).fetchone()

    if not row:
        click.echo(f"No pending action matching '{action_id}'")
        return

    full_id = row["id"]

    # Confirm
    click.echo(f"Approving action {full_id[:8]}...")
    if click.confirm("Are you sure?"):
        approve_action(conn, full_id, reviewer_note=note)
        execute_approved_action(conn, full_id)
        click.echo("✓ Action approved and executed.")
    else:
        click.echo("Cancelled.")


@approve.command("reject")
@click.argument("action_id")
@click.option("--note", type=str, default=None, help="Why you're rejecting this")
@click.pass_context
def approve_reject(ctx, action_id: str, note: str):
    """Reject a pending action — target remains untouched."""
    from .approval import reject_action
    conn = ctx.obj["conn"]

    row = conn.execute(
        "SELECT id FROM pending_actions WHERE id LIKE ? AND status = 'pending'",
        (f"{action_id}%",)
    ).fetchone()

    if not row:
        click.echo(f"No pending action matching '{action_id}'")
        return

    reject_action(conn, row["id"], reviewer_note=note)
    click.echo("✓ Action rejected. Original data untouched.")


# ============================================================
# MD BUILD
# ============================================================

@cli.command("md")
@click.option("--force", is_flag=True, help="Full rebuild even if unchanged")
@click.pass_context
def build_md(ctx, force: bool):
    """Rebuild all .md files from the pcms database."""
    from .md_builder import rebuild_all
    conn = ctx.obj["conn"]

    click.echo("Rebuilding .md files...")
    rebuild_all(conn)
    click.echo("✓ CLAUDE.md and memory/ directory rebuilt.")


# ============================================================
# CHECK (staleness)
# ============================================================

@cli.command("check")
@click.pass_context
def check(ctx):
    """Run staleness detection and propose actions."""
    from .staleness import run_staleness_check
    conn = ctx.obj["conn"]

    results = run_staleness_check(conn)
    click.echo(f"\nStaleness check complete:")
    click.echo(f"  Stale topics proposed for archive:        {results['stale_topics']}")
    click.echo(f"  Old conversations proposed for archive:   {results['old_conversations']}")
    click.echo(f"  Total new pending actions:                {results['total_proposed']}")

    if results["total_proposed"] > 0:
        click.echo(f"\nRun `pcms approve list` to review.")


# ============================================================
# STATS
# ============================================================

@cli.command("stats")
@click.pass_context
def stats(ctx):
    """Show pcms statistics."""
    from .search import get_stats
    conn = ctx.obj["conn"]

    s = get_stats(conn)
    click.echo(f"\n╔══════════════════════════════╗")
    click.echo(f"║       PCMS STATISTICS       ║")
    click.echo(f"╠══════════════════════════════╣")
    click.echo(f"║  Conversations:  {s['conversations']:>10}  ║")
    click.echo(f"║  Messages:       {s['messages']:>10}  ║")
    click.echo(f"║  Active Topics:  {s['topics']:>10}  ║")
    click.echo(f"║  Pending Actions:{s['pending_actions']:>10}  ║")
    click.echo(f"╚══════════════════════════════╝")

    if s["by_source"]:
        click.echo(f"\nBy source:")
        for source, count in s["by_source"].items():
            click.echo(f"  {source}: {count}")

    if s["by_category"]:
        click.echo(f"\nBy category:")
        for cat, count in s["by_category"].items():
            click.echo(f"  {cat}: {count}")


# ============================================================
# AUDIT
# ============================================================

@cli.command("audit")
@click.option("--limit", type=int, default=20)
@click.pass_context
def audit(ctx, limit: int):
    """Show recent audit log entries."""
    conn = ctx.obj["conn"]
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ?", (limit,)
    ).fetchall()

    if not rows:
        click.echo("No audit entries yet.")
        return

    for r in rows:
        click.echo(f"  [{r['timestamp']}] {r['action']} on {r['table_name']}/{r['record_id'][:8]} by {r['actor']}")


# ============================================================
# EXPORT
# ============================================================

@cli.command("export")
@click.option("--format", "fmt", type=click.Choice(["json"]), default="json")
@click.option("--output", type=click.Path(), default="pcms-export.json")
@click.pass_context
def export_data(ctx, fmt: str, output: str):
    """Full pcms export for backup."""
    conn = ctx.obj["conn"]

    data = {
        "exported_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        "conversations": [dict(r) for r in conn.execute("SELECT * FROM conversations").fetchall()],
        "messages": [dict(r) for r in conn.execute("SELECT * FROM messages ORDER BY conversation_id, sequence_num").fetchall()],
        "topics": [dict(r) for r in conn.execute("SELECT * FROM topics").fetchall()],
        "audit_log": [dict(r) for r in conn.execute("SELECT * FROM audit_log ORDER BY timestamp").fetchall()],
    }

    Path(output).write_text(json.dumps(data, indent=2), encoding="utf-8")
    click.echo(f"✓ Exported to {output}")
    click.echo(f"  {len(data['conversations'])} conversations, {len(data['messages'])} messages, {len(data['topics'])} topics")


def main():
    cli()


if __name__ == "__main__":
    main()
