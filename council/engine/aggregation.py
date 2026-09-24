"""Shared weighted-vote aggregator. Used unweighted for Phase A (the blind
round) and weighted by horizon-competence / data-quality / plausibility for
Phase D (spec section 4):

    final_score = Sigma(seat_vote x seat_weight x confidence x dispersion_penalty
                         x horizon_competence x data_quality_multiplier)

Dispersion is already baked into each seat's stored `probability` (Phase 2's
sampling discount), so it is not applied a second time here -- see
council/engine/sampling.py. seat_weight is the Calibration Officer's output
(council/calibration/officer.py); it stays 1.0 per seat until that seat has
20+ resolved predictions. An abstention (NO_READ / NO_CONVICTION) contributes to neither numerator nor
denominator.
"""
from __future__ import annotations

from council.seats.base import SeatVerdict, is_abstention

DATA_QUALITY_MULTIPLIER = {"GOOD": 1.0, "PARTIAL": 0.7, "POOR": 0.4}


def extremize(p: float, alpha: float = 1.0) -> float:
    """Addendum A4 Stage 3: log-odds extremizing. alpha=1.0 is a no-op.
    Legitimate only under information diversity across the aggregated
    forecasters -- which data-isolated seats are, by construction. Do not
    enable (alpha>1.0) without 50+ resolutions showing it actually helps on
    this system's own data; see Settings.extremize_alpha."""
    if p <= 0.0 or p >= 1.0 or alpha == 1.0:
        return p
    odds = p / (1 - p)
    extremized = (odds**alpha) / (1 + odds**alpha)
    return round(extremized, 3)


def weighted_vote(
    verdicts: dict[str, SeatVerdict], weights: dict[str, float] | None = None
) -> tuple[str, float, float]:
    """Returns (vote, confidence, consensus_pct). `weights` defaults to 1.0
    per seat (Phase A blind round); pass a computed dict for Phase D."""
    weights = weights or {}
    directional = {sid: v for sid, v in verdicts.items() if not is_abstention(v.vote)}
    if not directional:
        return "NO_CONVICTION", 0.5, 0.0

    weighted_sum = 0.0
    weight_total = 0.0
    for sid, v in directional.items():
        w = weights.get(sid, 1.0)
        if w <= 0:
            continue
        p_bullish = v.probability if v.vote == "BULLISH" else 1 - v.probability
        weighted_sum += w * p_bullish
        weight_total += w

    if weight_total == 0:
        return "NO_CONVICTION", 0.5, 0.0

    avg_p_bullish = weighted_sum / weight_total
    if round(avg_p_bullish, 3) == 0.5:
        # Bullish and bearish seats exactly cancel out -- a dead heat is no
        # conviction, not a coin flip that always lands BULLISH.
        return "NO_CONVICTION", 0.5, 0.0
    vote = "BULLISH" if avg_p_bullish > 0.5 else "BEARISH"
    confidence = avg_p_bullish if vote == "BULLISH" else 1 - avg_p_bullish
    agree = sum(1 for v in directional.values() if v.vote == vote)
    consensus_pct = 100.0 * agree / len(directional)
    return vote, round(confidence, 3), round(consensus_pct, 1)
