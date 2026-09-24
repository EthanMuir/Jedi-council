"""The Technician -- "Keeper of the Charts". Sees OHLCV only. The ticker is
anonymised to "Instrument A" so the LLM narrating the setup cannot
pattern-match the real name from its training data. The SHORT-term direction
is decided by the deterministic family-vote engine, never by the LLM; the
medium and long terms are the LLM's read of the longer trend, which counts
for less (see the competence matrix)."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import (
    ComparisonClass,
    MemoryLesson,
    MultiTermVerdict,
    SeatContext,
    SeatVerdict,
)
from council.seats.debiasing import DEBIASING_PREAMBLE
from council.seats.technical_indicators import TechnicalReading, analyse

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Technician, "Keeper of the Charts" on a market-prediction
council. You see ONLY an anonymised OHLCV price series for "Instrument A" --
never a name, never news, never fundamentals. Do not guess what the
instrument is.

Your SHORT-term direction has already been decided mechanically by a
deterministic family-vote engine (trend / breakout / oscillator / volume
families) and is given to you as a fact, along with the ATR-derived entry,
exit, and invalidation levels. For the short term your job is narration and
evidence, not direction: explain what each family vote means and do not
contradict the given direction or invent other price levels.

For the MEDIUM and LONG terms, read the longer trend yourself -- where
price sits against its 50-day average and its six-month range, and whether
that trend is strengthening or fading. Charts say less about months and
years than about next week, so keep those leans modest.
"""


class TechnicianSeat:
    id = "technician"
    title = "Keeper of the Charts"
    allowed_data = frozenset({"ohlcv"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=180)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"ohlcv": series},
            ticker=ticker,
            as_of=as_of,
        )

    async def deliberate(
        self,
        ctx: SeatContext,
        llm_client: LLMClient,
        round_n: int = 0,
        sample_index: int = 0,
        memories: list[MemoryLesson] | None = None,
        peer_summaries: list[dict] | None = None,
    ) -> MultiTermVerdict:
        series = ctx["ohlcv"]
        reading = analyse(series.bars)

        # Under 50 bars the trend family can't even be computed -- that's
        # too little history to read, not a genuine split.
        if reading.sma50 is None:
            return MultiTermVerdict.no_read(
                thesis="Not enough price history to compute the four technical families.",
                what_would_change_my_mind="At least 50 days of price history.",
                abstain_reason="insufficient_history",
            )

        closes = [bar.close for bar in series.bars]
        user_prompt = (
            f"Instrument A. Data as of {series.as_of.isoformat()}, "
            f"staleness {series.staleness_seconds:.0f}s.\n\n"
            f"Short-term direction (already decided mechanically, do not override): "
            f"{reading.mechanical_vote}\n"
            f"Family votes: trend={reading.family_votes.trend}, "
            f"breakout={reading.family_votes.breakout}, "
            f"oscillator={reading.family_votes.oscillator}, "
            f"volume={reading.family_votes.volume}\n"
            f"20-period MA: {reading.sma20}, 50-period MA: {reading.sma50}\n"
            f"14-period RSI: {reading.rsi14}\n"
            f"14-period ATR: {reading.atr14}\n"
            f"Last close: {reading.last_close}\n"
            f"20-day high/low: {reading.twenty_day_high}/{reading.twenty_day_low}\n"
            f"Six-month high/low: {max(closes):.2f}/{min(closes):.2f}, "
            f"six-month change: {(closes[-1] / closes[0] - 1) * 100:+.1f}%\n"
            f"Short-term levels -- entry: {reading.entry}, exit: {reading.exit}, "
            f"invalidation: {reading.invalidation}, expected move: "
            f"{reading.expected_move_pct}%\n\n"
            "Narrate the short-term configuration, then give your own lean on the "
            "medium and long terms from the longer trend."
        )

        answer = await llm_client.get_seat_answer(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name="technician",
            sample_index=sample_index,
            memories=memories,
        )

        if not answer.read:
            return answer
        # The short-term direction is mechanical, not the LLM's to set --
        # enforce it even if a live call narrated the other side.
        return answer.model_copy(update={"short": _mechanical_short(reading, answer)})


def _mechanical_short(reading: TechnicalReading, answer: MultiTermVerdict) -> MultiTermVerdict:
    narrated = answer.short
    if reading.mechanical_vote == "NO_CONVICTION":
        return SeatVerdict(
            vote="NO_CONVICTION",
            probability=0.5,
            expected_move_pct=0.0,
            data_quality=narrated.data_quality,
            what_would_change_my_mind="A fresh majority forming across the trend, breakout, "
            "oscillator, and volume families.",
            abstain_reason="The four technical families split evenly with no majority direction.",
            key_evidence=narrated.key_evidence,
            thesis=narrated.thesis,
            term_rationale=narrated.term_rationale,
        )
    probability = narrated.probability
    if narrated.vote == "NO_CONVICTION":
        # The LLM sat on the fence where the families didn't -- size the
        # lean from how many families agree instead (never a 0.05 multiple).
        votes = reading.family_votes
        tally = [votes.trend, votes.breakout, votes.oscillator, votes.volume]
        margin = abs(tally.count("BULLISH") - tally.count("BEARISH"))
        probability = round(0.503 + 0.03 * margin, 3)
    comparison_class = next(
        (answer.term(t).comparison_class for t in ("short", "medium", "long")
         if answer.term(t).comparison_class is not None),
        None,
    ) or ComparisonClass(
        definition="Chart setups with the same family-vote majority",
        n_observations=0,
        base_rate=0.5,
        why_this_class="No comparison class was given; this direction is the mechanical "
        "family vote alone.",
    )
    return SeatVerdict(
        vote=reading.mechanical_vote,
        probability=probability,
        expected_move_pct=reading.expected_move_pct or narrated.expected_move_pct,
        entry=reading.entry,
        exit=reading.exit,
        invalidation=reading.invalidation,
        comparison_class=comparison_class,
        data_quality=narrated.data_quality,
        what_would_change_my_mind=narrated.what_would_change_my_mind,
        key_evidence=narrated.key_evidence,
        thesis=narrated.thesis,
        term_rationale=narrated.term_rationale,
    )
