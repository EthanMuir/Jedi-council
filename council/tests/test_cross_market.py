"""The Cross-Market Navigator compares a stock against ITS OWN sector fund and
industry peers -- a real bug had every ticker read against semiconductors
(SMH) and AMD, with dollar/oil hardwired to 0.0, so a restaurant chain and
a power company got identical "cross-market" answers."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from council.data.cache import DiskCache
from council.data.service import DataService
from council.seats.cross_market import CrossMarketSeat

AS_OF = datetime(2026, 9, 24, 16, 0)


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    import council.data.service as data_service_module

    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_BASE_SECONDS", 0.001)
    monkeypatch.setattr(data_service_module, "_PROVIDER_BACKOFF_CAP_SECONDS", 0.001)


def _bars(start_price: float, daily_pct: float) -> list[dict]:
    bars, price = [], start_price
    for i in range(12):
        day = date(2026, 9, 10) + timedelta(days=i)
        bars.append({
            "trade_date": day.isoformat(), "open": price, "high": price, "low": price,
            "close": price, "volume": 1000,
        })
        price *= 1 + daily_pct / 100
    return bars


class _Provider:
    name = "fake"

    def __init__(self, profiles: dict, missing_symbols: set[str] = frozenset()):
        self.profiles = profiles
        self.missing = missing_symbols
        self.ohlcv_requested: list[str] = []

    async def fetch_market_profile(self, ticker):
        if ticker not in self.profiles:
            raise ValueError("unknown ticker")
        return self.profiles[ticker]

    async def fetch_ohlcv(self, ticker, start, end):
        self.ohlcv_requested.append(ticker)
        if ticker in self.missing:
            raise ValueError("no data")
        return _bars(100.0, 1.0)


MCD_PROFILE = {
    "sector_key": "consumer-cyclical", "sector": "Consumer Cyclical",
    "industry_key": "restaurants", "industry": "Restaurants",
    "peers": ["MCD", "SBUX", "CMG", "YUM", "DRI"],
}


def _service(tmp_path, provider) -> DataService:
    return DataService(providers=[provider], cache=DiskCache(str(tmp_path / "cache.db")))


async def test_a_restaurant_is_read_against_its_own_sector_and_peers(tmp_path):
    provider = _Provider({"MCD": MCD_PROFILE})
    snapshot = await _service(tmp_path, provider).get_cross_market_snapshot("MCD", AS_OF)

    assert snapshot.sector_etf_symbol == "XLY"
    assert snapshot.peer_symbols == ["SBUX", "CMG", "YUM"]  # never itself
    assert "SMH" not in provider.ohlcv_requested and "AMD" not in provider.ohlcv_requested
    assert "MCD" not in provider.ohlcv_requested  # the seat's mandate: never its own prices
    for value in (
        snapshot.sector_etf_return_5d_pct, snapshot.peer_basket_return_5d_pct,
        snapshot.dollar_index_change_pct, snapshot.oil_change_pct,
    ):
        assert value is not None and value > 0
    assert {"UUP", "USO"} <= set(provider.ohlcv_requested)


async def test_chipmakers_keep_the_semiconductor_fund(tmp_path):
    profile = {"sector_key": "technology", "industry_key": "semiconductors", "peers": ["AMD"]}
    snapshot = await _service(tmp_path, _Provider({"NVDA": profile})).get_cross_market_snapshot("NVDA", AS_OF)
    assert snapshot.sector_etf_symbol == "SMH"


async def test_unknown_data_is_unavailable_not_zero(tmp_path):
    provider = _Provider({}, missing_symbols={"UUP", "USO"})
    snapshot = await _service(tmp_path, provider).get_cross_market_snapshot("ZZZZ", AS_OF)

    assert snapshot.sector_etf_symbol is None
    assert snapshot.sector_etf_return_5d_pct is None
    assert snapshot.peer_symbols == [] and snapshot.peer_basket_return_5d_pct is None
    assert snapshot.dollar_index_change_pct is None and snapshot.oil_change_pct is None
    assert snapshot.index_futures_change_pct is not None  # SPY itself still worked


async def test_the_seat_prompt_says_unavailable_instead_of_flat(tmp_path):
    provider = _Provider({}, missing_symbols={"UUP", "USO"})
    service = _service(tmp_path, provider)
    seat = CrossMarketSeat()
    ctx = await seat.gather(service, "ZZZZ", AS_OF)

    captured = {}

    class _Client:
        settings = type("S", (), {"seat_model": "m"})()

        async def get_seat_answer(self, **kwargs):
            captured.update(kwargs)
            return None

    await seat.deliberate(ctx, _Client())
    prompt = captured["user_prompt"]
    assert "Dollar (UUP): unavailable" in prompt
    assert "Oil (USO): unavailable" in prompt
    assert "+0.00%" not in prompt
    assert "SMH" not in prompt


@pytest.mark.parametrize("ticker", ["NVDA", "MCD"])
async def test_sample_data_still_works_offline(tmp_path, ticker):
    from council.data.providers.fixtures import FixtureProvider

    service = DataService(providers=[FixtureProvider()], cache=DiskCache(str(tmp_path / "c.db")))
    snapshot = await service.get_cross_market_snapshot(ticker, datetime(2026, 9, 18, 16, 0))
    assert snapshot.index_futures_change_pct is not None
    if ticker == "NVDA":
        assert snapshot.sector_etf_symbol == "SMH"
    else:
        assert snapshot.sector_etf_symbol is None
