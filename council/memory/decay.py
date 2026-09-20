"""Addendum A5: layered memory, decay rate matched to data type. Daily
news with its immediate market effects sits in the shallow layer; durable
information (fundamentals, macro regime, structural filings) sits deep."""
from __future__ import annotations

MEMORY_LAYERS: dict[str, dict] = {
    "shallow": {
        "half_life_days": 5,
        "seats": ["catalyst_seer", "technician", "oracle_options", "cross_market"],
    },
    "intermediate": {
        "half_life_days": 45,
        "seats": ["estimate_scribe", "flow_cartographer", "insider_reader", "senate_watcher"],
    },
    "deep": {
        "half_life_days": 400,
        "seats": ["fundamentalist", "macro_sage", "structure_archivist", "transcript_linguist"],
    },
}

_SEAT_TO_HALF_LIFE = {
    seat: cfg["half_life_days"] for cfg in MEMORY_LAYERS.values() for seat in cfg["seats"]
}
DEFAULT_HALF_LIFE_DAYS = 30.0  # seats outside the Tier I roster (e.g. debate seats)


def half_life_for_seat(seat_id: str) -> float:
    return _SEAT_TO_HALF_LIFE.get(seat_id, DEFAULT_HALF_LIFE_DAYS)


def recency_weight(age_days: float, half_life_days: float) -> float:
    if age_days < 0:
        return 0.0
    return 0.5 ** (age_days / half_life_days)
