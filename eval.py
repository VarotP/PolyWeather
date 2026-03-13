"""Evaluation metrics for settled paper trades."""

from __future__ import annotations

import sys
from collections import defaultdict

import numpy as np

from config import STAKE_USD
from papertrade_store import read_trades


def _settled_trades() -> list[dict]:
    return [r for r in read_trades() if r.get("status") == "SETTLED"]


def _safe_float(val: str, default: float = 0.0) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def compute_metrics(trades: list[dict]) -> dict | None:
    """Compute aggregate metrics over a list of settled trade dicts."""
    if not trades:
        return None

    n = len(trades)
    wins = [int(r.get("win", 0)) for r in trades]
    pnls = [_safe_float(r.get("pnl_usd")) for r in trades]
    p_models = [_safe_float(r.get("p_model")) for r in trades]
    p_markets = [_safe_float(r.get("p_market")) for r in trades]
    edges = [_safe_float(r.get("edge")) for r in trades]

    win_arr = np.array(wins, dtype=float)
    pnl_arr = np.array(pnls)
    pm_arr = np.array(p_models)
    pk_arr = np.array(p_markets)
    edge_arr = np.array(edges)

    brier_model = float(np.mean((pm_arr - win_arr) ** 2))
    brier_market = float(np.mean((pk_arr - win_arr) ** 2))

    win_idx = win_arr == 1
    lose_idx = win_arr == 0

    return {
        "count": n,
        "win_rate": float(np.mean(win_arr)),
        "total_pnl": float(np.sum(pnl_arr)),
        "avg_pnl": float(np.mean(pnl_arr)),
        "pnl_std": float(np.std(pnl_arr)) if n > 1 else 0.0,
        "roi": float(np.sum(pnl_arr)) / (n * STAKE_USD) if n else 0.0,
        "brier_model": brier_model,
        "brier_market": brier_market,
        "avg_edge_winners": (
            float(np.mean(edge_arr[win_idx])) if win_idx.any() else None
        ),
        "avg_edge_losers": (
            float(np.mean(edge_arr[lose_idx])) if lose_idx.any() else None
        ),
    }


def _fmt(val, fmt=".3f", none_str="—") -> str:
    if val is None:
        return none_str
    return f"{val:{fmt}}"


def _print_block(label: str, m: dict) -> None:
    print(f"\n  {label}  (n={m['count']})")
    print(f"    Win rate     {m['win_rate']:.1%}")
    print(f"    Total P&L    ${m['total_pnl']:+,.2f}")
    print(f"    Avg P&L      ${m['avg_pnl']:+,.2f}   (σ ${m['pnl_std']:,.2f})")
    print(f"    ROI          {m['roi']:.1%}")
    print(f"    Brier model  {m['brier_model']:.4f}")
    print(f"    Brier market {m['brier_market']:.4f}")
    ew = _fmt(m["avg_edge_winners"])
    el = _fmt(m["avg_edge_losers"])
    print(f"    Avg edge     winners={ew}  losers={el}")


def print_eval_report() -> None:
    """Print full evaluation to stdout."""
    trades = _settled_trades()
    if not trades:
        print("No settled trades to evaluate.")
        return

    overall = compute_metrics(trades)
    if overall is None:
        return

    open_count = sum(1 for r in read_trades() if r.get("status") == "OPEN")

    print()
    print("═" * 60)
    print("  Paper-Trade Evaluation Report")
    print("─" * 60)
    print(f"  Settled: {overall['count']}    Open: {open_count}")

    _print_block("ALL CITIES", overall)

    # Per-city breakdown
    by_city: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_city[t["city"]].append(t)

    for city in sorted(by_city):
        m = compute_metrics(by_city[city])
        if m:
            _print_block(city.upper(), m)

    print()
    print("═" * 60)
