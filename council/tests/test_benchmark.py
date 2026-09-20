"""Addendum A10 scoreboard: council must be visibly compared against
buy-and-hold and always-bullish baselines, not just scored in isolation."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from council.calibration.benchmark import compute_benchmark
from council.crypt.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "crypt.db"))
    yield c
    c.close()


def _insert(conn, blind_vote, blind_p, council_vote, council_p, p_extremized, realised_move_pct):
    pred_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO predictions (id, created_at, ticker, horizon, resolve_at, "
        "price_at_prediction, data_snapshot_hash, model_versions_json, blind_vote, "
        "blind_probability, blind_consensus_pct, council_vote, council_confidence, "
        "p_extremized, discussion_enabled, prev_hash, row_hash) "
        "VALUES (?, ?, 'NVDA', '1w', ?, 100.0, 'x', '{}', ?, ?, 100.0, ?, ?, ?, 0, 'x', ?)",
        (
            pred_id, now.isoformat(), (now + timedelta(days=1)).isoformat(),
            blind_vote, blind_p, council_vote, council_p, p_extremized, pred_id,
        ),
    )
    conn.execute(
        "INSERT INTO resolutions (prediction_id, resolved_at, price_at_resolve, realised_move_pct) "
        "VALUES (?, ?, 101.0, ?)",
        (pred_id, now.isoformat(), realised_move_pct),
    )
    conn.commit()


def test_empty_crypt_returns_zero_resolutions(conn):
    assert compute_benchmark(conn) == {"n_resolutions": 0}


def test_always_bullish_and_buy_and_hold_computed(conn):
    _insert(conn, "BULLISH", 0.6, "BULLISH", 0.6, 0.6, realised_move_pct=2.0)
    _insert(conn, "BULLISH", 0.6, "BULLISH", 0.6, 0.6, realised_move_pct=-1.0)

    bench = compute_benchmark(conn)
    assert bench["n_resolutions"] == 2
    assert bench["always_bullish"]["hit_rate"] == 0.5  # 1 of 2 moves were up
    assert bench["buy_and_hold_avg_return_pct"] == pytest.approx(0.5, abs=1e-6)


def test_council_arms_scored_independently(conn):
    # blind said bullish (wrong), final council said bearish (right)
    _insert(conn, "BULLISH", 0.6, "BEARISH", 0.65, 0.35, realised_move_pct=-2.0)

    bench = compute_benchmark(conn)
    assert bench["council_blind"]["hit_rate"] == 0.0
    assert bench["council_final"]["hit_rate"] == 1.0


def test_no_conviction_rows_excluded_from_council_final_arm(conn):
    _insert(conn, "BULLISH", 0.6, "NO_CONVICTION", 0.5, 0.5, realised_move_pct=3.0)
    bench = compute_benchmark(conn)
    assert bench["council_final"]["n"] == 0
    assert bench["council_final"]["hit_rate"] is None
