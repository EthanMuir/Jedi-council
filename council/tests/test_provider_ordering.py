"""YFinance must be tried before Alpha Vantage -- it's free with no hard
daily cap, and now genuinely covers OHLCV/news/options (see
test_yfinance_provider.py), the same domains Alpha Vantage's free tier
caps at 25 requests/day. Trying Yahoo first means AV's scarce quota is
spent only on what nothing else can serve (macro) or when Yahoo itself is
down, not burned on things Yahoo already covers."""
from __future__ import annotations

from council.config import Settings
from council.data.providers.alpha_vantage import AlphaVantageProvider
from council.data.providers.fmp import FMPProvider
from council.data.providers.yfinance_provider import YFinanceProvider
from council.engine.orchestrator import build_data_service


def test_yfinance_tried_before_alpha_vantage_and_fmp():
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
    assert provider_types.index(YFinanceProvider) < provider_types.index(AlphaVantageProvider)
    assert provider_types.index(YFinanceProvider) < provider_types.index(FMPProvider)


def test_only_yfinance_when_no_keys_configured():
    settings = Settings(use_data_fixtures=False, alpha_vantage_api_key="", fmp_api_key="")
    service = build_data_service(settings)
    assert [type(p) for p in service._providers] == [YFinanceProvider]
