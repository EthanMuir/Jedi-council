"""Problem reports people send from the Guide ("Report a problem"). Kept in
settings.db next to accounts; the admin page lists them and marks them
resolved."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

KINDS = {
    "broken": "Something's broken",
    "result": "A result looks wrong",
    "idea": "Idea or request",
    "other": "Something else",
}
MAX_MESSAGE = 4000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS problem_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    page TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    ticker TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS problem_reports_status ON problem_reports(status, created_at);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create(db_path: str, *, user_id: int | None, name: str, email: str, kind: str, message: str,
           page: str = "", run_id: str = "", ticker: str = "", user_agent: str = "") -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO problem_reports (user_id, name, email, kind, message, page, run_id, ticker, "
            "user_agent, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, name, email, kind, message, page[:200], run_id[:100], ticker[:12],
             user_agent[:300], _now().isoformat(timespec="seconds")),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def recent_count(db_path: str, user_id: int | None, hours: int = 1) -> int:
    since = (_now() - timedelta(hours=hours)).isoformat(timespec="seconds")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM problem_reports WHERE COALESCE(user_id, 0) = ? AND created_at >= ?",
            (user_id or 0, since),
        ).fetchone()
        return int(row[0])
    finally:
        conn.close()


def list_reports(db_path: str, status: str | None = None, limit: int = 200) -> list[dict]:
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM problem_reports"
        params: list = []
        if status in ("open", "resolved"):
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def open_count(db_path: str) -> int:
    conn = _connect(db_path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM problem_reports WHERE status = 'open'").fetchone()[0])
    finally:
        conn.close()


def set_status(db_path: str, report_id: int, status: str) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE problem_reports SET status = ?, resolved_at = ? WHERE id = ?",
            (status, _now().isoformat(timespec="seconds") if status == "resolved" else None, report_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
