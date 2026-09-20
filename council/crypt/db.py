"""Connection + hash-chain primitives for the Crypt."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
GENESIS_HASH = "0" * 64


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text())
    conn.commit()
    return conn


def get_last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT row_hash FROM predictions ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    return row["row_hash"] if row else GENESIS_HASH


def compute_row_hash(fields: dict, prev_hash: str) -> str:
    """Deterministic hash over the row's own fields plus the previous row's
    hash, so any historical edit breaks the chain from that point forward."""
    canonical = json.dumps(fields, sort_keys=True, default=str)
    payload = f"{prev_hash}:{canonical}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def get_open_tickers(conn: sqlite3.Connection) -> list[str]:
    """Tickers with a prediction row that has no matching resolution yet --
    used by the Risk Warden's concentration check. Since the resolution
    sweep is Phase 4, every prediction is currently "open"."""
    rows = conn.execute(
        "SELECT p.ticker FROM predictions p "
        "LEFT JOIN resolutions r ON r.prediction_id = p.id "
        "WHERE r.prediction_id IS NULL"
    ).fetchall()
    return [row["ticker"] for row in rows]
