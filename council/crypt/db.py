"""Connection + hash-chain primitives for the Crypt."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
GENESIS_HASH = "0" * 64

# Columns added to `predictions` after its original CREATE TABLE shipped.
# `CREATE TABLE IF NOT EXISTS` is a no-op against an already-existing table,
# so an on-disk database created before one of these existed never picks it
# up on its own -- pulling new code doesn't touch an already-created local
# .db file. Each ALTER TABLE here is itself idempotent (skipped once the
# column exists), so this runs safely on every connect(), fresh database or
# old one alike. ALTER TABLE ADD COLUMN is DDL, not a row UPDATE, so it is
# not blocked by the immutability triggers below.
_PREDICTIONS_COLUMN_MIGRATIONS = [
    ("p_raw", "REAL"),
    ("p_extremized", "REAL"),
    ("total_cost_usd", "REAL"),
    ("total_input_tokens", "INTEGER"),
    ("total_output_tokens", "INTEGER"),
    ("run_mode", "TEXT"),  # "free" | "paid" | "sample"
    ("run_shape", "TEXT"),  # "full" | "lite"
    # One run writes one row per term (short/medium/long); run_id ties them
    # together, and synthesis_json carries the Grand Master's notes and each
    # term's position and warnings, identical on all three rows.
    ("run_id", "TEXT"),
    ("synthesis_json", "TEXT"),
    # Whose run it is. NULL is the site's owner (every run from before
    # accounts, and every run when sign-in is off); anyone else's id.
    ("user_id", "INTEGER"),
]


def owner_filter(account: int, column: str = "user_id") -> tuple[str, list]:
    """SQL (and its params) keeping only one person's rows: the owner's
    are NULL or 0, anyone else's carry their id."""
    if account == 0:
        return f"({column} IS NULL OR {column} = 0)", []
    return f"{column} = ?", [account]


def effective_run_mode(run_mode: str | None, total_cost_usd: float | None) -> str:
    """Runs saved before run_mode existed were either sample runs (nothing
    billed) or paid ones -- free mode didn't exist yet."""
    if run_mode:
        return run_mode
    return "paid" if total_cost_usd else "sample"


def _migrate_predictions_table(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(predictions)")}
    for column, sql_type in _PREDICTIONS_COLUMN_MIGRATIONS:
        if column not in existing:
            conn.execute(f"ALTER TABLE predictions ADD COLUMN {column} {sql_type}")
    conn.commit()


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_PATH.read_text())
    conn.commit()
    _migrate_predictions_table(conn)
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


def get_sweepable_predictions(conn: sqlite3.Connection, as_of: str) -> list[sqlite3.Row]:
    """Predictions whose resolve_at has passed `as_of` and have no
    resolution row yet -- exactly what the resolution sweep should touch."""
    return conn.execute(
        "SELECT p.* FROM predictions p "
        "LEFT JOIN resolutions r ON r.prediction_id = p.id "
        "WHERE r.prediction_id IS NULL AND p.resolve_at <= ? "
        "ORDER BY p.resolve_at ASC",
        (as_of,),
    ).fetchall()


def get_open_tickers(conn: sqlite3.Connection, account: int = 0) -> list[str]:
    """Tickers with a prediction row that has no matching resolution yet --
    used by the Risk Warden's concentration check, on one person's own
    runs (someone else's open NVDA call isn't in your book)."""
    mine, params = owner_filter(account, "p.user_id")
    rows = conn.execute(
        "SELECT p.ticker FROM predictions p "
        "LEFT JOIN resolutions r ON r.prediction_id = p.id "
        f"WHERE r.prediction_id IS NULL AND {mine}",
        params,
    ).fetchall()
    return [row["ticker"] for row in rows]
