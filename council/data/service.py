"""The only door onto market data. Seats must never call a provider
directly -- DataService owns caching, rate limiting, provider fallback, and
(most importantly) the point-in-time guard.

Point-in-time discipline: `get(...)` must never return a record whose
publication/filing timestamp is later than `as_of`. This is the single most
common way these systems silently cheat -- an LLM's training data already
contains the future, so any record dated after `as_of` that leaks through is
a lookahead, not a signal.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Iterable, TypeVar

from pydantic import ValidationError

from council.data.cache import DiskCache
from council.data.providers.base import MarketDataProvider
from council.data.schemas import (
    AnalystEstimatesSnapshot,
    CongressTrade,
    CongressTradeFeed,
    CrossMarketSnapshot,
    EarningsTranscriptFeed,
    EarningsTranscriptRecord,
    FundamentalsSnapshot,
    InsiderTransaction,
    InsiderTransactionFeed,
    InstitutionalHoldingsSnapshot,
    MacroSnapshot,
    NewsFeed,
    NewsItem,
    OHLCVBar,
    OHLCVSeries,
    OptionChainSnapshot,
    OptionContract,
    SECFiling,
    SECFilingFeed,
)

T = TypeVar("T", bound=dict[str, Any])

_TTL_SECONDS = {
    "ohlcv": 3600,
    "news": 900,
    "options": 300,
    "fundamentals": 21600,
    "insider": 3600,
    "congress": 3600,
    "institutional": 21600,
    "macro": 21600,
    "estimates": 21600,
    "transcripts": 86400,
    "filings": 3600,
    "profile": 7 * 86400,  # a company's sector/industry peers rarely change
}

# The SPDR sector fund for each Yahoo sector key -- the "sector ETF" the
# Cross-Market Navigator compares a stock against.
SECTOR_ETFS = {
    "technology": "XLK",
    "financial-services": "XLF",
    "healthcare": "XLV",
    "consumer-cyclical": "XLY",
    "consumer-defensive": "XLP",
    "energy": "XLE",
    "industrials": "XLI",
    "basic-materials": "XLB",
    "utilities": "XLU",
    "real-estate": "XLRE",
    "communication-services": "XLC",
}
# Industries with a closer-fitting fund than their broad sector's.
INDUSTRY_ETFS = {"semiconductors": "SMH", "semiconductor-equipment-materials": "SMH"}
_DOLLAR_PROXY = "UUP"
_OIL_PROXY = "USO"


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def filter_point_in_time(records: Iterable[T], as_of: datetime, time_field: str) -> list[T]:
    """Drop any record whose `time_field` timestamp is later than `as_of`.

    Both naive and tz-aware timestamps are normalised to naive UTC for the
    comparison so callers don't have to think about it.
    """
    out: list[T] = []
    cutoff = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
    for record in records:
        raw = record[time_field]
        ts = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        ts = ts.replace(tzinfo=None) if ts.tzinfo else ts
        if ts <= cutoff:
            out.append(record)
    return out


def _latest_timestamp(records: list[dict[str, Any]], time_field: str) -> datetime | None:
    if not records:
        return None
    latest = max(records, key=lambda r: r[time_field])[time_field]
    return datetime.fromisoformat(latest) if isinstance(latest, str) else latest


def _parse_ts(raw: str | datetime) -> datetime:
    return datetime.fromisoformat(raw) if isinstance(raw, str) else raw


_PROVIDER_MAX_RETRIES = 2
_PROVIDER_BACKOFF_BASE_SECONDS = 0.5
_PROVIDER_BACKOFF_CAP_SECONDS = 4.0


def _provider_backoff_seconds(attempt: int) -> float:
    return min(_PROVIDER_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _PROVIDER_BACKOFF_CAP_SECONDS)


class DataService:
    def __init__(
        self,
        providers: list[MarketDataProvider],
        cache: DiskCache,
    ):
        if not providers:
            raise ValueError("DataService requires at least one provider")
        self._providers = providers
        self._cache = cache

    async def _fetch_with_fallback(
        self, method_name: str, cache_key: str, ttl: int, *args, cache_empty: bool = True
    ):
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        last_error: Exception | None = None
        # One entry per provider, not just the last one -- an aggregate
        # failure message that only shows the final (often least useful,
        # e.g. the Yahoo backstop's expected "doesn't cover this domain")
        # provider's error hides what actually went wrong with the earlier,
        # more likely-to-matter providers (an unset key, a blocked
        # free-tier endpoint, ...), making real problems hard to diagnose.
        failures: list[str] = []
        for provider in self._providers:
            # Not every provider implements every fetch method (that's the
            # whole point of a fallback chain across providers with
            # different coverage) -- move on immediately, no point retrying
            # a method that will never exist no matter how many times we ask.
            if not hasattr(provider, method_name):
                last_error = AttributeError(
                    f"{provider.__class__.__name__} has no {method_name}"
                )
                failures.append(f"{provider.name}: no {method_name}")
                continue
            method = getattr(provider, method_name)
            # A couple of quick retries on the current provider before
            # falling through to the next one -- most failures at this layer
            # are a single transient blip (a dropped connection, a momentary
            # 5xx), not the provider being genuinely down.
            for attempt in range(1, _PROVIDER_MAX_RETRIES + 2):
                try:
                    result = await method(*args)
                    # cache_empty=False (get_ohlcv's own choice, not every
                    # domain's) exists because an empty result there is
                    # essentially always a provider-side problem, never a
                    # true answer -- caching it for a full hour would let one
                    # bad response silently outlive whatever bug caused it.
                    if result or cache_empty:
                        self._cache.set(cache_key, result, ttl)
                    return result
                except Exception as exc:  # noqa: BLE001 -- graceful provider degradation
                    last_error = exc
                    if attempt <= _PROVIDER_MAX_RETRIES:
                        await asyncio.sleep(_provider_backoff_seconds(attempt))
            failures.append(f"{provider.name}: {last_error}")
        raise RuntimeError(
            f"All providers failed for {method_name}({args}): " + "; ".join(failures)
        ) from last_error

    async def _fetch_news_merged(
        self, ticker: str, start: datetime, end: datetime, cache_key: str, ttl: int
    ) -> list[dict[str, Any]]:
        """News is the one domain that doesn't stop at the first success.
        Every other _fetch_with_fallback call treats "a provider answered
        without raising" as done -- correct when providers are interchangeable
        covers of the same ground truth (an OHLCV bar is an OHLCV bar), wrong
        for news: yfinance's Ticker.news is whatever Yahoo currently has
        cached for a ticker, with no guarantee it covers everything published
        in the requested window, and it almost never raises -- so a thin,
        genuinely-stale-but-technically-successful result would permanently
        block Alpha Vantage's or FMP's real, properly time-windowed feeds from
        ever being tried (Task #75; a live run on a low-news-volume ticker
        showed exactly this: a handful of 3-5-day-old headlines and nothing
        newer, because nothing else was ever asked). So every provider that
        implements fetch_news is always queried and the results unioned,
        deduped by URL -- a missed headline is a worse failure mode here than
        one extra (cheap, cached-15-minutes) API call."""
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        failures: list[str] = []
        any_succeeded = False

        for provider in self._providers:
            if not hasattr(provider, "fetch_news"):
                failures.append(f"{provider.name}: no fetch_news")
                continue
            last_error: Exception | None = None
            for attempt in range(1, _PROVIDER_MAX_RETRIES + 2):
                try:
                    items = await provider.fetch_news(ticker, start, end)
                    any_succeeded = True
                    last_error = None
                    for item in items:
                        key = item.get("url") or f"{item.get('headline')}|{item.get('published_at')}"
                        if key not in seen:
                            seen.add(key)
                            merged.append(item)
                    break
                except Exception as exc:  # noqa: BLE001 -- graceful provider degradation
                    last_error = exc
                    if attempt <= _PROVIDER_MAX_RETRIES:
                        await asyncio.sleep(_provider_backoff_seconds(attempt))
            if last_error is not None:
                failures.append(f"{provider.name}: {last_error}")

        if not any_succeeded:
            raise RuntimeError(
                f"All providers failed for fetch_news(({ticker!r}, {start!r}, {end!r})): "
                + "; ".join(failures)
            )

        merged.sort(key=lambda i: i["published_at"], reverse=True)
        self._cache.set(cache_key, merged, ttl)
        return merged

    async def get_ohlcv(
        self, ticker: str, as_of: datetime, lookback_days: int = 180
    ) -> OHLCVSeries:
        start = (as_of - timedelta(days=lookback_days)).date()
        end = as_of.date()
        cache_key = f"ohlcv:{ticker}:{start}:{end}"
        raw = await self._fetch_with_fallback(
            "fetch_ohlcv", cache_key, _TTL_SECONDS["ohlcv"], ticker, start, end, cache_empty=False
        )
        filtered = filter_point_in_time(raw, as_of, "trade_date")
        latest = _latest_timestamp(filtered, "trade_date")
        staleness = (as_of - latest).total_seconds() if latest else None
        source = self._providers[0].name
        return OHLCVSeries(
            ticker=ticker,
            bars=[OHLCVBar(**b) for b in filtered],
            as_of=as_of,
            staleness_seconds=staleness,
            source=source,
        )

    async def get_news(
        self, ticker: str, as_of: datetime, lookback_days: int = 14
    ) -> NewsFeed:
        start = as_of - timedelta(days=lookback_days)
        cache_key = f"news:{ticker}:{start.date()}:{as_of.date()}"
        raw = await self._fetch_news_merged(ticker, start, as_of, cache_key, _TTL_SECONDS["news"])
        filtered = filter_point_in_time(raw, as_of, "published_at")
        # A provider's start/end args are a request, not a guarantee --
        # yfinance's fetch_news ignores them entirely and returns whatever
        # Yahoo currently has cached, which is not necessarily inside the
        # requested lookback window. filter_point_in_time only enforces the
        # upper bound (no lookahead); enforce the lower bound here too, so
        # a provider's non-compliance can't hand a seat news older than it
        # asked for without at least being dropped centrally.
        cutoff = _naive(start)
        filtered = [item for item in filtered if _naive(_parse_ts(item["published_at"])) >= cutoff]
        latest = _latest_timestamp(filtered, "published_at")
        staleness = (as_of - latest).total_seconds() if latest else None
        sources = [p.name for p in self._providers if hasattr(p, "fetch_news")]
        return NewsFeed(
            ticker=ticker,
            items=[NewsItem(**n) for n in filtered],
            as_of=as_of,
            staleness_seconds=staleness,
            source=", ".join(sources) if sources else "none",
        )

    async def get_option_chain(self, ticker: str, as_of: datetime) -> OptionChainSnapshot:
        cache_key = f"options:{ticker}:{as_of.isoformat()}"
        raw = await self._fetch_with_fallback(
            "fetch_option_chain", cache_key, _TTL_SECONDS["options"], ticker
        )
        source = self._providers[0].name
        # Options snapshots are point-in-time by construction (a live quote),
        # so staleness is measured against the snapshot's own as_of if the
        # provider supplied one, else assumed fresh at fetch time.
        staleness = raw.get("staleness_seconds", 0.0)
        contracts = []
        for c in raw["contracts"]:
            # A single malformed contract (a provider sending a junk/sentinel
            # expiry, seen in practice: "2099-99-99") must not invalidate an
            # otherwise-good chain -- skip that one contract, keep the rest.
            try:
                contracts.append(OptionContract(**c))
            except ValidationError:
                continue
        return OptionChainSnapshot(
            ticker=ticker,
            underlying_price=raw["underlying_price"],
            contracts=contracts,
            put_call_ratio=raw.get("put_call_ratio"),
            as_of=as_of,
            staleness_seconds=staleness,
            source=source,
        )

    async def get_fundamentals(self, ticker: str, as_of: datetime) -> FundamentalsSnapshot:
        """Current-view snapshot. NOTE: unlike the dated feeds below, this
        does not yet enforce a filing-lag point-in-time guard -- rigorous
        historical PIT for fundamentals is deferred to Phase 4, when the
        Crypt actually backtests against historical `as_of` dates."""
        cache_key = f"fundamentals:{ticker}"
        raw = await self._fetch_with_fallback(
            "fetch_fundamentals", cache_key, _TTL_SECONDS["fundamentals"], ticker
        )
        return FundamentalsSnapshot(
            ticker=ticker, as_of=as_of, staleness_seconds=0.0, source=self._providers[0].name, **raw
        )

    async def get_insider_transactions(
        self, ticker: str, as_of: datetime, lookback_days: int = 180
    ) -> InsiderTransactionFeed:
        start = (as_of - timedelta(days=lookback_days)).date()
        end = as_of.date()
        cache_key = f"insider:{ticker}:{start}:{end}"
        raw = await self._fetch_with_fallback(
            "fetch_insider_transactions", cache_key, _TTL_SECONDS["insider"], ticker, start, end
        )
        filtered = filter_point_in_time(raw, as_of, "filed_at")
        latest = _latest_timestamp(filtered, "filed_at")
        staleness = (as_of - latest).total_seconds() if latest else None
        return InsiderTransactionFeed(
            ticker=ticker,
            transactions=[InsiderTransaction(**t) for t in filtered],
            as_of=as_of,
            staleness_seconds=staleness,
            source=self._providers[0].name,
        )

    async def get_congress_trades(
        self, ticker: str, as_of: datetime, lookback_days: int = 365
    ) -> CongressTradeFeed:
        start = (as_of - timedelta(days=lookback_days)).date()
        end = as_of.date()
        cache_key = f"congress:{ticker}:{start}:{end}"
        raw = await self._fetch_with_fallback(
            "fetch_congress_data", cache_key, _TTL_SECONDS["congress"], ticker, start, end
        )
        filtered = filter_point_in_time(raw["trades"], as_of, "filed_at")
        latest = _latest_timestamp(filtered, "filed_at")
        staleness = (as_of - latest).total_seconds() if latest else None
        return CongressTradeFeed(
            ticker=ticker,
            trades=[CongressTrade(**t) for t in filtered],
            pending_legislation=raw.get("pending_legislation", []),
            as_of=as_of,
            staleness_seconds=staleness,
            source=self._providers[0].name,
        )

    async def get_institutional_holdings(
        self, ticker: str, as_of: datetime
    ) -> InstitutionalHoldingsSnapshot:
        """13F filings lag their quarter by up to 45 days -- `filed_at` is
        the point-in-time guard here, not `quarter_end`. A 13F filed after
        `as_of` is treated as not-yet-available, not silently used."""
        cache_key = f"institutional:{ticker}"
        raw = await self._fetch_with_fallback(
            "fetch_institutional_holdings", cache_key, _TTL_SECONDS["institutional"], ticker
        )
        filed_at = raw["filed_at"]
        filed_at = datetime.fromisoformat(filed_at) if isinstance(filed_at, str) else filed_at
        if _naive(filed_at) > _naive(as_of):
            raw = {
                **raw,
                "total_institutional_shares": 0,
                "pct_of_float_held": 0.0,
                "qoq_share_change_pct": 0.0,
                "top_holders_net_buyers": 0,
                "top_holders_net_sellers": 0,
                "etf_inclusion_notes": "no 13F filed as of this date",
                "short_interest_shares": 0,
                "days_to_cover": 0.0,
            }
            staleness = None
        else:
            staleness = (_naive(as_of) - _naive(filed_at)).total_seconds()
        return InstitutionalHoldingsSnapshot(
            ticker=ticker, as_of=as_of, staleness_seconds=staleness, source=self._providers[0].name, **raw
        )

    async def get_macro_snapshot(self, ticker: str, as_of: datetime) -> MacroSnapshot:
        cache_key = "macro:latest"
        raw = await self._fetch_with_fallback("fetch_macro", cache_key, _TTL_SECONDS["macro"])
        beta = await self._estimate_beta(ticker, "SPY", as_of)
        # Computed here, not required of every fetch_macro implementation --
        # it's a pure derivative of two fields every provider already
        # returns, and requiring each one to compute it independently is
        # exactly how a provider that forgot to (Alpha Vantage's own
        # fetch_macro never did) would silently crash MacroSnapshot's
        # required field the first time it actually succeeded. Never caught
        # in practice because AV's daily cap meant fetch_macro had never
        # once succeeded against live data before this was noticed.
        raw = {k: v for k, v in raw.items() if k != "curve_10y_minus_2y"}
        curve = round(raw.get("treasury_10y", 0.0) - raw.get("treasury_2y", 0.0), 3)
        return MacroSnapshot(
            as_of=as_of,
            staleness_seconds=0.0,
            source=self._providers[0].name,
            ticker_beta_to_spx=beta,
            curve_10y_minus_2y=curve,
            **raw,
        )

    async def _estimate_beta(self, ticker: str, benchmark: str, as_of: datetime) -> float | None:
        """60-day daily-return beta of `ticker` to `benchmark`, computed from
        OHLCV this service already knows how to fetch -- no separate
        provider endpoint needed."""
        try:
            ticker_series, bench_series = (
                await self.get_ohlcv(ticker, as_of, lookback_days=90),
                await self.get_ohlcv(benchmark, as_of, lookback_days=90),
            )
        except Exception:  # noqa: BLE001 -- beta is a nice-to-have, never blocks the seat
            return None
        t_closes = [b.close for b in ticker_series.bars][-60:]
        b_closes = [b.close for b in bench_series.bars][-60:]
        n = min(len(t_closes), len(b_closes))
        if n < 10:
            return None
        t_returns = [(t_closes[i] / t_closes[i - 1]) - 1 for i in range(-n + 1, 0)]
        b_returns = [(b_closes[i] / b_closes[i - 1]) - 1 for i in range(-n + 1, 0)]
        mean_t = sum(t_returns) / len(t_returns)
        mean_b = sum(b_returns) / len(b_returns)
        covariance = sum((t - mean_t) * (b - mean_b) for t, b in zip(t_returns, b_returns))
        variance_b = sum((b - mean_b) ** 2 for b in b_returns)
        if variance_b == 0:
            return None
        return round(covariance / variance_b, 3)

    async def get_market_profile(self, ticker: str) -> dict[str, Any]:
        """The company's sector/industry and a few industry peers -- reference
        metadata, not price data. {} when no provider knows the ticker."""
        try:
            return await self._fetch_with_fallback(
                "fetch_market_profile", f"profile:{ticker}", _TTL_SECONDS["profile"], ticker
            )
        except RuntimeError:
            return {}

    async def get_cross_market_snapshot(
        self,
        ticker: str,
        as_of: datetime,
        index_proxy: str = "SPY",
        overseas_proxy: str = "EWJ",
    ) -> CrossMarketSnapshot:
        """Never fetches `ticker`'s own price series -- every field here
        comes from a different instrument, per the Cross-Market Navigator's
        mandate. Which sector fund and peers to compare against comes from
        the company's own sector/industry, so a restaurant chain is read
        against consumer-discretionary stocks and restaurant peers, not
        against chipmakers."""

        async def _return_5d(symbol: str) -> float | None:
            try:
                series = await self.get_ohlcv(symbol, as_of, lookback_days=15)
            except RuntimeError:
                return None
            closes = [b.close for b in series.bars]
            if len(closes) < 6:
                return None
            return round(((closes[-1] / closes[-6]) - 1) * 100, 2)

        profile = await self.get_market_profile(ticker)
        sector_etf = INDUSTRY_ETFS.get(profile.get("industry_key") or "") or SECTOR_ETFS.get(
            profile.get("sector_key") or ""
        )
        peers = [p for p in profile.get("peers", []) if p and p.upper() != ticker.upper()][:3]

        sector_return = await _return_5d(sector_etf) if sector_etf else None
        peer_returns = [r for r in [await _return_5d(p) for p in peers] if r is not None]
        peer_return = round(sum(peer_returns) / len(peer_returns), 2) if peer_returns else None

        # NOTE: deliberately does NOT call get_macro_snapshot(ticker, ...) here --
        # that computes ticker_beta_to_spx, which fetches the ticker's own OHLCV
        # internally (see _estimate_beta) and would violate this seat's "never the
        # ticker's own price series" mandate even though the beta value itself
        # would never reach CrossMarketSnapshot. Dollar and oil moves come from
        # their ETF proxies' own 5-day returns instead.
        return CrossMarketSnapshot(
            sector_etf_symbol=sector_etf,
            sector_etf_return_5d_pct=sector_return,
            peer_symbols=peers,
            peer_basket_return_5d_pct=peer_return,
            index_futures_change_pct=await _return_5d(index_proxy),
            dollar_index_change_pct=await _return_5d(_DOLLAR_PROXY),
            oil_change_pct=await _return_5d(_OIL_PROXY),
            overseas_session_return_pct=await _return_5d(overseas_proxy),
            as_of=as_of,
            staleness_seconds=0.0,
            source=self._providers[0].name,
        )

    async def get_analyst_estimates(self, ticker: str, as_of: datetime) -> AnalystEstimatesSnapshot:
        cache_key = f"estimates:{ticker}"
        raw = await self._fetch_with_fallback(
            "fetch_analyst_estimates", cache_key, _TTL_SECONDS["estimates"], ticker
        )
        return AnalystEstimatesSnapshot(
            ticker=ticker, as_of=as_of, staleness_seconds=0.0, source=self._providers[0].name, **raw
        )

    async def get_earnings_transcripts(
        self, ticker: str, as_of: datetime, limit: int = 4
    ) -> EarningsTranscriptFeed:
        cache_key = f"transcripts:{ticker}:{limit}"
        raw = await self._fetch_with_fallback(
            "fetch_earnings_transcripts", cache_key, _TTL_SECONDS["transcripts"], ticker, limit
        )
        filtered = filter_point_in_time(raw, as_of, "call_date")
        latest = _latest_timestamp(filtered, "call_date")
        staleness = (as_of - latest).total_seconds() if latest else None
        return EarningsTranscriptFeed(
            ticker=ticker,
            calls=[EarningsTranscriptRecord(**c) for c in filtered],
            as_of=as_of,
            staleness_seconds=staleness,
            source=self._providers[0].name,
        )

    async def get_sec_filings(
        self, ticker: str, as_of: datetime, lookback_days: int = 730
    ) -> SECFilingFeed:
        start = (as_of - timedelta(days=lookback_days)).date()
        end = as_of.date()
        cache_key = f"filings:{ticker}:{start}:{end}"
        raw = await self._fetch_with_fallback(
            "fetch_sec_filings", cache_key, _TTL_SECONDS["filings"], ticker, start, end
        )
        filtered = filter_point_in_time(raw, as_of, "filed_at")
        latest = _latest_timestamp(filtered, "filed_at")
        staleness = (as_of - latest).total_seconds() if latest else None
        return SECFilingFeed(
            ticker=ticker,
            filings=[SECFiling(**f) for f in filtered],
            as_of=as_of,
            staleness_seconds=staleness,
            source=self._providers[0].name,
        )
