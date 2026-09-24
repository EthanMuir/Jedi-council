"""API keys entered on the Settings screen, so nobody has to SSH in and edit
.env just to connect their accounts. Stored in the same small settings
SQLite file as the per-seat model overrides, and layered over whatever
.env provides by config.get_settings() -- a key saved here wins, and
removing it falls back to the .env value (if any).

Keys are never sent back to the browser: the API only ever reports whether
a key is set, where it came from, and its last four characters."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from council.config import Settings

KEY_NAMES = (
    "anthropic_api_key",
    "google_api_key",
    "groq_api_key",
    "openai_api_key",
    "fred_api_key",
)

_MAX_KEY_LENGTH = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    return conn


def clean_key_value(raw: str) -> str:
    """Pasted keys often carry a stray space or newline at either end;
    anything inside the key itself means something else got pasted."""
    value = raw.strip()
    if not value:
        raise ValueError("the key is empty")
    if len(value) > _MAX_KEY_LENGTH:
        raise ValueError("that's too long to be an API key")
    if any(ch.isspace() for ch in value):
        raise ValueError("API keys don't contain spaces -- check you copied only the key")
    return value


def saved_keys(db_path: str) -> dict[str, str]:
    # Reading must never create the file: get_settings() calls this on
    # every request, including from tools that never meant to touch it.
    if not Path(db_path).exists():
        return {}
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT name, value FROM api_keys").fetchall()
    finally:
        conn.close()
    return {name: value for name, value in rows if name in KEY_NAMES and value}


def save_key(db_path: str, name: str, value: str) -> None:
    if name not in KEY_NAMES:
        raise ValueError(f"unknown key {name!r}")
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO api_keys (name, value) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now')",
            (name, clean_key_value(value)),
        )
        conn.commit()
    finally:
        conn.close()


def delete_key(db_path: str, name: str) -> None:
    if name not in KEY_NAMES:
        raise ValueError(f"unknown key {name!r}")
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM api_keys WHERE name = ?", (name,))
        conn.commit()
    finally:
        conn.close()


def apply_saved_keys(settings: "Settings") -> "Settings":
    saved = saved_keys(settings.settings_db_path)
    return settings.model_copy(update=saved) if saved else settings
