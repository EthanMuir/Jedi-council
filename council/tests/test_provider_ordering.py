"""YFinance and SEC EDGAR must both be tried before Alpha Vantage / FMP --
both are free with no hard daily cap. YFinance covers OHLCV/news/options,
the same domains Alpha Vantage's free tier caps at 25 requests/day. SEC
EDGAR covers insider transactions / SEC filings and, unlike FMP's free
tier, isn't plan-gated away from those two domains -- no point spending an
HTTP round trip on FMP's guaranteed 403 first. Trying the free/unlimited
providers first means AV's and FMP's scarce quota/plan coverage is spent
only on what nothing else can serve, not burned on things already covered."""
from __future__ import annotations

from council.config import Settings
from council.data.providers.alpha_vantage import AlphaVantageProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.sec_edgar import SECEdgarProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.engine.orchestrator import build_data_service


def test_yfinance_and_sec_edgar_tried_before_alpha_vantage_and_fmp():
    settings = Settings(
        use_data_fixtures=False,
        alpha_vantage_api_key="test-av-key",
        fmp_api_key="test-fmp-key",
    )
    service = build_data_service(settings)
    provider_types = [type(p) for p in service._providers]

    assert provider_types[0] is YFinanceProvider
    assert AlphaVantageProvider in provider_types
    assert FMPProvider in provider_types
    assert SECEdgarProvider in provider_types
    assert provider_types.index(YFinanceProvider) < provider_types.index(AlphaVantageProvider)
    assert provider_types.index(YFinanceProvider) < provider_types.index(FMPProvider)
    assert provider_types.index(SECEdgarProvider) < provider_types.index(AlphaVantageProvider)
    assert provider_types.index(SECEdgarProvider) < provider_types.index(FMPProvider)


def test_only_yfinance_and_sec_edgar_when_no_keys_configured():
    settings = Settings(use_data_fixtures=False, alpha_vantage_api_key="", fmp_api_key="")
    service = build_data_service(settings)
    assert [type(p) for p in service._providers] == [YFinanceProvider, SECEdgarProvider]
