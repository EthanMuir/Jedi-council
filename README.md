# Jedi-council -- The High Council

A data-isolated multi-agent market prediction system. See
`JEDI_COUNCIL_SPEC.md` and `JEDI_COUNCIL_ADDENDUM_A.md` for the full design
(not committed to this repo -- ask for them if you need to re-read the
spec). The single governing constraint: every Council Member seat is
isolated by *data*, enforced in code (`DataIsolationError`), not by prompt.
Five LLM personas reading the same feed are one source wearing five robes.

## Status: Phase 0 + Phase 1 complete

- **DataService** (`council/data/`) -- normalised schemas, SQLite disk
  cache with TTL, a point-in-time guard (`filter_point_in_time`) that drops
  any record dated after `as_of`, and provider fallback (Alpha Vantage,
  yfinance backstop, or a recorded-fixture provider for offline use).
- **The Crypt** (`council/crypt/`) -- append-only, hash-chained SQLite
  ledger. `predictions` and `seat_votes` reject UPDATE/DELETE by trigger.
  `write_blind_prediction` refuses to write a prediction whose `resolve_at`
  isn't strictly in the future.
- **SeatVerdict v2** (`council/seats/base.py`) -- the Addendum A1 schema:
  three-decimal probabilities, a mandatory `comparison_class` for any
  directional vote, and code-level data isolation via `SeatContext`.
- **Three Tier I seats**: Technician (OHLCV only, ticker anonymised to
  "Instrument A", direction decided by a deterministic family-vote engine
  in `technical_indicators.py`), Catalyst Seer (news/sentiment only), Oracle
  of Options (option chain only).
- **Blind-round orchestrator + CLI** -- runs the three seats in parallel,
  isolated, no peer visibility, and writes the result to the Crypt.

Not yet built (by design -- stopping here per the spec's phase order): the
other 9 Tier I seats, Tiers II-IV (debate, Prosecutor, Base-Rate Keeper,
Grand Master), N-sampling/dispersion, calibration weighting, resolution
sweep, and the UI.

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
