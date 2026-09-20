"""Crypt immutability, hash chain integrity, and the forward-only
resolution-window guard."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from council.crypt.db import GENESIS_HASH, compute_row_hash, connect, get_last_hash
from council.crypt.ledger import ResolutionWindowError, write_prediction


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "crypt.db"))
    yield c
    c.close()


def _write_sample(conn, ticker="NVDA", days_out=7, created_at=None):
    created_at = created_at or datetime(2026, 9, 18, 12, 0, 0)
    return write_prediction(
        conn,
        ticker=ticker,
        horizon="1w",
        resolve_at=created_at + timedelta(days=days_out),
        price_at_prediction=162.07,
        data_snapshot_hash="deadbeef",
        model_versions={"technician": "claude-sonnet-5"},
        blind_vote="BULLISH",
        blind_probability=0.612,
        blind_consensus_pct=66.7,
        created_at=created_at,
    )


def test_predictions_table_rejects_update(conn):
    pred_id = _write_sample(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE predictions SET blind_vote = 'BEARISH' WHERE id = ?", (pred_id,))


def test_predictions_table_rejects_delete(conn):
    pred_id = _write_sample(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM predictions WHERE id = ?", (pred_id,))


def test_seat_votes_table_rejects_update_and_delete(conn):
    pred_id = _write_sample(conn)
    conn.execute(
        "INSERT INTO seat_votes (prediction_id, seat_id, vote, probability, "
        "verdict_json, created_at) VALUES (?, 'technician', 'BULLISH', 0.6, '{}', ?)",
        (pred_id, datetime.utcnow().isoformat()),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE seat_votes SET vote = 'BEARISH' WHERE prediction_id = ?", (pred_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM seat_votes WHERE prediction_id = ?", (pred_id,))


def test_resolutions_table_rejects_update_and_delete(conn):
    pred_id = _write_sample(conn)
    conn.execute(
        "INSERT INTO resolutions (prediction_id, resolved_at, price_at_resolve) "
        "VALUES (?, ?, ?)",
        (pred_id, datetime.utcnow().isoformat(), 170.0),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE resolutions SET price_at_resolve = 999 WHERE prediction_id = ?", (pred_id,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM resolutions WHERE prediction_id = ?", (pred_id,))


def test_hash_chain_links_successive_rows(conn):
    assert get_last_hash(conn) == GENESIS_HASH
    _write_sample(conn, ticker="NVDA")
    first_hash = get_last_hash(conn)
    assert first_hash != GENESIS_HASH

    _write_sample(conn, ticker="AAPL")
    second_hash = get_last_hash(conn)
    assert second_hash != first_hash

    rows = conn.execute("SELECT ticker, prev_hash, row_hash FROM predictions ORDER BY rowid").fetchall()
    assert rows[0]["prev_hash"] == GENESIS_HASH
    assert rows[0]["row_hash"] == first_hash
    assert rows[1]["prev_hash"] == first_hash
    assert rows[1]["row_hash"] == second_hash


def test_hash_chain_detects_tampering(conn):
    _write_sample(conn, ticker="NVDA")
    row = conn.execute("SELECT * FROM predictions LIMIT 1").fetchone()
    fields = {
        "id": row["id"],
        "created_at": row["created_at"],
        "ticker": row["ticker"],
        "horizon": row["horizon"],
        "resolve_at": row["resolve_at"],
        "price_at_prediction": row["price_at_prediction"],
        "data_snapshot_hash": row["data_snapshot_hash"],
        "model_versions_json": row["model_versions_json"],
        "blind_vote": row["blind_vote"],
        "blind_probability": row["blind_probability"],
        "blind_consensus_pct": row["blind_consensus_pct"],
        "council_vote": row["council_vote"],
        "council_confidence": row["council_confidence"],
        "consensus_pct": row["consensus_pct"],
        "entry": row["entry"],
        "exit": row["exit"],
        "invalidation": row["invalidation"],
        "stop": row["stop"],
        "expected_move_pct": row["expected_move_pct"],
        "base_rate_move_pct": row["base_rate_move_pct"],
        "dissent_summary": row["dissent_summary"],
        "correlated_evidence_warning": row["correlated_evidence_warning"],
        "prosecutor_verdict": row["prosecutor_verdict"],
        "cost_audit_passed": row["cost_audit_passed"],
        "p_raw": row["p_raw"],
        "p_extremized": row["p_extremized"],
        "total_cost_usd": row["total_cost_usd"],
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "discussion_enabled": row["discussion_enabled"],
    }
    recomputed = compute_row_hash(fields, row["prev_hash"])
    assert recomputed == row["row_hash"]

    tampered_fields = dict(fields, blind_vote="BEARISH")
    assert compute_row_hash(tampered_fields, row["prev_hash"]) != row["row_hash"]


def test_resolution_window_guard_rejects_past_resolve_at(conn):
    created_at = datetime(2026, 9, 18, 12, 0, 0)
    with pytest.raises(ResolutionWindowError):
        write_prediction(
            conn,
            ticker="NVDA",
            horizon="1w",
            resolve_at=created_at - timedelta(days=1),
            price_at_prediction=162.07,
            data_snapshot_hash="deadbeef",
            model_versions={},
            blind_vote="BULLISH",
            blind_probability=0.6,
            blind_consensus_pct=100.0,
            created_at=created_at,
        )


def test_resolution_window_guard_rejects_resolve_at_equal_to_created_at(conn):
    created_at = datetime(2026, 9, 18, 12, 0, 0)
    with pytest.raises(ResolutionWindowError):
        write_prediction(
            conn,
            ticker="NVDA",
            horizon="1d",
            resolve_at=created_at,
            price_at_prediction=162.07,
            data_snapshot_hash="deadbeef",
            model_versions={},
            blind_vote="BULLISH",
            blind_probability=0.6,
            blind_consensus_pct=100.0,
            created_at=created_at,
        )
