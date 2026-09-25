"""Public share links for single runs (/s/<token>). Kept in settings.db,
not the Crypt: the Crypt's rows can't be changed, and a share has to be
switchable off. One live link per run; stopping and re-sharing makes a new
token, so an old link stays dead."""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_shares (
    token TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    user_id INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS run_shares_run ON run_shares(run_id);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def active_token(db_path: str, run_id: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT token FROM run_shares WHERE run_id = ? AND revoked_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return row["token"] if row else None
    finally:
        conn.close()


def create(db_path: str, run_id: str, user_id: int) -> str:
    """The run's live link, making one if there isn't one."""
    existing = active_token(db_path, run_id)
    if existing:
        return existing
    token = secrets.token_urlsafe(12)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO run_shares (token, run_id, user_id, created_at) VALUES (?, ?, ?, ?)",
            (token, run_id, user_id, _now()),
        )
        conn.commit()
    finally:
        conn.close()
    return token


def revoke(db_path: str, run_id: str) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE run_shares SET revoked_at = ? WHERE run_id = ? AND revoked_at IS NULL",
            (_now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def run_for_token(db_path: str, token: str) -> str | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT run_id FROM run_shares WHERE token = ? AND revoked_at IS NULL", (token,)
        ).fetchone()
        return row["run_id"] if row else None
    finally:
        conn.close()
