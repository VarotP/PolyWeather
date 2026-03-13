"""Shared configuration for the paper-trading system."""

from pathlib import Path

# ── Cities to trade ──────────────────────────────────────────────────────────
CITIES: list[str] = ["seattle", "chicago", "london", "atlanta"]

# ── Station bias offsets (applied to GEFS highs before rounding) ─────────────
# Key = city slug, value = offset in the market's unit (°F or °C).
# Positive = warm bias correction, negative = cold bias correction.
CITY_OFFSETS: dict[str, float] = {
    "chicago": -4.0,   # °F — KORD gridpoint runs warm vs station obs
}

# ── Strategy thresholds ──────────────────────────────────────────────────────
EDGE_THRESHOLD: float = 0.10   # minimum edge (model − market) to enter
MIN_PRICE: float = 0.02        # floor price to avoid extreme share counts
STAKE_USD: float = 100.0       # fixed USD notional per trade
MIN_VOLUME: float = 20_000     # skip market if reported volume < this (0 = off)

# ── File paths ───────────────────────────────────────────────────────────────
CACHE_DIR = Path.home() / ".cache" / "polymarket_gefs"
TRADE_DIR = CACHE_DIR / "papertrades"
TRADES_CSV = TRADE_DIR / "trades.csv"
RUNS_JSONL = TRADE_DIR / "runs.jsonl"
