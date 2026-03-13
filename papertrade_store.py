"""CSV-backed store for paper trades.

The file is plain CSV with a fixed column order.  New trades are appended;
settlement updates rewrite the whole file (acceptable at this scale).
"""

from __future__ import annotations

import csv
import fcntl
from pathlib import Path

from config import TRADE_DIR, TRADES_CSV

COLUMNS: list[str] = [
    "timestamp_utc",
    "city",
    "tz",
    "target_date",
    "event_slug",
    "market_question",
    "unit",
    "selected_outcome_title",
    "outcome_interval_lo",
    "outcome_interval_hi",
    "p_model",
    "p_market",
    "edge",
    "ensemble_n",
    "ensemble_mean",
    "ensemble_p10",
    "ensemble_p50",
    "ensemble_p90",
    "ensemble_spread",
    "entry_price",
    "stake_usd",
    "shares",
    "status",
    "resolved_outcome_title",
    "win",
    "pnl_usd",
]


def _ensure_dir() -> None:
    TRADE_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_header() -> None:
    _ensure_dir()
    if not TRADES_CSV.exists() or TRADES_CSV.stat().st_size == 0:
        with open(TRADES_CSV, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()


def read_trades() -> list[dict]:
    """Return every row as an ordered dict.  Empty list if file missing."""
    if not TRADES_CSV.exists():
        return []
    with open(TRADES_CSV, newline="") as f:
        return list(csv.DictReader(f))


def append_trade(row: dict) -> None:
    """Append a single trade row, creating the file + header if needed."""
    _ensure_header()
    with open(TRADES_CSV, "a", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writerow(row)
        fcntl.flock(f, fcntl.LOCK_UN)


def rewrite_trades(rows: list[dict]) -> None:
    """Overwrite the CSV with *rows* (used by settlement to flip OPEN→SETTLED)."""
    _ensure_header()
    with open(TRADES_CSV, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
        fcntl.flock(f, fcntl.LOCK_UN)


def trade_exists(city: str, target_date: str) -> bool:
    """True if we already logged a trade for this city + date."""
    for row in read_trades():
        if row["city"] == city and row["target_date"] == target_date:
            return True
    return False
