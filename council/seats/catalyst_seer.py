"""The Catalyst Seer -- "Watcher of Omens". Sees only the news/sentiment
feed. Must classify each catalyst SCHEDULED vs SURPRISE and justify why a
headline is not already priced in -- LLM agents systematically over-weight
news relative to other evidence."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Catalyst Seer, "Watcher of Omens" on a market-prediction
council. You see ONLY a news/sentiment feed for this ticker -- no price
chart, no fundamentals, no option data.

For every catalyst you cite, explicitly classify it as SCHEDULED (a known,
dated event such as an earnings date or a Fed meeting) or SURPRISE (an
unscheduled development). A known-and-dated catalyst is not, by itself, an
edge -- it is very likely already reflected in the price. For every SURPRISE
catalyst, state your reasoning for why the market has or has not already
priced it in, given how long ago it was published relative to `as_of`.

News is mostly a short-term force: a surprise can move the stock this week
and fade by next quarter, while a real change to the business (a new
contract, a lost customer, a regulatory ruling) carries into the longer
terms. If the feed holds little beyond noise, keep your leans close to 0.5
rather than inventing conviction.
"""


class CatalystSeerSeat:
    id = "catalyst_seer"
    title = "Watcher of Omens"
    allowed_data = frozenset({"news"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        news = await data_service.get_news(ticker, as_of=as_of, lookback_days=14)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"news": news},
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
        news = ctx["news"]

        if not news.items:
            return MultiTermVerdict.no_read(
                thesis="No news items in the lookback window.",
                what_would_change_my_mind="Any dated, sourced headline entering the feed.",
                data_quality="POOR",
                abstain_reason="no_data",
            )

        lines = []
        for item in news.items:
            age_days = (news.as_of - item.published_at).total_seconds() / 86400
            lines.append(
                f"- [{item.published_at.isoformat()}, {age_days:.1f}d old, "
                f"sentiment={item.sentiment_label}({item.sentiment_score})] "
                f"{item.headline} ({item.source}): {item.summary}"
            )

        user_prompt = (
            f"Ticker news feed. Data as of "
            f"{news.as_of.isoformat()}, staleness {news.staleness_seconds:.0f}s.\n\n"
            + "\n".join(lines)
            + "\n\nClassify each catalyst SCHEDULED vs SURPRISE, assess pricing-in, "
            "and give your lean for each term."
        )

        return await llm_client.get_seat_answer(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name="catalyst_seer",
            sample_index=sample_index,
            memories=memories,
        )
