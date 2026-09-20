"""The Macro Sage -- "Keeper of the Outer Rim". Sees only yields, curve
shape, Fed path proxies, CPI, employment, the dollar, oil, and the ticker's
historical beta to the market -- never the ticker's own price series.
Strong at 1m/1y, should usually abstain at 1d (the competence matrix already
excludes it there)."""
from __future__ import annotations

from typing import Any

from council.data.service import DataService
from council.engine.horizons import competent_horizons
from council.engine.llm_client import LLMClient
from council.seats.base import MemoryLesson, SeatContext, SeatVerdict
from council.seats.debiasing import DEBIASING_PREAMBLE

_SYSTEM_PROMPT = DEBIASING_PREAMBLE + """
You are the Macro Sage, "Keeper of the Outer Rim" on a market-prediction
council. You see ONLY macro data: yields, curve shape, the Fed funds rate,
CPI, unemployment, the dollar, oil, and this ticker's historical beta to the
broad market. No price chart of the ticker itself, no news, no fundamentals.

You are a slow-moving seat. Macro regime shifts matter over months and
years, not days -- if asked about a short horizon your honest answer is
usually NO_READ, because nothing here plausibly moves a single name in a
day or a week.
"""


class MacroSageSeat:
    id = "macro_sage"
    title = "Keeper of the Outer Rim"
    allowed_data = frozenset({"macro"})
    horizons = competent_horizons("macro_sage")

    async def gather(
        self, data_service: DataService, ticker: str, as_of: Any, horizon: str
    ) -> SeatContext:
        snapshot = await data_service.get_macro_snapshot(ticker, as_of=as_of)
        return SeatContext(
            self.id,
            self.allowed_data,
            {"macro": snapshot},
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
        m = ctx["macro"]

        user_prompt = (
            f"Macro snapshot, horizon {ctx.horizon}. Data as of {m.as_of.isoformat()}.\n\n"
            f"Fed funds rate: {m.fed_funds_rate}%\n"
            f"10y yield: {m.treasury_10y}%, 2y yield: {m.treasury_2y}%, "
            f"curve (10y-2y): {m.curve_10y_minus_2y}%\n"
            f"CPI YoY: {m.cpi_yoy}%\n"
            f"Unemployment: {m.unemployment_rate}%\n"
            f"Dollar index (proxy): {m.dollar_index}\n"
            f"WTI crude: {m.wti_crude}\n"
            f"Ticker's estimated beta to the broad market: {m.ticker_beta_to_spx}\n\n"
            "Give your verdict -- remember short horizons are usually NO_READ for this seat."
        )

        return await llm_client.get_verdict(
            seat_id=self.id,
            model=llm_client.settings.seat_model,
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            fixture_name=self.id,
            sample_index=sample_index,
            memories=memories,
        )
