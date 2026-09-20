"""Resolution logic -- spec section 5: "be precise here, most systems get
this wrong." Sequence matters: a trade that breaks its stop before
reaching its target is a loss even if price later wanders back through the
target. Every hit is checked bar by bar, in the order the bars actually
occurred, not by comparing extremes independently.
"""
from __future__ import annotations

from dataclasses import dataclass

from council.data.schemas import OHLCVBar


@dataclass
class ResolutionOutcome:
    price_at_resolve: float
    high: float
    low: float
    direction_correct: bool | None
    entry_hit: bool | None
    exit_hit: bool | None
    invalidation_hit: bool | None
    mfe_pct: float
    mae_pct: float
    realised_move_pct: float
    brier: float | None


def resolve_prediction(
    bars: list[OHLCVBar],
    *,
    price_at_prediction: float,
    council_vote: str,
    council_confidence: float | None,
    entry: float | None,
    exit: float | None,
    invalidation: float | None,
) -> ResolutionOutcome:
    if not bars:
        raise ValueError("cannot resolve a prediction with no bars in its window")

    bars = sorted(bars, key=lambda b: b.trade_date)
    price_at_resolve = bars[-1].close
    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    realised_move_pct = round(((price_at_resolve / price_at_prediction) - 1) * 100, 3)

    directional = council_vote in ("BULLISH", "BEARISH")
    if directional:
        if price_at_resolve > price_at_prediction:
            actual_direction = "BULLISH"
        elif price_at_resolve < price_at_prediction:
            actual_direction = "BEARISH"
        else:
            actual_direction = None
        direction_correct = actual_direction == council_vote if actual_direction else False
        brier = (
            round((council_confidence - (1.0 if direction_correct else 0.0)) ** 2, 4)
            if council_confidence is not None
            else None
        )
    else:
        direction_correct = None
        brier = None

    entry_hit = exit_hit = invalidation_hit = None
    if directional and entry is not None:
        entry_hit, exit_hit, invalidation_hit = _walk_sequence(
            bars, council_vote, entry, exit, invalidation
        )
        anchor = entry
    else:
        anchor = price_at_prediction

    if directional and council_vote == "BEARISH":
        # favourable = price falling; adverse = price rising
        mfe_pct = round(((anchor / low) - 1) * 100, 3) if low else 0.0
        mae_pct = round(((anchor / high) - 1) * 100, 3) if high else 0.0
    else:
        # favourable = price rising (also the convention used when there is
        # no directional thesis at all -- NO_CONVICTION's mfe/mae are purely
        # descriptive, not an evaluation of correctness)
        mfe_pct = round(((high / anchor) - 1) * 100, 3)
        mae_pct = round(((low / anchor) - 1) * 100, 3)

    return ResolutionOutcome(
        price_at_resolve=price_at_resolve,
        high=high,
        low=low,
        direction_correct=direction_correct,
        entry_hit=entry_hit,
        exit_hit=exit_hit,
        invalidation_hit=invalidation_hit,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
        realised_move_pct=realised_move_pct,
        brier=brier,
    )


def _walk_sequence(
    bars: list[OHLCVBar],
    vote: str,
    entry: float,
    exit: float | None,
    invalidation: float | None,
) -> tuple[bool, bool, bool]:
    """Bar by bar, in order: has the trade even triggered (entry touched)?
    Once triggered, does it hit invalidation or exit first? Whichever comes
    first wins -- a later, opposite touch doesn't undo it."""
    entry_hit = False
    exit_hit = False
    invalidation_hit = False

    for bar in bars:
        if not entry_hit:
            if bar.low <= entry <= bar.high:
                entry_hit = True
            else:
                continue  # not triggered yet -- can't hit exit/invalidation before entry

        if vote == "BULLISH":
            hit_invalidation = invalidation is not None and bar.low <= invalidation
            hit_exit = exit is not None and bar.high >= exit
        else:  # BEARISH
            hit_invalidation = invalidation is not None and bar.high >= invalidation
            hit_exit = exit is not None and bar.low <= exit

        if hit_invalidation and hit_exit:
            # Same bar touched both -- without intrabar sequencing data,
            # assume the worse outcome (invalidation) rather than guess favourably.
            invalidation_hit = True
            break
        if hit_invalidation:
            invalidation_hit = True
            break
        if hit_exit:
            exit_hit = True
            break

    return entry_hit, exit_hit, invalidation_hit
