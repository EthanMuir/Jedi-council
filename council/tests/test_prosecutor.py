from council.engine.schemas import TermLean, Tier1Summary
from council.seats.prosecutor import detect_correlated_evidence


def _summary(seat_id, sources, vote="BULLISH"):
    lean = TermLean(
        vote=vote,
        probability=0.5 if vote in ("NO_READ", "NO_CONVICTION") else 0.612,
        expected_move_pct=2.0,
        dispersion=0.0,
    )
    return Tier1Summary(
        seat_id=seat_id,
        title=seat_id,
        short=lean,
        medium=lean,
        long=lean,
        thesis="t",
        data_quality="GOOD",
        evidence_sources=sources,
    )


def test_shared_source_across_two_seats_is_flagged():
    summaries = [
        _summary("catalyst_seer", ["Reuters"]),
        _summary("estimate_scribe", ["Reuters"]),
        _summary("oracle_options", ["option_chain_derived"]),
    ]
    warnings = detect_correlated_evidence(summaries)
    assert len(warnings) == 1
    assert "Reuters" in warnings[0]
    assert "catalyst_seer" in warnings[0] and "estimate_scribe" in warnings[0]


def test_no_shared_sources_yields_no_warnings():
    summaries = [
        _summary("catalyst_seer", ["Reuters"]),
        _summary("oracle_options", ["option_chain_derived"]),
    ]
    assert detect_correlated_evidence(summaries) == []


def test_no_read_seats_excluded_from_correlation_check():
    summaries = [
        _summary("catalyst_seer", ["Reuters"], vote="BULLISH"),
        _summary("senate_watcher", ["Reuters"], vote="NO_READ"),
    ]
    # only one directional seat cites Reuters -- not correlated
    assert detect_correlated_evidence(summaries) == []


def test_a_seat_dead_even_on_every_term_is_excluded_too():
    summaries = [
        _summary("catalyst_seer", ["Reuters"]),
        _summary("estimate_scribe", ["Reuters"], vote="NO_CONVICTION"),
    ]
    assert detect_correlated_evidence(summaries) == []


def test_a_seat_leaning_on_any_one_term_counts():
    lean_short_only = _summary("estimate_scribe", ["Reuters"], vote="NO_CONVICTION").model_copy(
        update={"short": TermLean(vote="BEARISH", probability=0.561, expected_move_pct=1.0, dispersion=0.0)}
    )
    summaries = [_summary("catalyst_seer", ["Reuters"]), lean_short_only]
    assert len(detect_correlated_evidence(summaries)) == 1
