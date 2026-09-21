"""python -m council deliberate NVDA --horizon 1w
python -m council resolve"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime

from council.config import get_settings
from council.crypt.db import connect
from council.crypt.export import fetch_predictions_for_export, write_csv, write_json
from council.engine.cost_estimate import CostEstimate, estimate_deliberation_cost
from council.engine.horizons import is_competent
from council.engine.orchestrator import (
    TIER_I_SEATS,
    DeliberationResult,
    build_data_service,
    run_deliberation,
)
from council.engine.resolution_sweep import sweep_unresolved


def _print_result(result: DeliberationResult) -> None:
    print(f"\n=== THE HIGH COUNCIL -- {result.ticker} @ {result.horizon} ===")
    print(f"as_of: {result.as_of.isoformat()}   price_at_prediction: {result.price_at_prediction}")
    print(f"resolve_at: {result.resolve_at.isoformat()}")
    print(f"prediction_id: {result.prediction_id}")

    print("\n--- PHASE A: BLIND ROUND ---")
    for sr in result.seat_results:
        v = sr.verdict
        print(f"\n  [{sr.seat_id}] {sr.title}")
        print(
            f"    vote: {v.vote}   probability: {v.probability}   data_quality: {v.data_quality}"
            f"   dispersion: {sr.dispersion} (n={sr.sample_count})"
        )
        if v.vote == "NO_READ":
            print(f"    abstain_reason: {v.abstain_reason}")
        else:
            print(
                f"    entry: {v.entry}   exit: {v.exit}   invalidation: {v.invalidation}"
                f"   expected_move: {v.expected_move_pct}%"
            )
        print(f"    thesis: {v.thesis}")
        if v.memory_applied:
            print(f"    memory_applied: {[m.lesson for m in v.memory_applied]}")
    print(
        f"\n  BLIND VOTE: {result.blind_vote}   probability={result.blind_probability}"
        f"   consensus={result.blind_consensus_pct}%"
    )

    print("\n--- PHASE B: REALITY ANCHOR ---")
    ra = result.reality_anchor
    print(
        f"  horizon={ra.horizon} ({ra.horizon_trading_days}td)   n_observations={ra.n_observations}"
        f"   hit_rate_up={ra.hit_rate_up}"
    )
    print(
        f"  realised_vol_annualised={ra.realised_vol_annualised_pct}%"
        f"   atr_implied_range={ra.atr_implied_range_pct}%"
        f"   max_plausible_move={ra.max_plausible_move_pct}%"
        f"   options_implied_move={ra.options_implied_move_pct}%"
    )
    implausible = [sid for sid, f in result.plausibility_flags.items() if f == "IMPLAUSIBLE"]
    print(f"  IMPLAUSIBLE targets: {implausible or 'none'}")

    print("\n--- PHASE C: DEBATE ---")
    for d in result.debate_transcript:
        rebut = f" [rebutting: {d.rebuts}]" if d.rebuts else ""
        print(f"\n  [{d.side} round {d.round_n}]{rebut}")
        print(f"    {d.argument}")
    for pv in result.prosecutor_verdicts:
        print(f"\n  [PROSECUTOR round {pv.round_n}] target={pv.target_direction} veto={pv.veto}")
        for f in pv.findings:
            print(f"    - ({f.category}) {f.description}")
        if pv.veto_reason:
            print(f"    veto_reason: {pv.veto_reason}")
    print(f"\n  Correlated evidence: {result.correlated_evidence or 'none'}")
    if result.incoherent_decompositions:
        print("  INCOHERENT_CONFIDENCE (decomposition doesn't support headline number):")
        for sid, r in result.incoherent_decompositions.items():
            print(f"    - {sid}: stated {r.stated_probability}, implied {r.implied_probability}")

    print("\n--- PHASE D/E: WEIGHTED VOTE + AUDIT GATES ---")
    wv_vote, wv_conf, wv_consensus = result.weighted_vote_result
    print(f"  Weighted vote: {wv_vote}   confidence={wv_conf}   consensus={wv_consensus}%")
    print(f"  p_raw={result.p_raw}   p_extremized={result.p_extremized}")
    print(f"  Cost Auditor: edge={result.cost_audit.edge_pct}%   passed={result.cost_audit.passed}")
    print(f"  Gates passed: {result.gates_passed}")
    if result.gate_failure_reasons:
        for reason in result.gate_failure_reasons:
            print(f"    - {reason}")

    print("\n--- RISK WARDEN (sizing only, never sees the vote) ---")
    rs = result.risk_sizing
    print(
        f"  position_size={rs.position_size_pct_of_book}% of book"
        f"   (risk_budget={rs.risk_budget_pct}%, kelly_cap={rs.kelly_cap_pct}%,"
        f" vol_estimate={rs.vol_estimate_pct}%)"
    )
    if rs.concentration_warning:
        print(f"  WARNING: {rs.concentration_warning}")

    print("\n--- PHASE F: GRAND MASTER SYNTHESIS ---")
    gm = result.grand_master_verdict
    print(f"  VERDICT: {gm.vote}   confidence={gm.confidence}   expected_move={gm.expected_move_pct}%")
    if gm.entry is not None:
        print(f"  entry: {gm.entry}   exit: {gm.exit}   invalidation: {gm.invalidation}   stop: {gm.stop}")
    print(f"  dissent_summary: {gm.dissent_summary}")
    print(f"  correlated_evidence_warning: {gm.correlated_evidence_warning}")
    print(f"  reasoning: {gm.reasoning}")

    total_cost = sum(c.cost_usd for c in result.call_log)
    print(f"\nLLM calls: {len(result.call_log)}   total estimated cost: ${total_cost:.4f}\n")


def _print_cost_estimate(estimate: CostEstimate) -> None:
    print(f"\n=== DRY RUN -- estimated cost for {estimate.ticker} @ {estimate.horizon} ===")
    print("(no network calls made -- rough per-call token constants, not measured usage)\n")
    for li in estimate.line_items:
        print(
            f"  {li.label:<32} model={li.model:<18} calls={li.call_count:<3}"
            f" ~tokens_in={li.est_input_tokens:<6} ~tokens_out={li.est_output_tokens:<6}"
            f" ~${li.est_cost_usd:.4f}"
        )
    print(f"\n  total calls: {estimate.total_calls}")
    print(f"  total estimated cost: ${estimate.total_cost_usd:.4f}\n")


def _print_sweep_results(settings, swept) -> None:
    print(f"\n=== THE CRYPT -- resolution sweep ===")
    if not swept:
        print("  Nothing to resolve -- no unresolved prediction has a passed resolve_at.\n")
        return
    for s in swept:
        mark = "amber (correct)" if s.direction_correct else (
            "grey (n/a)" if s.direction_correct is None else "crimson (wrong)"
        )
        print(
            f"  [{s.ticker} {s.horizon}] {s.council_vote} -> realised {s.realised_move_pct:+.2f}%"
            f"   {mark}   lessons_written={s.lessons_written}"
        )

    print("\n--- CALIBRATION SNAPSHOT (per seat, all horizons) ---")
    from council.calibration.officer import compute_seat_calibration
    from council.crypt.db import connect

    conn = connect(settings.council_db_path)
    for seat in TIER_I_SEATS:
        calib = compute_seat_calibration(conn, seat.id)
        if calib.n_resolutions == 0:
            continue
        gate = "OK" if calib.n_resolutions >= settings.calibration_min_resolutions else (
            f"{calib.n_resolutions}/{settings.calibration_min_resolutions} until weight can move"
        )
        print(
            f"  [{seat.id}] n={calib.n_resolutions}   hit_rate={calib.hit_rate}"
            f"   brier={calib.brier_score}   log_loss={calib.log_loss}   ({gate})"
        )
    conn.close()
    print()


async def _dump_data(ticker: str, horizon: str, settings) -> None:
    """Runs each competent seat's real gather() and prints exactly what
    came back -- no LLM call anywhere in this path, fixture or live. This
    is the only way to actually verify the data layer works when
    NO_LLM=true, since every seat's thesis/probability is a canned
    placeholder in that mode regardless of what gather() actually fetched."""
    import json

    from council.api.serialize import to_jsonable

    data_service = build_data_service(settings)
    as_of = datetime.utcnow()
    eligible = [s for s in TIER_I_SEATS if is_competent(s.id, horizon)]

    print(f"\n=== RAW DATA DUMP -- {ticker} @ {horizon} (no LLM call made, fixture or live) ===")
    if len(eligible) < len(TIER_I_SEATS):
        skipped = [s.id for s in TIER_I_SEATS if s not in eligible]
        print(f"(not competent at this horizon, skipped: {', '.join(skipped)})")

    for seat in eligible:
        print(f"\n--- [{seat.id}] {seat.title} ---")
        try:
            ctx = await seat.gather(data_service, ticker, as_of, horizon)
        except Exception as exc:  # noqa: BLE001 -- show every seat's result, don't stop at the first failure
            print(f"  FAILED: {exc}")
            continue
        for key in sorted(seat.allowed_data):
            if key not in ctx:
                print(f"  {key}: <missing>")
                continue
            print(f"  {key}:")
            print(json.dumps(to_jsonable(ctx.get(key)), indent=2, default=str))
    print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="council")
    sub = parser.add_subparsers(dest="command", required=True)

    deliberate = sub.add_parser("deliberate", help="Run a full council deliberation for a ticker")
    deliberate.add_argument("ticker")
    deliberate.add_argument("--horizon", choices=["1d", "1w", "1m", "1y"], required=True)
    deliberate.add_argument(
        "--as-of",
        default=None,
        help="ISO datetime to backdate the deliberation to (e.g. 2026-08-01T16:00:00), "
        "for demoing the deliberate -> resolve lifecycle without waiting real time to pass.",
    )
    deliberate.add_argument(
        "--dry-run-cost",
        action="store_true",
        help="Print an estimated $ cost for this deliberation and exit -- no network calls, "
        "no Crypt write, just call-count x rough token constants x model pricing.",
    )

    sub.add_parser("resolve", help="Sweep unresolved predictions whose resolve_at has passed")

    export = sub.add_parser("export", help="Export predictions + resolutions from the Crypt")
    export.add_argument("--format", choices=["csv", "json"], required=True)
    export.add_argument("--out", required=True, help="Output file path")
    export.add_argument("--ticker", default=None)
    export.add_argument("--horizon", choices=["1d", "1w", "1m", "1y"], default=None)

    inspect_data = sub.add_parser(
        "inspect-data",
        help="Dump each seat's raw gathered data for a ticker -- no LLM call at all, fixture "
        "or live, so this is the only way to verify the data layer works when NO_LLM=true "
        "(every seat's thesis/probability is a canned placeholder in that mode regardless of "
        "what was actually fetched).",
    )
    inspect_data.add_argument("ticker")
    inspect_data.add_argument("--horizon", choices=["1d", "1w", "1m", "1y"], required=True)

    args = parser.parse_args(argv)
    settings = get_settings()
    settings.ensure_dirs()

    if args.command == "deliberate":
        if args.dry_run_cost:
            estimate = estimate_deliberation_cost(args.ticker.upper(), args.horizon, settings)
            _print_cost_estimate(estimate)
            return
        as_of = datetime.fromisoformat(args.as_of) if args.as_of else None
        result = asyncio.run(run_deliberation(args.ticker.upper(), args.horizon, settings, as_of=as_of))
        _print_result(result)
    elif args.command == "resolve":
        swept = asyncio.run(sweep_unresolved(settings))
        _print_sweep_results(settings, swept)
    elif args.command == "export":
        conn = connect(settings.council_db_path)
        try:
            rows = fetch_predictions_for_export(
                conn, ticker=args.ticker.upper() if args.ticker else None, horizon=args.horizon
            )
        finally:
            conn.close()
        if args.format == "csv":
            write_csv(rows, args.out)
        else:
            write_json(rows, args.out)
        print(f"Exported {len(rows)} prediction(s) to {args.out}")
    elif args.command == "inspect-data":
        asyncio.run(_dump_data(args.ticker.upper(), args.horizon, settings))


if __name__ == "__main__":
    main()
