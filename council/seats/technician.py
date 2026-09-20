"""The Technician -- "Keeper of the Charts". Sees OHLCV only. The ticker is
anonymised to "Instrument A" so the LLM narrating the setup cannot
pattern-match the real name from its training data; direction is decided by
the deterministic family-vote engine, never by the LLM."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE
from council.seats.technical_indicators import analyse

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Technician, "Keeper of the Charts" on a market-prediction
council. You see ONLY an anonymised OHLCV price series for "Instrument A" --
never a name, never news, never fundamentals. Do not guess what the
instrument is.

The DIRECTION of this call has already been decided mechanically by a
deterministic family-vote engine (trend / breakout / oscillator / volume
families) and is given to you as a fact, along with the ATR-derived entry,
exit, and invalidation levels. Your job is narration and evidence, not
direction: describe the configuration precisely, explain what each family
vote means, and state what would change your mind. Do not contradict the
given direction and do not invent price levels other than the ones supplied.
"""


class TechnicianSeat:
    id = "technician"
    title = "Keeper of the Charts"
    allowed_data = frozenset({"ohlcv"})
    horizons = competent_horizons("technician")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        series = await data_service.get_ohlcv(ticker, as_of=as_of, lookback_days=180)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"ohlcv": series},
            ticker=ticker,
            as_of=as_of,
            horizon=horizon,
        )

    async def deliberate(
        self,
        ctx: SeatContext,
        llm_client: LLMClient,
        round_n: int = 0,
        sample_index: int = 0,
        memories: list[MemoryLesson] | None = None,
        peer_summaries: list[dict] | None = None,
    ) -> SeatVerdict:
        series = ctx["ohlcv"]
        reading = analyse(series.bars)

        if reading.mechanical_vote == "NO_READ":
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="Insufficient history or the four technical families split evenly with no majority direction.",
                what_would_change_my_mind="Enough additional history to break the tie, or a fresh majority forming across the trend, breakout, oscillator, and volume families.",
                data_quality="PARTIAL" if series.staleness_seconds else "POOR",
                abstain_reason="no_mechanical_majority",
            )

        user_prompt = (
            f"Instrument A, horizon {ctx.horizon}. Data as of {series.as_of.isoformat()}, "
            f"staleness {series.staleness_seconds:.0f}s.\n\n"
            f"Mechanical direction (already decided, do not override): {reading.mechanical_vote}\n"
            f"Family votes: trend={reading.family_votes.trend}, "
            f"breakout={reading.family_votes.breakout}, "
            f"oscillator={reading.family_votes.oscillator}, "
            f"volume={reading.family_votes.volume}\n"
            f"20-period MA: {reading.sma20}, 50-period MA: {reading.sma50}\n"
            f"14-period RSI: {reading.rsi14}\n"
            f"14-period ATR: {reading.atr14}\n"
            f"Last close: {reading.last_close}\n"
            f"20-day high/low: {reading.twenty_day_high}/{reading.twenty_day_low}\n"
            f"Given levels -- entry: {reading.entry}, exit: {reading.exit}, "
            f"invalidation: {reading.invalidation}, expected move: "
            f"{reading.expected_move_pct}%\n\n"
            "Write the thesis, comparison class, and evidence for this configuration."
        )

        verdict = await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name="technician",
            sample_index=sample_index,
            memories=memories,
        )

        if verdict.vote == "NO_READ":
            return verdict

        # Direction is mechanical, not the LLM's to set -- enforce it even if
        # a live call somehow narrated the wrong side.
        return verdict.model_copy(
            update={
                "vote": reading.mechanical_vote,
                "entry": reading.entry,
                "exit": reading.exit,
                "invalidation": reading.invalidation,
                "expected_move_pct": reading.expected_move_pct,
            }
        )
