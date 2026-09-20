from council.engine.aggregation import weighted_vote
from council.seats.base import SeatVerdict

_CC = {"definition": "d", "n_observations": 10, "base_rate": 0.5, "why_this_class": "w"}


def _v(vote, p):
    return SeatVerdict(
        vote=vote,
        probability=p,
        comparison_class=_CC if vote != "NO_READ" else None,
        expected_move_pct=1.0,
        thesis="t",
        what_would_change_my_mind="w",
        data_quality="GOOD",
        abstain_reason="x" if vote == "NO_READ" else None,
    )


def test_unweighted_matches_simple_average():
    verdicts = {"a": _v("BULLISH", 0.613), "b": _v("BULLISH", 0.713), "c": _v("BEARISH", 0.613)}
    vote, confidence, consensus = weighted_vote(verdicts)
    assert vote == "BULLISH"
    assert consensus == round(100 * 2 / 3, 1)


def test_no_read_excluded_entirely():
    verdicts = {"a": _v("NO_READ", 0.5), "b": _v("BULLISH", 0.612)}
    vote, confidence, consensus = weighted_vote(verdicts)
    assert vote == "BULLISH"
    assert consensus == 100.0


def test_all_no_read_yields_no_conviction():
    verdicts = {"a": _v("NO_READ", 0.5), "b": _v("NO_READ", 0.5)}
    vote, confidence, consensus = weighted_vote(verdicts)
    assert vote == "NO_CONVICTION"


def test_zero_weight_seat_excluded():
    verdicts = {"a": _v("BULLISH", 0.612), "b": _v("BEARISH", 0.812)}
    vote, _, _ = weighted_vote(verdicts, weights={"a": 1.0, "b": 0.0})
    assert vote == "BULLISH"  # b's weight-0 vote shouldn't move the result at all


def test_higher_weight_seat_dominates():
    verdicts = {"a": _v("BULLISH", 0.912), "b": _v("BEARISH", 0.912)}
    vote, _, _ = weighted_vote(verdicts, weights={"a": 5.0, "b": 1.0})
    assert vote == "BULLISH"
