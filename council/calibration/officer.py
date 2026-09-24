"""The Calibration Officer -- "Keeper of the Crypt's Memory". Not an LLM,
pure computation. Reads the resolved Crypt and computes per seat (and
optionally per horizon): hit rate, Brier score, log loss, and a
calibration curve. Output: a weight vector. Every seat starts at 1.0;
weight is only allowed to move once a seat has at least
`Settings.calibration_min_resolutions` resolved predictions -- a lucky
streak can't mint influence.

A seat's own resolutions.direction_correct is scored against the COUNCIL's
vote, not this seat's own -- so per-seat correctness here is derived
independently from the resolution's realised_move_pct sign against this
seat's own vote, per Addendum A4's per-seat calibration requirement.
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

from council.config import Settings
from council.crypt.db import effective_run_mode


@dataclass
class CalibrationBin:
    range_label: str
    mean_predicted: float
    actual_rate: float
    n: int


@dataclass
class SeatCalibration:
    seat_id: str
    horizon: str | None
    n_resolutions: int
    hit_rate: float | None
    brier_score: float | None
    log_loss: float | None
    calibration_curve: list[CalibrationBin] = field(default_factory=list)


def _seat_history_rows(conn: sqlite3.Connection, seat_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT sv.probability, sv.vote, p.horizon, r.realised_move_pct,
               p.run_mode, p.total_cost_usd
        FROM seat_votes sv
        JOIN predictions p ON p.id = sv.prediction_id
        JOIN resolutions r ON r.prediction_id = sv.prediction_id
        WHERE sv.seat_id = ? AND sv.vote != 'NO_READ'
        """,
        (seat_id,),
    ).fetchall()


def _outcomes(rows: list[sqlite3.Row]) -> list[tuple[float, float]]:
    """(predicted_probability, 1.0-if-this-seat's-own-vote-was-correct)."""
    out = []
    for r in rows:
        correct = (r["vote"] == "BULLISH" and r["realised_move_pct"] > 0) or (
            r["vote"] == "BEARISH" and r["realised_move_pct"] < 0
        )
        out.append((r["probability"], 1.0 if correct else 0.0))
    return out


def _calibration_bins(
    outcomes: list[tuple[float, float]], edges: tuple[float, ...] = tuple(x / 10 for x in range(0, 11))
) -> list[CalibrationBin]:
    bins = []
    for lo, hi in zip(edges, edges[1:]):
        bucket = [(p, o) for p, o in outcomes if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not bucket:
            continue
        bins.append(
            CalibrationBin(
                range_label=f"[{lo:.1f},{hi:.1f})",
                mean_predicted=round(sum(p for p, _ in bucket) / len(bucket), 3),
                actual_rate=round(sum(o for _, o in bucket) / len(bucket), 3),
                n=len(bucket),
            )
        )
    return bins


def compute_seat_calibration(
    conn: sqlite3.Connection, seat_id: str, horizon: str | None = None, run_mode: str | None = None
) -> SeatCalibration:
    """`run_mode` ("free" / "paid" / "sample") limits the track record to
    runs of that kind -- free-tier runs are scored apart from paid ones."""
    rows = _seat_history_rows(conn, seat_id)
    if horizon:
        rows = [r for r in rows if r["horizon"] == horizon]
    if run_mode:
        rows = [r for r in rows if effective_run_mode(r["run_mode"], r["total_cost_usd"]) == run_mode]
    outcomes = _outcomes(rows)
    n = len(outcomes)
    if n == 0:
        return SeatCalibration(seat_id, horizon, 0, None, None, None, [])

    hit_rate = sum(o for _, o in outcomes) / n
    brier = sum((p - o) ** 2 for p, o in outcomes) / n
    eps = 1e-6
    log_loss = -sum(
        o * math.log(max(p, eps)) + (1 - o) * math.log(max(1 - p, eps)) for p, o in outcomes
    ) / n

    return SeatCalibration(
        seat_id=seat_id,
        horizon=horizon,
        n_resolutions=n,
        hit_rate=round(hit_rate, 3),
        brier_score=round(brier, 4),
        log_loss=round(log_loss, 4),
        calibration_curve=_calibration_bins(outcomes),
    )


def compute_weights(
    conn: sqlite3.Connection, seat_ids: list[str], settings: Settings, horizon: str | None = None
) -> dict[str, float]:
    """Addendum A4 Stage 1 (percentile selection) + weighting. Never
    excludes a seat with fewer than `calibration_min_resolutions`
    resolutions -- deleting your best seat before it has proven itself is
    exactly the failure mode this floor exists to prevent."""
    calibrations = {sid: compute_seat_calibration(conn, sid, horizon) for sid in seat_ids}
    weights = {sid: 1.0 for sid in seat_ids}

    qualifying = {
        sid: c
        for sid, c in calibrations.items()
        if c.n_resolutions >= settings.calibration_min_resolutions and c.brier_score is not None
    }
    if not qualifying:
        return weights

    if len(qualifying) >= settings.calibration_min_cohort_for_exclusion:
        ranked = sorted(qualifying.items(), key=lambda kv: kv[1].brier_score)
        keep_count = math.ceil(len(ranked) * (1 - settings.calibration_exclude_worst_pct))
        keep_ids = {sid for sid, _ in ranked[:keep_count]}
        for sid in qualifying:
            if sid not in keep_ids:
                weights[sid] = 0.0

    mean_brier = sum(c.brier_score for c in qualifying.values()) / len(qualifying)
    for sid, c in qualifying.items():
        if weights[sid] == 0.0:
            continue  # excluded in Stage 1, stays excluded
        raw = 1.0 + (mean_brier - c.brier_score) / mean_brier if mean_brier > 0 else 1.0
        weights[sid] = round(max(0.25, min(2.0, raw)), 3)

    return weights


RANKS = ("YOUNGLING", "PADAWAN", "KNIGHT", "MASTER", "GRAND_MASTER")


def rank_for_seat(calib: SeatCalibration, min_resolutions: int) -> str:
    """Gated on a resolved track record -- a lucky streak can't mint a
    Master, per spec section 3's Archives description."""
    if calib.n_resolutions < min_resolutions or calib.hit_rate is None:
        return "YOUNGLING"
    if calib.hit_rate >= 0.65:
        return "GRAND_MASTER"
    if calib.hit_rate >= 0.58:
        return "MASTER"
    if calib.hit_rate >= 0.52:
        return "KNIGHT"
    return "PADAWAN"
