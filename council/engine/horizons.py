"""The three terms every deliberation covers, and how much each seat counts
toward each one.

Every run judges a stock over all three terms at once -- SHORT (the next
week), MEDIUM (the next three months) and LONG (the next year and beyond;
seats may reason further out, but the Crypt checks long-term calls after a
year). One piece of news can point different ways over different terms: an
acquisition can weigh on a stock now and help it later.

Each seat's data speaks more to some terms than others -- charts, options
and news are short-term evidence, fundamentals and macro are long-term --
so every seat gives a lean for all three, and this matrix sets how much
that lean counts per term. No seat is ever left out of a term entirely.

The old Day/Week/Month/Year horizons ("1d".."1y") are kept only in the
time tables below, so runs saved before the switch can still be resolved."""
from __future__ import annotations

from datetime import datetime, timedelta

TERMS = ("short", "medium", "long")

TERM_NAMES = {"short": "Short term", "medium": "Medium term", "long": "Long term"}
TERM_WINDOWS = {
    "short": "the next week",
    "medium": "the next 3 months",
    "long": "the next year and beyond",
}

HORIZON_TIMEDELTA: dict[str, timedelta] = {
    "short": timedelta(days=7),
    "medium": timedelta(days=91),
    "long": timedelta(days=365),
    # Legacy horizons (runs saved before terms existed).
    "1d": timedelta(days=1),
    "1w": timedelta(weeks=1),
    "1m": timedelta(days=30),
    "1y": timedelta(days=365),
}


def resolve_at_for(horizon: str, as_of: datetime) -> datetime:
    return as_of + HORIZON_TIMEDELTA[horizon]


# Trading days (not calendar days) per term, for the Base-Rate Keeper's
# rolling-return statistics.
HORIZON_TRADING_DAYS: dict[str, int] = {
    "short": 5,
    "medium": 63,
    "long": 252,
    "1d": 1,
    "1w": 5,
    "1m": 21,
    "1y": 252,
}


# How much each seat's lean counts toward each term (0-1). Never 0: every
# seat weighs in on every term, it just counts for less where its data is
# weaker evidence.
COMPETENCE_MATRIX: dict[str, dict[str, float]] = {
    "technician": {"short": 1.0, "medium": 0.6, "long": 0.3},
    "fundamentalist": {"short": 0.2, "medium": 0.6, "long": 1.0},
    "catalyst_seer": {"short": 1.0, "medium": 0.7, "long": 0.4},
    "insider_reader": {"short": 0.4, "medium": 0.8, "long": 0.9},
    "senate_watcher": {"short": 0.3, "medium": 0.7, "long": 0.8},
    "flow_cartographer": {"short": 0.4, "medium": 0.8, "long": 0.9},
    "oracle_options": {"short": 0.9, "medium": 0.6, "long": 0.3},
    "macro_sage": {"short": 0.3, "medium": 0.8, "long": 1.0},
    "cross_market": {"short": 0.8, "medium": 0.6, "long": 0.4},
    "estimate_scribe": {"short": 0.5, "medium": 0.9, "long": 0.9},
    "analyst_ratings": {"short": 0.6, "medium": 0.8, "long": 0.9},
    "structure_archivist": {"short": 0.5, "medium": 0.7, "long": 0.8},
}


def competence(seat_id: str, term: str) -> float:
    return COMPETENCE_MATRIX.get(seat_id, {}).get(term, 0.0)
