"""SEC EDGAR is the alternative source for insider transactions / SEC
filings on the domains the user's FMP plan 403s on (insider_reader,
structure_archivist). data.sec.gov and www.sec.gov are both blocked by
this sandbox's egress policy, so this verifies the parsing logic against a
faked httpx transport shaped like the two officially-documented schemas
involved (the submissions.json filing list, and the Form 3/4/5
"ownershipDocument" XML) -- it cannot verify that shape is what EDGAR
actually returns today.

Task #64: the first live run of this provider hit 403 Forbidden on every
request -- confirmed to be SEC's fair-access policy rejecting a missing or
placeholder User-Agent (https://www.sec.gov/os/webmaster-faq#developers),
not a code bug. SECEdgarForbidden makes that self-diagnosing instead of a
bare "403 Forbidden" the user has to reverse-engineer."""
from __future__ import annotations

from datetime import date

import httpx
import pytest

from council.data.providers.sec_edgar import SECEdgarForbidden, SECEdgarProvider

_TICKERS_PAYLOAD = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

_SUBMISSIONS_PAYLOAD = {
    "filings": {
        "recent": {
            "form": ["8-K", "10-Q", "SC 13G", "4", "3", "DEF 14A"],
            "filingDate": [
                "2026-08-01",
                "2026-07-15",
                "2026-06-01",
                "2026-08-10",
                "2025-01-01",  # outside lookback window
                "2026-08-05",
            ],
            "accessionNumber": [
                "0001045810-26-000001",
                "0001045810-26-000002",
                "0001045810-26-000003",
                "0001045810-26-000004",
                "0001045810-25-000099",
                "0001045810-26-000005",
            ],
            "primaryDocument": [
                "form8k.htm",
                "form10q.htm",
                "sc13g.htm",
                "form4.xml",
                "form3.xml",
                "defproxy.htm",
            ],
            "primaryDocDescription": ["", "10-Q", "", "", "", "Proxy Statement"],
        }
    }
}

_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>Jane Doe</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>0</isDirector>
      <isOfficer>1</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>Chief Financial Officer</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-08-09</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>1500</value></transactionShares>
        <transactionPricePerShare><value>182.5</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>48500</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
  <derivativeTable>
    <derivativeTransaction>
      <transactionDate><value>2026-08-09</value></transactionDate>
      <transactionCoding><transactionCode>M</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>2000</value></transactionShares>
        <transactionPricePerShare><value>0</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>50000</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </derivativeTransaction>
  </derivativeTable>
</ownershipDocument>
"""


def _make_provider(handler) -> SECEdgarProvider:
    provider = SECEdgarProvider(user_agent="Test Suite test@example.com")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider


def _default_handler(request: httpx.Request) -> httpx.Response:
    if "company_tickers.json" in str(request.url):
        return httpx.Response(200, json=_TICKERS_PAYLOAD)
    if "submissions/CIK" in str(request.url):
        return httpx.Response(200, json=_SUBMISSIONS_PAYLOAD)
    if str(request.url).endswith("form4.xml"):
        return httpx.Response(200, text=_FORM4_XML)
    return httpx.Response(404, text="not found")


@pytest.mark.asyncio
async def test_resolve_cik_maps_ticker_and_caches_across_calls():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _default_handler(request)

    provider = _make_provider(handler)
    cik1 = await provider._resolve_cik("nvda")
    cik2 = await provider._resolve_cik("NVDA")

    assert cik1 == cik2 == 1045810
    assert sum("company_tickers.json" in c for c in calls) == 1


@pytest.mark.asyncio
async def test_resolve_cik_raises_for_unknown_ticker():
    provider = _make_provider(_default_handler)
    with pytest.raises(KeyError):
        await provider._resolve_cik("NOTATICKER")


@pytest.mark.asyncio
async def test_fetch_sec_filings_maps_types_filters_by_date_and_skips_insider_forms():
    provider = _make_provider(_default_handler)
    filings = await provider.fetch_sec_filings("NVDA", date(2026, 1, 1), date(2026, 12, 31))

    form_types = {f["form_type"] for f in filings}
    assert form_types == {"8-K", "10-Q", "13G", "OTHER"}
    assert len(filings) == 4  # 8-K, 10-Q, SC 13G, DEF 14A -- Form 4/3 excluded, 2025 filing out of range

    filing_10q = next(f for f in filings if f["form_type"] == "10-Q")
    assert filing_10q["summary"] == "10-Q"
    assert filing_10q["url"].startswith("https://www.sec.gov/Archives/edgar/data/1045810/")


@pytest.mark.asyncio
async def test_fetch_sec_filings_respects_lookback_window():
    provider = _make_provider(_default_handler)
    filings = await provider.fetch_sec_filings("NVDA", date(2026, 6, 1), date(2026, 6, 30))
    assert len(filings) == 1
    assert filings[0]["form_type"] == "13G"


@pytest.mark.asyncio
async def test_fetch_insider_transactions_parses_non_derivative_and_derivative_rows():
    provider = _make_provider(_default_handler)
    txns = await provider.fetch_insider_transactions("NVDA", date(2026, 1, 1), date(2026, 12, 31))

    assert len(txns) == 2
    sale = next(t for t in txns if t["transaction_type"] == "OPEN_MARKET_SELL")
    assert sale["shares"] == 1500
    assert sale["price"] == 182.5
    assert sale["shares_owned_after"] == 48500
    assert sale["insider_name"] == "Jane Doe"
    assert sale["insider_title"] == "Chief Financial Officer"
    assert sale["is_officer"] is True
    assert sale["filed_at"] == "2026-08-10T00:00:00"

    exercise = next(t for t in txns if t["transaction_type"] == "OPTION_EXERCISE")
    assert exercise["shares"] == 2000


@pytest.mark.asyncio
async def test_fetch_insider_transactions_skips_form3_and_out_of_range_filings():
    provider = _make_provider(_default_handler)
    txns = await provider.fetch_insider_transactions("NVDA", date(2026, 1, 1), date(2026, 12, 31))
    # Form 3 (2025-01-01, also outside window) never gets fetched at all --
    # only the one Form 4 in range should ever be requested.
    assert all(t["filed_at"].startswith("2026-08-10") for t in txns)


@pytest.mark.asyncio
async def test_fetch_insider_transactions_survives_unreachable_form4_document():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("form4.xml"):
            return httpx.Response(404, text="not found")
        return _default_handler(request)

    provider = _make_provider(handler)
    txns = await provider.fetch_insider_transactions("NVDA", date(2026, 1, 1), date(2026, 12, 31))
    assert txns == []


def _forbidden_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(403, text="<html>Forbidden</html>")


@pytest.mark.asyncio
async def test_403_on_ticker_lookup_raises_actionable_forbidden_error():
    provider = _make_provider(_forbidden_handler)
    with pytest.raises(SECEdgarForbidden, match="SEC_EDGAR_USER_AGENT"):
        await provider.fetch_sec_filings("NVDA", date(2026, 1, 1), date(2026, 12, 31))


@pytest.mark.asyncio
async def test_403_on_submissions_raises_actionable_forbidden_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if "company_tickers.json" in str(request.url):
            return httpx.Response(200, json=_TICKERS_PAYLOAD)
        return httpx.Response(403, text="<html>Forbidden</html>")

    provider = _make_provider(handler)
    with pytest.raises(SECEdgarForbidden, match="SEC_EDGAR_USER_AGENT"):
        await provider.fetch_insider_transactions("NVDA", date(2026, 1, 1), date(2026, 12, 31))


@pytest.mark.asyncio
async def test_403_is_not_silently_swallowed_as_a_missing_form4_document():
    # A systemic 403 (misconfigured User-Agent) must propagate as a real
    # failure, unlike a single missing/malformed Form 4 document (see the
    # 404 test above) -- silently returning [] would hide a config problem
    # behind what looks like "no insider activity this window".
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("form4.xml"):
            return httpx.Response(403, text="<html>Forbidden</html>")
        return _default_handler(request)

    provider = _make_provider(handler)
    with pytest.raises(SECEdgarForbidden):
        await provider.fetch_insider_transactions("NVDA", date(2026, 1, 1), date(2026, 12, 31))
