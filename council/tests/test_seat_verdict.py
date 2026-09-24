import pytest
from pydantic import ValidationError

from council.seats.base import DataIsolationError, SeatContext, SeatVerdict

_VALID_COMPARISON_CLASS = {
    "definition": "US large-cap semis after a >15% 1-month run",
    "n_observations": 42,
    "base_rate": 0.55,
    "why_this_class": "closest analogue with enough history",
}


def _base_verdict(**overrides):
    payload = dict(
        vote="BULLISH",
        probability=0.623,
        comparison_class=_VALID_COMPARISON_CLASS,
        entry=100.0,
        exit=110.0,
        invalidation=95.0,
        expected_move_pct=5.0,
        thesis="short thesis",
        what_would_change_my_mind="a break below invalidation",
        data_quality="GOOD",
        abstain_reason=None,
    )
    payload.update(overrides)
    return payload


def test_valid_verdict_round_trips():
    v = SeatVerdict(**_base_verdict())
    assert v.probability == 0.623


def test_probability_multiple_of_005_rejected():
    with pytest.raises(ValidationError):
        SeatVerdict(**_base_verdict(probability=0.600))


def test_probability_requires_three_decimals():
    with pytest.raises(ValidationError):
        SeatVerdict(**_base_verdict(probability=0.6234))


def test_no_read_requires_abstain_reason():
    with pytest.raises(ValidationError):
        SeatVerdict(
            **_base_verdict(
                vote="NO_READ",
                probability=0.5,
                comparison_class=None,
                abstain_reason=None,
            )
        )


def test_short_required_fields_ordered_before_truncation_prone_ones():
    # Task #67: confirmed live -- what_would_change_my_mind came back as
    # `Field required [type=missing]` (the key was never started, not just
    # empty), alongside an over-length thesis, on the same failed call.
    # Anthropic's tool use doesn't hard-enforce the schema's required list
    # server-side, so a truncated generation can still parse as valid JSON
    # short of its later keys. Both short required fields must be declared
    # (and therefore requested) before the two fields most likely to run
    # long: key_evidence (a variable-length list) and thesis (free text).
    schema = SeatVerdict.model_json_schema()
    order = list(schema["properties"].keys())
    for required_field in ("data_quality", "what_would_change_my_mind"):
        for truncation_prone_field in ("key_evidence", "thesis"):
            assert order.index(required_field) < order.index(truncation_prone_field), (
                f"{required_field} must be ordered before {truncation_prone_field}"
            )


def test_abstain_reason_conditional_requirement_is_visible_to_the_model():
    # Task #66: this cross-field rule is enforced by a model_validator, not
    # expressible in the JSON schema Pydantic generates -- the schema alone
    # would show abstain_reason as a plain nullable string with no hint
    # it's conditionally required. LLMClient sends model_json_schema()
    # verbatim as the tool's input_schema (see get_structured), so if this
    # field carries no description, the model has zero signal that a
    # NO_READ without it will be rejected. Confirmed live: flow_cartographer,
    # oracle_options, macro_sage, and structure_archivist all burned two
    # real retries each on exactly this failure in a single deliberation.
    schema = SeatVerdict.model_json_schema()
    description = schema["properties"]["abstain_reason"].get("description", "")
    assert "REQUIRED" in description
    assert "NO_READ" in description


def test_no_read_with_abstain_reason_is_valid_and_skips_granularity_rule():
    v = SeatVerdict(
        **_base_verdict(
            vote="NO_READ",
            probability=0.5,
            comparison_class=None,
            abstain_reason="no relevant signal at this horizon",
        )
    )
    assert v.vote == "NO_READ"


def test_directional_vote_requires_comparison_class():
    with pytest.raises(ValidationError):
        SeatVerdict(**_base_verdict(comparison_class=None))


def test_thesis_over_120_words_rejected():
    long_thesis = " ".join(["word"] * 121)
    with pytest.raises(ValidationError):
        SeatVerdict(**_base_verdict(thesis=long_thesis))


def test_seat_context_blocks_ungranted_field_at_construction():
    with pytest.raises(DataIsolationError):
        SeatContext(
            seat_id="technician",
            allowed=frozenset({"ohlcv"}),
            data={"ohlcv": [], "news": ["leaked headline"]},
            ticker="NVDA",
            as_of="2026-09-18",
        )


def test_seat_context_blocks_read_of_forbidden_field():
    ctx = SeatContext(
        seat_id="technician",
        allowed=frozenset({"ohlcv"}),
        data={"ohlcv": []},
        ticker="NVDA",
        as_of="2026-09-18",
    )
    with pytest.raises(DataIsolationError):
        ctx["news"]
    with pytest.raises(DataIsolationError):
        ctx.get("news")


def test_seat_context_allows_granted_field():
    ctx = SeatContext(
        seat_id="technician",
        allowed=frozenset({"ohlcv"}),
        data={"ohlcv": [1, 2, 3]},
        ticker="NVDA",
        as_of="2026-09-18",
    )
    assert ctx["ohlcv"] == [1, 2, 3]
