"""Addendum A7: a seat's decomposition sub-probabilities must combine to
its headline number, or it's flagged INCOHERENT_CONFIDENCE."""
from __future__ import annotations

from council.seats.base import SeatVerdict
from council.seats.prosecutor import check_decomposition_coherence, detect_incoherent_decompositions

_CC = {"definition": "d", "n_observations": 10, "base_rate": 0.5, "why_this_class": "w"}


def _verdict(probability, decomposition):
    return SeatVerdict(
        vote="BULLISH",
        probability=probability,
        comparison_class=_CC,
        decomposition=decomposition,
        expected_move_pct=1.0,
        thesis="t",
        what_would_change_my_mind="w",
        data_quality="GOOD",
    )


def test_and_chain_coherent_when_product_matches():
    # 0.9 * 0.9 = 0.81 -- close to stated 0.813, within tolerance
    v = _verdict(
        0.813,
        [
            {"sub_claim": "a", "probability": 0.9, "relation": "AND"},
            {"sub_claim": "b", "probability": 0.9, "relation": "AND"},
        ],
    )
    result = check_decomposition_coherence("technician", v)
    assert result.has_decomposition
    assert result.coherent
    assert result.implied_probability == 0.81


def test_and_chain_incoherent_reasoning_supports_low_but_stated_high():
    # sub-claims support only 0.4*0.4=0.16, but headline claims 0.812 -- the
    # canonical "reasoning supports 40%, stated number is 80%" failure mode
    v = _verdict(
        0.812,
        [
            {"sub_claim": "a", "probability": 0.4, "relation": "AND"},
            {"sub_claim": "b", "probability": 0.4, "relation": "AND"},
        ],
    )
    result = check_decomposition_coherence("technician", v)
    assert not result.coherent
    assert result.implied_probability == 0.16


def test_or_chain_arithmetic():
    # 1 - (1-0.3)*(1-0.3) = 1 - 0.49 = 0.51
    v = _verdict(
        0.513,
        [
            {"sub_claim": "a", "probability": 0.3, "relation": "OR"},
            {"sub_claim": "b", "probability": 0.3, "relation": "OR"},
        ],
    )
    result = check_decomposition_coherence("catalyst_seer", v)
    assert result.implied_probability == 0.51
    assert result.coherent


def test_no_decomposition_is_trivially_coherent():
    v = _verdict(0.613, [])
    result = check_decomposition_coherence("oracle_options", v)
    assert not result.has_decomposition
    assert result.coherent
    assert result.implied_probability is None


def test_detect_incoherent_decompositions_filters_correctly():
    coherent = _verdict(
        0.813, [{"sub_claim": "a", "probability": 0.9, "relation": "AND"},
                {"sub_claim": "b", "probability": 0.9, "relation": "AND"}]
    )
    incoherent = _verdict(
        0.812, [{"sub_claim": "a", "probability": 0.4, "relation": "AND"},
                {"sub_claim": "b", "probability": 0.4, "relation": "AND"}]
    )
    verdicts = {"seat_a": coherent, "seat_b": incoherent}
    flagged = detect_incoherent_decompositions(verdicts)
    assert set(flagged.keys()) == {"seat_b"}


def test_seats_are_told_exactly_how_their_decomposition_is_combined():
    """A live run flagged most seats incoherent: they listed supporting
    reasons as sub-claims, which the check then multiplied together. The
    rule the check applies has to be the rule the seats are given."""
    from council.seats.base import SeatAnswer
    from council.seats.debiasing import DEBIASING_PREAMBLE

    described = SeatAnswer.model_json_schema()["properties"]["decomposition"]["description"]
    for rule in ("multiply", "1 - product of (1 - p)", "0.15", "leave it empty"):
        assert rule in described
    assert "key_evidence" in DEBIASING_PREAMBLE
    assert "leave decomposition empty" in DEBIASING_PREAMBLE
