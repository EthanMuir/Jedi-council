"""Task #74 -- persisted per-seat/role model overrides set from the
Settings pane. A tiny dedicated SQLite file (settings_db_path), separate
from the Crypt (append-only/hash-chained -- the wrong shape for a value
that must be freely overwritten) and from DiskCache (TTL-based -- the
wrong shape for a choice meant to persist until changed again)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from council.engine.model_catalog import RECOMMENDED, get_model

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_overrides (
    role TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn


def get_overrides(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT role, model_id FROM model_overrides").fetchall()
    return {row["role"]: row["model_id"] for row in rows}


def get_effective_model(conn: sqlite3.Connection, role: str) -> str:
    """The model a role actually uses right now: its override if one is
    set, otherwise the catalog's recommendation."""
    row = conn.execute(
        "SELECT model_id FROM model_overrides WHERE role = ?", (role,)
    ).fetchone()
    if row:
        return row["model_id"]
    return RECOMMENDED.get(role, "claude-sonnet-5")


def set_override(conn: sqlite3.Connection, role: str, model_id: str) -> None:
    if role not in RECOMMENDED:
        raise ValueError(f"unknown role {role!r}")
    if get_model(model_id) is None:
        raise ValueError(f"unknown model id {model_id!r}")
    conn.execute(
        "INSERT INTO model_overrides (role, model_id) VALUES (?, ?) "
        "ON CONFLICT(role) DO UPDATE SET model_id = excluded.model_id, "
        "updated_at = datetime('now')",
        (role, model_id),
    )
    conn.commit()


def clear_override(conn: sqlite3.Connection, role: str) -> None:
    """Reverts a role back to the catalog's recommended model."""
    conn.execute("DELETE FROM model_overrides WHERE role = ?", (role,))
    conn.commit()
