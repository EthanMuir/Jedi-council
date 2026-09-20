# Jedi-council -- The High Council

A data-isolated multi-agent market prediction system. See
`JEDI_COUNCIL_SPEC.md` and `JEDI_COUNCIL_ADDENDUM_A.md` for the full design
(not committed to this repo -- ask for them if you need to re-read the
spec). The single governing constraint: every Council Member seat is
isolated by *data*, enforced in code (`DataIsolationError`), not by prompt.
Five LLM personas reading the same feed are one source wearing five robes.

## Status: Phase 0 + Phase 1 + Phase 2 + Phase 3 complete

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
- **Full pipeline orchestrator + CLI** (`council/engine/orchestrator.py`) --
  runs Phases A-G entirely in memory and writes the Crypt exactly once, at
  the end (Phase G) -- the immutability trigger makes a later UPDATE
  impossible, so `blind_*` and the Tier IV synthesis columns are populated
  together in one row, not two writes.
- **Phase B -- Reality Anchor** (`council/engine/base_rate.py`) -- the
  Base-Rate Keeper (pure computation, no LLM): hit-rate-up, realised vol,
  ATR-implied range, and a 95th-percentile max-plausible-move ceiling from
  the ticker's own rolling-return history, combined with Oracle of Options'
  implied move. Every directional Tier I target is checked against it and
  flagged PLAUSIBLE/IMPLAUSIBLE (flagged, never deleted).
- **Phase C -- Debate** (`council/seats/advocates.py`,
  `council/seats/prosecutor.py`) -- Bull and Bear Advocate see only Tier I
  *summaries* (never raw data), argue their fixed side over N rounds
  (default 2), and rebut the opposing side from round 2 on. The Prosecutor
  sees everything, including a deterministically pre-computed
  correlated-evidence check (`detect_correlated_evidence` -- two seats
  citing the same source, not just the LLM's own read), and can veto to
  force `NO_CONVICTION`.
- **Phase D -- weighted vote** (`council/engine/aggregation.py`) -- each
  directional seat weighted by horizon-competence x data-quality x
  plausibility (Calibration Officer weighting arrives Phase 4; every seat
  is 1.0 until then).
- **Phase E -- audit gates** -- Cost Auditor (`council/engine/cost_auditor.py`,
  pure computation: spread/break-even/edge, using a configurable assumed
  spread since there's no live bid/ask feed for the underlying yet),
  Prosecutor veto, base-rate plausibility (gate fails if a majority of
  directional targets are IMPLAUSIBLE), and a minimum-participating-seats
  floor. Any gate failing short-circuits Phase F entirely -- Grand Master
  is never called, a synthetic `NO_CONVICTION` verdict is written instead,
  and the reasons are recorded.
- **Phase F -- Grand Master synthesis** (`council/seats/grand_master.py`) --
  final verdict using the stronger model. Hard rule enforced by the schema:
  `dissent_summary` is a required, non-empty field, and it must report the
  *structure* of disagreement, not just a consensus number.
- **Risk Warden** (`council/engine/risk_warden.py`) -- ATR/IV-based
  position sizing with a fractional-Kelly cap and a same-ticker
  concentration check against other open Crypt positions. Its function
  signature takes no vote, probability, or direction parameter at all
  (Addendum A9's wall enforced at the type level, not by convention).

Not yet built (by design -- stopping here per the spec's phase order): the
Calibration Officer and its weight vector, the resolution sweep and
scoring, and the UI (all Phase 4+).

Known scope cuts (see inline comments): Fundamentals/AnalystEstimates/Macro
snapshots don't yet enforce a filing-lag point-in-time guard the way the
dated feeds (insider transactions, congress trades, transcripts, SEC
filings, 13F) do -- deferred to Phase 4, when the Crypt actually backtests
against historical `as_of` dates. FMP provider endpoints are unverified
against a live key (none configured). Cross-Market Navigator's sector/peer/
index/overseas proxies all resolve to the same `DEFAULT_ohlcv.json` fixture
in offline mode, so their values are currently identical -- a fixture-mode
artifact, not a code issue. Cost Auditor's spread is an assumed constant
(`Settings.assumed_spread_bps`), not a live quote. Risk Warden's
"correlation against other open positions" is currently a same-ticker
concentration check only, not a real cross-asset correlation matrix. Debate
and Prosecutor fixtures are static text (not horizon- or ticker-aware), so
at short horizons the Reality Anchor will often flag their fixed
expected-move numbers IMPLAUSIBLE and fail the base-rate gate -- a real,
correct audit-gate response to a fixture-mode limitation, not a bug (try
`--horizon 1d` vs `--horizon 1w` to see both paths).

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
