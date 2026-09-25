"""Calibration Officer: per-seat scoring derived independently of the
council's own vote, the 20-resolution floor, and percentile exclusion."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from council.calibration.officer import compute_seat_calibration, compute_weights
from council.config import Settings
from council.crypt.db import connect


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "crypt.db"))
    yield c
    c.close()


def _insert_resolved_prediction(
    conn, seat_id, seat_vote, seat_probability, realised_move_pct, horizon="short", run_mode="paid"
):
    pred_id = str(uuid.uuid4())
    now = datetime.utcnow()
    conn.execute(
        "INSERT INTO predictions (id, created_at, ticker, horizon, resolve_at, "
        "price_at_prediction, data_snapshot_hash, model_versions_json, blind_vote, "
        "blind_probability, blind_consensus_pct, discussion_enabled, run_mode, prev_hash, row_hash) "
        "VALUES (?, ?, 'NVDA', ?, ?, 100.0, 'x', '{}', 'BULLISH', 0.6, 100.0, 0, ?, 'x', ?)",
        (pred_id, now.isoformat(), horizon, (now + timedelta(days=1)).isoformat(), run_mode, pred_id),
    )
    conn.execute(
        "INSERT INTO seat_votes (prediction_id, seat_id, vote, probability, verdict_json, created_at) "
        "VALUES (?, ?, ?, ?, '{}', ?)",
        (pred_id, seat_id, seat_vote, seat_probability, now.isoformat()),
    )
    conn.execute(
        "INSERT INTO resolutions (prediction_id, resolved_at, price_at_resolve, realised_move_pct) "
        "VALUES (?, ?, 101.0, ?)",
        (pred_id, now.isoformat(), realised_move_pct),
    )
    conn.commit()
    return pred_id


def test_perfectly_calibrated_seat_scores_well(conn):
    for _ in range(10):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.7, realised_move_pct=2.0)  # correct
    for _ in range(3):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.7, realised_move_pct=-1.0)  # wrong

    calib = compute_seat_calibration(conn, "technician")
    assert calib.n_resolutions == 13
    assert calib.hit_rate == pytest.approx(10 / 13, abs=1e-3)
    assert calib.brier_score is not None
    assert calib.log_loss is not None


def test_no_read_votes_excluded_from_calibration(conn):
    _insert_resolved_prediction(conn, "senate_watcher", "NO_READ", 0.5, realised_move_pct=3.0)
    calib = compute_seat_calibration(conn, "senate_watcher")
    assert calib.n_resolutions == 0


def test_bearish_correctness_evaluated_against_negative_move(conn):
    _insert_resolved_prediction(conn, "catalyst_seer", "BEARISH", 0.65, realised_move_pct=-2.0)
    calib = compute_seat_calibration(conn, "catalyst_seer")
    assert calib.hit_rate == 1.0


def test_weight_stays_1_until_minimum_resolutions_met(conn):
    settings = Settings(calibration_min_resolutions=20)
    for _ in range(19):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.9, realised_move_pct=2.0)
    weights = compute_weights(conn, ["technician"], settings)
    assert weights["technician"] == 1.0  # 19 < 20, not allowed to move yet


def test_weight_moves_after_minimum_resolutions(conn):
    """Weight is relative to the qualifying cohort's mean Brier -- needs a
    second qualified seat to have anything to compare against."""
    settings = Settings(calibration_min_resolutions=20, calibration_min_cohort_for_exclusion=999)
    for _ in range(20):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.9, realised_move_pct=2.0)  # always right
    for _ in range(20):
        _insert_resolved_prediction(conn, "flow_cartographer", "BULLISH", 0.9, realised_move_pct=-1.0)  # always wrong

    weights = compute_weights(conn, ["technician", "flow_cartographer"], settings)
    assert weights["technician"] > 1.0  # better than cohort mean -> upweighted
    assert weights["flow_cartographer"] < 1.0  # worse than cohort mean -> downweighted


def test_bottom_30_percent_excluded_when_cohort_large_enough(conn):
    """10 qualifying seats, strictly ranked performance -- the worst 3
    (30%, rounded up) must be excluded, the best must not be."""
    settings = Settings(calibration_min_resolutions=20, calibration_min_cohort_for_exclusion=8)
    seat_ids = [f"seat_{i}" for i in range(10)]
    # seat_i's hit rate degrades monotonically with i, so ranking is unambiguous
    for i, sid in enumerate(seat_ids):
        wrong_count = i * 2  # seat_0: 0 wrong, seat_9: 18 wrong (out of 20)
        for _ in range(20 - wrong_count):
            _insert_resolved_prediction(conn, sid, "BULLISH", 0.7, realised_move_pct=2.0)
        for _ in range(wrong_count):
            _insert_resolved_prediction(conn, sid, "BULLISH", 0.7, realised_move_pct=-1.0)

    weights = compute_weights(conn, seat_ids, settings)
    assert weights["seat_0"] > 0.0  # best performer, must survive
    assert weights["seat_9"] == 0.0  # worst performer, must be excluded


def test_never_excludes_seat_below_resolution_floor_regardless_of_cohort_size(conn):
    settings = Settings(calibration_min_resolutions=20, calibration_min_cohort_for_exclusion=2)
    # 8 well-qualified good seats + 1 seat with only 5 resolutions and a bad record
    for i in range(8):
        for _ in range(20):
            _insert_resolved_prediction(conn, f"good_{i}", "BULLISH", 0.7, realised_move_pct=2.0)
    for _ in range(5):
        _insert_resolved_prediction(conn, "unproven_bad", "BULLISH", 0.9, realised_move_pct=-1.0)

    weights = compute_weights(conn, [f"good_{i}" for i in range(8)] + ["unproven_bad"], settings)
    assert weights["unproven_bad"] == 1.0  # below floor, never touched


def test_sample_runs_never_train_the_weights(conn):
    """Sample runs replay canned answers -- a seat can't earn (or lose)
    weight from them, however many there are."""
    settings = Settings(calibration_min_resolutions=20)
    for _ in range(30):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.9, realised_move_pct=-3.0, run_mode="sample")
    assert compute_weights(conn, ["technician"], settings) == {"technician": 1.0}
    # Still visible in the track record, where the Crypt labels them SAMPLE.
    assert compute_seat_calibration(conn, "technician").n_resolutions == 30


def test_real_runs_still_move_the_weights_alongside_sample_ones(conn):
    settings = Settings(calibration_min_resolutions=20)
    for _ in range(25):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.7, realised_move_pct=2.0, run_mode="free")
    for _ in range(25):
        _insert_resolved_prediction(conn, "technician", "BULLISH", 0.9, realised_move_pct=-3.0, run_mode="sample")
    calib = compute_seat_calibration(conn, "technician", exclude_sample=True)
    assert calib.n_resolutions == 25 and calib.hit_rate == 1.0
