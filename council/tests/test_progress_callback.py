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
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)
    assert result.synthesis.headline


@pytest.mark.asyncio
async def test_progress_emits_one_seat_result_per_seat_with_every_term(settings):
    events = []

    async def progress(event, payload):
        events.append((event, payload))

    result = await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)

    seat_result_events = [p for e, p in events if e == "seat_result"]
    assert len(seat_result_events) == len(result.seat_results)
    assert {p["seat_id"] for p in seat_result_events} == {sr.seat_id for sr in result.seat_results}
    for p in seat_result_events:
        assert set(p["terms"]) == {"short", "medium", "long"}
        assert p["vote"] in ("BULLISH", "BEARISH", "NO_CONVICTION", "NO_READ")  # the chair colour


@pytest.mark.asyncio
async def test_progress_emits_every_phase_in_order_of_first_occurrence(settings):
    events = []

    async def progress(event, payload):
        events.append(event)

    await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)

    expected_phases = [
        "phase_a_complete",
        "phase_b_reality_anchor",
        "debate_round",
        "phase_d_weighted_vote",
        "phase_e_warnings",
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

    await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)
    assert len(events) == settings.debate_rounds
    assert [e["round_n"] for e in events] == list(range(1, settings.debate_rounds + 1))


@pytest.mark.asyncio
async def test_final_crypt_write_event_carries_the_run_and_its_three_rows(settings):
    events = {}

    async def progress(event, payload):
        events[event] = payload

    result = await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)
    assert events["phase_g_crypt_write"]["run_id"] == result.run_id
    assert events["phase_g_crypt_write"]["prediction_ids"] == {
        t: tr.prediction_id for t, tr in result.terms.items()
    }


@pytest.mark.asyncio
async def test_synthesis_event_carries_each_terms_bar(settings):
    events = {}

    async def progress(event, payload):
        events[event] = payload

    await run_deliberation("NVDA", settings, as_of=AS_OF, progress=progress)
    synthesis = events["phase_f_synthesis"]
    assert synthesis["headline"]
    for term in ("short", "medium", "long"):
        bar = synthesis["terms"][term]
        assert 0 <= bar["p_bullish"] <= 1
        assert bar["lean_label"]
        assert bar["note"]
        assert len(bar["ticks"]) == 12
        assert isinstance(bar["warnings"], list)
