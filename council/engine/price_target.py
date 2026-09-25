"""Price targets: a most-likely price and a likely range for each term,
worked out from the run itself -- no extra AI calls, and never at odds with
the verdict.

- How far the stock usually moves over the term (sigma): its daily moves
  over the last two years, scaled to the term's length. For the next week,
  averaged with what the options market expects when that seat read it.
- Where the middle sits: the Council's own chance of rising, p, turned into
  a drift. Price moves are treated as a bell curve in log terms, and the
  drift is set so that the chance of ending above today's price is exactly
  p. So a 54% lean nudges the target up a little; a 50/50 leaves it at
  today's price.
- The range is the middle 70% of that curve.

The chance shown with the range is then calculated from the record: once
enough past ranges for the term have been scored, it's the share that
actually held the final price (blended with the 70% the maths assumes,
until there are plenty). Worked out when the run happens and saved with it,
so a run's number never changes afterwards."""
from __future__ import annotations

import json
import math
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from statistics import NormalDist

from council.data.schemas import OHLCVBar
from council.engine.horizons import HORIZON_TRADING_DAYS

NOMINAL = 0.70           # the share of the curve the range covers
PRIOR_WEIGHT = 20        # how many scored ranges the 70% counts as
_NORMAL = NormalDist()
# E|X| = sigma * sqrt(2/pi) for a bell curve centred on zero.
_MEAN_ABS = math.sqrt(2 / math.pi)


@dataclass
class PriceTarget:
    price_now: float
    target: float             # the most likely (median) price at the end of the term
    low: float
    high: float
    chance_pct: int           # the calculated chance the price ends in [low, high]
    nominal_pct: int          # what the maths alone assumes (70)
    scored_ranges: int        # how many past ranges the chance is drawn from
    sigma_pct: float          # how far this stock usually moves over the term, one standard deviation

    def as_dict(self) -> dict:
        return asdict(self)


def term_sigma(bars: list[OHLCVBar], term: str, options_implied_move_pct: float | None = None) -> float | None:
    """One standard deviation of the log price change over the term, as a
    fraction (0.25 = 25%). None without enough price history."""
    closes = [b.close for b in bars if b.close and b.close > 0]
    if len(closes) < 20:
        return None
    daily = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    sigma = statistics.pstdev(daily) * math.sqrt(HORIZON_TRADING_DAYS[term])
    if options_implied_move_pct and term == "short":
        sigma = (sigma + (options_implied_move_pct / 100) / _MEAN_ABS) / 2
    return sigma if sigma > 0 else None


def calibrated_chance(hits: int, scored: int) -> float:
    """The chance a range holds the final price, from how past ranges did,
    pulled toward the nominal 70% while there are few of them."""
    return (hits + NOMINAL * PRIOR_WEIGHT) / (scored + PRIOR_WEIGHT)


def price_target(
    price_now: float,
    p_bullish: float,
    sigma: float | None,
    *,
    hits: int = 0,
    scored: int = 0,
) -> PriceTarget | None:
    if not price_now or not sigma:
        return None
    p = min(0.98, max(0.02, p_bullish))
    drift = sigma * _NORMAL.inv_cdf(p)
    z = _NORMAL.inv_cdf(0.5 + NOMINAL / 2)
    return PriceTarget(
        price_now=round(price_now, 2),
        target=round(price_now * math.exp(drift), 2),
        low=round(price_now * math.exp(drift - z * sigma), 2),
        high=round(price_now * math.exp(drift + z * sigma), 2),
        chance_pct=round(calibrated_chance(hits, scored) * 100),
        nominal_pct=round(NOMINAL * 100),
        scored_ranges=scored,
        sigma_pct=round(sigma * 100, 2),
    )


def landed_in_range(target: dict, realised_move_pct: float | None) -> bool | None:
    """Whether the price at the end of the term was inside the range."""
    if realised_move_pct is None or not target:
        return None
    final = target["price_now"] * (1 + realised_move_pct / 100)
    return target["low"] <= final <= target["high"]


def final_price(target: dict, realised_move_pct: float | None) -> float | None:
    if realised_move_pct is None or not target:
        return None
    return round(target["price_now"] * (1 + realised_move_pct / 100), 2)


def range_record(conn: sqlite3.Connection, term: str | None = None, limit: int = 5000) -> dict[str, tuple[int, int]]:
    """How past ranges did, per term: {term: (landed in range, scored)}.
    Pooled across everyone's runs, like the seats' track records."""
    rows = conn.execute(
        """
        SELECT p.horizon, r.realised_move_pct,
               (SELECT s.synthesis_json FROM predictions s WHERE s.run_id = p.run_id AND s.synthesis_json IS NOT NULL LIMIT 1) AS synthesis_json
        FROM predictions p JOIN resolutions r ON r.prediction_id = p.id
        WHERE p.run_id IS NOT NULL AND (? IS NULL OR p.horizon = ?)
        ORDER BY r.resolved_at DESC LIMIT ?
        """,
        (term, term, limit),
    ).fetchall()
    record: dict[str, list[int]] = {}
    for horizon, realised, synthesis_json in rows:
        if not synthesis_json:
            continue
        try:
            target = (json.loads(synthesis_json).get("terms") or {}).get(horizon, {}).get("price_target")
        except (ValueError, AttributeError):
            continue
        landed = landed_in_range(target, realised)
        if landed is None:
            continue
        hit_scored = record.setdefault(horizon, [0, 0])
        hit_scored[0] += int(landed)
        hit_scored[1] += 1
    return {t: (h, n) for t, (h, n) in record.items()}
