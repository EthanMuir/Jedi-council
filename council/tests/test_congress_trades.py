"""Congressional trades come from Alpha Vantage's CONGRESS_TRADES on the
free key (#106). The sample below is the shape it returned live for NVDA."""
from __future__ import annotations

from datetime import date, datetime

import httpx
import pytest

from council.data.cache import DiskCache
from council.data.providers.alpha_vantage import AlphaVantageCongressProvider
from council.data.service import DataService

_LIVE_SHAPE = {
    "symbol": "NVDA",
    "bioguide_id": "",
    "trades": [
        {
            "chamber": "HOUSE", "politician": "Hon. Gilbert Cisneros", "bioguide_id": "C001123",
            "politician_canonical": "Gilbert Ray Cisneros, Jr.", "party": "D", "state": "CA",
            "state_district": "CA31", "symbol": "NVDA", "ticker_reported": "NVDA",
            "ticker_source": "reported", "asset_name": "NVIDIA Corporation - Common Stock (NVDA) [ST]",
            "asset_type_code": "ST", "transaction_type": "SELL", "transaction_date": "2026-08-18",
            "notification_date": "2026-09-04", "amount_min": "1001.00", "amount_max": "15000.00",
            "owner_code": "SELF", "filing_status": "NEW", "filed_date": "2026-09-10",
        },
        {
            "chamber": "SENATE", "politician": "Markwayne Mullin", "bioguide_id": "M001190",
            "politician_canonical": "Markwayne Mullin", "party": "R", "state": "OK",
            "state_district": None, "symbol": "NVDA", "transaction_type": "BUY",
            "transaction_date": "2026-02-25", "notification_date": None,
            "amount_min": "15001.00", "amount_max": "50000.00", "owner_code": "JOINT",
            "filing_status": None, "filed_date": "2026-03-10",
        },
        {   # far outside the lookback window
            "chamber": "HOUSE", "politician": "Old Trade", "party": "D", "state": "NY",
            "transaction_type": "BUY", "transaction_date": "2023-01-02",
            "amount_min": "1001.00", "amount_max": "15000.00", "filed_date": "2023-02-01",
        },
        {"chamber": "HOUSE", "transaction_type": "BUY"},  # malformed -- skipped
    ],
    "trades_total_count": 4,
}


def _provider(payload, seen=None) -> AlphaVantageCongressProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        return httpx.Response(200, json=payload)

    provider = AlphaVantageCongressProvider("test-key")
    provider._av._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


async def test_maps_house_and_senate_trades_in_the_window():
    seen = []
    data = await _provider(_LIVE_SHAPE, seen).fetch_congress_data("NVDA", date(2025, 9, 24), date(2026, 9, 24))

    assert seen[0]["function"] == "CONGRESS_TRADES" and seen[0]["symbol"] == "NVDA"
    trades = data["trades"]
    assert [t["member_name"] for t in trades] == ["Gilbert Ray Cisneros, Jr.", "Markwayne Mullin"]
    house, senate = trades
    assert (house["chamber"], house["transaction_type"], house["party"], house["state"]) == ("House", "SELL", "D", "CA")
    assert house["amount_range"] == "$1,001-$15,000"
    assert house["filed_at"] == "2026-09-10T00:00:00" and house["transaction_date"] == "2026-08-18"
    assert (senate["chamber"], senate["transaction_type"], senate["owner"]) == ("Senate", "BUY", "JOINT")


async def test_only_offers_congress_trades():
    """Anything else falling through to Alpha Vantage would spend the free
    key's 25 requests a day."""
    provider = AlphaVantageCongressProvider("k")
    for method in ("fetch_ohlcv", "fetch_news", "fetch_option_chain", "fetch_macro"):
        assert not hasattr(provider, method)


async def test_a_daily_limit_message_is_a_failure_not_an_empty_list():
    from council.data.providers.alpha_vantage import AlphaVantageRateLimited

    limited = {"Information": "Our standard API rate limit is 25 requests per day."}
    with pytest.raises(AlphaVantageRateLimited):
        await _provider(limited).fetch_congress_data("NVDA", date(2025, 9, 24), date(2026, 9, 24))


async def test_without_a_key_the_seat_is_told_where_to_get_one(tmp_path):
    class _NoCongress:
        name = "yfinance"

    service = DataService(providers=[_NoCongress()], cache=DiskCache(str(tmp_path / "c.db")))
    with pytest.raises(RuntimeError, match="free Alpha Vantage key"):
        await service.get_congress_trades("NVDA", datetime(2026, 9, 24))


async def test_the_key_adds_the_provider_and_counts_the_seat(tmp_path):
    from council.config import Settings
    from council.engine.orchestrator import _no_data_seats, build_data_service

    base = dict(
        anthropic_api_key="sk-ant-test", use_data_fixtures=False,
        cache_db_path=str(tmp_path / "cache.db"),
    )
    with_key = Settings(alpha_vantage_api_key="av-key", **base)
    without = Settings(alpha_vantage_api_key="", **base)
    assert any(p.name == "alpha_vantage" for p in build_data_service(with_key)._providers)
    assert not any(p.name == "alpha_vantage" for p in build_data_service(without)._providers)
    assert _no_data_seats(with_key) == frozenset()
    assert _no_data_seats(without) == frozenset({"senate_watcher"})


async def test_no_trades_in_a_year_is_dead_even_not_no_read():
    from council.data.schemas import CongressTradeFeed
    from council.seats.base import SeatContext
    from council.seats.senate_watcher import SenateWatcherSeat

    feed = CongressTradeFeed(
        ticker="CRWD", trades=[], pending_legislation=[], as_of=datetime(2026, 9, 24),
        staleness_seconds=None, source="alpha_vantage",
    )
    seat = SenateWatcherSeat()
    ctx = SeatContext(seat.id, seat.allowed_data, {"congress": feed}, ticker="CRWD", as_of=datetime(2026, 9, 24))
    answer = await seat.deliberate(ctx, llm_client=None)
    assert answer.read
    assert {answer.term(t).vote for t in ("short", "medium", "long")} == {"NO_CONVICTION"}


async def test_the_seat_prompt_shows_who_traded_and_whose_account(tmp_path):
    from council.data.schemas import CongressTrade, CongressTradeFeed
    from council.seats.base import MultiTermVerdict, SeatContext
    from council.seats.senate_watcher import SenateWatcherSeat

    feed = CongressTradeFeed(
        ticker="NVDA",
        trades=[CongressTrade(
            filed_at=datetime(2026, 9, 2), transaction_date=date(2026, 8, 18),
            member_name="John J. McGuire III", chamber="House", transaction_type="BUY",
            amount_range="$1,001-$15,000", party="R", state="VA", owner="SPOUSE",
        )],
        pending_legislation=[], as_of=datetime(2026, 9, 24), staleness_seconds=None, source="alpha_vantage",
    )
    captured = {}

    class _Client:
        class settings:  # noqa: N801
            seat_model = "m"

        async def get_seat_answer(self, **kwargs):
            captured.update(kwargs)
            return MultiTermVerdict.no_read(thesis="t", abstain_reason="r")

    seat = SenateWatcherSeat()
    ctx = SeatContext(seat.id, seat.allowed_data, {"congress": feed}, ticker="NVDA", as_of=datetime(2026, 9, 24))
    await seat.deliberate(ctx, _Client())
    prompt = captured["user_prompt"]
    assert "John J. McGuire III (R-VA): BUY $1,001-$15,000, owner: spouse" in prompt
    assert "15d disclosure lag" in prompt
    assert "Pending legislation" not in prompt  # none from this source
