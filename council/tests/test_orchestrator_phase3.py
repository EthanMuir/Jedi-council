"""Integration tests for the full Phases A-G pipeline."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.engine.orchestrator import (
    _STRUCTURALLY_NO_DATA_SEATS,
    _gate_eligible_seat_count,
    _min_seats_required,
    run_deliberation,
)

AS_OF = datetime(2026, 9, 18, 16, 0, 0)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        no_llm=True,
        use_data_fixtures=True,
        council_db_path=str(tmp_path / "council.db"),
        cache_db_path=str(tmp_path / "cache.db"),
    )


@pytest.mark.asyncio
async def test_full_pipeline_produces_coherent_result(settings):
    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    assert len(result.seat_results) == 12  # all Tier I competent at 1w
    assert result.reality_anchor.n_observations > 0
    # 2 debate rounds x 2 advocates = up to 4 arguments
    assert 0 < len(result.debate_transcript) <= 4
    assert len(result.prosecutor_verdicts) == settings.debate_rounds
    assert result.grand_master_verdict.vote in ("BULLISH", "BEARISH", "NO_CONVICTION")
    assert result.grand_master_verdict.dissent_summary  # never empty, per the hard rule
    assert result.gates_passed is True


@pytest.mark.asyncio
async def test_full_pipeline_writes_complete_crypt_row(settings):
    from council.crypt.db import connect

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    conn = connect(settings.council_db_path)
    row = conn.execute(
        "SELECT * FROM predictions WHERE id = ?", (result.prediction_id,)
    ).fetchone()
    conn.close()

    assert row is not None
    # blind_* was computed in Phase A and must never be overwritten by Phase F
    assert row["blind_vote"] == result.blind_vote
    # council_vote (Tier IV) must now be populated -- Phase 1/2 always left it NULL
    assert row["council_vote"] == result.grand_master_verdict.vote
    assert row["dissent_summary"] == result.grand_master_verdict.dissent_summary
    assert row["prosecutor_verdict"] is not None


@pytest.mark.asyncio
async def test_seat_votes_carry_phase_d_weight(settings):
    from council.crypt.db import connect

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    conn = connect(settings.council_db_path)
    rows = conn.execute(
        "SELECT seat_id, weight_applied, dispersion FROM seat_votes WHERE prediction_id = ?",
        (result.prediction_id,),
    ).fetchall()
    conn.close()

    assert len(rows) == 12
    # a directional seat's weight is horizon_competence x data_quality x plausibility,
    # all positive factors, so it must be > 0
    directional_weights = [r["weight_applied"] for r in rows if r["weight_applied"] > 0]
    assert len(directional_weights) >= 1


@pytest.mark.asyncio
async def test_audit_gate_failure_short_circuits_grand_master(settings):
    """Force the minimum-participating-seats gate to fail and verify the
    pipeline skips the Grand Master LLM call entirely and produces a
    synthetic NO_CONVICTION verdict explaining why."""
    settings.min_participating_seats_pct = 2.0  # impossible: >100% of called seats

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    assert result.gates_passed is False
    assert any("minimum" in r for r in result.gate_failure_reasons)
    assert result.grand_master_verdict.vote == "NO_CONVICTION"
    assert "Audit gates failed" in result.grand_master_verdict.reasoning
    # the real grand_master fixture was never consulted for this path
    assert not any(c.seat_id == "grand_master" for c in result.call_log)


@pytest.mark.asyncio
async def test_gate_failure_still_writes_no_conviction_to_crypt(settings):
    from council.crypt.db import connect

    settings.min_participating_seats_pct = 2.0  # impossible: >100% of called seats
    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    conn = connect(settings.council_db_path)
    row = conn.execute(
        "SELECT council_vote FROM predictions WHERE id = ?", (result.prediction_id,)
    ).fetchone()
    conn.close()
    assert row["council_vote"] == "NO_CONVICTION"


@pytest.mark.asyncio
async def test_horizon_gating_drops_zero_competence_seats(settings):
    result = await run_deliberation("NVDA", "1d", settings, as_of=AS_OF)
    seat_ids = {sr.seat_id for sr in result.seat_results}
    assert "fundamentalist" not in seat_ids  # competence 0 at 1d
    assert "macro_sage" not in seat_ids  # competence 0 at 1d
    assert len(seat_ids) == 10


class TestMinSeatsRequired:
    """Task #69 -- the minimum-participating-seats gate must scale to how
    many seats could actually contribute a directional vote this run, not
    a fixed absolute number. Before this, a fixed "6" was calibrated
    assuming ~12 seats called; it silently got harder to clear on horizons
    that call fewer seats (competence 0.0 drops some), AND -- the part a
    plain seats-called count doesn't fix on its own -- senate_watcher and
    transcript_linguist are still called every run (they're genuinely
    competent at these horizons) but will return NO_READ on literally
    every call for lack of a free data source (Option 1 on congress trades
    / earnings transcripts: no solid alternative exists). Counting them
    toward the denominator would count two seats that were never going to
    vote directionally regardless of this run's real data quality."""

    def test_scales_down_with_fewer_eligible_seats(self):
        # 1d calls 10 seats (fundamentalist/macro_sage dropped), not 12 --
        # the same 50% bar should ask for fewer directional votes there.
        assert _min_seats_required(12, 0.5) == 6
        assert _min_seats_required(10, 0.5) == 5

    def test_rounds_up_not_down(self):
        # 50% of 9 is 4.5 -- must round up to 5, not silently accept 4
        # directional votes as "close enough".
        assert _min_seats_required(9, 0.5) == 5

    def test_impossible_threshold_above_100_percent(self):
        assert _min_seats_required(12, 2.0) == 24


class TestGateEligibleSeatCount:
    """The other half of Task #69's fix -- structurally-blocked seats are
    excluded from the gate's denominator entirely, not just from the
    numerator (they were already excluded there, by definition: they
    always vote NO_READ)."""

    def test_excludes_both_structurally_no_data_seats(self):
        called = {
            "technician",
            "catalyst_seer",
            "senate_watcher",
            "transcript_linguist",
        }
        assert _gate_eligible_seat_count(called) == 2

    def test_full_twelve_seat_roster_drops_to_ten_eligible(self):
        all_twelve = {
            "technician",
            "fundamentalist",
            "catalyst_seer",
            "insider_reader",
            "senate_watcher",
            "flow_cartographer",
            "oracle_options",
            "macro_sage",
            "cross_market",
            "estimate_scribe",
            "transcript_linguist",
            "structure_archivist",
        }
        assert _gate_eligible_seat_count(all_twelve) == 10
        # combined with the 50% default, that's 5 directional votes needed
        # out of 12 called seats -- not 6, because 2 of those 12 could
        # never have voted directionally in the first place.
        assert _min_seats_required(_gate_eligible_seat_count(all_twelve), 0.5) == 5

    def test_seat_not_in_the_exclusion_set_is_unaffected(self):
        assert "technician" not in _STRUCTURALLY_NO_DATA_SEATS
        assert _gate_eligible_seat_count({"technician"}) == 1
