"""python -m council deliberate NVDA --horizon 1w"""
from __future__ import annotations

import argparse
import asyncio

from council.config import get_settings
from council.engine.orchestrator import DeliberationResult, run_blind_round


def _print_result(result: DeliberationResult) -> None:
    print(f"\n=== THE HIGH COUNCIL -- blind round: {result.ticker} @ {result.horizon} ===")
    print(f"as_of: {result.as_of.isoformat()}   price_at_prediction: {result.price_at_prediction}")
    print(f"resolve_at: {result.resolve_at.isoformat()}")
    print(f"prediction_id: {result.prediction_id}")

    for sr in result.seat_results:
        v = sr.verdict
        print(f"\n--- {sr.title} ({sr.seat_id}) ---")
        print(f"  vote: {v.vote}   probability: {v.probability}   data_quality: {v.data_quality}")
        if v.vote == "NO_READ":
            print(f"  abstain_reason: {v.abstain_reason}")
        else:
            print(
                f"  entry: {v.entry}   exit: {v.exit}   invalidation: {v.invalidation}"
                f"   expected_move: {v.expected_move_pct}%"
            )
            if v.comparison_class:
                print(
                    f"  comparison_class: {v.comparison_class.definition} "
                    f"(n={v.comparison_class.n_observations}, "
                    f"base_rate={v.comparison_class.base_rate})"
                )
        print(f"  thesis: {v.thesis}")

    print(
        f"\nBLIND COUNCIL VOTE: {result.blind_vote}   "
        f"probability={result.blind_probability}   consensus={result.blind_consensus_pct}%"
    )
    total_cost = sum(c.cost_usd for c in result.call_log)
    print(f"LLM calls: {len(result.call_log)}   total estimated cost: ${total_cost:.4f}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="council")
    sub = parser.add_subparsers(dest="command", required=True)

    deliberate = sub.add_parser("deliberate", help="Run a blind-round deliberation for a ticker")
    deliberate.add_argument("ticker")
    deliberate.add_argument("--horizon", choices=["1d", "1w", "1m", "1y"], required=True)

    args = parser.parse_args(argv)
    settings = get_settings()
    settings.ensure_dirs()

    if args.command == "deliberate":
        result = asyncio.run(run_blind_round(args.ticker.upper(), args.horizon, settings))
        _print_result(result)


if __name__ == "__main__":
    main()
