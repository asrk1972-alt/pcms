"""PCMS configuration and paths."""

import os
from pathlib import Path

# --- Paths ---
PCMS_HOME = Path(os.environ.get("PCMS_HOME", Path.home() / ".pcms"))
DB_PATH = PCMS_HOME / "pcms.db"
MD_OUTPUT_DIR = PCMS_HOME / "memory"
CLAUDE_MD_PATH = PCMS_HOME / "CLAUDE.md"
PENDING_ACTIONS_LOG = PCMS_HOME / "audit"

# --- Staleness Rules ---
STALENESS_RULES = {
    "no_reference_days": 180,       # topic not referenced in 6 months → propose archive
    "superseded_threshold": 0.85,   # >85% overlap with newer topic → propose supersede
    "conversation_age_days": 365,   # conversations >1 year → propose archive
}

# --- Importer Settings ---
DEDUP_ENABLED = True
MAX_IMPORT_BATCH = 500

# --- MD Builder Settings ---
CLAUDE_MD_MAX_LINES = 200          # keep the index file compact for agent context windows
RECENT_DECISIONS_DAYS = 30
RECENT_CONVERSATIONS_DAYS = 14

# --- Source identifiers ---
VALID_SOURCES = {"claude", "chatgpt", "cursor", "gemini", "manual"}


def ensure_dirs():
    """Create all required directories if they don't exist."""
    PCMS_HOME.mkdir(parents=True, exist_ok=True)
    MD_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (MD_OUTPUT_DIR / "projects").mkdir(exist_ok=True)
    (MD_OUTPUT_DIR / "decisions").mkdir(exist_ok=True)
    (MD_OUTPUT_DIR / "preferences").mkdir(exist_ok=True)
    (MD_OUTPUT_DIR / "people").mkdir(exist_ok=True)
    (MD_OUTPUT_DIR / "insights").mkdir(exist_ok=True)
    (MD_OUTPUT_DIR / "conversations").mkdir(exist_ok=True)
    PENDING_ACTIONS_LOG.mkdir(parents=True, exist_ok=True)
