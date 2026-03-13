"""Settlement logic — resolves OPEN paper trades via Polymarket Gamma API.

The single source of truth for who won is the Gamma event: after resolution
each child market's ``outcomePrices`` is ``["1","0"]`` (winner) or
``["0","1"]`` (loser), and ``closed`` is True on every market.
"""

from __future__ import annotations

import json
import sys

from config import STAKE_USD
from papertrade_store import read_trades, rewrite_trades
from polymarket_gefs import fetch_event_fresh


def _winner_from_event(event: dict) -> str | None:
    """Return the winning ``groupItemTitle`` or *None* if not yet resolved."""
    markets = event.get("markets", [])
    if not markets:
        return None

    # All child markets must be closed for us to trust the result.
    if not all(m.get("closed", False) for m in markets):
        return None

    for mkt in markets:
        prices = mkt.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        if not prices:
            continue
        try:
            yes_price = float(prices[0])
        except (ValueError, TypeError):
            continue
        if yes_price >= 0.95:
            return mkt.get("groupItemTitle", "")

    return None


def settle_open_trades(*, verbose: bool = False) -> int:
    """Try to settle every OPEN trade.  Returns count of newly settled."""
    rows = read_trades()
    if not rows:
        if verbose:
            print("No trades in log.")
        return 0

    changed = 0
    for row in rows:
        if row.get("status") != "OPEN":
            continue

        slug = row.get("event_slug", "")
        if not slug:
            continue

        if verbose:
            print(f"  Checking {row['city']} {row['target_date']} …", end=" ")

        event = fetch_event_fresh(slug)
        if event is None:
            if verbose:
                print("API error, skipping.")
            continue

        winner = _winner_from_event(event)
        if winner is None:
            if verbose:
                print("not yet resolved.")
            continue

        selected = row.get("selected_outcome_title", "")
        win = 1 if selected == winner else 0
        stake = float(row.get("stake_usd", STAKE_USD))
        entry = float(row.get("entry_price", 0))
        shares = float(row.get("shares", 0))

        if win:
            pnl = shares * 1.0 - stake
        else:
            pnl = -stake

        row["status"] = "SETTLED"
        row["resolved_outcome_title"] = winner
        row["win"] = str(win)
        row["pnl_usd"] = f"{pnl:.2f}"
        changed += 1

        if verbose:
            tag = "WIN" if win else "LOSS"
            print(f"{tag}  resolved={winner!r}  pnl=${pnl:+.2f}")

    if changed:
        rewrite_trades(rows)

    return changed
