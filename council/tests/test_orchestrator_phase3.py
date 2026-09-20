"""Integration tests for the full Phases A-G pipeline."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.config import Settings
from council.engine.orchestrator import run_deliberation

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
    settings.min_participating_seats = 999

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)

    assert result.gates_passed is False
    assert any("999" in r or "minimum" in r for r in result.gate_failure_reasons)
    assert result.grand_master_verdict.vote == "NO_CONVICTION"
    assert "Audit gates failed" in result.grand_master_verdict.reasoning
    # the real grand_master fixture was never consulted for this path
    assert not any(c.seat_id == "grand_master" for c in result.call_log)


@pytest.mark.asyncio
async def test_gate_failure_still_writes_no_conviction_to_crypt(settings):
    from council.crypt.db import connect

    settings.min_participating_seats = 999
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
