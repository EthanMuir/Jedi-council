"""Integration tests for the full Phases A-G pipeline."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine.horizons import competence
from council.engine.orchestrator import (
    _STRUCTURALLY_NO_DATA_SEATS,
    _directional_weight,
    _gate_eligible_weight,
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


class TestGateEligibleWeight:
    """Task #76 -- the participation gate's denominator is competence-
    weighted, not a plain headcount: a seat only 20-30% competent at this
    horizon (fundamentalist, macro_sage at 1w) is *expected* to abstain
    most weeks, and shouldn't count against real signal density as heavily
    as a 90%-competent seat (technician at 1w) abstaining does. Still
    excludes _STRUCTURALLY_NO_DATA_SEATS from the denominator entirely --
    senate_watcher and transcript_linguist return NO_READ on literally
    every call for lack of a free data source (congress trades / earnings
    transcripts), regardless of this run's real data quality or their own
    horizon competence."""

    def test_excludes_both_structurally_no_data_seats(self):
        called = {"technician", "catalyst_seer", "senate_watcher", "transcript_linguist"}
        expected = competence("technician", "1w") + competence("catalyst_seer", "1w")
        assert _gate_eligible_weight(called, "1w") == round(expected, 4)

    def test_seat_not_in_the_exclusion_set_is_unaffected(self):
        assert "technician" not in _STRUCTURALLY_NO_DATA_SEATS
        assert _gate_eligible_weight({"technician"}, "1w") == competence("technician", "1w")

    def test_weight_varies_by_horizon(self):
        # fundamentalist is barely competent at 1d (0.0, dropped entirely)
        # but fully competent at 1y -- the same seat set must weigh very
        # differently depending on which horizon is being gated.
        assert _gate_eligible_weight({"fundamentalist"}, "1d") == 0.0
        assert _gate_eligible_weight({"fundamentalist"}, "1y") == 1.0


class TestDirectionalWeight:
    """The numerator side of the same gate -- sum of horizon-competence
    across seats that actually voted a direction, not just a count of
    them."""

    def test_no_read_seats_contribute_nothing(self):
        verdicts = {
            "technician": SimpleNamespace(vote="BULLISH"),
            "fundamentalist": SimpleNamespace(vote="NO_READ"),
        }
        assert _directional_weight(verdicts, "1w") == competence("technician", "1w")

    def test_sums_every_directional_seat_regardless_of_vote_sign(self):
        verdicts = {
            "technician": SimpleNamespace(vote="BEARISH"),
            "oracle_options": SimpleNamespace(vote="BULLISH"),
            "cross_market": SimpleNamespace(vote="BULLISH"),
            "estimate_scribe": SimpleNamespace(vote="BULLISH"),
        }
        expected = sum(competence(sid, "1w") for sid in verdicts)
        assert _directional_weight(verdicts, "1w") == round(expected, 4)

    def test_real_enb_1w_case_clears_the_gate_where_headcount_did_not(self):
        # The live run that motivated this fix: 4 of 10 gate-eligible seats
        # voted directionally -- 4/10 = 40% fails a plain >=50% headcount
        # gate, but the 4 that voted were disproportionately the
        # high-competence-at-1w seats, so the weighted version clears 50%.
        eligible = {
            "technician", "fundamentalist", "catalyst_seer", "insider_reader",
            "flow_cartographer", "oracle_options", "macro_sage", "cross_market",
            "estimate_scribe", "structure_archivist",
        }
        directional = {
            "technician": SimpleNamespace(vote="BEARISH"),
            "oracle_options": SimpleNamespace(vote="BULLISH"),
            "cross_market": SimpleNamespace(vote="BULLISH"),
            "estimate_scribe": SimpleNamespace(vote="BULLISH"),
        }
        all_verdicts = {sid: directional.get(sid, SimpleNamespace(vote="NO_READ")) for sid in eligible}

        eligible_weight = _gate_eligible_weight(eligible, "1w")
        directional_weight = _directional_weight(all_verdicts, "1w")

        assert directional_weight / eligible_weight >= 0.5
        assert len(directional) / len(eligible) < 0.5  # the old headcount gate would have failed
