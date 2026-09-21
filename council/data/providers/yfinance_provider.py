"""yfinance fallback -- OHLCV, news (stubbed, Yahoo has no clean feed for
it), options, fundamentals, institutional holdings, and analyst estimates.
Free, no key, no official rate limit, per spec: "free, unreliable, use as
backstop only".

The three newest methods (fundamentals, institutional holdings, analyst
estimates) lean on yfinance's `Ticker.info` dict and a handful of
purpose-built DataFrames (`institutional_holders`, `major_holders`,
`analyst_price_targets`, `earnings_estimate`, `revenue_estimate`,
`eps_trend`, `earnings_history`). None of this is a documented, stable
Yahoo API -- yfinance scrapes it, field availability varies by ticker and
by yfinance version, and this couldn't be verified against live Yahoo data
from the sandbox that built it (network policy blocks it same as it blocks
most external hosts). Every extraction below is wrapped defensively and
falls back to the same "unavailable" defaults FMPProvider already uses for
fields it can't fill either -- a missing number here should degrade the
seat's data_quality, not crash it."""
from __future__ import annotations

import asyncio
from datetime import date, datetime
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

    async def fetch_news(self, ticker, start, end) -> list[dict[str, Any]]:
        return []

    async def fetch_option_chain(self, ticker: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._fetch_option_chain_sync, ticker)

    def _fetch_option_chain_sync(self, ticker: str) -> dict[str, Any]:
        import yfinance as yf

        t = yf.Ticker(ticker)
        expiries = t.options
        if not expiries:
            return {"underlying_price": 0.0, "contracts": [], "put_call_ratio": None}

        expiry = expiries[0]
        chain = t.option_chain(expiry)

        underlying_price = 0.0
        try:
            underlying_price = float(chain.underlying.get("regularMarketPrice", 0.0) or 0.0)
        except (AttributeError, TypeError, ValueError):
            pass

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
            # Yahoo doesn't publish a sector-median comparator; FMP's own
            # provider leaves these null too rather than approximate one.
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
                # closest free approximation available, same spirit as
                # FMP's own defaults below for fields Yahoo doesn't expose
                # at all.
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
            # Not available from Yahoo's free surface -- FMP's own provider
            # defaults several of these same fields for the same reason.
            "qoq_share_change_pct": 0.0,
            "top_holders_net_buyers": 0,
            "top_holders_net_sellers": 0,
            "etf_inclusion_notes": "",
            "short_interest_shares": _safe_int(info.get("sharesShort")) or 0,
            "days_to_cover": _safe_float(info.get("shortRatio")) or 0.0,
        }

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
