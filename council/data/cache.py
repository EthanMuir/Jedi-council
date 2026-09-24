"""SQLite-backed disk cache with a per-entry TTL. Shared across providers so
repeated deliberations don't re-hit rate-limited APIs."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache_entries (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


class DiskCache:
    def __init__(self, db_path: str, namespace: str = ""):
        """`namespace` keeps entries from different data sources apart in
        one file -- without it, sample data cached before a market-data key
        was added would keep being served as if it were live, for up to
        its full TTL (hours, for fundamentals)."""
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._prefix = f"{namespace}:" if namespace else ""
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, key: str) -> Any | None:
        key = self._prefix + key
        row = self._conn.execute(
            "SELECT value, expires_at FROM cache_entries WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at < time.time():
            self._conn.execute("DELETE FROM cache_entries WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return json.loads(value)

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._conn.execute(
            "INSERT INTO cache_entries (key, value, expires_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "expires_at = excluded.expires_at",
            (self._prefix + key, json.dumps(value), time.time() + ttl_seconds),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
