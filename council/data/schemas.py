"""Normalised, provider-agnostic data schemas. Every payload the DataService
hands to a seat carries `as_of` and `staleness_seconds` so the seat can factor
data age into its confidence."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class OHLCVBar(BaseModel):
    trade_date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adjusted_close: float | None = None


class OHLCVSeries(BaseModel):
    ticker: str
    bars: list[OHLCVBar]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class NewsItem(BaseModel):
    headline: str
    summary: str
    source: str
    url: str
    published_at: datetime
    sentiment_score: float | None = None
    sentiment_label: str | None = None


class NewsFeed(BaseModel):
    ticker: str
    items: list[NewsItem]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class EventCalendarItem(BaseModel):
    event_type: str
    event_date: date
    description: str
    scheduled: bool


class OptionContract(BaseModel):
    strike: float
    expiry: date
    option_type: Literal["call", "put"]
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: int | None = None
    open_interest: int | None = None
    implied_volatility: float | None = None


class OptionChainSnapshot(BaseModel):
    ticker: str
    underlying_price: float
    contracts: list[OptionContract]
    put_call_ratio: float | None
    as_of: datetime
    staleness_seconds: float | None
    source: str


# --- Phase 2 domains -------------------------------------------------------


class FundamentalsSnapshot(BaseModel):
    ticker: str
    fiscal_period: str
    revenue: float
    revenue_growth_yoy: float
    gross_margin: float
    operating_margin: float
    net_margin: float
    total_debt: float
    cash_and_equivalents: float
    free_cash_flow: float
    pe_ratio: float | None
    ev_to_ebitda: float | None
    price_to_sales: float | None
    sector_median_pe: float | None
    sector_median_ev_ebitda: float | None
    as_of: datetime
    staleness_seconds: float | None
    source: str


class InsiderTransaction(BaseModel):
    filed_at: datetime
    transaction_date: date
    insider_name: str
    insider_title: str
    is_officer: bool
    transaction_type: Literal[
        "OPEN_MARKET_BUY", "OPEN_MARKET_SELL", "OPTION_EXERCISE", "RULE_10B5_1_SALE", "OTHER"
    ]
    shares: float
    price: float
    shares_owned_after: float


class InsiderTransactionFeed(BaseModel):
    ticker: str
    transactions: list[InsiderTransaction]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class CongressTrade(BaseModel):
    filed_at: datetime
    transaction_date: date
    member_name: str
    chamber: Literal["House", "Senate"]
    committees: list[str]
    transaction_type: Literal["BUY", "SELL", "EXCHANGE"]
    amount_range: str


class CongressTradeFeed(BaseModel):
    ticker: str
    trades: list[CongressTrade]
    pending_legislation: list[str]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class InstitutionalHoldingsSnapshot(BaseModel):
    ticker: str
    quarter_end: date
    filed_at: datetime
    total_institutional_shares: int
    pct_of_float_held: float
    qoq_share_change_pct: float
    top_holders_net_buyers: int
    top_holders_net_sellers: int
    etf_inclusion_notes: str
    short_interest_shares: int
    days_to_cover: float
    as_of: datetime
    staleness_seconds: float | None
    source: str


class MacroSnapshot(BaseModel):
    fed_funds_rate: float
    treasury_10y: float
    treasury_2y: float
    curve_10y_minus_2y: float
    cpi_yoy: float
    unemployment_rate: float
    dollar_index: float
    wti_crude: float
    ticker_beta_to_spx: float | None
    as_of: datetime
    staleness_seconds: float | None
    source: str


class EarningsSurprise(BaseModel):
    fiscal_period: str
    surprise_pct: float


class AnalystEstimatesSnapshot(BaseModel):
    ticker: str
    consensus_eps_next_q: float
    consensus_revenue_next_q: float
    eps_revision_pct_30d: float
    price_target_mean: float
    price_target_high: float
    price_target_low: float
    price_target_dispersion: float
    guidance_vs_consensus: str
    historical_surprises: list[EarningsSurprise]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class EarningsTranscriptRecord(BaseModel):
    call_date: date
    fiscal_period: str
    prepared_remarks: str
    qa_highlights: str
    source: str
    url: str


class EarningsTranscriptFeed(BaseModel):
    ticker: str
    calls: list[EarningsTranscriptRecord]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class SECFiling(BaseModel):
    filed_at: datetime
    form_type: Literal["8-K", "S-1", "S-3", "13D", "13G", "10-Q", "10-K", "OTHER"]
    headline: str
    summary: str
    url: str
    flags: list[str]


class SECFilingFeed(BaseModel):
    ticker: str
    filings: list[SECFiling]
    as_of: datetime
    staleness_seconds: float | None
    source: str


class CrossMarketSnapshot(BaseModel):
    sector_etf_symbol: str
    sector_etf_return_5d_pct: float
    peer_basket_return_5d_pct: float
    index_futures_change_pct: float
    dollar_index_change_pct: float
    oil_change_pct: float
    overseas_session_return_pct: float
    as_of: datetime
    staleness_seconds: float | None
    source: str
