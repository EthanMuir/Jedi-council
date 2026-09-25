"""yfinance fallback -- OHLCV, news, options, fundamentals, institutional
holdings, analyst estimates, and analyst price targets & ratings. Free, no key, no official rate limit, per
spec: "free, unreliable, use as backstop only".

fetch_news used to be a permanent `return []` stub -- harmless-looking, but
because YFinanceProvider is tried first in the provider chain (see
orchestrator.build_data_service) and DataService._fetch_with_fallback
treats *any* non-exception result as success, that empty list silently
"succeeded" and permanently blocked Alpha Vantage's real NEWS_SENTIMENT
feed (the only other implementation of fetch_news in this chain -- FMP
doesn't have one) from ever being tried. Confirmed live: catalyst_seer
reporting "No news items in the lookback window" for a ticker with real
recent news. Wired up for real now, same spirit as fetch_option_chain's
own former-stub history (see test_yfinance_provider.py's docstring).

The fundamentals/holdings/estimates/ratings methods lean on yfinance's
`Ticker.info` dict and a handful of purpose-built DataFrames
(`institutional_holders`, `major_holders`, `analyst_price_targets`,
`earnings_estimate`, `revenue_estimate`, `eps_trend`, `earnings_history`,
`recommendations_summary`, `upgrades_downgrades`).
None of this is a documented, stable Yahoo API -- yfinance scrapes it,
field availability varies by ticker and by yfinance version, and this
couldn't be verified against live Yahoo data from the sandbox that built it
(network policy blocks it same as it blocks most external hosts). Every
extraction below is wrapped defensively and falls back to the same
"unavailable" defaults for fields it can't fill -- a missing number here should degrade the seat's data_quality,
not crash it."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any


def _safe_float(value: Any) -> float | None:
    """None and NaN (pandas' way of saying "no bid/ask/OI for this
    contract") both mean "we don't have this number" -- normalise both to
    None rather than letting NaN silently pass Pydantic's float validation."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN never equals itself


def _safe_int(value: Any) -> int | None:
    f = _safe_float(value)
    return None if f is None else int(f)


def _parse_news_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _parse_news_item(raw: dict) -> dict[str, Any] | None:
    """yfinance's news shape has changed across versions -- newer releases
    nest the real fields under "content" (title/summary/pubDate/provider/
    canonicalUrl), older ones are flat (title/publisher/link/
    providerPublishTime as a unix timestamp). Handle both; skip an item
    that matches neither rather than guess at a headline/url/timestamp."""
    content = raw.get("content")
    if isinstance(content, dict):
        title = content.get("title")
        summary = content.get("summary") or content.get("description") or ""
        provider = content.get("provider")
        source = provider.get("displayName") if isinstance(provider, dict) else None
        url_obj = content.get("canonicalUrl") or content.get("clickThroughUrl")
        url = url_obj.get("url") if isinstance(url_obj, dict) else None
        published_at = _parse_news_timestamp(content.get("pubDate") or content.get("displayTime"))
    else:
        title = raw.get("title")
        summary = raw.get("summary") or ""
        source = raw.get("publisher")
        url = raw.get("link")
        published_at = _parse_news_timestamp(raw.get("providerPublishTime"))

    if not title or not url or published_at is None:
        return None

    return {
        "headline": title,
        "summary": summary,
        "source": source or "yahoo",
        "url": url,
        "published_at": published_at.isoformat(),
        "sentiment_score": None,
        "sentiment_label": None,
    }


class YFinanceProvider:
    name = "yfinance"

    async def fetch_ohlcv(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_sync, ticker, start, end)

    def _fetch_sync(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        import yfinance as yf

        hist = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
        bars = []
        for idx, row in hist.iterrows():
            bars.append(
                {
                    "trade_date": idx.date().isoformat(),
                    "open": float(row["Open"]),
                    "high": float(row["High"]),
                    "low": float(row["Low"]),
                    "close": float(row["Close"]),
                    "adjusted_close": float(row["Close"]),
                    "volume": int(row["Volume"]),
                }
            )
        return bars

    async def fetch_news(self, ticker: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._fetch_news_sync, ticker)

    def _fetch_news_sync(self, ticker: str) -> list[dict[str, Any]]:
        import yfinance as yf

        raw_items = yf.Ticker(ticker).news or []
        items = []
        for raw in raw_items:
            parsed = _parse_news_item(raw)
            if parsed is not None:
                items.append(parsed)
        return items

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_option_chain_sync, ticker)

    def _resolve_underlying_price(self, ticker_obj, chain) -> float:
        """chain.underlying.get("regularMarketPrice") alone silently falls
        back to 0.0 on any parsing hiccup -- and oracle_options picks its
        "ATM" contract as whichever strike is *closest* to this number
        (`min(contracts, key=lambda c: abs(c.strike - underlying_price))`).
        A price of 0.0 means that picks the single LOWEST-strike contract
        in the whole chain instead -- typically a deep, near-worthless,
        zero-volume one, which is exactly the shape of "ATM IV wildly
        inconsistent with the rest of the chain" seen live on every ticker
        tested so far (0.78% specifically, on two unrelated tickers -- too
        exact to be two independent bad quotes, much more likely the same
        systematic wrong-contract selection every time). Tries progressively
        more reliable fallbacks before giving up."""
        try:
            price = float(chain.underlying.get("regularMarketPrice", 0.0) or 0.0)
            if price > 0:
                return price
        except (AttributeError, TypeError, ValueError):
            pass

        try:
            price = float(ticker_obj.fast_info.get("lastPrice", 0.0) or 0.0)
            if price > 0:
                return price
        except (AttributeError, TypeError, ValueError):
            pass

        try:
            info = ticker_obj.info or {}
            price = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0)
            if price > 0:
                return price
        except (AttributeError, TypeError, ValueError):
            pass

        return 0.0

    def _fetch_option_chain_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}

        expiry = expiries[0]
        chain = t.option_chain(expiry)
        underlying_price = self._resolve_underlying_price(t, chain)

        contracts = []
        total_call_volume = 0
        total_put_volume = 0
        for option_type, df in (("call", chain.calls), ("put", chain.puts)):
            for _, row in df.iterrows():
                volume = int(_safe_float(row.get("volume")) or 0)
                if option_type == "call":
                    total_call_volume += volume
                else:
                    total_put_volume += volume
                contracts.append(
                    {
                        "strike": float(row["strike"]),
                        "expiry": expiry,
                        "option_type": option_type,
                        "bid": _safe_float(row.get("bid")),
                        "ask": _safe_float(row.get("ask")),
                        "last": _safe_float(row.get("lastPrice")),
                        "volume": volume,
                        "open_interest": int(_safe_float(row.get("openInterest")) or 0) or None,
                        "implied_volatility": _safe_float(row.get("impliedVolatility")),
                    }
                )

        put_call_ratio = (total_put_volume / total_call_volume) if total_call_volume else None
        return {
            "underlying_price": underlying_price,
            "contracts": contracts,
            "put_call_ratio": put_call_ratio,
        }

    # ---- fundamentals ----------------------------------------------------

    async def fetch_market_profile(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_market_profile_sync, ticker)

    def _fetch_market_profile_sync(self, ticker: str) -> dict[str, Any]:
        """Sector/industry keys from Ticker.info, plus the industry's top
        companies as peers. Reference metadata only -- no price data."""
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        sector_key, industry_key = info.get("sectorKey"), info.get("industryKey")
        if not sector_key and not industry_key:
            raise ValueError(f"Yahoo has no sector/industry for {ticker}")
        peers: list[str] = []
        if industry_key:
            try:
                top = yf.Industry(industry_key).top_companies
                if top is not None and not top.empty:
                    peers = [str(s) for s in top.index if str(s).upper() != ticker.upper()][:5]
            except Exception:  # noqa: BLE001 -- peers are optional; the sector fund still works
                peers = []
        return {
            "name": info.get("longName") or info.get("shortName"),
            "sector_key": sector_key,
            "sector": info.get("sector"),
            "industry_key": industry_key,
            "industry": info.get("industry"),
            "peers": peers,
        }

    async def fetch_fundamentals(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_fundamentals_sync, ticker)

    def _fetch_fundamentals_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}

        fiscal_period = "unknown"
        try:
            most_recent_income = yf.Ticker(ticker).quarterly_income_stmt
            if most_recent_income is not None and not most_recent_income.empty:
                fiscal_period = f"quarter ending {most_recent_income.columns[0].date().isoformat()}"
        except Exception:  # noqa: BLE001 -- a label is a nice-to-have, never blocks the seat
            pass

        return {
            "fiscal_period": fiscal_period,
            "revenue": _safe_float(info.get("totalRevenue")) or 0.0,
            "revenue_growth_yoy": _safe_float(info.get("revenueGrowth")) or 0.0,
            "gross_margin": _safe_float(info.get("grossMargins")) or 0.0,
            "operating_margin": _safe_float(info.get("operatingMargins")) or 0.0,
            "net_margin": _safe_float(info.get("profitMargins")) or 0.0,
            "total_debt": _safe_float(info.get("totalDebt")) or 0.0,
            "cash_and_equivalents": _safe_float(info.get("totalCash")) or 0.0,
            "free_cash_flow": _safe_float(info.get("freeCashflow")) or 0.0,
            "pe_ratio": _safe_float(info.get("trailingPE")),
            "ev_to_ebitda": _safe_float(info.get("enterpriseToEbitda")),
            "price_to_sales": _safe_float(info.get("priceToSalesTrailing12Months")),
            # Yahoo doesn't publish a sector-median comparator; left null
            # rather than approximated.
            "sector_median_pe": None,
            "sector_median_ev_ebitda": None,
        }

    # ---- institutional holdings -------------------------------------------

    async def fetch_institutional_holdings(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_institutional_holdings_sync, ticker)

    def _fetch_institutional_holdings_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        info = t.info or {}

        quarter_end = date.today().isoformat()
        total_shares = 0
        try:
            holders = t.institutional_holders
            if holders is not None and not holders.empty:
                # Top holders only, not a true market-wide aggregate -- the
                # closest free approximation available, same spirit as the
                # defaults below for fields Yahoo doesn't expose at all.
                total_shares = int(holders["Shares"].sum())
                if "Date Reported" in holders.columns:
                    most_recent = holders["Date Reported"].max()
                    if hasattr(most_recent, "date"):
                        quarter_end = most_recent.date().isoformat()
        except Exception:  # noqa: BLE001
            pass

        pct_of_float = 0.0
        try:
            major = t.major_holders
            if major is not None and not major.empty:
                # yfinance's major_holders shape has varied across versions
                # (a 2-column frame vs. a labeled Series) -- search whatever
                # came back for the institutional-ownership row rather than
                # assume a fixed position.
                flat = major.reset_index().astype(str)
                for _, row in flat.iterrows():
                    joined = " ".join(row.values).lower()
                    if "institution" in joined:
                        for cell in row.values:
                            parsed = _safe_float(str(cell).replace("%", ""))
                            if parsed is not None:
                                pct_of_float = parsed
                                break
                        break
        except Exception:  # noqa: BLE001
            pass

        return {
            "quarter_end": quarter_end,
            "filed_at": quarter_end,
            "total_institutional_shares": total_shares,
            "pct_of_float_held": pct_of_float,
            # Not available from Yahoo's free surface -- defaulted.
            "qoq_share_change_pct": 0.0,
            "top_holders_net_buyers": 0,
            "top_holders_net_sellers": 0,
            "etf_inclusion_notes": "",
            "short_interest_shares": _safe_int(info.get("sharesShort")) or 0,
            "days_to_cover": _safe_float(info.get("shortRatio")) or 0.0,
        }

    # ---- analyst price targets & ratings -------------------------------------

    async def fetch_analyst_ratings(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_analyst_ratings_sync, ticker)

    def _fetch_analyst_ratings_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)

        targets: dict[str, Any] = {}
        try:
            targets = t.analyst_price_targets or {}
        except Exception:  # noqa: BLE001
            pass

        analyst_count = None
        try:
            analyst_count = _safe_int((t.info or {}).get("numberOfAnalystOpinions"))
        except Exception:  # noqa: BLE001
            pass

        # Yahoo's recommendation trend: one row per month, "0m" (this
        # month) back to "-3m".
        splits: dict[str, dict[str, int]] = {}
        try:
            trend = t.recommendations_summary
            if trend is not None and not trend.empty:
                for _, row in trend.iterrows():
                    splits[str(row.get("period"))] = {
                        key: _safe_int(row.get(col)) or 0
                        for key, col in (
                            ("strong_buy", "strongBuy"), ("buy", "buy"), ("hold", "hold"),
                            ("sell", "sell"), ("strong_sell", "strongSell"),
                        )
                    }
        except Exception:  # noqa: BLE001
            pass
        now = splits.get("0m", {})
        prior = splits.get("-3m")

        changes: list[dict[str, Any]] = []
        try:
            history = t.upgrades_downgrades
            if history is not None and not history.empty:
                for changed_at, row in history.sort_index(ascending=False).head(40).iterrows():
                    changes.append({
                        "changed_at": changed_at.to_pydatetime().isoformat(),
                        "firm": str(row.get("Firm") or ""),
                        "action": str(row.get("Action") or ""),
                        "from_grade": str(row.get("FromGrade") or ""),
                        "to_grade": str(row.get("ToGrade") or ""),
                        "price_target": _safe_float(row.get("currentPriceTarget")) or None,
                        "prior_price_target": _safe_float(row.get("priorPriceTarget")) or None,
                    })
        except Exception:  # noqa: BLE001
            pass

        if not targets.get("mean") and not now and not changes:
            raise ValueError(f"No analyst coverage for {ticker}")

        return {
            "current_price": _safe_float(targets.get("current")),
            "target_mean": _safe_float(targets.get("mean")),
            "target_median": _safe_float(targets.get("median")),
            "target_high": _safe_float(targets.get("high")),
            "target_low": _safe_float(targets.get("low")),
            "analyst_count": analyst_count,
            "strong_buy": now.get("strong_buy", 0),
            "buy": now.get("buy", 0),
            "hold": now.get("hold", 0),
            "sell": now.get("sell", 0),
            "strong_sell": now.get("strong_sell", 0),
            **({f"prior_{k}": v for k, v in prior.items()} if prior else {}),
            "recent_changes": changes,
        }

    # ---- earnings calendar -------------------------------------------------

    async def fetch_earnings_calendar(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_earnings_calendar_sync, ticker)

    def _fetch_earnings_calendar_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        calendar = yf.Ticker(ticker).calendar
        raw: Any = []
        if isinstance(calendar, dict):
            raw = calendar.get("Earnings Date") or []
        elif calendar is not None and not getattr(calendar, "empty", True):
            # Older yfinance returned a DataFrame with the dates in a row.
            try:
                raw = list(calendar.loc["Earnings Date"].values)
            except (KeyError, AttributeError):
                raw = []
        if not isinstance(raw, (list, tuple)):
            raw = [raw]
        dates = []
        for value in raw:
            try:
                dates.append(str(value)[:10])
            except Exception:  # noqa: BLE001
                continue
        return {"earnings_dates": dates}

    # ---- analyst estimates -------------------------------------------------

    async def fetch_analyst_estimates(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_analyst_estimates_sync, ticker)

    def _fetch_analyst_estimates_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)

        consensus_eps_next_q = 0.0
        try:
            earnings_est = t.earnings_estimate
            if earnings_est is not None and not earnings_est.empty and "0q" in earnings_est.index:
                consensus_eps_next_q = _safe_float(earnings_est.loc["0q", "avg"]) or 0.0
        except Exception:  # noqa: BLE001
            pass

        consensus_revenue_next_q = 0.0
        try:
            revenue_est = t.revenue_estimate
            if revenue_est is not None and not revenue_est.empty and "0q" in revenue_est.index:
                consensus_revenue_next_q = _safe_float(revenue_est.loc["0q", "avg"]) or 0.0
        except Exception:  # noqa: BLE001
            pass

        eps_revision_pct_30d = 0.0
        try:
            trend = t.eps_trend
            if trend is not None and not trend.empty and "0q" in trend.index:
                current = _safe_float(trend.loc["0q", "current"])
                ago_30d = _safe_float(trend.loc["0q", "30daysAgo"])
                if current is not None and ago_30d:
                    eps_revision_pct_30d = round((current - ago_30d) / abs(ago_30d) * 100, 2)
        except Exception:  # noqa: BLE001
            pass

        price_target_mean = price_target_high = price_target_low = 0.0
        try:
            targets = t.analyst_price_targets
            if targets:
                price_target_mean = _safe_float(targets.get("mean")) or 0.0
                price_target_high = _safe_float(targets.get("high")) or 0.0
                price_target_low = _safe_float(targets.get("low")) or 0.0
        except Exception:  # noqa: BLE001
            pass

        price_target_dispersion = 0.0
        if price_target_mean:
            price_target_dispersion = round(
                (price_target_high - price_target_low) / price_target_mean, 3
            )

        historical_surprises = []
        try:
            history = t.earnings_history
            if history is not None and not history.empty:
                for report_date, row in history.tail(4).iterrows():
                    surprise = _safe_float(row.get("surprisePercent"))
                    if surprise is None:
                        continue
                    period_label = (
                        report_date.date().isoformat()
                        if hasattr(report_date, "date")
                        else str(report_date)
                    )
                    historical_surprises.append(
                        {"fiscal_period": period_label, "surprise_pct": surprise * 100}
                    )
        except Exception:  # noqa: BLE001
            pass

        return {
            "consensus_eps_next_q": consensus_eps_next_q,
            "consensus_revenue_next_q": consensus_revenue_next_q,
            "eps_revision_pct_30d": eps_revision_pct_30d,
            "price_target_mean": price_target_mean,
            "price_target_high": price_target_high,
            "price_target_low": price_target_low,
            "price_target_dispersion": price_target_dispersion,
            # Not available from Yahoo's free surface.
            "guidance_vs_consensus": "",
            "historical_surprises": historical_surprises,
        }
