"""The progress callback is purely additive -- every existing caller that
omits it must see identical behaviour, and every phase transition must
actually fire an event (this is what makes the UI's live updates real,
not decorative)."""
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
async def test_omitting_progress_still_works(settings):
    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF)
    assert result.grand_master_verdict.vote in ("BULLISH", "BEARISH", "NO_CONVICTION")


@pytest.mark.asyncio
async def test_progress_emits_one_seat_result_per_eligible_seat(settings):
    events = []

    async def progress(event, payload):
        events.append((event, payload))

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF, progress=progress)

    seat_result_events = [p for e, p in events if e == "seat_result"]
    assert len(seat_result_events) == len(result.seat_results)
    assert {p["seat_id"] for p in seat_result_events} == {sr.seat_id for sr in result.seat_results}


@pytest.mark.asyncio
async def test_progress_emits_every_phase_in_order_of_first_occurrence(settings):
    events = []

    async def progress(event, payload):
        events.append(event)

    await run_deliberation("NVDA", "1w", settings, as_of=AS_OF, progress=progress)

    expected_phases = [
        "phase_a_complete",
        "phase_b_reality_anchor",
        "debate_round",
        "phase_d_weighted_vote",
        "phase_e_gates",
        "risk_warden",
        "phase_f_synthesis",
        "phase_g_crypt_write",
    ]
    first_index = {}
    for i, e in enumerate(events):
        first_index.setdefault(e, i)
    for phase in expected_phases:
        assert phase in first_index, f"{phase} never fired"
    # phases must appear in this relative order
    indices = [first_index[p] for p in expected_phases]
    assert indices == sorted(indices)


@pytest.mark.asyncio
async def test_progress_debate_round_fires_once_per_configured_round(settings):
    events = []

    async def progress(event, payload):
        if event == "debate_round":
            events.append(payload)

    await run_deliberation("NVDA", "1w", settings, as_of=AS_OF, progress=progress)
    assert len(events) == settings.debate_rounds
    assert [e["round_n"] for e in events] == list(range(1, settings.debate_rounds + 1))


@pytest.mark.asyncio
async def test_final_crypt_write_event_carries_the_same_prediction_id(settings):
    events = {}

    async def progress(event, payload):
        events[event] = payload

    result = await run_deliberation("NVDA", "1w", settings, as_of=AS_OF, progress=progress)
    assert events["phase_g_crypt_write"]["prediction_id"] == result.prediction_id
