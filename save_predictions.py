#!/usr/bin/env python3
"""Auto-save prediction snapshots for configured stations.

Fetches GEFS, HRRR, NAM, and TWC forecasts for tomorrow (or a specified
date) and writes JSON files identical to the dashboard's "Save Predictions"
button.  Designed to run as a nightly cron job before midnight.

Usage:
    python3 save_predictions.py                  # tomorrow, all configured stations
    python3 save_predictions.py --date 2026-03-17
    python3 save_predictions.py --stations KSEA KORD
    python3 save_predictions.py --dry-run        # fetch but don't write files
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PREDICTIONS_DIR = Path(__file__).parent / "predictions"
TWC_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"
REQUEST_TIMEOUT = 20

# ── Station database ─────────────────────────────────────────────────────────
# Coordinates sourced from Weather.com v3/location/point API.
# Edit this dict to add/remove stations from the nightly save.

STATIONS: dict[str, dict] = {
    "KSEA": {"lat": 47.441, "lon": -122.300, "tz": "America/Los_Angeles", "elevM": 132, "offset": 0},
    "KORD": {"lat": 41.977, "lon": -87.905,  "tz": "America/Chicago",     "elevM": 205, "offset": -4},
    "KLGA": {"lat": 40.761, "lon": -73.864,  "tz": "America/New_York",    "elevM": 9,   "offset": 0},
    # "KATL": {"lat": 33.639, "lon": -84.405,  "tz": "America/New_York",    "elevM": 313, "offset": 0},
}

# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "SavePredictions/1.0"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fmt_date(d: date) -> str:
    return d.isoformat()


# ── Percentile (matching the dashboard's linear-interpolation formula) ───────

def _pctile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    i = (p / 100.0) * (len(sorted_vals) - 1)
    lo, hi = int(math.floor(i)), int(math.ceil(i))
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


def _c_to_f(c: float, offset: float = 0.0) -> float:
    return c * 9.0 / 5.0 + 32.0 + offset


# ── GEFS ensemble fetch & process ───────────────────────────────────────────

def fetch_gefs(lat: float, lon: float, tz: str, date_str: str, offset: float) -> dict | None:
    d = date.fromisoformat(date_str)
    start = _fmt_date(d - timedelta(days=1))
    end = _fmt_date(d + timedelta(days=1))
    url = (
        f"https://ensemble-api.open-meteo.com/v1/ensemble"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&hourly=temperature_2m&models=gfs_seamless"
        f"&timezone={urllib.request.quote(tz)}"
        f"&start_date={start}&end_date={end}"
    )
    data = _get_json(url)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    members: list[list[float | None]] = []
    if hourly.get("temperature_2m"):
        members.append(hourly["temperature_2m"])
    for i in range(1, 31):
        key = f"temperature_2m_member{i:02d}"
        if hourly.get(key):
            members.append(hourly[key])
    if not members:
        return None

    hourly_stats = []
    for ti, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        vals = sorted(
            _c_to_f(m[ti], offset)
            for m in members
            if m[ti] is not None
        )
        if not vals:
            continue
        hr = int(t.split("T")[1].split(":")[0])
        hourly_stats.append({
            "hour": hr,
            "mean": round(sum(vals) / len(vals), 1),
            "p10": round(_pctile(vals, 10), 1),
            "p25": round(_pctile(vals, 25), 1),
            "p50": round(_pctile(vals, 50), 1),
            "p75": round(_pctile(vals, 75), 1),
            "p90": round(_pctile(vals, 90), 1),
            "min": round(vals[0], 1),
            "max": round(vals[-1], 1),
        })

    day_idxs = [i for i, t in enumerate(times) if t.startswith(date_str)]
    member_highs = []
    for m in members:
        day_vals = [m[i] for i in day_idxs if m[i] is not None]
        if day_vals:
            member_highs.append(round(_c_to_f(max(day_vals), offset)))
    member_highs.sort()

    if not member_highs:
        return None

    return {
        "nMembers": len(members),
        "gridLat": data.get("latitude"),
        "gridLon": data.get("longitude"),
        "memberHighs": member_highs,
        "medianHigh": round(_pctile(member_highs, 50)),
        "p10High": round(_pctile(member_highs, 10)),
        "p90High": round(_pctile(member_highs, 90)),
        "hourlyStats": hourly_stats,
    }


# ── HRRR / NAM fetch & process (same response format) ───────────────────────

def fetch_deterministic(
    lat: float, lon: float, tz: str, date_str: str,
    model: str, offset: float,
) -> dict | None:
    d = date.fromisoformat(date_str)
    start = _fmt_date(d - timedelta(days=1))
    end = _fmt_date(d + timedelta(days=1))
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        f"&hourly=temperature_2m&models={model}"
        f"&timezone={urllib.request.quote(tz)}"
        f"&start_date={start}&end_date={end}"
    )
    data = _get_json(url)
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("temperature_2m", [])
    if not temps:
        return None

    hourly_temps: dict[int, float] = {}
    daily_max = -math.inf
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        if temps[i] is None:
            continue
        t_f = round(_c_to_f(temps[i], offset), 1)
        hr = int(t.split("T")[1].split(":")[0])
        hourly_temps[hr] = t_f
        if t_f > daily_max:
            daily_max = t_f

    if not hourly_temps:
        return None

    return {
        "gridLat": data.get("latitude"),
        "gridLon": data.get("longitude"),
        "predictedHigh": round(daily_max),
        "hourlyTemps": hourly_temps,
    }


# ── TWC (Weather.com) blended 48h forecast ──────────────────────────────────

def fetch_twc(lat: float, lon: float, tz: str, date_str: str) -> dict | None:
    url = (
        f"https://api.weather.com/v1/geocode/{lat:.4f}/{lon:.4f}"
        f"/forecast/hourly/48hour.json"
        f"?apiKey={TWC_API_KEY}&units=e&language=en-US"
    )
    data = _get_json(url)
    forecasts = data.get("forecasts", [])
    if not forecasts:
        return None

    tz_obj = ZoneInfo(tz)
    hourly_temps: dict[int, int] = {}
    daily_max = -math.inf
    for f in forecasts:
        epoch = f.get("fcst_valid")
        temp = f.get("temp")
        if epoch is None or temp is None:
            continue
        local_dt = datetime.fromtimestamp(epoch, tz=tz_obj)
        if local_dt.strftime("%Y-%m-%d") != date_str:
            continue
        hourly_temps[local_dt.hour] = temp
        if temp > daily_max:
            daily_max = temp

    if not hourly_temps:
        return None

    return {
        "predictedHigh": round(daily_max),
        "hourlyTemps": hourly_temps,
    }


# ── Build & save snapshot ────────────────────────────────────────────────────

def build_snapshot(station: str, info: dict, date_str: str) -> dict:
    lat, lon, tz = info["lat"], info["lon"], info["tz"]
    offset = info.get("offset", 0)

    print(f"    GEFS...", end="", flush=True)
    try:
        gefs = fetch_gefs(lat, lon, tz, date_str, offset)
        print(f" {gefs['nMembers']} members" if gefs else " no data", end="")
    except Exception as e:
        print(f" error: {e}", end="")
        gefs = None

    print(f"  HRRR...", end="", flush=True)
    try:
        hrrr = fetch_deterministic(lat, lon, tz, date_str, "gfs_hrrr", offset)
        print(f" {hrrr['predictedHigh']}°F" if hrrr else " no data", end="")
    except Exception as e:
        print(f" error: {e}", end="")
        hrrr = None

    print(f"  NAM...", end="", flush=True)
    try:
        nam = fetch_deterministic(lat, lon, tz, date_str, "ncep_nam_conus", offset)
        print(f" {nam['predictedHigh']}°F" if nam else " no data", end="")
    except Exception as e:
        print(f" error: {e}", end="")
        nam = None

    print(f"  TWC...", end="", flush=True)
    try:
        twc = fetch_twc(lat, lon, tz, date_str)
        print(f" {twc['predictedHigh']}°F" if twc else " no data", end="")
    except Exception as e:
        print(f" error: {e}", end="")
        twc = None

    print()

    return {
        "station": station,
        "date": date_str,
        "tz": tz,
        "offset": offset,
        "savedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "coordinates": {"lat": lat, "lon": lon, "elevM": info.get("elevM")},
        "observations": {"count": 0, "high": None, "hourly": {}, "all": []},
        "models": {
            "gefs": gefs,
            "hrrr": hrrr,
            "nam": nam,
            "twc": twc,
        },
    }


def save_snapshot(snapshot: dict) -> Path:
    PREDICTIONS_DIR.mkdir(exist_ok=True)
    station = snapshot["station"]
    date_str = snapshot["date"]
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{station}_{date_str}_{now_utc}.json"
    out_path = PREDICTIONS_DIR / filename
    out_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return out_path


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(
        description="Save prediction snapshots for configured weather stations.",
    )
    parser.add_argument(
        "--date", default=tomorrow,
        help=f"Target date in YYYY-MM-DD format (default: tomorrow = {tomorrow})",
    )
    parser.add_argument(
        "--stations", nargs="+", metavar="ICAO",
        help=f"Station(s) to save (default: all configured = {' '.join(STATIONS)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Fetch data and print summary but don't write files",
    )
    args = parser.parse_args()

    stations = args.stations or list(STATIONS.keys())
    unknown = [s for s in stations if s not in STATIONS]
    if unknown:
        print(f"Error: unknown station(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Available: {', '.join(STATIONS)}", file=sys.stderr)
        sys.exit(1)

    date_str = args.date
    print(f"Saving predictions for {date_str}")
    print(f"Stations: {', '.join(stations)}")
    print()

    saved = []
    for station in stations:
        info = STATIONS[station]
        offset_note = f" [offset {info['offset']:+d}°F]" if info.get("offset") else ""
        print(f"  {station}{offset_note}")
        snapshot = build_snapshot(station, info, date_str)

        models_ok = sum(
            1 for m in snapshot["models"].values() if m is not None
        )
        if models_ok == 0:
            print(f"    ⚠ No model data — skipping save")
            continue

        if args.dry_run:
            gefs = snapshot["models"].get("gefs")
            print(f"    [dry-run] Would save: {station}_{date_str}")
            if gefs:
                print(f"    GEFS median={gefs['medianHigh']}°F  P10–P90={gefs['p10High']}–{gefs['p90High']}°F")
        else:
            path = save_snapshot(snapshot)
            saved.append(path.name)
            print(f"    ✓ Saved → predictions/{path.name}")
        print()

    if saved:
        print(f"Done — {len(saved)} file(s) saved to predictions/")
    elif not args.dry_run:
        print("No files saved.")


if __name__ == "__main__":
    main()
