"""The Reader of the Guild's Targets -- Wall Street's published price targets
and buy/hold/sell ratings. Sees only how far the analysts' targets sit from
today's price (as a percentage, never the price itself), how the ratings
split and drift, and which firms recently upgraded or downgraded.

Targets are written for twelve months out, so this seat speaks most to the
medium and long terms; a fresh upgrade or downgrade is its only real
short-term evidence."""
from __future__ import annotations

from typing import Any

from council.data.schemas import AnalystRatingsSnapshot
from council.data.service import DataService
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, MultiTermVerdict, SeatContext
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Reader of the Guild's Targets on a market-prediction council.
You see ONLY what professional analysts publish: how far their 12-month
price targets sit above or below today's price, how many cover the stock,
how their buy/hold/sell ratings split (now and three months ago), and which
firms recently changed their rating or target. No chart, no news, no
fundamentals.

Read it with a sceptic's eye:
- Analysts are structurally optimistic -- most stocks carry mostly buy
  ratings and targets above the price. A typical large cap's average target
  sits 10-15% above its price; only upside well beyond that, or a rating mix
  unusually heavy with buys, is a signal. Upside well below it is a mildly
  bearish one.
- CHANGES carry more information than levels: a cluster of upgrades or
  target raises, or a shift in the rating split over three months, says
  more than a static buy rating.
- Downgrades are rarer than upgrades and so carry more weight each.
- Wide disagreement (a far-apart high and low target) means a weaker
  consensus -- keep your leans closer to 0.5.
- Thin coverage (a handful of analysts) is weak evidence.

Targets are 12-month calls, so they speak most to the medium and long
terms. For the short term, only a fresh rating change in the last week or
two is real evidence; without one, keep that lean close to 0.5.
"""


def _pct_from_price(value: float | None, price: float | None) -> str:
    if value is None or not price:
        return "unavailable"
    return f"{(value / price - 1) * 100:+.1f}%"


_ACTION_NAMES = {
    "up": "UPGRADE",
    "down": "DOWNGRADE",
    "init": "initiated coverage",
    "main": "maintained",
    "reit": "reiterated",
}


def format_ratings_prompt(r: AnalystRatingsSnapshot) -> str:
    price = r.current_price
    split = (
        f"strong buy {r.strong_buy}, buy {r.buy}, hold {r.hold}, sell {r.sell}, "
        f"strong sell {r.strong_sell}"
    )
    if r.prior_buy is not None:
        prior = (
            f"strong buy {r.prior_strong_buy}, buy {r.prior_buy}, hold {r.prior_hold}, "
            f"sell {r.prior_sell}, strong sell {r.prior_strong_sell}"
        )
    else:
        prior = "unavailable"

    change_lines = []
    for c in r.recent_changes:
        age_days = (r.as_of.replace(tzinfo=None) - c.changed_at.replace(tzinfo=None)).days
        target = ""
        if c.price_target:
            target = f", target now {_pct_from_price(c.price_target, price)} vs today's price"
            if c.prior_price_target and c.prior_price_target != c.price_target:
                direction = "raised" if c.price_target > c.prior_price_target else "cut"
                move = (c.price_target / c.prior_price_target - 1) * 100
                target += f" ({direction} {move:+.0f}%)"
        grades = (
            f"{c.from_grade} -> {c.to_grade}" if c.from_grade and c.from_grade != c.to_grade
            else c.to_grade or "rating not given"
        )
        change_lines.append(
            f"- {age_days}d ago, {c.firm}: {_ACTION_NAMES.get(c.action, c.action)} "
            f"({grades}){target}"
        )

    return (
        f"Analyst coverage. Data as of {r.as_of.isoformat()}.\n\n"
        f"Analysts with a published target: {r.analyst_count or 'unknown'}\n"
        f"Mean target vs today's price: {_pct_from_price(r.target_mean, price)}\n"
        f"Median target vs today's price: {_pct_from_price(r.target_median, price)}\n"
        f"Highest target: {_pct_from_price(r.target_high, price)}, "
        f"lowest target: {_pct_from_price(r.target_low, price)}\n"
        f"Rating split now ({r.rated} rated): {split}\n"
        f"Rating split three months ago: {prior}\n\n"
        f"Rating and target changes, last 120 days (newest first):\n"
        + ("\n".join(change_lines) if change_lines else "- none")
        + "\n\nWeigh the implied upside against analysts' usual optimism, and changes "
        "over levels, then give your lean for each term."
    )


class AnalystRatingsSeat:
    id = "analyst_ratings"
    title = "Reader of the Guild's Targets"
    allowed_data = frozenset({"analyst_ratings"})

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any
    ) -> SeatContext:
        ratings = await data_service.get_analyst_ratings(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"analyst_ratings": ratings},
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
        r = ctx["analyst_ratings"]

        if not r.target_mean and not r.rated and not r.recent_changes:
            return MultiTermVerdict.no_read(
                thesis="No analyst price targets or ratings for this stock.",
                what_would_change_my_mind="An analyst initiating coverage.",
                abstain_reason="no_data",
            )

        return await llm_client.get_seat_answer(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=format_ratings_prompt(r),
            fixture_name=self.id,
            sample_index=sample_index,
            memories=memories,
        )
