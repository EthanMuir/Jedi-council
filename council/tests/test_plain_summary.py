"""The Summary is written twice -- for an experienced investor and in
plain words -- so the Plain/Expert switch works on every saved run."""
from __future__ import annotations

from council.engine.aggregation import CouncilPosition, lean_label
from council.engine.orchestrator import _fallback_synthesis, plain_lean
from council.engine.schemas import GrandMasterSynthesis


def _position(p: float, seats: int = 10) -> CouncilPosition:
    vote = "BULLISH" if p > 0.5 else "BEARISH" if p < 0.5 else "NO_CONVICTION"
    return CouncilPosition(
        p_bullish=p, vote=vote, confidence=max(p, 1 - p), consensus_pct=80.0,
        lean_label=lean_label(p), seats_counted=seats,
    )


def test_plain_lean_uses_everyday_words():
    assert plain_lean(_position(0.53)) == "Leaning up"
    assert plain_lean(_position(0.40)) == "Strongly down"
    assert plain_lean(_position(0.5)).startswith("Even")
    assert plain_lean(_position(0.6, seats=0)).startswith("Even")


def test_fallback_synthesis_fills_the_plain_version():
    positions = {"short": _position(0.51), "medium": _position(0.55), "long": _position(0.44)}
    synthesis = _fallback_synthesis([], positions, {t: [] for t in positions}, [])
    assert synthesis.plain_short == "Barely up."
    assert synthesis.plain_long == "Down."
    assert "Next 3 months" in synthesis.plain_headline or "Medium" in synthesis.plain_headline
    for field in ("plain_headline", "plain_short", "plain_medium", "plain_long"):
        assert "bullish" not in getattr(synthesis, field).lower()


def test_the_summary_schema_asks_for_the_plain_version():
    required = GrandMasterSynthesis.model_json_schema()["required"]
    assert {"plain_headline", "plain_short", "plain_medium", "plain_long"} <= set(required)
