"""get_macro_snapshot's curve_10y_minus_2y field was never actually
computed by any provider -- AlphaVantageProvider.fetch_macro's real dict
only ever returned 7 of MacroSnapshot's 8 required macro fields, which
would have crashed the moment fetch_macro genuinely succeeded against live
data. Never caught in practice because Alpha Vantage's 25-requests/day cap
meant a real macro_sage gather() (7 AV calls) had essentially never
succeeded end to end -- only FixtureProvider's hardcoded fixture value
(which happens to already be internally consistent: 3.92 - 3.55 = 0.37)
papered over it. Computed centrally in DataService now, so no provider
needs to supply it at all."""
from __future__ import annotations

from datetime import datetime

import pytest

from council.data.cache import DiskCache
from council.data.service import DataService


class _MacroProviderMissingCurveField:
    """Shaped exactly like AlphaVantageProvider.fetch_macro's real return
    value -- no curve_10y_minus_2y key at all."""

    name = "fake_macro"

    async def fetch_macro(self) -> dict:
        return {
            "treasury_10y": 4.25,
            "treasury_2y": 3.80,
            "fed_funds_rate": 4.5,
            "cpi_yoy": 2.9,
            "unemployment_rate": 4.0,
            "dollar_index": 103.2,
            "wti_crude": 71.5,
        }

    async def fetch_ohlcv(self, ticker, start, end):
        return []


@pytest.mark.asyncio
async def test_curve_is_computed_when_provider_omits_it(tmp_path):
    service = DataService(
        providers=[_MacroProviderMissingCurveField()], cache=DiskCache(str(tmp_path / "cache.db"))
    )
    snapshot = await service.get_macro_snapshot("NVDA", datetime(2026, 9, 21))
    assert snapshot.curve_10y_minus_2y == pytest.approx(0.45)
    assert snapshot.treasury_10y == 4.25
    assert snapshot.treasury_2y == 3.80


class _MacroProviderWithStaleCurveField:
    """A provider that DOES send a curve_10y_minus_2y key -- must be
    overridden by the freshly computed value, not trusted verbatim, so a
    stale/inconsistent value can never silently disagree with the two
    fields it's derived from."""

    name = "fake_macro_stale"

    async def fetch_macro(self) -> dict:
        return {
            "treasury_10y": 4.25,
            "treasury_2y": 3.80,
            "curve_10y_minus_2y": 999.0,
            "fed_funds_rate": 4.5,
            "cpi_yoy": 2.9,
            "unemployment_rate": 4.0,
            "dollar_index": 103.2,
            "wti_crude": 71.5,
        }

    async def fetch_ohlcv(self, ticker, start, end):
        return []


@pytest.mark.asyncio
async def test_curve_override_ignores_a_stale_provider_supplied_value(tmp_path):
    service = DataService(
        providers=[_MacroProviderWithStaleCurveField()], cache=DiskCache(str(tmp_path / "cache.db"))
    )
    snapshot = await service.get_macro_snapshot("NVDA", datetime(2026, 9, 21))
    assert snapshot.curve_10y_minus_2y == pytest.approx(0.45)
