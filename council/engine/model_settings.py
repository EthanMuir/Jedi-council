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
CREATE TABLE IF NOT EXISTS account_model_overrides (
    account INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL,
    model_id TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account, role)
);
CREATE TABLE IF NOT EXISTS account_free_mode (
    account INTEGER PRIMARY KEY,
    previous_overrides_json TEXT NOT NULL,
    enabled_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Each person has their own model choices and Free Mode switch, keyed by
# account (0 = the owner, and the only account when sign-in is off).
OWNER = 0


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    _migrate_single_user_tables(conn)
    conn.commit()
    return conn


def _migrate_single_user_tables(conn: sqlite3.Connection) -> None:
    """Before accounts, the one set of choices lived in model_overrides /
    free_mode; it was the owner's."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "model_overrides" in tables:
        conn.execute(
            "INSERT OR IGNORE INTO account_model_overrides (account, role, model_id, updated_at) "
            "SELECT 0, role, model_id, updated_at FROM model_overrides"
        )
        conn.execute("DROP TABLE model_overrides")
    if "free_mode" in tables:
        conn.execute(
            "INSERT OR IGNORE INTO account_free_mode (account, previous_overrides_json, enabled_at) "
            "SELECT 0, previous_overrides_json, enabled_at FROM free_mode"
        )
        conn.execute("DROP TABLE free_mode")


def get_overrides(conn: sqlite3.Connection, account: int = OWNER) -> dict[str, str]:
    rows = conn.execute(
        "SELECT role, model_id FROM account_model_overrides WHERE account = ?", (account,)
    ).fetchall()
    return {row["role"]: row["model_id"] for row in rows}


def get_effective_model(conn: sqlite3.Connection, role: str, account: int = OWNER) -> str:
    """The model a role actually uses right now: its override if one is
    set, otherwise the catalog's recommendation."""
    row = conn.execute(
        "SELECT model_id FROM account_model_overrides WHERE account = ? AND role = ?",
        (account, role),
    ).fetchone()
    if row:
        return row["model_id"]
    return RECOMMENDED.get(role, "claude-sonnet-5")


def set_override(conn: sqlite3.Connection, role: str, model_id: str, account: int = OWNER) -> None:
    if role not in RECOMMENDED:
        raise ValueError(f"unknown role {role!r}")
    if get_model(model_id) is None:
        raise ValueError(f"unknown model id {model_id!r}")
    conn.execute(
        "INSERT INTO account_model_overrides (account, role, model_id) VALUES (?, ?, ?) "
        "ON CONFLICT(account, role) DO UPDATE SET model_id = excluded.model_id, "
        "updated_at = datetime('now')",
        (account, role, model_id),
    )
    conn.commit()


def clear_override(conn: sqlite3.Connection, role: str, account: int = OWNER) -> None:
    """Reverts a role back to the catalog's recommended model."""
    conn.execute("DELETE FROM account_model_overrides WHERE account = ? AND role = ?", (account, role))
    conn.commit()


# ---- free mode: one switch that moves every seat to a free-tier model --------
# Turning it on remembers the per-seat choices it replaces; turning it off
# puts exactly those back, so a user who had customised their paid setup
# doesn't lose it by trying free mode.


def free_mode_enabled(conn: sqlite3.Connection, account: int = OWNER) -> bool:
    return conn.execute(
        "SELECT 1 FROM account_free_mode WHERE account = ?", (account,)
    ).fetchone() is not None


def enable_free_mode(conn: sqlite3.Connection, free_model_id: str, account: int = OWNER) -> None:
    model = get_model(free_model_id)
    if model is None or not model.free:
        raise ValueError(f"{free_model_id!r} is not a free-tier model")
    if not free_mode_enabled(conn, account):
        conn.execute(
            "INSERT INTO account_free_mode (account, previous_overrides_json) VALUES (?, ?)",
            (account, json.dumps(get_overrides(conn, account))),
        )
    for role in RECOMMENDED:
        set_override(conn, role, free_model_id, account)
    conn.commit()


def disable_free_mode(conn: sqlite3.Connection, account: int = OWNER) -> None:
    row = conn.execute(
        "SELECT previous_overrides_json FROM account_free_mode WHERE account = ?", (account,)
    ).fetchone()
    if row is None:
        return
    previous = json.loads(row["previous_overrides_json"])
    conn.execute("DELETE FROM account_model_overrides WHERE account = ?", (account,))
    for role, model_id in previous.items():
        if role in RECOMMENDED and get_model(model_id) is not None:
            set_override(conn, role, model_id, account)
    conn.execute("DELETE FROM account_free_mode WHERE account = ?", (account,))
    conn.commit()


def delete_account(conn: sqlite3.Connection, account: int) -> None:
    conn.execute("DELETE FROM account_model_overrides WHERE account = ?", (account,))
    conn.execute("DELETE FROM account_free_mode WHERE account = ?", (account,))
    conn.commit()
