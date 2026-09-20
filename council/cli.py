"""python -m council deliberate NVDA --horizon 1w"""
from __future__ import annotations

import argparse
import asyncio

from council.config import get_settings
from council.engine.orchestrator import DeliberationResult, run_deliberation


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

    print("\n--- PHASE D/E: WEIGHTED VOTE + AUDIT GATES ---")
    wv_vote, wv_conf, wv_consensus = result.weighted_vote_result
    print(f"  Weighted vote: {wv_vote}   confidence={wv_conf}   consensus={wv_consensus}%")
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


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="council")
    sub = parser.add_subparsers(dest="command", required=True)

    deliberate = sub.add_parser("deliberate", help="Run a full council deliberation for a ticker")
    deliberate.add_argument("ticker")
    deliberate.add_argument("--horizon", choices=["1d", "1w", "1m", "1y"], required=True)

    args = parser.parse_args(argv)
    settings = get_settings()
    settings.ensure_dirs()

    if args.command == "deliberate":
        result = asyncio.run(run_deliberation(args.ticker.upper(), args.horizon, settings))
        _print_result(result)


if __name__ == "__main__":
    main()
