"""SEC EDGAR adapter -- official, free, no API key, for insider transactions (Form 4) and SEC filings
(8-K/10-Q/10-K/S-1/S-3/13D/13G). Built on two schemas that are unusually
stable and officially documented, unlike yfinance's scraped surface:

- data.sec.gov/submissions/CIK##########.json -- the filing list for a
  company (https://www.sec.gov/os/accessing-edgar-data). Only the "recent"
  page is used here (roughly the newest ~1000 filings); a company with a
  longer history than that within the lookback window would need the
  paginated "files" entries too, not implemented.
- The Form 3/4/5 "ownershipDocument" XML schema, stable since electronic
  filing became mandatory in 2003. Each Form 4's primaryDocument (from the
  submissions JSON) is the raw XML at its archive URL.

SEC requires a descriptive User-Agent with real contact info on every
request (https://www.sec.gov/os/webmaster-faq#developers) -- set
SEC_EDGAR_USER_AGENT to something real, not the placeholder default; a
generic one risks a 403 or an IP ban. Rate limit is a hard 10 req/sec
cap; this stays well under it, especially since fetching insider
transactions costs one request per Form 4 on top of the submissions call.

data.sec.gov and www.sec.gov are both blocked by this sandbox's egress
policy, same as every other external host used this session -- none of
this has been exercised against live EDGAR data. Tested here against a
faked httpx transport shaped like the documented schemas above; treat the
first live run like any new integration, not a known-good one."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

import httpx

from council.data.rate_limit import TokenBucket

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/{document}"

_FORM_TYPE_MAP = {
    "8-K": "8-K",
    "10-Q": "10-Q",
    "10-K": "10-K",
    "S-1": "S-1",
    "S-3": "S-3",
    "SC 13D": "13D",
    "SC 13D/A": "13D",
    "SC 13G": "13G",
    "SC 13G/A": "13G",
}
# Forms 3/4/5 and 144 are insider-related, not filings -- Form 4 is handled
# separately by fetch_insider_transactions below; surfacing every one of
# them here too would flood structure_archivist's feed with noise instead
# of the corporate-action filings it actually wants.
_SKIP_FILING_FORMS = {"3", "3/A", "4", "4/A", "5", "5/A", "144"}

_INSIDER_FORMS = {"4", "4/A"}
# One HTTP request per Form 4 on top of the submissions.json call -- cap
# how many get fetched per lookback window so a heavily-traded name doesn't
# turn one gather() into dozens of requests.
_MAX_FORM4_DETAIL_FETCHES = 20


def _text(el: ET.Element, path: str, default: str | None = None) -> str | None:
    node = el.find(path)
    if node is None or node.text is None:
        return default
    return node.text.strip()


def _parse_form4_xml(xml_text: str) -> list[dict[str, Any]]:
    """A single Form 4 can report several transactions (and, rarely,
    several reporting owners for a joint filing) -- this returns one dict
    per transaction, matching InsiderTransaction's shape minus filed_at
    (the caller knows that from the filing index, not the document body).
    The first reporting owner's identity is used for every transaction in
    the document; joint filings with genuinely different owners per
    transaction aren't distinguished."""
    root = ET.fromstring(xml_text)

    owner_name = "unknown"
    owner_title = "unknown"
    is_officer = False
    owner = root.find(".//reportingOwner")
    if owner is not None:
        owner_name = _text(owner, ".//rptOwnerName", "unknown") or "unknown"
        rel = owner.find(".//reportingOwnerRelationship")
        if rel is not None:
            is_officer = _text(rel, "isOfficer") == "1"
            is_director = _text(rel, "isDirector") == "1"
            is_ten_pct = _text(rel, "isTenPercentOwner") == "1"
            officer_title = _text(rel, "officerTitle")
            if is_officer and officer_title:
                owner_title = officer_title
            elif is_officer:
                owner_title = "Officer"
            elif is_director:
                owner_title = "Director"
            elif is_ten_pct:
                owner_title = "10% Owner"
            else:
                owner_title = "Other"

    transactions = []
    for table_tag, txn_tag in (
        ("nonDerivativeTable", "nonDerivativeTransaction"),
        ("derivativeTable", "derivativeTransaction"),
    ):
        table = root.find(table_tag)
        if table is None:
            continue
        for txn in table.findall(txn_tag):
            txn_date = _text(txn, "transactionDate/value")
            shares = _text(txn, "transactionAmounts/transactionShares/value")
            if txn_date is None or shares is None:
                continue
            code = _text(txn, "transactionCoding/transactionCode", "") or ""
            price = _text(txn, "transactionAmounts/transactionPricePerShare/value")
            owned_after = _text(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
            transactions.append(
                {
                    "transaction_date": txn_date,
                    "transaction_type": _classify_insider_txn(code),
                    "shares": float(shares),
                    "price": float(price) if price else 0.0,
                    "shares_owned_after": float(owned_after) if owned_after else 0.0,
                    "insider_name": owner_name,
                    "insider_title": owner_title,
                    "is_officer": is_officer,
                }
            )
    return transactions


class SECEdgarForbidden(Exception):
    """SEC EDGAR returned 403 -- confirmed live (Task #64): this is almost
    always the User-Agent header, not the request itself. SEC's fair-access
    policy requires a real contact on every request
    (https://www.sec.gov/os/webmaster-faq#developers) and its edge rejects
    a missing or placeholder one outright. Distinct from a generic
    httpx.HTTPStatusError so the failure message that reaches a seat's
    abstain_reason names the actual fix instead of a bare "403 Forbidden"
    the user has to reverse-engineer."""


class SECEdgarProvider:
    name = "sec_edgar"

    def __init__(self, user_agent: str, requests_per_second: float = 5.0):
        self._client = httpx.AsyncClient(
            timeout=30, headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        )
        self._limiter = TokenBucket(rate_per_second=requests_per_second)
        self._cik_cache: dict[str, int] | None = None

    def _raise_for_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 403:
            raise SECEdgarForbidden(
                f"SEC EDGAR returned 403 Forbidden for {resp.request.url} -- SEC requires a "
                "real contact in the User-Agent on every request "
                "(https://www.sec.gov/os/webmaster-faq#developers) and rejects a missing or "
                'placeholder one. Set SEC_EDGAR_USER_AGENT in .env to something like '
                '"YourApp your-real-email@example.com" and retry. Current User-Agent sent: '
                f"{self._client.headers.get('user-agent')!r}"
            )
        resp.raise_for_status()

    async def _get_json(self, url: str) -> Any:
        await self._limiter.acquire()
        resp = await self._client.get(url)
        self._raise_for_status(resp)
        return resp.json()

    async def _get_text(self, url: str) -> str:
        await self._limiter.acquire()
        resp = await self._client.get(url)
        self._raise_for_status(resp)
        return resp.text

    async def _resolve_cik(self, ticker: str) -> int:
        if self._cik_cache is None:
            raw = await self._get_json(_TICKERS_URL)
            self._cik_cache = {row["ticker"].upper(): int(row["cik_str"]) for row in raw.values()}
        cik = self._cik_cache.get(ticker.upper())
        if cik is None:
            raise KeyError(f"SEC EDGAR has no CIK mapping for ticker {ticker!r}")
        return cik

    async def _submissions(self, ticker: str) -> tuple[dict[str, Any], int]:
        cik = await self._resolve_cik(ticker)
        return await self._get_json(_SUBMISSIONS_URL.format(cik=cik)), cik

    async def fetch_sec_filings(self, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
        data, cik = await self._submissions(ticker)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        out = []
        for i, form in enumerate(forms):
            if form in _SKIP_FILING_FORMS:
                continue
            filed_date = dates[i]
            if not (start.isoformat() <= filed_date <= end.isoformat()):
                continue
            doc = primary_docs[i] if i < len(primary_docs) else ""
            url = _ARCHIVE_URL.format(
                cik=cik, accession_no_dashes=accessions[i].replace("-", ""), document=doc
            )
            summary = descriptions[i] if i < len(descriptions) and descriptions[i] else f"{form} filing"
            out.append(
                {
                    "filed_at": datetime.fromisoformat(filed_date).isoformat(),
                    "form_type": _FORM_TYPE_MAP.get(form, "OTHER"),
                    "headline": f"{form} filed",
                    "summary": summary,
                    "url": url,
                    "flags": [],
                }
            )
        return out

    async def fetch_insider_transactions(
        self, ticker: str, start: date, end: date
    ) -> list[dict[str, Any]]:
        data, cik = await self._submissions(ticker)
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        candidates = []
        for i, form in enumerate(forms):
            if form not in _INSIDER_FORMS:
                continue
            filed_date = dates[i]
            if not (start.isoformat() <= filed_date <= end.isoformat()):
                continue
            doc = primary_docs[i] if i < len(primary_docs) else ""
            if not doc:
                continue
            candidates.append((filed_date, accessions[i], doc))

        candidates.sort(key=lambda c: c[0], reverse=True)
        candidates = candidates[:_MAX_FORM4_DETAIL_FETCHES]

        out = []
        for filed_date, accession, doc in candidates:
            url = _ARCHIVE_URL.format(cik=cik, accession_no_dashes=accession.replace("-", ""), document=doc)
            try:
                xml_text = await self._get_text(url)
                parsed = _parse_form4_xml(xml_text)
            except (httpx.HTTPStatusError, ET.ParseError):
                # One malformed/unreachable Form 4 shouldn't sink the whole
                # feed -- same spirit as the option-chain per-contract
                # skip fix (Task #58).
                continue
            filed_at = datetime.fromisoformat(filed_date).isoformat()
            for txn in parsed:
                out.append({"filed_at": filed_at, **txn})
        return out


def _classify_insider_txn(code: str) -> str:
    """Form 4 transaction codes (P = open-market purchase, S = sale, M/A =
    option exercise / grant) mapped onto InsiderTransaction's categories."""
    code = code.upper()
    if code.startswith("P"):
        return "OPEN_MARKET_BUY"
    if code.startswith("S"):
        return "OPEN_MARKET_SELL"
    if code.startswith("M") or code.startswith("A"):
        return "OPTION_EXERCISE"
    return "OTHER"
