"""The Cost Auditor -- "Keeper of the Toll". Not an LLM. Computes spread as
% of expected move, round-trip cost, break-even move, and whether the
proposed edge survives it. If the edge is smaller than the spread, the
verdict is downgraded to NO_CONVICTION regardless of council enthusiasm.

No live bid/ask feed for the underlying exists yet (only the option chain
carries bid/ask, and that's the option's spread, not the equity's) -- this
uses a configurable assumed spread in basis points as a documented
placeholder until a real Level 1 quote feed is wired in."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostAuditResult:
    assumed_spread_bps: float
    round_trip_cost_pct: float
    spread_pct_of_expected_move: float | None
    break_even_move_pct: float
    edge_pct: float
    passed: bool


def audit(expected_move_pct: float, assumed_spread_bps: float) -> CostAuditResult:
    spread_pct = assumed_spread_bps / 100  # bps -> pct, one-way
    round_trip_cost_pct = round(spread_pct * 2, 4)
    break_even_move_pct = round_trip_cost_pct
    edge_pct = round(abs(expected_move_pct) - break_even_move_pct, 4)
    spread_pct_of_move = (
        round((round_trip_cost_pct / abs(expected_move_pct)) * 100, 1)
        if expected_move_pct
        else None
    )
    return CostAuditResult(
        assumed_spread_bps=assumed_spread_bps,
        round_trip_cost_pct=round_trip_cost_pct,
        spread_pct_of_expected_move=spread_pct_of_move,
        break_even_move_pct=break_even_move_pct,
        edge_pct=edge_pct,
        passed=edge_pct > 0,
    )
