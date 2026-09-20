# Jedi-council -- The High Council

A data-isolated multi-agent market prediction system. See
`JEDI_COUNCIL_SPEC.md` and `JEDI_COUNCIL_ADDENDUM_A.md` for the full design
(not committed to this repo -- ask for them if you need to re-read the
spec). The single governing constraint: every Council Member seat is
isolated by *data*, enforced in code (`DataIsolationError`), not by prompt.
Five LLM personas reading the same feed are one source wearing five robes.

## Status: Phase 0 + Phase 1 + Phase 2 complete

- **DataService** (`council/data/`) -- normalised schemas, SQLite disk
  cache with TTL, a point-in-time guard (`filter_point_in_time`) that drops
  any record dated after `as_of`, and provider fallback (Alpha Vantage, FMP,
  yfinance backstop, or a recorded-fixture provider for offline use). Covers
  OHLCV, news/sentiment, options, fundamentals, insider transactions,
  congressional disclosures, 13F/institutional holdings, macro data,
  cross-market instruments, analyst estimates, earnings transcripts, and SEC
  filings.
- **The Crypt** (`council/crypt/`) -- append-only, hash-chained SQLite
  ledger. `predictions` and `seat_votes` reject UPDATE/DELETE by trigger.
  `write_blind_prediction` refuses to write a prediction whose `resolve_at`
  isn't strictly in the future.
- **SeatVerdict v2** (`council/seats/base.py`) -- the Addendum A1 schema:
  three-decimal probabilities, a mandatory `comparison_class` for any
  directional vote, and code-level data isolation via `SeatContext`.
- **All 12 Tier I seats**: Technician (OHLCV only, ticker anonymised,
  mechanical family-vote direction), Fundamentalist, Catalyst Seer, Insider
  Reader, Senate Watcher, Flow Cartographer, Oracle of Options, Macro Sage,
  Cross-Market Navigator (never sees the ticker's own price series), Estimate
  Scribe, Transcript Linguist, Structure Archivist -- each isolated to
  exactly one data domain, gated by the spec's hardcoded horizon-competence
  matrix (`council/engine/horizons.py`).
- **N-sampling + dispersion** (`council/engine/sampling.py`) -- every seat is
  sampled N times (`Settings.n_samples_per_seat`, default 3). Majority vote across samples
  becomes the seat's consensus; disagreement fraction becomes `dispersion`
  and automatically discounts the reported confidence toward 0.5. A tie with
  no majority resolves to `NO_READ`.
- **Blind-round orchestrator + CLI** -- runs all competent seats in parallel,
  isolated, no peer visibility, each sampled 3x, and writes the result to
  the Crypt (including per-seat dispersion).

Not yet built (by design -- stopping here per the spec's phase order):
Tiers II-IV (debate, Prosecutor, Base-Rate Keeper, Cost Auditor, Risk Warden,
Grand Master), calibration weighting, the resolution sweep, and the UI.

Known Phase 2 scope cuts (see inline comments): Fundamentals/AnalystEstimates
/Macro snapshots don't yet enforce a filing-lag point-in-time guard the way
the dated feeds (insider transactions, congress trades, transcripts, SEC
filings, 13F) do -- deferred to Phase 4, when the Crypt actually backtests
against historical `as_of` dates. FMP provider endpoints are unverified
against a live key (none configured). Cross-Market Navigator's sector/peer/
index/overseas proxies all resolve to the same `DEFAULT_ohlcv.json` fixture
in offline mode, so their values are currently identical -- a fixture-mode
artifact, not a code issue.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # optional -- empty is fine, runs in fixture mode
```

With no `ANTHROPIC_API_KEY` / `ALPHA_VANTAGE_API_KEY` set, the system
automatically runs in fixture mode: seat verdicts come from
`council/tests/fixtures/seat_verdicts/`, market data from
`council/tests/fixtures/market_data/` (NVDA only, for now). Set the keys in
`.env` to go live -- no code changes needed.

## Run it

```bash
.venv/bin/python -m council deliberate NVDA --horizon 1w
```

## Test

```bash
.venv/bin/python -m pytest council/tests/ -q
```
