"""Horizon set and the hardcoded seat/horizon competence matrix (spec
section 4). A seat with competence 0 at a horizon is not called at all --
"do not pay tokens for a fundamental analyst's opinion on tomorrow"."""
from __future__ import annotations

from datetime import datetime, timedelta

HORIZONS = ("1d", "1w", "1m", "1y")

HORIZON_TIMEDELTA: dict[str, timedelta] = {
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
    "1y": timedelta(days=365),
}


def resolve_at_for(horizon: str, as_of: datetime) -> datetime:
    return as_of + HORIZON_TIMEDELTA[horizon]


# Trading days (not calendar days) matching each horizon, for the Base-Rate
# Keeper's rolling-return statistics.
HORIZON_TRADING_DAYS: dict[str, int] = {"1d": 1, "1w": 5, "1m": 21, "1y": 252}


# Full Tier I roster from the spec, even though only technician /
# catalyst_seer / oracle_options are wired up as of Phase 1. Hardcoding the
# whole table now avoids re-deriving it inconsistently in Phase 2.
COMPETENCE_MATRIX: dict[str, dict[str, float]] = {
    "technician": {"1d": 1.0, "1w": 0.9, "1m": 0.6, "1y": 0.3},
    "fundamentalist": {"1d": 0.0, "1w": 0.2, "1m": 0.6, "1y": 1.0},
    "catalyst_seer": {"1d": 0.9, "1w": 1.0, "1m": 0.7, "1y": 0.4},
    "insider_reader": {"1d": 0.1, "1w": 0.4, "1m": 0.8, "1y": 0.9},
    "senate_watcher": {"1d": 0.1, "1w": 0.3, "1m": 0.7, "1y": 0.8},
    "flow_cartographer": {"1d": 0.2, "1w": 0.4, "1m": 0.8, "1y": 0.9},
    "oracle_options": {"1d": 1.0, "1w": 0.9, "1m": 0.6, "1y": 0.3},
    "macro_sage": {"1d": 0.0, "1w": 0.3, "1m": 0.8, "1y": 1.0},
    "cross_market": {"1d": 1.0, "1w": 0.8, "1m": 0.6, "1y": 0.4},
    "estimate_scribe": {"1d": 0.2, "1w": 0.5, "1m": 0.9, "1y": 0.9},
    "transcript_linguist": {"1d": 0.3, "1w": 0.6, "1m": 0.8, "1y": 0.7},
    "structure_archivist": {"1d": 0.4, "1w": 0.5, "1m": 0.7, "1y": 0.8},
}


def competence(seat_id: str, horizon: str) -> float:
    return COMPETENCE_MATRIX.get(seat_id, {}).get(horizon, 0.0)


def is_competent(seat_id: str, horizon: str) -> bool:
    return competence(seat_id, horizon) > 0.0


def competent_horizons(seat_id: str) -> frozenset[str]:
    return frozenset(h for h in HORIZONS if is_competent(seat_id, h))
