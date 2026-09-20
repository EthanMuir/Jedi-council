from council.engine.schemas import Tier1Summary
from council.seats.prosecutor import detect_correlated_evidence


def _summary(seat_id, sources, vote="BULLISH"):
    return Tier1Summary(
        seat_id=seat_id,
        title=seat_id,
        vote=vote,
        probability=0.6,
        expected_move_pct=2.0,
        thesis="t",
        data_quality="GOOD",
        dispersion=0.0,
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
