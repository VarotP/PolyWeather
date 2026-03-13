#!/usr/bin/env python3
"""Paper-trading CLI for Polymarket temperature markets.

Usage:
    polymarket_papertrade.py --run              # open new trades (daily 19:15 UTC)
    polymarket_papertrade.py --settle           # settle resolved trades (hourly)
    polymarket_papertrade.py --eval             # print evaluation metrics
    polymarket_papertrade.py --run --verbose    # verbose diagnostics
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from config import (
    CITIES,
    EDGE_THRESHOLD,
    MIN_PRICE,
    MIN_VOLUME,
    RUNS_JSONL,
    STAKE_USD,
    TRADE_DIR,
)
from eval import print_eval_report
from papertrade_store import append_trade, trade_exists
from polymarket_gefs import (
    SLUG_DB,
    compute_gefs_probs,
    find_market,
    parse_outcome_intervals,
)
from settle import settle_open_trades


# ── Helpers ──────────────────────────────────────────────────────────────────

def _tomorrow_for_city(tz_name: str) -> date:
    """The calendar date of 'tomorrow' in the city's local timezone."""
    now_local = datetime.now(ZoneInfo(tz_name))
    return (now_local + timedelta(days=1)).date()


def _interval_str(val: float) -> str:
    if math.isinf(val) and val < 0:
        return "-inf"
    if math.isinf(val) and val > 0:
        return "+inf"
    return str(int(val)) if val == int(val) else str(val)


def _log_run(entry: dict) -> None:
    """Append a diagnostics line to runs.jsonl."""
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    with open(RUNS_JSONL, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Run strategy ─────────────────────────────────────────────────────────────

def run_strategy(*, verbose: bool = False) -> int:
    """Execute the paper-trading strategy for each city."""
    ts = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
    trades_opened = 0

    for city_slug in CITIES:
        city_info = SLUG_DB.get(city_slug)
        if city_info is None:
            print(f"  [{city_slug}] SKIP — unknown city slug", file=sys.stderr)
            continue

        tz_name = city_info["tz"]
        target = _tomorrow_for_city(tz_name)

        print(f"\n{'─'*60}")
        print(f"  {city_slug.upper()}  target_date={target}  tz={tz_name}")
        print(f"{'─'*60}")

        # Duplicate guard
        if trade_exists(city_slug, target.isoformat()):
            print(f"  SKIP — trade already exists for {city_slug} {target}")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_DUPLICATE"})
            continue

        # Fetch market
        event = find_market(city_slug, target)
        if event is None:
            print(f"  SKIP — no market found")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_NO_MARKET"})
            continue

        event_slug = event.get("slug", "")
        title = event.get("title", "")
        volume = event.get("volume", 0)
        try:
            volume = float(volume)
        except (TypeError, ValueError):
            volume = 0.0

        # Volume filter
        if MIN_VOLUME > 0 and volume > 0 and volume < MIN_VOLUME:
            print(f"  SKIP — volume ${volume:,.0f} < ${MIN_VOLUME:,.0f}")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_LOW_VOLUME", "volume": volume})
            continue

        # Parse outcomes
        parsed = parse_outcome_intervals(event)
        if parsed is None:
            print(f"  SKIP — could not parse outcome intervals")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_PARSE_FAIL"})
            continue

        unit, labels, intervals, market_probs = parsed

        if len(labels) != len(market_probs):
            print(f"  SKIP — outcomes/prices length mismatch")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_LENGTH_MISMATCH"})
            continue

        # Compute GEFS model probabilities
        gefs = compute_gefs_probs(city_slug, target, unit, intervals)
        if gefs is None:
            print(f"  SKIP — GEFS data unavailable")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "SKIP_GEFS_FAIL"})
            continue

        p_model = gefs["probs"]
        edges = [pm - pk for pm, pk in zip(p_model, market_probs)]

        # Print full bin table
        if verbose:
            print(f"  {'Outcome':<20s}  {'Market':>7s} {'Model':>7s} {'Edge':>7s}")
            for i, lab in enumerate(labels):
                print(
                    f"  {lab:<20s}  {market_probs[i]*100:>6.1f}% "
                    f"{p_model[i]*100:>6.1f}% {edges[i]*100:>+6.1f}%"
                )
            print(
                f"  Ensemble: n={gefs['ensemble_n']}  "
                f"μ={gefs['mean']:.1f}  p10={gefs['p10']:.1f}  "
                f"p50={gefs['p50']:.1f}  p90={gefs['p90']:.1f}  "
                f"spread={gefs['spread']:.1f}"
            )

        # Select best edge
        best_i = int(np.argmax(edges))
        best_edge = edges[best_i]
        best_label = labels[best_i]
        best_market_p = market_probs[best_i]
        best_model_p = p_model[best_i]
        lo, hi = intervals[best_i]

        print(f"  Best edge: {best_label!r}  "
              f"model={best_model_p:.1%}  market={best_market_p:.1%}  "
              f"edge={best_edge:+.1%}")

        # Entry criteria
        if best_edge < EDGE_THRESHOLD:
            print(f"  NO TRADE — edge {best_edge:.1%} < threshold {EDGE_THRESHOLD:.0%}")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "NO_TRADE_EDGE", "best_edge": best_edge,
                       "best_outcome": best_label})
            continue

        if best_market_p < MIN_PRICE:
            print(f"  NO TRADE — market price {best_market_p:.3f} < floor {MIN_PRICE}")
            _log_run({"ts": ts, "city": city_slug, "target_date": str(target),
                       "action": "NO_TRADE_PRICE", "best_price": best_market_p})
            continue

        # Size the trade
        entry_price = best_market_p
        shares = STAKE_USD / entry_price

        row = {
            "timestamp_utc": ts,
            "city": city_slug,
            "tz": tz_name,
            "target_date": target.isoformat(),
            "event_slug": event_slug,
            "market_question": title,
            "unit": unit,
            "selected_outcome_title": best_label,
            "outcome_interval_lo": _interval_str(lo),
            "outcome_interval_hi": _interval_str(hi),
            "p_model": f"{best_model_p:.4f}",
            "p_market": f"{best_market_p:.4f}",
            "edge": f"{best_edge:.4f}",
            "ensemble_n": str(gefs["ensemble_n"]),
            "ensemble_mean": str(gefs["mean"]),
            "ensemble_p10": str(gefs["p10"]),
            "ensemble_p50": str(gefs["p50"]),
            "ensemble_p90": str(gefs["p90"]),
            "ensemble_spread": str(gefs["spread"]),
            "entry_price": f"{entry_price:.4f}",
            "stake_usd": f"{STAKE_USD:.2f}",
            "shares": f"{shares:.2f}",
            "status": "OPEN",
            "resolved_outcome_title": "",
            "win": "",
            "pnl_usd": "",
        }

        append_trade(row)
        trades_opened += 1

        print(
            f"  TRADE OPENED  buy {best_label!r}  "
            f"${STAKE_USD:.0f} @ {entry_price:.3f}  "
            f"({shares:.1f} shares)"
        )

        _log_run({
            "ts": ts, "city": city_slug, "target_date": str(target),
            "action": "TRADE_OPENED", "outcome": best_label,
            "edge": best_edge, "entry_price": entry_price,
            "bins": {lab: {"model": p_model[j], "market": market_probs[j]}
                     for j, lab in enumerate(labels)},
            "ensemble": {k: gefs[k] for k in
                         ("ensemble_n", "mean", "p10", "p50", "p90", "spread")},
        })

    print(f"\nDone — {trades_opened} trade(s) opened.\n")
    return 0


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Paper-trade Polymarket temperature markets using GEFS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Cron schedule (example):\n"
            "  15 19 * * *  python3 /path/to/polymarket_papertrade.py --run\n"
            "   0  * * * *  python3 /path/to/polymarket_papertrade.py --settle\n"
            "  30 19 * * *  python3 /path/to/polymarket_papertrade.py --eval\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--run", action="store_true", default=True,
        help="Run strategy and open paper trades (default action)",
    )
    group.add_argument(
        "--settle", action="store_true",
        help="Settle OPEN trades using Polymarket resolution",
    )
    group.add_argument(
        "--eval", action="store_true",
        help="Print evaluation metrics for settled trades",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose diagnostics",
    )

    args = parser.parse_args()

    if args.settle:
        print("Settling open trades …")
        n = settle_open_trades(verbose=args.verbose or True)
        print(f"Settled {n} trade(s).")
        return 0

    if args.eval:
        print_eval_report()
        return 0

    # --run (default)
    return run_strategy(verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
