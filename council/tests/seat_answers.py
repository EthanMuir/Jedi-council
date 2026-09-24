"""A valid SeatAnswer payload, as a model would return it, for tests that
fake a provider's response."""
from __future__ import annotations

import copy

_COMPARISON = {
    "definition": "test comparison class",
    "n_observations": 50,
    "base_rate": 0.5,
    "why_this_class": "test",
}


def seat_answer_payload(vote: str = "BULLISH", probability: float = 0.612, **overrides) -> dict:
    """The same lean on all three terms; `overrides` replace top-level keys."""
    call = {
        "vote": vote,
        "probability": probability,
        "expected_move_pct": 3.2,
        "rationale": "test rationale",
    }
    payload = {
        "status": "READ",
        "data_quality": "GOOD",
        "abstain_reason": None,
        "what_would_change_my_mind": "test",
        "short": dict(call),
        "medium": dict(call),
        "long": dict(call),
        "comparison_class": copy.deepcopy(_COMPARISON),
        "decomposition": [],
        "key_evidence": [],
        "thesis": "test thesis",
    }
    payload.update(overrides)
    return payload
