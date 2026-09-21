"""YFinance, SEC EDGAR, and FRED must all be tried before Alpha Vantage /
FMP. YFinance covers OHLCV/news/options, SEC EDGAR covers insider
transactions/SEC filings, FRED covers macro -- together that's every
domain Alpha Vantage's free tier caps at 25 requests/day and FMP's free
plan 403s on. YFinance and SEC EDGAR need no key at all; FRED needs a free
one but stays far under its own 120-req/min cap even so. Trying the
free/unlimited providers first means AV's and FMP's scarce quota/plan
coverage is spent only on what nothing else can serve, not burned on
things already covered -- and with FRED configured, AV's quota (macro was
its only remaining domain) is never touched at all."""
from __future__ import annotations

from council.config import Settings
from council.data.providers.alpha_vantage import AlphaVantageProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.fred import FREDProvider
from council.data.providers.sec_edgar import SECEdgarProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.engine.orchestrator import build_data_service


def test_free_providers_tried_before_alpha_vantage_and_fmp():
    settings = Settings(
        use_data_fixtures=False,
        alpha_vantage_api_key="test-av-key",
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    service = build_data_service(settings)
    provider_types = [type(p) for p in service._providers]

    assert provider_types[0] is YFinanceProvider
    for free_provider in (YFinanceProvider, SECEdgarProvider, FREDProvider):
        assert free_provider in provider_types
        for paid_provider in (AlphaVantageProvider, FMPProvider):
            assert provider_types.index(free_provider) < provider_types.index(paid_provider)


def test_only_yfinance_and_sec_edgar_when_no_keys_configured():
    settings = Settings(
        use_data_fixtures=False, alpha_vantage_api_key="", fmp_api_key="", fred_api_key=""
    )
    service = build_data_service(settings)
    assert [type(p) for p in service._providers] == [YFinanceProvider, SECEdgarProvider]


def test_fred_only_added_when_key_configured():
    without_key = build_data_service(Settings(use_data_fixtures=False, fred_api_key=""))
    assert FREDProvider not in [type(p) for p in without_key._providers]

    with_key = build_data_service(Settings(use_data_fixtures=False, fred_api_key="test-fred-key"))
    assert FREDProvider in [type(p) for p in with_key._providers]
