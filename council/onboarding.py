"""First sign-in (#125): whether someone has seen the welcome tour and been
through the key setup that follows it, and the parts of the Getting started
checklist that can't be read from elsewhere (opened a seat, added the app to
their Home Screen, hid the checklist).
Kept per account in settings.db, so it follows them to a new phone."""
from __future__ import annotations

import sqlite3
from pathlib import Path

FLAGS = ("tour_done", "setup_done", "checklist_hidden", "seat_opened", "home_screen")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS onboarding (
    account INTEGER PRIMARY KEY,
    tour_done INTEGER NOT NULL DEFAULT 0,
    checklist_hidden INTEGER NOT NULL DEFAULT 0,
    seat_opened INTEGER NOT NULL DEFAULT 0,
    home_screen INTEGER NOT NULL DEFAULT 0,
    setup_done INTEGER NOT NULL DEFAULT 0
);
"""

# Added after the table first shipped; older databases get the column here.
_ADDED_COLUMNS = ("setup_done",)


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(onboarding)")}
    for column in _ADDED_COLUMNS:
        if column not in have:
            conn.execute(f"ALTER TABLE onboarding ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")
    return conn


def get(db_path: str, account: int) -> dict[str, bool]:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM onboarding WHERE account = ?", (account,)).fetchone()
        return {f: bool(row[f]) if row else False for f in FLAGS}
    finally:
        conn.close()


def update(db_path: str, account: int, changes: dict[str, bool]) -> dict[str, bool]:
    changes = {k: bool(v) for k, v in changes.items() if k in FLAGS}
    conn = _connect(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO onboarding (account) VALUES (?)", (account,))
        for key, value in changes.items():
            conn.execute(f"UPDATE onboarding SET {key} = ? WHERE account = ?", (1 if value else 0, account))
        conn.commit()
    finally:
        conn.close()
    return get(db_path, account)
