"""The Transcript Linguist -- "Listener to the Council of Officers". Sees
only earnings call transcripts (prepared remarks vs Q&A). Looks for hedging
language shifts, guidance verb changes, analyst question tone, avoided
topics, and how answers to similar questions change across quarters --
compares the last 4 calls, never one in isolation."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Transcript Linguist, "Listener to the Council of Officers" on a
market-prediction council. You see ONLY earnings call transcripts -- no
price chart, no news, no fundamentals, no numbers beyond what management
says out loud.

Never judge a single call in isolation. Compare across the calls you're
given (up to the last 4) for: hedging language shifts, changes in guidance
verbs (e.g. "expect" softening to "hope" or "believe"), analyst question
tone, topics the company discussed before but now avoids, and whether
answers to recurring questions have changed. A single confident-sounding
call means little; a *trend* across calls is the signal.
"""


class TranscriptLinguistSeat:
    id = "transcript_linguist"
    title = "Listener to the Council of Officers"
    allowed_data = frozenset({"transcripts"})
    horizons = competent_horizons("transcript_linguist")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        feed = await data_service.get_earnings_transcripts(ticker, as_of=as_of, limit=4)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"transcripts": feed},
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
        peer_summaries: list[dict] | None = None,
    ) -> SeatVerdict:
        feed = ctx["transcripts"]

        if len(feed.calls) < 2:
            return SeatVerdict(
                vote="NO_READ",
                probability=0.5,
                expected_move_pct=0.0,
                thesis="Fewer than two calls available -- cannot compare trend across calls.",
                what_would_change_my_mind="A second available transcript to compare against.",
                data_quality="PARTIAL" if feed.calls else "POOR",
                abstain_reason="insufficient_history",
            )

        blocks = []
        for c in feed.calls:
            blocks.append(
                f"--- {c.fiscal_period} ({c.call_date}) ---\n"
                f"Prepared remarks: {c.prepared_remarks}\n"
                f"Q&A highlights: {c.qa_highlights}"
            )

        user_prompt = (
            f"Earnings calls, most recent first, horizon {ctx.horizon}. Data as of "
            f"{feed.as_of.isoformat()}.\n\n" + "\n\n".join(blocks)
            + "\n\nCompare across these calls for the trend signals described in your "
            "system prompt, then give your verdict."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
        )
