"""API keys entered on the Settings screen, so nobody has to SSH in and edit
.env just to connect their accounts. Stored in the small settings SQLite
file, encrypted (council/secrets_box.py), one set per account: account 0
is the site's owner (and the only account when sign-in is off), every
other person has their own. config.get_settings() layers the owner's over
.env -- a key saved here wins, and removing it falls back to the .env value
(if any). Other people never see or use the .env keys, or anyone else's.

Keys are never sent back to the browser: the API only ever reports whether
a key is set, where it came from, and its last four characters."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from council import secrets_box

if TYPE_CHECKING:
    from council.config import Settings

KEY_NAMES = (
    "anthropic_api_key",
    "google_api_key",
    "groq_api_key",
    "openai_api_key",
    "fred_api_key",
    "alpha_vantage_api_key",
)
OWNER_ACCOUNT = 0

_MAX_KEY_LENGTH = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_api_keys (
    account INTEGER NOT NULL,
    name TEXT NOT NULL,
    value_enc TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (account, name)
);
"""


def _connect(db_path: str, secret: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    _migrate_plaintext_keys(conn, secret)
    return conn


def _migrate_plaintext_keys(conn: sqlite3.Connection, secret: str) -> None:
    """Keys saved before encryption sat in plain text in `api_keys`; they
    were always the owner's. Moved over once, then the old table goes."""
    has_old = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'api_keys'"
    ).fetchone()
    if not has_old:
        return
    for name, value in conn.execute("SELECT name, value FROM api_keys").fetchall():
        if name in KEY_NAMES and value:
            conn.execute(
                "INSERT OR IGNORE INTO account_api_keys (account, name, value_enc) VALUES (?, ?, ?)",
                (OWNER_ACCOUNT, name, secrets_box.encrypt(secret, value)),
            )
    conn.execute("DROP TABLE api_keys")
    conn.commit()


def _secret(db_path: str, secret: str | None) -> str:
    return secret or secrets_box.load_secret(db_path)


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


def saved_keys(db_path: str, account: int = OWNER_ACCOUNT, secret: str | None = None) -> dict[str, str]:
    # Reading must never create the file: get_settings() calls this on
    # every request, including from tools that never meant to touch it.
    if not Path(db_path).exists():
        return {}
    secret = _secret(db_path, secret)
    conn = _connect(db_path, secret)
    try:
        rows = conn.execute(
            "SELECT name, value_enc FROM account_api_keys WHERE account = ?", (account,)
        ).fetchall()
    finally:
        conn.close()
    keys = {}
    for name, value_enc in rows:
        value = secrets_box.decrypt(secret, value_enc)
        if name in KEY_NAMES and value:
            keys[name] = value
    return keys


def save_key(
    db_path: str, name: str, value: str, account: int = OWNER_ACCOUNT, secret: str | None = None
) -> None:
    if name not in KEY_NAMES:
        raise ValueError(f"unknown key {name!r}")
    cleaned = clean_key_value(value)
    secret = _secret(db_path, secret)
    conn = _connect(db_path, secret)
    try:
        conn.execute(
            "INSERT INTO account_api_keys (account, name, value_enc) VALUES (?, ?, ?) "
            "ON CONFLICT(account, name) DO UPDATE SET value_enc = excluded.value_enc, "
            "updated_at = datetime('now')",
            (account, name, secrets_box.encrypt(secret, cleaned)),
        )
        conn.commit()
    finally:
        conn.close()


def delete_key(db_path: str, name: str, account: int = OWNER_ACCOUNT, secret: str | None = None) -> None:
    if name not in KEY_NAMES:
        raise ValueError(f"unknown key {name!r}")
    conn = _connect(db_path, _secret(db_path, secret))
    try:
        conn.execute("DELETE FROM account_api_keys WHERE account = ? AND name = ?", (account, name))
        conn.commit()
    finally:
        conn.close()


def delete_account_keys(db_path: str, account: int) -> None:
    if not Path(db_path).exists():
        return
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_SCHEMA)
        conn.execute("DELETE FROM account_api_keys WHERE account = ?", (account,))
        conn.commit()
    finally:
        conn.close()


def apply_saved_keys(settings: "Settings") -> "Settings":
    """The owner's keys over .env; anyone else gets only their own keys --
    the .env ones are blanked, so they can never run on the owner's."""
    account = settings.council_account
    saved = saved_keys(settings.settings_db_path, account, settings.secret_key or None)
    if account == OWNER_ACCOUNT:
        return settings.model_copy(update=saved) if saved else settings
    return settings.model_copy(update={**{k: "" for k in KEY_NAMES}, **saved})
