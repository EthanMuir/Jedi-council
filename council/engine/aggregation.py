"""The council's position on each term, built from every seat's lean.

A seat's lean is turned into a probability the stock goes UP ("p_bullish"):
a BULLISH 0.62 is 0.62, a BEARISH 0.62 is 0.38, and NO_CONVICTION -- a seat
that read its data and found it genuinely even -- is exactly 0.5, so it
pulls the position toward the middle instead of vanishing. NO_READ (no data,
a failed call) is left out entirely. The position is the weighted average
of those numbers (spec section 4):

    weight = term competence x data quality x plausibility x coherence
             x calibration weight

Dispersion is already baked into each seat's stored `probability` (the
sampling discount), so it is not applied a second time here -- see
council/engine/sampling.py. The calibration weight is the Calibration
Officer's output (council/calibration/officer.py); it stays 1.0 per seat
until that seat has 20+ resolved predictions on that term.

The position always leans unless it is dead even -- how far it leans is
what shows how much to trust it (see lean_label)."""
from __future__ import annotations

from dataclasses import dataclass

from council.seats.base import SeatVerdict

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


@dataclass
class CouncilPosition:
    p_bullish: float  # 0-1: where the marker sits on the bearish<->bullish bar
    vote: str  # "BULLISH" | "BEARISH" | "NO_CONVICTION" (dead even, or nobody could read)
    confidence: float  # how likely `vote` is right: max(p_bullish, 1 - p_bullish)
    consensus_pct: float  # share of the seats that leaned a way which agree with `vote`
    lean_label: str
    seats_counted: int  # seats whose lean went into the average


def lean_label(p_bullish: float) -> str:
    """Plain words for how far a position leans -- a weak lean must read
    as weak, not as a call."""
    distance = round(p_bullish - 0.5, 3)
    if distance == 0:
        return "Dead even"
    side = "bullish" if distance > 0 else "bearish"
    distance = abs(distance)
    if distance < 0.02:
        return f"Barely {side}"
    if distance < 0.05:
        return f"Leaning {side}"
    if distance < 0.10:
        return side.capitalize()
    return f"Strongly {side}"


def p_bullish(verdict: SeatVerdict) -> float | None:
    """A seat's lean as the probability the stock goes up; None for NO_READ."""
    if verdict.vote == "NO_READ":
        return None
    if verdict.vote == "NO_CONVICTION":
        return 0.5
    return verdict.probability if verdict.vote == "BULLISH" else round(1 - verdict.probability, 3)


def council_position(
    verdicts: dict[str, SeatVerdict], weights: dict[str, float] | None = None
) -> CouncilPosition:
    """`weights` defaults to 1.0 per seat (the blind round)."""
    weights = weights or {}
    weighted_sum = weight_total = 0.0
    counted = 0
    for sid, verdict in verdicts.items():
        p = p_bullish(verdict)
        w = weights.get(sid, 1.0)
        if p is None or w <= 0:
            continue
        weighted_sum += w * p
        weight_total += w
        counted += 1

    if weight_total == 0:
        return CouncilPosition(0.5, "NO_CONVICTION", 0.5, 0.0, "No read", 0)

    position = round(weighted_sum / weight_total, 3)
    if position == 0.5:
        vote = "NO_CONVICTION"
    else:
        vote = "BULLISH" if position > 0.5 else "BEARISH"
    directional = [v.vote for v in verdicts.values() if v.vote in ("BULLISH", "BEARISH")]
    consensus_pct = (
        round(100.0 * directional.count(vote) / len(directional), 1)
        if directional and vote != "NO_CONVICTION"
        else 0.0
    )
    return CouncilPosition(
        p_bullish=position,
        vote=vote,
        confidence=round(max(position, 1 - position), 3),
        consensus_pct=consensus_pct,
        lean_label=lean_label(position),
        seats_counted=counted,
    )
