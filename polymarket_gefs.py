#!/usr/bin/env python3
"""GEFS ensemble probabilities for Polymarket temperature markets.

Fetches Polymarket "Highest temperature in {CITY} on {DATE}?" markets,
pulls GEFS ensemble 2m-temperature forecasts via Open-Meteo, computes
per-bin probabilities, and prints a comparison report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests

from config import CITY_OFFSETS

# ── Configuration ────────────────────────────────────────────────────────────

GAMMA_API = "https://gamma-api.polymarket.com"
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"
CACHE_DIR = Path.home() / ".cache" / "polymarket_gefs"
CACHE_TTL_MARKETS = 300     # seconds
CACHE_TTL_GEFS = 3600       # seconds
REQUEST_TIMEOUT = 20         # seconds
BAR_WIDTH = 20               # max histogram bar width

# ── City database ────────────────────────────────────────────────────────────
# name -> {lat, lon, tz, slug}   (slug matches Polymarket event URLs)

_CITY_RAW: list[tuple[str, float, float, str, str]] = [
    # (slug, lat, lon, timezone)
    # Coordinates are the Polymarket resolution weather stations (airport ICAO),
    # NOT city centres, so the GEFS nearest-gridpoint matches the measurement site.
    ("nyc",            40.7772,  -73.8726, "America/New_York"),      # KLGA LaGuardia
    ("atlanta",        33.6407,  -84.4277, "America/New_York"),      # KATL Hartsfield-Jackson
    ("chicago",        41.9742,  -87.9073, "America/Chicago"),       # KORD O'Hare
    ("la",             33.9425, -118.4081, "America/Los_Angeles"),    # KLAX LAX
    ("miami",          25.7959,  -80.2870, "America/New_York"),      # KMIA Miami Intl
    ("dallas",         32.8471,  -96.8518, "America/Chicago"),       # KDAL Love Field
    ("houston",        29.9844,  -95.3414, "America/Chicago"),       # KIAH Bush Intercontinental
    ("phoenix",        33.4373, -112.0078, "America/Phoenix"),       # KPHX Sky Harbor
    ("denver",         39.8561, -104.6737, "America/Denver"),        # KDEN Denver Intl
    ("seattle",        47.4489, -122.3094, "America/Los_Angeles"),   # KSEA Sea-Tac
    ("san-francisco",  37.6213, -122.3790, "America/Los_Angeles"),   # KSFO SFO
    ("boston",          42.3656,  -71.0096, "America/New_York"),      # KBOS Logan
    ("washington-dc",  38.8512,  -77.0402, "America/New_York"),      # KDCA Reagan National
    ("paris",          49.0097,    2.5479, "Europe/Paris"),           # LFPG Charles de Gaulle
    ("london",         51.5053,    0.0553, "Europe/London"),          # EGLC London City
    ("tokyo",          35.5494,  139.7798, "Asia/Tokyo"),             # RJTT Haneda
    ("sydney",        -33.9461,  151.1772, "Australia/Sydney"),       # YSSY Kingsford Smith
    ("berlin",         52.5597,   13.2877, "Europe/Berlin"),          # EDDT Tegel / EDDL
    ("toronto",        43.6772,  -79.6306, "America/Toronto"),       # CYYZ Pearson
    ("rome",           41.8003,   12.2389, "Europe/Rome"),            # LIRF Fiumicino
    ("madrid",         40.4719,   -3.5626, "Europe/Madrid"),          # LEMD Barajas
    ("portland",       45.5887, -122.5975, "America/Los_Angeles"),   # KPDX
    ("austin",         30.1945,  -97.6699, "America/Chicago"),       # KAUS Austin-Bergstrom
    ("nashville",      36.1245,  -86.6782, "America/Chicago"),       # KBNA
    ("las-vegas",      36.0840, -115.1537, "America/Los_Angeles"),   # KLAS McCarran
    ("minneapolis",    44.8848,  -93.2223, "America/Chicago"),       # KMSP
    ("detroit",        42.2124,  -83.3534, "America/Detroit"),        # KDTW
    ("philadelphia",   39.8721,  -75.2411, "America/New_York"),      # KPHL
    ("san-diego",      32.7336, -117.1897, "America/Los_Angeles"),   # KSAN
    ("orlando",        28.4312,  -81.3081, "America/New_York"),      # KMCO
    ("tampa",          27.9755,  -82.5332, "America/New_York"),      # KTPA
    ("salt-lake-city", 40.7884, -111.9778, "America/Denver"),        # KSLC
    ("sacramento",     38.6955, -121.5908, "America/Los_Angeles"),   # KSMF
    ("new-orleans",    29.9934,  -90.2580, "America/Chicago"),       # KMSY
    ("charlotte",      35.2144,  -80.9473, "America/New_York"),      # KCLT
    ("columbus",       39.9980,  -82.8919, "America/New_York"),      # KCMH
    ("jacksonville",   30.4941,  -81.6879, "America/New_York"),      # KJAX
    ("memphis",        35.0424,  -89.9767, "America/Chicago"),       # KMEM
    ("oklahoma-city",  35.3931,  -97.6007, "America/Chicago"),       # KOKC
    ("st-louis",       38.7487,  -90.3700, "America/Chicago"),       # KSTL
    ("milwaukee",      42.9472,  -87.8966, "America/Chicago"),       # KMKE
    ("raleigh",        35.8776,  -78.7875, "America/New_York"),      # KRDU
    ("kansas-city",    39.2976,  -94.7139, "America/Chicago"),       # KMCI
    ("indianapolis",   39.7173,  -86.2944, "America/Indiana/Indianapolis"), # KIND
    ("pittsburgh",     40.4915,  -80.2329, "America/New_York"),      # KPIT
    ("buffalo",        42.9405,  -78.7322, "America/New_York"),      # KBUF
    ("anchorage",      61.1743, -149.9962, "America/Anchorage"),     # PANC
    ("honolulu",       21.3187, -157.9225, "Pacific/Honolulu"),      # PHNL
    ("mexico-city",    19.4363,  -99.0721, "America/Mexico_City"),   # MMMX
    ("mumbai",         19.0896,   72.8656, "Asia/Kolkata"),           # VABB
    ("wellington",    -41.3278,  174.8053, "Pacific/Auckland"),       # NZWN Wellington Intl
    ("seoul",          37.4691,  126.4510, "Asia/Seoul"),              # RKSI Incheon Intl
]

# Build lookup tables
SLUG_DB: dict[str, dict] = {}
ALIAS_DB: dict[str, str] = {}  # alias -> slug

for slug, lat, lon, tz in _CITY_RAW:
    SLUG_DB[slug] = {"lat": lat, "lon": lon, "tz": tz, "slug": slug}
    name = slug.replace("-", " ")
    ALIAS_DB[name] = slug
    ALIAS_DB[slug] = slug

# Extra aliases
_ALIASES = {
    "new york": "nyc", "new york city": "nyc",
    "los angeles": "la", "san francisco": "san-francisco",
    "sf": "san-francisco", "dc": "washington-dc",
    "washington": "washington-dc", "slc": "salt-lake-city",
    "okc": "oklahoma-city", "kc": "kansas-city",
    "philly": "philadelphia", "phx": "phoenix",
    "nola": "new-orleans", "indy": "indianapolis",
    "vegas": "las-vegas", "stl": "st-louis",
    "wlg": "wellington", "icn": "seoul", "incheon": "seoul",
}
ALIAS_DB.update(_ALIASES)


# ── Cache utilities ──────────────────────────────────────────────────────────

def _cache_path(key: str) -> Path:
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return CACHE_DIR / f"{h}.json"


def cache_get(key: str, ttl: int) -> dict | list | None:
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("_ts", 0) > ttl:
            return None
        return data.get("payload")
    except (json.JSONDecodeError, KeyError):
        return None


def cache_set(key: str, payload) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _cache_path(key).write_text(
        json.dumps({"_ts": time.time(), "payload": payload})
    )


# ── Polymarket API ───────────────────────────────────────────────────────────

def fetch_event_by_slug(slug: str) -> dict | None:
    """Fetch a single event by its slug. Returns None if not found."""
    cache_key = f"event:{slug}"
    cached = cache_get(cache_key, CACHE_TTL_MARKETS)
    if cached is not None:
        return cached

    try:
        resp = requests.get(
            f"{GAMMA_API}/events",
            params={"slug": slug},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"  ⚠ API error for slug {slug}: {exc}", file=sys.stderr)
        return None

    if not data:
        return None

    event = data[0] if isinstance(data, list) else data
    cache_set(cache_key, event)
    return event


def discover_temperature_events(target_date: date) -> list[dict]:
    """Try known city slugs to find active temperature markets for target_date."""
    month_name = target_date.strftime("%B").lower()
    day = target_date.day
    year = target_date.year

    seen_slugs: set[str] = set()
    events: list[dict] = []

    for slug in SLUG_DB:
        event_slug = f"highest-temperature-in-{slug}-on-{month_name}-{day}-{year}"
        if event_slug in seen_slugs:
            continue
        seen_slugs.add(event_slug)

        event = fetch_event_by_slug(event_slug)
        if event and _has_open_markets(event):
            events.append(event)

    return events


def _has_open_markets(event: dict) -> bool:
    return any(not m.get("closed", True) for m in event.get("markets", []))


# ── Title parsing ────────────────────────────────────────────────────────────

_TITLE_RE = re.compile(
    r"Highest temperature in (.+?) on (.+?)(?:\?|$)", re.IGNORECASE
)

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def parse_event_title(title: str) -> tuple[str, date | None]:
    """Extract (city_name, target_date) from an event title."""
    m = _TITLE_RE.match(title)
    if not m:
        return title, None
    city = m.group(1).strip()
    date_str = m.group(2).strip()

    # Parse "March 5" or "March 5, 2026"
    parts = re.match(r"(\w+)\s+(\d+)(?:,?\s*(\d{4}))?", date_str)
    if not parts:
        return city, None
    month_name = parts.group(1).lower()
    day = int(parts.group(2))
    year = int(parts.group(3)) if parts.group(3) else datetime.now().year
    month = _MONTH_MAP.get(month_name)
    if month is None:
        return city, None
    try:
        return city, date(year, month, day)
    except ValueError:
        return city, None


def resolve_city(city_name: str) -> dict | None:
    """Look up city info from a name or alias."""
    key = city_name.lower().strip()
    slug = ALIAS_DB.get(key)
    if slug:
        return SLUG_DB.get(slug)

    # Try slug form
    slug_form = key.replace(" ", "-")
    if slug_form in SLUG_DB:
        return SLUG_DB[slug_form]

    return None


# ── Outcome parsing ──────────────────────────────────────────────────────────

_UNIT_RE = re.compile(r"°([FCfc])")


def parse_outcome(text: str) -> tuple[float, float, str] | None:
    """Parse a groupItemTitle like '34-35°F' into (lo, hi, unit).

    Intervals are inclusive: a member with rounded temp T hits if lo <= T <= hi.
    Open-ended bins use ±inf.
    """
    s = text.strip()

    # Detect unit
    um = _UNIT_RE.search(s)
    unit = um.group(1).upper() if um else "F"

    # Strip unit markers for numeric parsing
    clean = re.sub(r"\s*°[FCfc]", "", s).strip()

    # "X or below" / "X or lower"
    m = re.match(r"(-?\d+)\s+or\s+(below|lower)", clean, re.I)
    if m:
        return float("-inf"), float(m.group(1)), unit

    # "Below X" / "Under X"
    m = re.match(r"(below|under)\s+(-?\d+)", clean, re.I)
    if m:
        return float("-inf"), float(m.group(2)) - 1, unit

    # "X or above" / "X or higher"
    m = re.match(r"(-?\d+)\s+or\s+(above|higher|more)", clean, re.I)
    if m:
        return float(m.group(1)), float("inf"), unit

    # "Above X" / "Over X"
    m = re.match(r"(above|over)\s+(-?\d+)", clean, re.I)
    if m:
        return float(m.group(2)) + 1, float("inf"), unit

    # Range "X-Y"
    m = re.match(r"(-?\d+)\s*[-–]\s*(-?\d+)", clean)
    if m:
        return float(m.group(1)), float(m.group(2)), unit

    # Exact value
    m = re.match(r"(-?\d+)$", clean)
    if m:
        val = float(m.group(1))
        return val, val, unit

    return None


# ── GEFS ensemble data ──────────────────────────────────────────────────────

def fetch_gefs_ensemble(
    lat: float, lon: float, tz_name: str, target_date: date
) -> dict | None:
    """Fetch GEFS ensemble 2m temperature from Open-Meteo.

    Returns {"times": [...], "members": [[t0,t1,...], ...]} with temps in °C
    and times as local ISO strings.
    """
    cache_key = (
        f"gefs:{lat:.2f}:{lon:.2f}:{tz_name}:"
        f"{target_date.isoformat()}"
    )
    cached = cache_get(cache_key, CACHE_TTL_GEFS)
    if cached is not None:
        return cached

    start = target_date - timedelta(days=1)
    end = target_date + timedelta(days=1)

    try:
        resp = requests.get(
            ENSEMBLE_API,
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "hourly": "temperature_2m",
                "models": "gfs_seamless",
                "timezone": tz_name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"  ⚠ Open-Meteo error: {exc}", file=sys.stderr)
        return None

    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    if not times:
        print("  ⚠ No hourly data from Open-Meteo", file=sys.stderr)
        return None

    # Collect all members: control (temperature_2m) + member01..member30
    members: list[list[float]] = []
    control = hourly.get("temperature_2m")
    if control:
        members.append(control)
    for i in range(1, 31):
        key = f"temperature_2m_member{i:02d}"
        vals = hourly.get(key)
        if vals:
            members.append(vals)

    result = {
        "times": times,
        "members": members,
        "grid_lat": data.get("latitude"),
        "grid_lon": data.get("longitude"),
    }
    cache_set(cache_key, result)
    return result


def compute_daily_highs(
    gefs: dict, target_date: date, unit: str,
    offset: float = 0.0,
) -> np.ndarray:
    """Compute daily-max temperature per ensemble member for target_date.

    *offset* is a station-bias correction in the market's unit, applied after
    unit conversion but before rounding (e.g. -4 shifts all highs down by 4°).

    Returns array of shape (n_members,) in the requested unit, rounded to
    nearest integer.
    """
    times = gefs["times"]
    date_str = target_date.isoformat()  # "2026-03-05"

    # Find indices for hours on the target local day
    idx = [i for i, t in enumerate(times) if t.startswith(date_str)]
    if not idx:
        raise ValueError(f"No GEFS hours found for {date_str}")

    n_members = len(gefs["members"])
    highs = np.empty(n_members)

    for m, temps in enumerate(gefs["members"]):
        day_temps = [temps[i] for i in idx if temps[i] is not None]
        if not day_temps:
            highs[m] = np.nan
        else:
            highs[m] = max(day_temps)

    # Convert °C -> °F if needed
    if unit == "F":
        highs = highs * 1.8 + 32.0

    if offset:
        highs = highs + offset

    return np.round(highs).astype(int)


# ── Probability computation ─────────────────────────────────────────────────

def compute_probabilities(
    highs: np.ndarray, bins: list[tuple[float, float]]
) -> list[float]:
    """Fraction of ensemble members falling in each bin."""
    valid = highs[~np.isnan(highs)]
    n = len(valid)
    if n == 0:
        return [0.0] * len(bins)
    probs = []
    for lo, hi in bins:
        count = int(np.sum((valid >= lo) & (valid <= hi)))
        probs.append(count / n)
    return probs


# ── Display ──────────────────────────────────────────────────────────────────

def _bar(fraction: float, width: int = BAR_WIDTH) -> str:
    n = int(round(fraction * width))
    return "█" * n


def print_report(
    title: str,
    outcomes: list[dict],
    highs: np.ndarray,
    grid_lat: float | None,
    grid_lon: float | None,
    unit: str,
    offset: float = 0.0,
) -> None:
    n_members = len(highs)
    valid = highs[~np.isnan(highs)]

    print()
    print("═" * 72)
    print(f"  {title}")
    grid_str = ""
    if grid_lat is not None and grid_lon is not None:
        lat_h = "N" if grid_lat >= 0 else "S"
        lon_h = "E" if grid_lon >= 0 else "W"
        grid_str = (
            f" · Grid: {abs(grid_lat):.2f}°{lat_h} {abs(grid_lon):.2f}°{lon_h}"
        )
    offset_str = f" · Offset: {offset:+.0f}°{unit}" if offset else ""
    print(f"  GEFS {n_members} members{grid_str}{offset_str}")
    print("─" * 72)

    header = f"  {'Outcome':<20s} {'Market':>7s} {'GEFS':>7s} {'Edge':>7s}  {'Distribution'}"
    print(header)
    print(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*7}  {'─'*BAR_WIDTH}")

    for o in outcomes:
        label = o["label"]
        mkt_p = o["market_prob"]
        gefs_p = o["gefs_prob"]
        edge = gefs_p - mkt_p
        edge_str = f"{edge*100:+6.1f}%"

        print(
            f"  {label:<20s} {mkt_p*100:>6.1f}% {gefs_p*100:>6.1f}% "
            f"{edge_str}  {_bar(gefs_p)}"
        )

    print(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*7}  {'─'*BAR_WIDTH}")

    if len(valid) > 0:
        u = "°" + unit
        print(
            f"  GEFS high: "
            f"μ={np.mean(valid):.1f}{u}  "
            f"σ={np.std(valid):.1f}{u}  "
            f"range=[{int(np.min(valid))}, {int(np.max(valid))}]{u}"
        )
    print("═" * 72)


# ── Importable API for paper-trading layer ───────────────────────────────────

def find_market(city_slug: str, target_date: date) -> dict | None:
    """Locate the Polymarket temperature event for *city_slug* on *target_date*.

    Returns the raw Gamma event dict (with embedded ``markets`` list) or None.
    """
    event_slug = build_event_slug(city_slug, target_date)
    return fetch_event_by_slug(event_slug)


def fetch_event_fresh(event_slug: str) -> dict | None:
    """Fetch an event from Gamma, always bypassing the local cache."""
    try:
        resp = requests.get(
            f"{GAMMA_API}/events",
            params={"slug": event_slug},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"  Warning: API error for slug {event_slug}: {exc}", file=sys.stderr)
        return None
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


def parse_outcome_intervals(
    event: dict,
) -> tuple[str, list[str], list[tuple[float, float]], list[float]] | None:
    """Extract outcome intervals from an event's markets.

    Uses **all** child markets (open or closed) so the function works both
    before and after resolution.

    Returns ``(unit, labels, intervals, market_probs)`` sorted by interval
    lower bound, or *None* if nothing is parseable.
    """
    markets = event.get("markets", [])
    bins: list[tuple[float, float]] = []
    labels: list[str] = []
    market_probs: list[float] = []
    unit = "F"

    for mkt in markets:
        git = mkt.get("groupItemTitle", "")
        parsed = parse_outcome(git)
        if parsed is None:
            continue
        lo, hi, u = parsed
        unit = u
        bins.append((lo, hi))
        labels.append(git)

        prices = mkt.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes_price = float(prices[0]) if prices else 0.0
        market_probs.append(yes_price)

    if not bins:
        return None

    order = sorted(range(len(bins)), key=lambda i: (bins[i][0], bins[i][1]))
    return (
        unit,
        [labels[i] for i in order],
        [bins[i] for i in order],
        [market_probs[i] for i in order],
    )


def compute_gefs_probs(
    city_slug: str,
    target_date: date,
    unit: str,
    intervals: list[tuple[float, float]],
) -> dict | None:
    """Run the full GEFS pipeline and return per-bin probabilities + stats.

    Returns a dict with keys: ``probs``, ``ensemble_n``, ``mean``, ``p10``,
    ``p50``, ``p90``, ``spread`` — or *None* on failure.
    """
    city_info = SLUG_DB.get(city_slug)
    if city_info is None:
        return None

    gefs = fetch_gefs_ensemble(
        city_info["lat"], city_info["lon"], city_info["tz"], target_date,
    )
    if gefs is None:
        return None

    offset = CITY_OFFSETS.get(city_slug, 0.0)
    try:
        highs = compute_daily_highs(gefs, target_date, unit, offset=offset)
    except ValueError:
        return None

    probs = compute_probabilities(highs, intervals)
    valid = highs[~np.isnan(highs)].astype(float)

    if len(valid) == 0:
        return None

    return {
        "probs": probs,
        "ensemble_n": int(len(valid)),
        "mean": round(float(np.mean(valid)), 2),
        "p10": round(float(np.percentile(valid, 10)), 2),
        "p50": round(float(np.percentile(valid, 50)), 2),
        "p90": round(float(np.percentile(valid, 90)), 2),
        "spread": round(float(np.ptp(valid)), 2),
    }


# ── Event processing ─────────────────────────────────────────────────────────

def process_event(event: dict, verbose: bool = False) -> bool:
    """Process one temperature event: fetch GEFS, compute probs, print report.

    Returns True on success.
    """
    title = event.get("title", "")
    city_name, target_date = parse_event_title(title)

    if target_date is None:
        print(f"  ⚠ Could not parse date from: {title}", file=sys.stderr)
        return False

    city_info = resolve_city(city_name)
    if city_info is None:
        # Try extracting slug from the event slug itself
        event_slug = event.get("slug", "")
        m = re.search(r"highest-temperature-in-(.+?)-on-", event_slug)
        if m:
            city_slug = m.group(1)
            city_info = SLUG_DB.get(city_slug)

    if city_info is None:
        print(
            f"  ⚠ Unknown city '{city_name}' — add it to SLUG_DB or use "
            f"--lat/--lon",
            file=sys.stderr,
        )
        return False

    # Parse market outcomes (use all markets, including closed, for full picture)
    markets = event.get("markets", [])
    if not markets:
        if verbose:
            print(f"  ℹ No markets for: {title}")
        return False

    bins: list[tuple[float, float]] = []
    labels: list[str] = []
    market_probs: list[float] = []
    unit = "F"

    for mkt in markets:
        git = mkt.get("groupItemTitle", "")
        parsed = parse_outcome(git)
        if parsed is None:
            if verbose:
                print(f"  ⚠ Unparseable outcome: {git!r}", file=sys.stderr)
            continue
        lo, hi, u = parsed
        unit = u
        bins.append((lo, hi))
        labels.append(git)

        prices = mkt.get("outcomePrices", "[]")
        if isinstance(prices, str):
            prices = json.loads(prices)
        yes_price = float(prices[0]) if prices else 0.0
        market_probs.append(yes_price)

    if not bins:
        print(f"  ⚠ No parseable outcomes for: {title}", file=sys.stderr)
        return False

    # Sort bins by lower bound
    order = sorted(range(len(bins)), key=lambda i: (bins[i][0], bins[i][1]))
    bins = [bins[i] for i in order]
    labels = [labels[i] for i in order]
    market_probs = [market_probs[i] for i in order]

    # Fetch GEFS data
    if verbose:
        print(f"  Fetching GEFS for {city_name} ({city_info['tz']})...")

    gefs = fetch_gefs_ensemble(
        city_info["lat"], city_info["lon"], city_info["tz"], target_date
    )
    if gefs is None:
        return False

    # Compute daily highs (with optional station-bias offset)
    offset = CITY_OFFSETS.get(city_info.get("slug", ""), 0.0)
    try:
        highs = compute_daily_highs(gefs, target_date, unit, offset=offset)
    except ValueError as exc:
        print(f"  ⚠ {exc}", file=sys.stderr)
        return False

    # Compute probabilities
    gefs_probs = compute_probabilities(highs, bins)

    # Build outcomes list
    outcomes = []
    for i in range(len(bins)):
        outcomes.append({
            "label": labels[i],
            "market_prob": market_probs[i],
            "gefs_prob": gefs_probs[i],
        })

    print_report(
        title, outcomes, highs,
        gefs.get("grid_lat"), gefs.get("grid_lon"),
        unit, offset=offset,
    )
    return True


# ── CLI ──────────────────────────────────────────────────────────────────────

def build_event_slug(city_slug: str, target_date: date) -> str:
    month = target_date.strftime("%B").lower()
    return f"highest-temperature-in-{city_slug}-on-{month}-{target_date.day}-{target_date.year}"


def cmd_run(args: argparse.Namespace) -> int:
    tomorrow = date.today() + timedelta(days=1)
    target_date = tomorrow

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date '{args.date}' (use YYYY-MM-DD)")
            return 1

    if args.city:
        key = args.city.lower().strip()
        slug = ALIAS_DB.get(key)
        if slug is None:
            slug = key.replace(" ", "-")
        if slug not in SLUG_DB:
            print(
                f"Warning: '{args.city}' not in city database — "
                f"will try slug '{slug}' anyway"
            )

        event_slug = build_event_slug(slug, target_date)
        if args.verbose:
            print(f"Looking up event: {event_slug}")

        event = fetch_event_by_slug(event_slug)
        if event is None:
            print(
                f"No market found for '{args.city}' on {target_date}.\n"
                f"Tried slug: {event_slug}\n"
                f"Use --list to see available markets."
            )
            return 1

        process_event(event, verbose=args.verbose)
        return 0

    # Auto-discover all temperature markets for the target date
    print(f"Scanning for temperature markets on {target_date}...")
    events = discover_temperature_events(target_date)

    if not events:
        print(
            f"No open temperature markets found for {target_date}.\n"
            f"Markets may not be posted yet, or try a different date."
        )
        return 1

    print(f"Found {len(events)} market(s).\n")
    ok = 0
    for event in events:
        if process_event(event, verbose=args.verbose):
            ok += 1

    if ok == 0:
        print("No markets could be processed (missing GEFS data?).")
        return 1
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    tomorrow = date.today() + timedelta(days=1)
    target_date = tomorrow

    if args.date:
        try:
            target_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"Error: invalid date '{args.date}' (use YYYY-MM-DD)")
            return 1

    print(f"Scanning for temperature markets on {target_date}...\n")
    events = discover_temperature_events(target_date)

    if not events:
        print("No open temperature markets found.")
        return 0

    for event in events:
        title = event.get("title", "?")
        slug = event.get("slug", "")
        markets = event.get("markets", [])
        open_count = sum(1 for m in markets if not m.get("closed"))
        labels = [
            m.get("groupItemTitle", "?")
            for m in markets
            if not m.get("closed")
        ]
        print(f"  {title}")
        print(f"    slug: {slug}")
        print(f"    outcomes ({open_count}): {', '.join(labels)}")
        print()

    return 0


def cmd_clear_cache(_args: argparse.Namespace) -> int:
    if CACHE_DIR.exists():
        count = 0
        for f in CACHE_DIR.glob("*.json"):
            f.unlink()
            count += 1
        print(f"Cleared {count} cached file(s).")
    else:
        print("Cache directory does not exist.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="GEFS ensemble probabilities for Polymarket temperature markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s                          # auto-discover tomorrow's markets\n"
            "  %(prog)s --city NYC               # specific city, tomorrow\n"
            "  %(prog)s --city Atlanta --date 2026-03-06\n"
            "  %(prog)s --list                   # list available markets\n"
            "  %(prog)s --clear-cache            # clear cached data\n"
        ),
    )
    parser.add_argument(
        "--city", "-c",
        help="City name or alias (e.g. NYC, Atlanta, Paris)",
    )
    parser.add_argument(
        "--date", "-d",
        help="Target date YYYY-MM-DD (default: tomorrow)",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List active temperature markets",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="Clear cached API data",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output",
    )

    args = parser.parse_args()

    if args.clear_cache:
        return cmd_clear_cache(args)
    if args.list:
        return cmd_list(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
