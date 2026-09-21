"""A real bug hit in practice: a local .db file created before Phase 6
added cost-tracking columns kept its stale schema forever -- pulling new
code never touches an already-existing SQLite file, and CREATE TABLE IF
NOT EXISTS is a no-op against a table that already exists. write_prediction
then failed outright with "table predictions has no column named
total_cost_usd" on every single run, even against the latest code."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from council.crypt.db import connect
from council.crypt.ledger import write_prediction

# A stand-in for a predictions table as it looked before Phase 4/6 added
# p_raw, p_extremized, and the three cost-tracking columns -- just enough
# of the real schema to exercise the same migration path Ethan's actual
# on-disk database hit.
_OLD_SCHEMA = """
CREATE TABLE predictions (
    id                          TEXT PRIMARY KEY,
    created_at                  TEXT NOT NULL,
    ticker                      TEXT NOT NULL,
    horizon                     TEXT NOT NULL,
    resolve_at                  TEXT NOT NULL,
    council_vote                TEXT,
    council_confidence          REAL,
    consensus_pct               REAL,
    entry                       REAL,
    exit                        REAL,
    invalidation                REAL,
    stop                        REAL,
    price_at_prediction         REAL NOT NULL,
    expected_move_pct           REAL,
    base_rate_move_pct          REAL,
    dissent_summary             TEXT,
    correlated_evidence_warning TEXT,
    prosecutor_verdict          TEXT,
    cost_audit_passed           INTEGER,
    model_versions_json         TEXT,
    data_snapshot_hash          TEXT NOT NULL,
    blind_vote                  TEXT,
    blind_probability           REAL,
    blind_consensus_pct         REAL,
    discussed_vote               TEXT,
    discussed_probability        REAL,
    discussion_enabled           INTEGER NOT NULL DEFAULT 0,
    flip_count                   INTEGER,
    protected_dissenters         TEXT,
    prev_hash                    TEXT NOT NULL,
    row_hash                     TEXT NOT NULL UNIQUE
);
"""


def test_connect_migrates_a_pre_existing_database_missing_new_columns(tmp_path):
    db_path = str(tmp_path / "old_council.db")

    # Simulate an on-disk database created before the new columns existed --
    # bypassing connect()/schema.sql entirely, the same way a real user's
    # already-existing file predates the current code.
    raw_conn = sqlite3.connect(db_path)
    raw_conn.executescript(_OLD_SCHEMA)
    raw_conn.commit()
    raw_conn.close()

    # connect() must migrate it in place, not just apply the base schema
    # (which would no-op against the already-existing table).
    conn = connect(db_path)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(predictions)")}
    for expected in (
        "p_raw",
        "p_extremized",
        "total_cost_usd",
        "total_input_tokens",
        "total_output_tokens",
    ):
        assert expected in columns

    # And a real write -- the exact operation that used to fail -- must
    # succeed against the migrated table.
    created_at = datetime(2026, 9, 21, 12, 0, 0)
    prediction_id = write_prediction(
        conn,
        ticker="MRVL",
        horizon="1w",
        resolve_at=created_at + timedelta(days=7),
        price_at_prediction=75.0,
        data_snapshot_hash="deadbeef",
        model_versions={"technician": "claude-sonnet-5"},
        blind_vote="BULLISH",
        blind_probability=0.6,
        blind_consensus_pct=80.0,
        total_cost_usd=0.42,
        total_input_tokens=5000,
        total_output_tokens=1200,
        created_at=created_at,
    )
    row = conn.execute("SELECT * FROM predictions WHERE id = ?", (prediction_id,)).fetchone()
    assert row["total_cost_usd"] == 0.42
    conn.close()


def test_connect_is_idempotent_on_an_already_current_database(tmp_path):
    # A fresh database (already has every column) must not error when the
    # same migration logic runs against it a second time.
    db_path = str(tmp_path / "fresh.db")
    connect(db_path).close()
    conn = connect(db_path)  # second connect() -- must not raise
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(predictions)")}
    assert "total_cost_usd" in columns
    conn.close()
