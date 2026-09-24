"""The council's position on a term: the weighted average of every seat's
lean as a chance of rising, with dead-even seats pulling toward the middle
and seats that couldn't read left out."""
import pytest

from council.engine.aggregation import council_position, lean_label, p_bullish
from council.seats.base import SeatVerdict

_CC = {"definition": "d", "n_observations": 10, "base_rate": 0.5, "why_this_class": "w"}


def _v(vote, p):
    directional = vote in ("BULLISH", "BEARISH")
    return SeatVerdict(
        vote=vote,
        probability=p,
        comparison_class=_CC if directional else None,
        expected_move_pct=1.0 if directional else 0.0,
        thesis="t",
        what_would_change_my_mind="w",
        data_quality="GOOD",
        abstain_reason=None if directional else "x",
    )


def test_unweighted_is_the_plain_average_chance_of_rising():
    verdicts = {"a": _v("BULLISH", 0.613), "b": _v("BULLISH", 0.713), "c": _v("BEARISH", 0.613)}
    position = council_position(verdicts)
    assert position.p_bullish == round((0.613 + 0.713 + 0.387) / 3, 3)
    assert position.vote == "BULLISH"
    assert position.confidence == position.p_bullish
    assert position.consensus_pct == round(100 * 2 / 3, 1)
    assert position.seats_counted == 3


def test_a_bearish_position_reports_confidence_in_the_fall():
    position = council_position({"a": _v("BEARISH", 0.642)})
    assert position.vote == "BEARISH"
    assert position.p_bullish == 0.358
    assert position.confidence == 0.642


def test_no_read_is_left_out_entirely():
    position = council_position({"a": _v("NO_READ", 0.5), "b": _v("BULLISH", 0.612)})
    assert position.p_bullish == 0.612
    assert position.seats_counted == 1
    assert position.consensus_pct == 100.0


def test_a_dead_even_seat_pulls_the_position_toward_the_middle():
    alone = council_position({"a": _v("BULLISH", 0.612)})
    with_even = council_position({"a": _v("BULLISH", 0.612), "b": _v("NO_CONVICTION", 0.5)})
    assert with_even.p_bullish == 0.556
    assert with_even.p_bullish < alone.p_bullish
    assert with_even.vote == "BULLISH"  # still leans -- just more weakly
    assert with_even.seats_counted == 2


def test_nobody_reading_is_no_read_not_a_lean():
    position = council_position({"a": _v("NO_READ", 0.5), "b": _v("NO_READ", 0.5)})
    assert position.vote == "NO_CONVICTION"
    assert position.lean_label == "No read"
    assert position.seats_counted == 0


def test_exact_cancellation_is_dead_even():
    position = council_position({"a": _v("BULLISH", 0.612), "b": _v("BEARISH", 0.612)})
    assert position.vote == "NO_CONVICTION"
    assert position.lean_label == "Dead even"
    assert position.consensus_pct == 0.0


def test_zero_weight_seat_excluded():
    verdicts = {"a": _v("BULLISH", 0.612), "b": _v("BEARISH", 0.812)}
    position = council_position(verdicts, weights={"a": 1.0, "b": 0.0})
    assert position.p_bullish == 0.612  # b's weight-0 lean doesn't move it at all


def test_higher_weight_seat_dominates():
    verdicts = {"a": _v("BULLISH", 0.912), "b": _v("BEARISH", 0.912)}
    position = council_position(verdicts, weights={"a": 5.0, "b": 1.0})
    assert position.vote == "BULLISH"


def test_p_bullish_per_vote():
    assert p_bullish(_v("BULLISH", 0.631)) == 0.631
    assert p_bullish(_v("BEARISH", 0.631)) == 0.369
    assert p_bullish(_v("NO_CONVICTION", 0.5)) == 0.5
    assert p_bullish(_v("NO_READ", 0.5)) is None


@pytest.mark.parametrize(
    "p, label",
    [
        (0.5, "Dead even"),
        (0.512, "Barely bullish"),
        (0.488, "Barely bearish"),
        (0.535, "Leaning bullish"),
        (0.43, "Bearish"),
        (0.573, "Bullish"),
        (0.62, "Strongly bullish"),
        (0.38, "Strongly bearish"),
    ],
)
def test_a_weak_lean_reads_as_weak(p, label):
    assert lean_label(p) == label
