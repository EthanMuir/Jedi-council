"""Reflection loop: hard-constrained lessons, and the "our own facts are
never trusted from the model" override pattern."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from council.config import Settings
from council.crypt.resolution import ResolutionOutcome
from council.engine.llm_client import LLMClient
from council.memory.reflection import (
    ReflectionLesson,
    generate_extended_reflection,
    generate_immediate_reflection,
)
from council.seats.base import SeatVerdict

_CC = {"definition": "d", "n_observations": 10, "base_rate": 0.5, "why_this_class": "w"}


def _verdict():
    return SeatVerdict(
        vote="BULLISH",
        probability=0.617,
        comparison_class=_CC,
        expected_move_pct=3.0,
        thesis="t",
        what_would_change_my_mind="w",
        data_quality="GOOD",
    )


def _outcome(direction_correct=True):
    return ResolutionOutcome(
        price_at_resolve=105.0, high=106.0, low=99.0, direction_correct=direction_correct,
        entry_hit=None, exit_hit=None, invalidation_hit=None,
        mfe_pct=6.0, mae_pct=-1.0, realised_move_pct=5.0, brier=0.09,
    )


def _lesson(**overrides):
    payload = dict(
        lesson_id="x", seat_id="technician", ticker="NVDA", horizon="1w",
        was_correct=True, probability_stated=0.6, error_type="CORRECT",
        lesson="When trend and volume both confirm, hold through minor pullbacks rather than trimming early.",
        generalises_beyond_this_ticker=True,
    )
    payload.update(overrides)
    return ReflectionLesson(**payload)


def test_vague_lesson_rejected():
    with pytest.raises(ValidationError):
        _lesson(lesson="I should be more careful next time.")


def test_too_short_lesson_rejected():
    with pytest.raises(ValidationError):
        _lesson(lesson="Do better.")


def test_concrete_lesson_accepted():
    lesson = _lesson()
    assert lesson.lesson


@pytest.mark.asyncio
async def test_immediate_reflection_overrides_known_facts_regardless_of_model_output():
    settings = Settings(no_llm=True)
    llm_client = LLMClient(settings)
    result = await generate_immediate_reflection(
        seat_id="technician", ticker="NVDA", horizon="1w",
        verdict=_verdict(), outcome=_outcome(True),
        llm_client=llm_client, model="claude-sonnet-5",
    )
    assert result is not None
    # these are asserted regardless of what the fixture file says -- they're
    # overridden by our own code, not trusted from the model
    assert result.seat_id == "technician"
    assert result.ticker == "NVDA"
    assert result.horizon == "1w"
    assert result.was_correct is True
    assert result.probability_stated == 0.617


@pytest.mark.asyncio
async def test_immediate_reflection_picks_correct_vs_incorrect_fixture():
    settings = Settings(no_llm=True)
    llm_client = LLMClient(settings)

    correct = await generate_immediate_reflection(
        seat_id="technician", ticker="NVDA", horizon="1w",
        verdict=_verdict(), outcome=_outcome(True),
        llm_client=llm_client, model="claude-sonnet-5",
    )
    incorrect = await generate_immediate_reflection(
        seat_id="technician", ticker="NVDA", horizon="1w",
        verdict=_verdict(), outcome=_outcome(False),
        llm_client=llm_client, model="claude-sonnet-5",
    )
    assert correct.error_type == "CORRECT"
    assert incorrect.error_type != "CORRECT"


@pytest.mark.asyncio
async def test_extended_reflection_empty_history_returns_none():
    settings = Settings(no_llm=True)
    llm_client = LLMClient(settings)
    result = await generate_extended_reflection(
        seat_id="technician", resolution_summaries=[], llm_client=llm_client, model="claude-sonnet-5"
    )
    assert result is None


@pytest.mark.asyncio
async def test_extended_reflection_never_writes_to_predictions_or_resolutions():
    """The reflection loop only ever writes to memory -- it has no access
    to the Crypt's write functions at all, enforced by import surface: this
    module never imports council.crypt.ledger."""
    import council.memory.reflection as reflection_module

    source = open(reflection_module.__file__).read()
    assert "crypt.ledger" not in source
    assert "write_prediction" not in source
    assert "write_resolution" not in source
