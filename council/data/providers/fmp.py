"""Financial Modeling Prep adapter. Covers the seats spec section 3 assigns
to FMP: Fundamentalist, Insider Reader, Senate Watcher, Flow Cartographer,
Estimate Scribe, Transcript Linguist, Structure Archivist. Also supplies
fetch_news (Task #75) -- Catalyst Seer's domain isn't FMP-assigned by spec,
but yfinance's news was the only source ever actually reachable in
practice (see DataService._fetch_news_merged's own docstring for why);
FMP's stock_news is now unioned in alongside it.

Endpoint paths follow FMP's documented v3/v4 REST conventions. Untested
against a live key (none configured yet -- see README) -- FixtureProvider is
what's actually exercised until a real FMP_API_KEY is supplied."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx

from council.data.rate_limit import TokenBucket

_BASE_V3 = "https://financialmodelingprep.com/api/v3"
_BASE_V4 = "https://financialmodelingprep.com/api/v4"


class FMPProvider:
    name = "fmp"

    def __init__(self, api_key: str, requests_per_minute: int = 250):
        self._api_key = api_key
        self._limiter = TokenBucket(rate_per_second=requests_per_minute / 60)
        self._client = httpx.AsyncClient(timeout=30)

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        await self._limiter.acquire()
        params = {**(params or {}), "apikey": self._api_key}
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    async def fetch_fundamentals(self, ticker: str) -> dict[str, Any]:
        income, ratios, metrics = (
            await self._get(f"{_BASE_V3}/income-statement/{ticker}", {"period": "quarter", "limit": 1}),
            await self._get(f"{_BASE_V3}/ratios-ttm/{ticker}"),
            await self._get(f"{_BASE_V3}/key-metrics-ttm/{ticker}"),
        )
        inc = income[0] if income else {}
        rat = ratios[0] if ratios else {}
        met = metrics[0] if metrics else {}
        return {
            "fiscal_period": f"{inc.get('period', '')} {inc.get('calendarYear', '')}".strip(),
            "revenue": float(inc.get("revenue", 0.0)),
            "revenue_growth_yoy": float(inc.get("growthRevenue", 0.0) or 0.0),
            "gross_margin": float(inc.get("grossProfitRatio", 0.0) or 0.0),
            "operating_margin": float(inc.get("operatingIncomeRatio", 0.0) or 0.0),
            "net_margin": float(inc.get("netIncomeRatio", 0.0) or 0.0),
            "total_debt": float(met.get("totalDebt", 0.0) or 0.0),
            "cash_and_equivalents": float(met.get("cashAndShortTermInvestments", 0.0) or 0.0),
            "free_cash_flow": float(met.get("freeCashFlowTTM", 0.0) or 0.0),
            "pe_ratio": float(rat.get("peRatioTTM")) if rat.get("peRatioTTM") is not None else None,
            "ev_to_ebitda": float(rat.get("enterpriseValueMultipleTTM"))
            if rat.get("enterpriseValueMultipleTTM") is not None
            else None,
            "price_to_sales": float(rat.get("priceToSalesRatioTTM"))
            if rat.get("priceToSalesRatioTTM") is not None
            else None,
            "sector_median_pe": None,
            "sector_median_ev_ebitda": None,
        }

    async def fetch_insider_transactions(
        self, ticker: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        raw = await self._get(f"{_BASE_V4}/insider-trading", {"symbol": ticker, "page": 0})
        out = []
        for row in raw:
            filed = datetime.fromisoformat(row["filingDate"])
            if not (start.isoformat() <= filed.date().isoformat() <= end.isoformat()):
                continue
            txn_code = row.get("transactionType", "")
            out.append(
                {
                    "filed_at": filed.isoformat(),
                    "transaction_date": row["transactionDate"],
                    "insider_name": row.get("reportingName", "unknown"),
                    "insider_title": row.get("typeOfOwner", "unknown"),
                    "is_officer": "officer" in row.get("typeOfOwner", "").lower()
                    or "10-b5-1" not in txn_code.lower()
                    and "chief" in row.get("typeOfOwner", "").lower(),
                    "transaction_type": _classify_insider_txn(txn_code),
                    "shares": float(row.get("securitiesTransacted", 0.0)),
                    "price": float(row.get("price", 0.0) or 0.0),
                    "shares_owned_after": float(row.get("securitiesOwned", 0.0)),
                }
            )
        return out

    async def fetch_congress_data(
        self, ticker: str, start: date, end: date
    ) -> dict[str, Any]:
        senate, house = (
            await self._get(f"{_BASE_V4}/senate-trading", {"symbol": ticker}),
            await self._get(f"{_BASE_V4}/senate-disclosure-house", {"symbol": ticker}),
        )
        trades = []
        for row, chamber in ((s, "Senate") for s in senate):
            filed = datetime.fromisoformat(row["disclosureDate"])
            if not (start.isoformat() <= filed.date().isoformat() <= end.isoformat()):
                continue
            trades.append(
                {
                    "filed_at": filed.isoformat(),
                    "transaction_date": row["transactionDate"],
                    "member_name": row.get("senator", row.get("representative", "unknown")),
                    "chamber": chamber,
                    "committees": row.get("committees", []),
                    "transaction_type": row.get("type", "OTHER").upper()[:20] or "OTHER",
                    "amount_range": row.get("amount", "unknown"),
                }
            )
        for row, chamber in ((h, "House") for h in house):
            filed = datetime.fromisoformat(row["disclosureDate"])
            if not (start.isoformat() <= filed.date().isoformat() <= end.isoformat()):
                continue
            trades.append(
                {
                    "filed_at": filed.isoformat(),
                    "transaction_date": row["transactionDate"],
                    "member_name": row.get("representative", "unknown"),
                    "chamber": chamber,
                    "committees": row.get("committees", []),
                    "transaction_type": row.get("type", "OTHER").upper()[:20] or "OTHER",
                    "amount_range": row.get("amount", "unknown"),
                }
            )
        return {"trades": trades, "pending_legislation": []}

    async def fetch_news(self, ticker: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        # Task #75 -- FMP was never wired up as a news source at all before
        # this (only yfinance and, if keyed, Alpha Vantage were), despite
        # having a real stock-news endpoint the whole time. stock_news has
        # no server-side date filter, only `limit` -- over-fetch and filter
        # client-side, same pattern as fetch_insider_transactions above.
        raw = await self._get(f"{_BASE_V3}/stock_news", {"tickers": ticker, "limit": 100})
        out = []
        for row in raw or []:
            title = row.get("title")
            url = row.get("url")
            raw_date = row.get("publishedDate")
            if not title or not url or not raw_date:
                continue
            published = datetime.fromisoformat(raw_date.replace(" ", "T"))
            if not (start <= published <= end):
                continue
            out.append(
                {
                    "headline": title,
                    "summary": row.get("text", ""),
                    "source": row.get("site", "fmp"),
                    "url": url,
                    "published_at": published.isoformat(),
                    "sentiment_score": None,
                    "sentiment_label": None,
                }
            )
        return out

    async def fetch_institutional_holdings(self, ticker: str) -> dict[str, Any]:
        raw = await self._get(f"{_BASE_V4}/institutional-ownership/symbol-ownership", {"symbol": ticker})
        row = raw[0] if raw else {}
        return {
            "quarter_end": row.get("date", date.today().isoformat()),
            "filed_at": row.get("date", date.today().isoformat()),
            "total_institutional_shares": int(row.get("investorsHolding", 0) or 0),
            "pct_of_float_held": float(row.get("ownershipPercent", 0.0) or 0.0),
            "qoq_share_change_pct": float(row.get("changeInOwnershipPercentage", 0.0) or 0.0),
            "top_holders_net_buyers": int(row.get("newPositions", 0) or 0),
            "top_holders_net_sellers": int(row.get("closedPositions", 0) or 0),
            "etf_inclusion_notes": "",
            "short_interest_shares": 0,
            "days_to_cover": 0.0,
        }

    async def fetch_analyst_estimates(self, ticker: str) -> dict[str, Any]:
        estimates, targets, surprises = (
            await self._get(f"{_BASE_V3}/analyst-estimates/{ticker}", {"limit": 1}),
            await self._get(f"{_BASE_V4}/price-target-summary", {"symbol": ticker}),
            await self._get(f"{_BASE_V3}/earnings-surprises/{ticker}"),
        )
        est = estimates[0] if estimates else {}
        tgt = targets[0] if isinstance(targets, list) and targets else (targets or {})
        return {
            "consensus_eps_next_q": float(est.get("estimatedEpsAvg", 0.0) or 0.0),
            "consensus_revenue_next_q": float(est.get("estimatedRevenueAvg", 0.0) or 0.0),
            "eps_revision_pct_30d": 0.0,
            "price_target_mean": float(tgt.get("lastMonthAvgPriceTarget", 0.0) or 0.0),
            "price_target_high": float(est.get("estimatedEpsHigh", 0.0) or 0.0),
            "price_target_low": float(est.get("estimatedEpsLow", 0.0) or 0.0),
            "price_target_dispersion": 0.0,
            "guidance_vs_consensus": "",
            "historical_surprises": [
                {
                    "fiscal_period": f"{s.get('date', '')}",
                    "surprise_pct": float(s.get("surprisePercentage", 0.0) or 0.0),
                }
                for s in (surprises or [])[:4]
            ],
        }

    async def fetch_earnings_transcripts(self, ticker: str, limit: int = 4) -> list[dict[str, Any]]:
        raw = await self._get(f"{_BASE_V4}/batch_earning_call_transcript/{ticker}")
        out = []
        for row in (raw or [])[:limit]:
            out.append(
                {
                    "call_date": row.get("date", date.today().isoformat()),
                    "fiscal_period": f"Q{row.get('quarter', '')} {row.get('year', '')}",
                    "prepared_remarks": row.get("content", "")[:4000],
                    "qa_highlights": "",
                    "source": "fmp",
                    "url": "",
                }
            )
        return out

    async def fetch_sec_filings(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        raw = await self._get(f"{_BASE_V3}/sec_filings/{ticker}", {"page": 0})
        out = []
        for row in raw:
            filed = datetime.fromisoformat(row["fillingDate"].replace(" ", "T"))
            if not (start.isoformat() <= filed.date().isoformat() <= end.isoformat()):
                continue
            form_type = row.get("type", "OTHER")
            out.append(
                {
                    "filed_at": filed.isoformat(),
                    "form_type": form_type if form_type in _KNOWN_FORM_TYPES else "OTHER",
                    "headline": f"{form_type} filed",
                    "summary": row.get("finalLink", ""),
                    "url": row.get("finalLink", ""),
                    "flags": [],
                }
            )
        return out


_KNOWN_FORM_TYPES = {"8-K", "S-1", "S-3", "13D", "13G", "10-Q", "10-K"}


def _classify_insider_txn(code: str) -> str:
    code = code.upper()
    if code.startswith("P"):
        return "OPEN_MARKET_BUY"
    if code.startswith("S"):
        return "OPEN_MARKET_SELL"
    if code.startswith("M") or code.startswith("A"):
        return "OPTION_EXERCISE"
    return "OTHER"
