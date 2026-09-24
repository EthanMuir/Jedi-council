"""Task #74 -- persisted per-seat/role model overrides set from the
Settings pane. A tiny dedicated SQLite file (settings_db_path), separate
from the Crypt (append-only/hash-chained -- the wrong shape for a value
that must be freely overwritten) and from DiskCache (TTL-based -- the
wrong shape for a choice meant to persist until changed again)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from council.engine.model_catalog import RECOMMENDED, get_model

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_overrides (
    role TEXT PRIMARY KEY,
    model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS free_mode (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    previous_overrides_json TEXT NOT NULL,
    enabled_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
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


# ---- free mode: one switch that moves every seat to a free-tier model --------
# Turning it on remembers the per-seat choices it replaces; turning it off
# puts exactly those back, so a user who had customised their paid setup
# doesn't lose it by trying free mode.


def free_mode_enabled(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM free_mode WHERE id = 1").fetchone() is not None


def enable_free_mode(conn: sqlite3.Connection, free_model_id: str) -> None:
    model = get_model(free_model_id)
    if model is None or not model.free:
        raise ValueError(f"{free_model_id!r} is not a free-tier model")
    if not free_mode_enabled(conn):
        conn.execute(
            "INSERT INTO free_mode (id, previous_overrides_json) VALUES (1, ?)",
            (json.dumps(get_overrides(conn)),),
        )
    for role in RECOMMENDED:
        set_override(conn, role, free_model_id)
    conn.commit()


def disable_free_mode(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT previous_overrides_json FROM free_mode WHERE id = 1").fetchone()
    if row is None:
        return
    previous = json.loads(row["previous_overrides_json"])
    conn.execute("DELETE FROM model_overrides")
    for role, model_id in previous.items():
        if role in RECOMMENDED and get_model(model_id) is not None:
            set_override(conn, role, model_id)
    conn.execute("DELETE FROM free_mode WHERE id = 1")
    conn.commit()
