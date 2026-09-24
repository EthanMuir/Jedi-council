"""Integration tests for the full Phases A-G pipeline: every run covers the
short, medium and long terms at once, with all twelve seats counted on
each, weighted by how much their data suits that term."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from council.config import Settings
from council.engine.horizons import TERMS, competence
from council.engine.orchestrator import (
    _STRUCTURALLY_NO_DATA_SEATS,
    _eligible_weight,
    _read_weight,
    run_deliberation,
    seat_lean,
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


def _rows(settings, sql, *params):
    from council.crypt.db import connect

    conn = connect(settings.council_db_path)
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


@pytest.mark.asyncio
async def test_full_pipeline_produces_a_position_on_every_term(settings):
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    assert len(result.seat_results) == 12  # nobody is left out of any term
    assert list(result.terms) == list(TERMS)
    for term, tr in result.terms.items():
        assert tr.position.seats_counted == 12
        assert tr.position.vote == "BULLISH"  # the sample council leans bullish everywhere
        assert tr.position.lean_label.endswith("bullish")
        assert 0.5 < tr.position.p_bullish < 0.6
        assert tr.reality_anchor.horizon == term
        assert tr.note  # the Grand Master explained each term
        assert len(tr.ticks) == 12
    assert result.terms["short"].reality_anchor.options_implied_move_pct is not None
    assert result.terms["long"].reality_anchor.options_implied_move_pct is None
    assert 0 < len(result.debate_transcript) <= 4
    assert len(result.prosecutor_verdicts) == settings.debate_rounds
    assert result.synthesis.headline
    assert result.synthesis.dissent_summary  # never empty, per the hard rule


@pytest.mark.asyncio
async def test_each_term_is_scored_on_its_own_window(settings):
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)
    assert result.terms["short"].resolve_at == AS_OF + timedelta(days=7)
    assert result.terms["medium"].resolve_at == AS_OF + timedelta(days=91)
    assert result.terms["long"].resolve_at == AS_OF + timedelta(days=365)


@pytest.mark.asyncio
async def test_one_crypt_row_per_term_tied_by_run_id(settings):
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    rows = _rows(settings, "SELECT * FROM predictions ORDER BY rowid")
    assert [r["horizon"] for r in rows] == list(TERMS)
    assert {r["run_id"] for r in rows} == {result.run_id}
    for row in rows:
        tr = result.terms[row["horizon"]]
        assert row["id"] == tr.prediction_id
        # blind_* was computed in Phase A and must never be overwritten later
        assert row["blind_vote"] == tr.blind.vote
        assert row["council_vote"] == tr.position.vote
        assert row["council_confidence"] == tr.position.confidence
        assert row["p_raw"] == tr.position.p_bullish
        assert row["dissent_summary"] == tr.dissent_summary
        assert row["prosecutor_verdict"] is not None
        synthesis = json.loads(row["synthesis_json"])
        assert synthesis["headline"] == result.synthesis.headline
        assert set(synthesis["terms"]) == set(TERMS)


@pytest.mark.asyncio
async def test_seat_votes_are_written_per_term_with_that_terms_weight(settings):
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    weights = {}
    for term, tr in result.terms.items():
        rows = _rows(
            settings,
            "SELECT seat_id, vote, weight_applied FROM seat_votes WHERE prediction_id = ?",
            tr.prediction_id,
        )
        assert len(rows) == 12
        by_seat = {r["seat_id"]: r for r in rows}
        for sr in result.seat_results:
            assert by_seat[sr.seat_id]["vote"] == sr.verdicts.term(term).vote
        weights[term] = by_seat["fundamentalist"]["weight_applied"]
    # The Fundamentalist counts for far more on the long term than the short.
    assert weights["long"] > weights["medium"] > weights["short"] > 0


@pytest.mark.asyncio
async def test_thin_participation_is_a_warning_not_an_override(settings):
    settings.min_participating_seats_pct = 2.0  # impossible: >100% of seats

    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    for tr in result.terms.values():
        assert [w.kind for w in tr.warnings] == ["participation"]
        assert tr.position.vote == "BULLISH"  # the lean survives the warning
    # the Grand Master still explains the run instead of being skipped
    assert any(c.seat_id == "grand_master" for c in result.call_log)


@pytest.mark.asyncio
async def test_a_prosecutor_veto_is_a_warning_on_the_terms_it_names(settings, monkeypatch):
    from council.engine.schemas import ProsecutorVerdict
    from council.seats.prosecutor import ProsecutorSeat

    async def veto_the_long_term(self, *args, **kwargs):
        return ProsecutorVerdict(
            round_n=args[6], target_direction="BULLISH", veto=True,
            veto_reason="the long-term case leans on one stale filing", veto_terms=["long"],
        )

    monkeypatch.setattr(ProsecutorSeat, "review", veto_the_long_term)
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)

    long_warnings = result.terms["long"].warnings
    assert [w.kind for w in long_warnings] == ["prosecutor"]
    assert "one stale filing" in long_warnings[0].message
    assert result.terms["long"].position.vote == "BULLISH"
    assert result.terms["short"].warnings == []


@pytest.mark.asyncio
async def test_a_failed_grand_master_still_leaves_readable_notes(settings, monkeypatch):
    from council.seats.grand_master import GrandMasterSeat

    async def fails(self, **kwargs):
        return None

    monkeypatch.setattr(GrandMasterSeat, "synthesize", fails)
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)
    assert "Short term: leaning bullish" in result.synthesis.headline
    assert "chance of rising" in result.terms["medium"].note


@pytest.mark.asyncio
async def test_short_term_levels_come_from_the_technician_when_it_agrees(settings):
    result = await run_deliberation("NVDA", settings, as_of=AS_OF)
    tech = next(s for s in result.seat_results if s.seat_id == "technician")
    assert tech.verdicts.short.vote == result.terms["short"].position.vote
    assert result.terms["short"].entry == tech.verdicts.short.entry
    assert result.terms["medium"].entry is None and result.terms["long"].entry is None


class TestParticipationWeights:
    """Task #76 -- the participation warning is competence-weighted, not a
    plain headcount: a seat that barely counts on a term missing its data
    matters less than one that counts fully. senate_watcher is left out of
    both sides -- no free congress-trades source exists, so it can never
    read."""

    def test_excludes_the_structurally_no_data_seat(self):
        seats = {"technician", "catalyst_seer", "senate_watcher"}
        expected = competence("technician", "short") + competence("catalyst_seer", "short")
        assert _eligible_weight(seats, "short") == round(expected, 4)
        assert _STRUCTURALLY_NO_DATA_SEATS == frozenset({"senate_watcher"})

    def test_weight_varies_by_term(self):
        assert _eligible_weight({"fundamentalist"}, "short") == 0.2
        assert _eligible_weight({"fundamentalist"}, "long") == 1.0

    def test_dead_even_is_a_read_and_no_read_is_not(self):
        verdicts = {
            "technician": SimpleNamespace(vote="NO_CONVICTION"),
            "fundamentalist": SimpleNamespace(vote="NO_READ"),
            "oracle_options": SimpleNamespace(vote="BEARISH"),
        }
        expected = competence("technician", "short") + competence("oracle_options", "short")
        assert _read_weight(verdicts, "short") == round(expected, 4)


def test_every_seat_counts_on_every_term():
    from council.engine.orchestrator import TIER_I_SEATS

    for seat in TIER_I_SEATS:
        for term in TERMS:
            assert competence(seat.id, term) > 0, (seat.id, term)


def test_a_seats_overall_lean_weights_the_terms_it_suits():
    from council.seats.base import SeatAnswer
    from council.tests.seat_answers import seat_answer_payload

    payload = seat_answer_payload("BULLISH", 0.612)
    payload["short"] = {"vote": "BEARISH", "probability": 0.612, "expected_move_pct": 2.0, "rationale": "r"}
    answer = SeatAnswer(**payload).to_multi_term()
    # The Fundamentalist barely counts short-term, so its bullish longer
    # terms win; the Technician counts most short-term, so it tips bearish.
    assert seat_lean("fundamentalist", answer)[0] == "BULLISH"
    assert seat_lean("technician", answer)[0] == "BEARISH"


@pytest.mark.asyncio
async def test_unknown_ticker_is_rejected_before_any_seat_runs(settings, monkeypatch):
    """A ticker with no price data used to fail only after Phase A -- after
    every seat had gathered data and made its (paid) model calls."""
    from council.data.service import DataService
    from council.engine import orchestrator
    from council.engine.llm_client import LLMClient

    async def no_prices(self, ticker, as_of, lookback_days=180):
        raise RuntimeError("All providers failed for fetch_ohlcv")

    calls = []

    async def counting_structured(self, **kwargs):
        calls.append(kwargs)
        raise AssertionError("no model call may happen for an unknown ticker")

    monkeypatch.setattr(DataService, "get_ohlcv", no_prices)
    monkeypatch.setattr(LLMClient, "get_structured", counting_structured)
    events = []

    async def progress(event, payload):
        events.append(event)

    with pytest.raises(orchestrator.TickerNotFound, match="XYZZ"):
        await run_deliberation("XYZZ", settings, as_of=AS_OF, progress=progress)
    assert calls == []
    assert "seat_stage" not in events
