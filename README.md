# Jedi-council -- The High Council

A data-isolated multi-agent market prediction system. See
`JEDI_COUNCIL_SPEC.md` and `JEDI_COUNCIL_ADDENDUM_A.md` for the full design
(not committed to this repo -- ask for them if you need to re-read the
spec). The single governing constraint: every Council Member seat is
isolated by *data*, enforced in code (`DataIsolationError`), not by prompt.
Five LLM personas reading the same feed are one source wearing five robes.

## Status: Phase 0 + Phase 1 + Phase 2 + Phase 3 + Phase 4 complete

- **DataService** (`council/data/`) -- normalised schemas, SQLite disk
  cache with TTL, a point-in-time guard (`filter_point_in_time`) that drops
  any record dated after `as_of`, and provider fallback (yfinance and SEC
  EDGAR first -- both free with no key and no hard daily cap -- then FRED,
  then FMP, or a recorded-fixture provider for offline use). News is the
  one domain that doesn't stop at the first success: every provider that
  covers it (yfinance, FMP) is queried and the results unioned, since
  yfinance's feed is whatever Yahoo currently has cached, not a real
  windowed query. Covers OHLCV, news/sentiment, options, fundamentals,
  insider transactions, congressional disclosures, 13F/institutional
  holdings, macro data, cross-market instruments, analyst estimates,
  earnings transcripts, and SEC filings. SEC EDGAR
  (`council/data/providers/sec_edgar.py`) is the alternative for insider
  transactions / SEC filings when FMP's plan doesn't cover them -- set
  `SEC_EDGAR_USER_AGENT` in `.env` to a real contact string (SEC requires
  one on every request). FRED (`council/data/providers/fred.py`) is the
  only source for macro data -- get a free key at fred.stlouisfed.org and
  set `FRED_API_KEY` in `.env`; without it, macro_sage has no live source
  at all and abstains every run. Alpha Vantage is not wired into the live
  chain (its free tier's 25-requests/day cap made it structurally
  unusable, and its options endpoints require a paid plan regardless of
  quota) -- the provider class is kept in
  `council/data/providers/alpha_vantage.py` in case a premium key gets
  wired back in later, but nothing calls it today.
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
  plausibility x decomposition-coherence x the Calibration Officer's
  weight (1.0 until a seat has 20+ resolutions).
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
- **Decomposition coherence check** (`council/seats/prosecutor.py`) --
  Addendum A7. Verifies a seat's `decomposition` sub-probabilities actually
  combine (AND/OR/CONDITIONAL arithmetic) to within 0.15 of its headline
  `probability`; flags `INCOHERENT_CONFIDENCE` and discounts that seat's
  Phase D weight by 30% for the deliberation.
- **Multi-provider routing** (`council/engine/routing.py`,
  `config/models.yaml`) -- Addendum A3 infrastructure. Every seat has a
  declared provider/model and a fallback; routing resolves to the intended
  provider only if that provider's key is configured, else falls back to
  Anthropic automatically. Since no OpenAI/Google keys exist yet, every
  seat resolves to its Anthropic fallback in practice -- one `.env` change
  away from real heterogeneity, no code changes needed. Live calling for
  non-Anthropic providers deliberately raises `NotImplementedError` rather
  than silently mis-calling the wrong API.
- **The resolution sweep** (`council/crypt/resolution.py`,
  `council/engine/resolution_sweep.py`, `python -m council resolve`) --
  bar-by-bar sequencing: a stop broken before the target is a loss even if
  price later reaches the target (spec: "be precise here, most systems get
  this wrong"). Computes direction_correct, entry/exit/invalidation hits in
  the order they actually occurred, MFE/MAE, and per-prediction Brier.
  Never modifies a prediction row -- resolutions are new, immutable rows.
- **The Calibration Officer** (`council/calibration/officer.py`) --
  per-seat hit rate, Brier score, log loss, and a calibration curve,
  derived independently of the council's own vote (a seat's own
  correctness is scored against its own vote, not `council_vote`). Every
  seat starts at weight 1.0; weight only moves after 20+ resolutions.
  Addendum A4 Stage 1 percentile selection: once 8+ seats qualify, the
  worst 30% by Brier get excluded (weight 0) for that deliberation --  a
  seat below the resolution floor is never excluded, regardless of cohort
  size.
- **Extremizing** (`council/engine/aggregation.py::extremize`) -- Addendum
  A4 Stage 3, log-odds extremizing. `Settings.extremize_alpha` defaults to
  1.0 (off, a no-op). Both `p_raw` and `p_extremized` are stored on every
  prediction regardless, so the Crypt can score which is better once
  there's enough resolved history to ask that question honestly.
- **Layered memory + decay** (`council/memory/`) -- shallow (5-day
  half-life: Technician, Catalyst Seer, Oracle of Options, Cross-Market),
  intermediate (45-day), and deep (400-day) layers matched to each seat's
  data type. Retrieval score = relevance x recency_weight x importance,
  capped at 200 events/seat with lowest-score eviction. The point-in-time
  guard covers memory too: `retrieve()` only returns events whose `as_of`
  is *strictly* earlier than the prediction being made -- a memory event
  from the future is exactly the kind of lookahead the rest of this system
  works to prevent.
- **The reflection loop** (`council/memory/reflection.py`) -- immediate
  reflection runs on every resolution: each participating seat is given
  only its own verdict and the realised outcome (nothing about what other
  seats said) and writes one concrete, actionable lesson with a hard
  vagueness filter ("I should be more careful" is rejected outright).
  Extended (monthly, per-seat bias summary) reflection is implemented and
  tested but never alters a stored verdict -- it can only be written to
  memory. Memory is genuinely wired into deliberation, not just plumbing
  that exists on paper: `python -m council deliberate` retrieves relevant
  memories before calling each seat, and a live end-to-end test proves a
  lesson from one resolved prediction gets applied on the next
  deliberation of the same ticker.

Not yet built (by design -- stopping here per the spec's phase order): the
UI (Phase 5), the discussion mechanism (Phase 7, deliberately gated behind
an A/B that doesn't exist yet), and actually calling a non-Anthropic
provider (the routing decision exists; the API call doesn't).

Known scope cuts (see inline comments): Fundamentals/AnalystEstimates/Macro
snapshots still don't enforce a filing-lag point-in-time guard the way the
dated feeds do -- a real gap, deferred again; it matters most for
historical backtesting, which this system doesn't do yet either. FMP
provider endpoints are unverified against a live key (none configured).
Cross-Market Navigator's sector/peer/index/overseas proxies all resolve to
the same `DEFAULT_ohlcv.json` fixture offline, so their values are
currently identical -- a fixture-mode artifact, not a code issue. Cost
Auditor's spread is an assumed constant, not a live quote. Risk Warden's
"correlation against other open positions" is a same-ticker concentration
check only, not a real cross-asset correlation matrix. Debate/Prosecutor/
reflection fixtures are static text (not horizon- or ticker-aware), so at
short horizons the Reality Anchor will often flag their fixed expected-move
numbers IMPLAUSIBLE and fail the base-rate gate -- a real, correct
audit-gate response to a fixture-mode limitation, not a bug (try
`--horizon 1d` vs `--horizon 1w` to see both paths). Memory relevance is a
same-ticker-or-not heuristic, not semantic/embedding-based -- a documented
simplification, not an oversight.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
cp .env.example .env   # optional -- empty is fine, runs in fixture mode
```

With no `ANTHROPIC_API_KEY` set, seat verdicts automatically come from
`council/tests/fixtures/seat_verdicts/` instead of a real LLM call. Market
data fixture mode (`council/tests/fixtures/market_data/`, NVDA only, for
now) is separate and only auto-forces when `FMP_API_KEY` is unset -- with
no keys at all, market data is still live by default via yfinance and SEC
EDGAR (both free, no key required); set `USE_DATA_FIXTURES=true`
explicitly if you want fully offline market data too. Set the keys in
`.env` to go live -- no code changes needed. Adding `OPENAI_API_KEY` /
`GOOGLE_API_KEY` similarly activates the seats routed to those providers in
`config/models.yaml`, with no code changes either.

## Run it

```bash
# Deliberate -- writes one row to the Crypt (blind + synthesized together)
.venv/bin/python -m council deliberate NVDA --horizon 1w

# Backdate it, so the resolution sweep has something to resolve without
# waiting real time to pass -- also demonstrates the point-in-time guards
# correctly narrowing what each seat can see at an earlier as_of
.venv/bin/python -m council deliberate NVDA --horizon 1w --as-of 2026-08-01T16:00:00

# Sweep every unresolved prediction whose resolve_at has passed: writes
# resolutions, runs immediate reflection into memory, prints a per-seat
# calibration snapshot
.venv/bin/python -m council resolve
```

## Test

```bash
.venv/bin/python -m pytest council/tests/ -q
```

## Run the web UI

```bash
.venv/bin/python -m uvicorn council.api.main:app --reload --port 8000
```

Open `http://localhost:8000` -- The Chamber, The Crypt, The Archives, The
Oracle, and The Guide are all served from `council/ui/`. `--reload` is a dev
convenience (restarts on file changes); drop it for anything long-running.

## Host it locally, always-on

Running it means it's only up while that command's terminal is open. To keep
it running in the background and reachable from other devices on your home
network:

1. **Bind to every interface, not just localhost.** `scripts/run-server.bat`
   (Windows) does this already -- `--host 0.0.0.0` instead of the default
   `127.0.0.1`. Find this machine's LAN IP with `ipconfig` (look for
   `IPv4 Address` under your active adapter); other devices on the same
   network reach it at `http://<that-IP>:8000`.
2. **Allow the port through Windows Firewall** the first time you run it --
   Windows will prompt automatically; allow it for Private networks.
3. **Auto-start at login, with no console window**, via Task Scheduler:
   - Task Scheduler -> Create Task -> Trigger: *At log on*.
   - Action: *Start a program* -> point it at
     `scripts\run-server-hidden.vbs` (uses `wscript.exe` to launch the
     `.bat` with a hidden window, so it starts silently).
   - Under Settings, consider "Restart if the task fails" for resilience
     across crashes.
4. **Schedule `council resolve` too**, on its own daily trigger (Action:
   *Start a program*, Program: `.venv\Scripts\python.exe`, Arguments:
   `-m council resolve`, Start in: the repo root) -- otherwise nothing
   scores past predictions and the Archives/calibration data never
   accumulates.

**Security note:** with `APP_PASSWORD` unset (the default), there is no
authentication on any of this -- anyone who can reach the port can run
deliberations (spending your configured API keys) and read every
prediction in the Crypt. That's an acceptable risk on your own home
network, where only devices you control can reach it. Do **not**
port-forward this out to the public internet without setting
`APP_PASSWORD` in `.env` first (see "Host it on the public internet,
for free" below) -- it's a single shared password gating the whole app,
not real per-user accounts, but it's the minimum needed before this is
reachable by strangers.

## Host it on the public internet, for free

Most "free" hosting doesn't actually fit this app: it needs a persistent
SQLite file (the Crypt -- your prediction history and calibration data),
long-lived Server-Sent-Events connections (the live Chamber view), and
outbound HTTPS to several different APIs. That rules out most serverless/
PaaS free tiers -- Render's free web services have an *ephemeral*
filesystem (any SQLite file is wiped on every redeploy or 15-minute-idle
spin-down), and PythonAnywhere's free tier restricts outbound requests to
an allowlist of approved APIs (Yahoo Finance, which `yfinance` depends on,
isn't an official public API and almost certainly isn't on it).

What actually fits: a real, always-on Linux VM. **Oracle Cloud's "Always
Free" tier** (currently 2 OCPUs / 12GB RAM, genuinely free forever, no
time limit -- sign up at oracle.com/cloud/free) is the best fit -- you run
this exactly like the local always-on setup above, just on a machine
that's always on with a public IP instead of your own PC. Signup/capacity
for the free ARM shape is known to be flaky; it can take a couple of
retries to get an instance provisioned in your region.

Once you have a fresh Ubuntu VM and can SSH into it:

```bash
git clone https://github.com/EthanMuir/jedi-council
cd jedi-council
cp .env.example .env   # fill in at minimum ANTHROPIC_API_KEY and APP_PASSWORD
bash scripts/setup-oracle-vm.sh
```

This installs Python, creates the venv, installs dependencies, and sets
the app up as a systemd service (`jedi-council`) that auto-restarts on
crash and starts on boot -- `sudo systemctl status jedi-council` /
`sudo journalctl -u jedi-council -f` to check on it. **`APP_PASSWORD` is
not optional here** -- the script warns and asks for confirmation if it's
blank, because a public IP with no password means anyone who finds it can
run deliberations billed to your API keys.

Two things Oracle-specific that the script can't do for you:
- **Open the port in your VCN's Security List** (Networking -> Virtual
  Cloud Networks -> your VCN -> Security Lists -> Add Ingress Rule, source
  `0.0.0.0/0`, TCP, port 8000). Oracle blocks incoming traffic at the
  network level by default, separately from the VM's own firewall.
- **Get HTTPS**, which matters more here than usual: without it, the
  password you just set is sent in cleartext on every login. Point any
  domain at the VM's public IP (a free subdomain from duckdns.org works
  fine) and run:
  ```bash
  bash scripts/setup-https.sh yourdomain.example.com
  ```
  This installs Caddy as a reverse proxy and gets you a free,
  auto-renewing Let's Encrypt certificate with no manual cert management.
  Afterwards, set `COOKIE_SECURE=true` in `.env` and restart the service
  so the session cookie can't leak over plain HTTP, and open ports 80/443
  in the Security List instead of (or alongside) 8000.

Finally, schedule `council resolve` to run daily so predictions actually
get scored -- a cron entry, run via `crontab -e`:

```
0 6 * * * cd /path/to/jedi-council && .venv/bin/python -m council resolve >> /tmp/council-resolve.log 2>&1
```
