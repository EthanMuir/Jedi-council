"""The reflection loop (Addendum A6) -- how the council actually improves
rather than just being measured. Immediate reflection runs on every
resolution; extended reflection runs monthly per seat over its last ~30
resolutions and writes to deep memory only, never touching a stored
verdict."""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, field_validator

from council.crypt.resolution import ResolutionOutcome
from council.engine.horizons import TERM_NAMES, TERM_WINDOWS
from council.engine.llm_client import LLMClient, SchemaRetryExhausted
from council.seats.base import SeatVerdict

_VAGUE_PHRASES = (
    "be more careful",
    "pay more attention",
    "be more cautious",
    "try harder",
    "be more diligent",
    "do better next time",
    "should have been more careful",
)


class ReflectionLesson(BaseModel):
    lesson_id: str
    seat_id: str
    ticker: str
    horizon: str
    was_correct: bool
    probability_stated: float
    error_type: Literal["DIRECTION", "MAGNITUDE", "TIMING", "CALIBRATION", "CORRECT"]
    lesson: str
    generalises_beyond_this_ticker: bool

    @field_validator("lesson")
    @classmethod
    def _reject_vague(cls, v: str) -> str:
        word_count = len(v.split())
        if word_count > 60:
            raise ValueError(f"lesson is {word_count} words, must be <= 60")
        if word_count < 4:
            raise ValueError("lesson too short to carry a concrete trigger condition")
        lowered = v.lower()
        for phrase in _VAGUE_PHRASES:
            if phrase in lowered:
                raise ValueError(
                    f"lesson is too vague ('{phrase}' carries no concrete trigger condition)"
                )
        return v


class ExtendedReflection(BaseModel):
    seat_id: str
    n_resolutions_reviewed: int
    systematic_bias_summary: str
    direction_bias: Literal["BULLISH_LEANING", "BEARISH_LEANING", "NONE_DETECTED"]
    confidence_bias: Literal["OVERCONFIDENT", "UNDERCONFIDENT", "WELL_CALIBRATED"]
    horizons_to_abstain_more: list[str]


_IMMEDIATE_SYSTEM_PROMPT = """
You are writing an immediate reflection on your own resolved prediction, in
your own voice. You are given your own verdict, your stated comparison
class, and the realised outcome (including max favourable/adverse
excursion) -- nothing about what any other seat said or how the council
voted overall.

Write ONE concrete, actionable lesson with a specific trigger condition.
"I should have weighted the earnings date more heavily given how close it
was" is useful. "I should be more careful" is noise -- it will be rejected.

Classify the error type precisely:
- DIRECTION: you called the wrong direction entirely.
- MAGNITUDE: right direction, wrong size of move.
- TIMING: right thesis, wrong term -- would likely have been correct over
  a different timeframe (the next week, the next 3 months, the next year).
- CALIBRATION: right direction, but your stated probability was badly
  miscalibrated (too confident or not confident enough).
- CORRECT: your call and confidence were both good -- write what worked,
  concretely, so it can be repeated.

State honestly whether this lesson would generalise to other tickers in a
similar situation, or whether it is specific to this one.
"""

_EXTENDED_SYSTEM_PROMPT = """
You are conducting your own monthly extended reflection, reviewing your
last ~30 resolved predictions as a batch. Look for a SYSTEMATIC pattern,
not a recap of individual calls: a consistent directional bias, consistent
over/under-confidence, terms (short/medium/long) where your leans should
sit closer to 0.5, or
tickers/sectors where you are reliably wrong.

This reflection is written to memory only. It can never alter a stored
verdict or the historical record -- you may learn from your history, you
may not edit it.
"""


async def generate_immediate_reflection(
    *,
    seat_id: str,
    ticker: str,
    horizon: str,
    verdict: SeatVerdict,
    outcome: ResolutionOutcome,
    llm_client: LLMClient,
    model: str,
) -> ReflectionLesson | None:
    was_correct = bool(outcome.direction_correct)
    comparison_class = (
        verdict.comparison_class.definition if verdict.comparison_class else "none stated"
    )
    user_prompt = (
        f"Term: {TERM_NAMES.get(horizon, horizon)} ({TERM_WINDOWS.get(horizon, horizon)})\n"
        f"Your verdict: vote={verdict.vote}, probability={verdict.probability}\n"
        f"Comparison class: {comparison_class}\n"
        f"Thesis: {verdict.thesis}\n\n"
        f"Realised outcome: direction_correct={outcome.direction_correct}, "
        f"realised_move={outcome.realised_move_pct}%, "
        f"MFE={outcome.mfe_pct}%, MAE={outcome.mae_pct}%\n\n"
        "Write your reflection."
    )
    fixture_name = "reflection_correct" if was_correct else "reflection_incorrect"
    try:
        result = await llm_client.get_structured(
            seat_id=seat_id,
            model=model,
            system_prompt=_IMMEDIATE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=ReflectionLesson,
            fixture_name=fixture_name,
        )
    except SchemaRetryExhausted:
        return None

    # Facts our own system already knows are never trusted from the model's
    # own output, even though the schema requires them for validation --
    # only error_type/lesson/generalises_beyond_this_ticker are its own.
    return result.model_copy(
        update={
            "lesson_id": str(uuid.uuid4()),
            "seat_id": seat_id,
            "ticker": ticker,
            "horizon": horizon,
            "was_correct": was_correct,
            "probability_stated": verdict.probability,
        }
    )


async def generate_extended_reflection(
    *,
    seat_id: str,
    resolution_summaries: list[str],
    llm_client: LLMClient,
    model: str,
) -> ExtendedReflection | None:
    if not resolution_summaries:
        return None
    user_prompt = (
        f"Your last {len(resolution_summaries)} resolutions:\n"
        + "\n".join(f"- {s}" for s in resolution_summaries)
        + "\n\nWrite your extended reflection."
    )
    try:
        result = await llm_client.get_structured(
            seat_id=seat_id,
            model=model,
            system_prompt=_EXTENDED_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_model=ExtendedReflection,
            fixture_name="extended_reflection",
        )
    except SchemaRetryExhausted:
        return None
    return result.model_copy(
        update={"seat_id": seat_id, "n_resolutions_reviewed": len(resolution_summaries)}
    )
