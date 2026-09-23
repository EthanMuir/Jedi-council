"""YFinance and SEC EDGAR must both be tried before FRED / FMP. YFinance
covers OHLCV/news/options, SEC EDGAR covers insider transactions/SEC
filings -- together that's every domain FMP's free plan 403s on. Both need
no key at all; FRED needs a free one but stays far under its own
120-req/min cap even so. Trying the free/unlimited providers first means
FMP's scarce plan coverage is spent only on what nothing else can serve.

Alpha Vantage (Task #77) is never added to the live chain regardless of
alpha_vantage_api_key -- its free tier's 25-requests/day cap made it
structurally unusable and its options endpoints require a paid plan
regardless of quota, so it was pulled out entirely rather than kept as a
fallback nothing ever reaches."""
from __future__ import annotations

from council.config import Settings
from council.data.providers.fmp import FMPProvider
from council.data.providers.fred import FREDProvider
from council.data.providers.sec_edgar import SECEdgarProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.engine.orchestrator import build_data_service


def test_free_providers_tried_before_fred_and_fmp():
    settings = Settings(
        use_data_fixtures=False,
        fmp_api_key="test-fmp-key",
        fred_api_key="test-fred-key",
    )
    service = build_data_service(settings)
    provider_types = [type(p) for p in service._providers]

    assert provider_types[0] is YFinanceProvider
    for free_provider in (YFinanceProvider, SECEdgarProvider):
        assert free_provider in provider_types
        for paid_provider in (FREDProvider, FMPProvider):
            assert provider_types.index(free_provider) < provider_types.index(paid_provider)


def test_only_yfinance_and_sec_edgar_when_no_keys_configured():
    settings = Settings(use_data_fixtures=False, fmp_api_key="", fred_api_key="")
    service = build_data_service(settings)
    assert [type(p) for p in service._providers] == [YFinanceProvider, SECEdgarProvider]


def test_fred_only_added_when_key_configured():
    without_key = build_data_service(Settings(use_data_fixtures=False, fred_api_key=""))
    assert FREDProvider not in [type(p) for p in without_key._providers]

    with_key = build_data_service(Settings(use_data_fixtures=False, fred_api_key="test-fred-key"))
    assert FREDProvider in [type(p) for p in with_key._providers]


def test_alpha_vantage_is_never_added_even_with_a_key_configured():
    settings = Settings(use_data_fixtures=False, alpha_vantage_api_key="test-av-key")
    service = build_data_service(settings)
    provider_names = [type(p).__name__ for p in service._providers]
    assert "AlphaVantageProvider" not in provider_names
