#!/usr/bin/env python3
"""Analyze prediction accuracy: compare model forecasts to actual observations.

Reads saved prediction snapshots from predictions/, fetches actual observed
temperatures from Weather.com (Weather Underground) — the same source
Polymarket uses for market resolution — and produces an accuracy report
for each model (GEFS, HRRR, NAM, TWC).
"""

from __future__ import annotations

import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PREDICTIONS_DIR = Path(__file__).parent / "predictions"
TODAY = date.today()

TWC_API_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

# ── Weather.com (Wunderground) fetch ─────────────────────────────────────────

def fetch_weathercom_obs(station: str, date_str: str) -> list[dict]:
    """Fetch historical observations from Weather.com for a given date.

    Returns the raw observations list; each entry has 'temp' (°F int),
    'valid_time_gmt' (epoch), and other fields.
    """
    ds = date_str.replace("-", "")
    url = (
        f"https://api.weather.com/v1/location/{station}:9:US"
        f"/observations/historical.json"
        f"?apiKey={TWC_API_KEY}&units=e&startDate={ds}&endDate={ds}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "PredictionAnalysis/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("observations", [])


def get_observed_high(
    station: str, date_str: str, tz_name: str,
) -> tuple[int | None, dict[int, int], int]:
    """Return (observed_high_F, {hour: temp_F}, obs_count) for a local date.

    Uses Weather.com historical observations — the same source Polymarket
    resolves from (Weather Underground).
    """
    tz = ZoneInfo(tz_name)
    observations = fetch_weathercom_obs(station, date_str)

    temps_f: list[int] = []
    hourly_temps: dict[int, int] = {}

    for obs in observations:
        temp = obs.get("temp")
        epoch = obs.get("valid_time_gmt")
        if temp is None or epoch is None:
            continue

        local_dt = datetime.fromtimestamp(epoch, tz=tz)
        local_date = local_dt.strftime("%Y-%m-%d")
        if local_date != date_str:
            continue

        temps_f.append(temp)
        hourly_temps[local_dt.hour] = temp

    if not temps_f:
        return None, {}, 0

    return max(temps_f), hourly_temps, len(temps_f)


# ── Analysis ─────────────────────────────────────────────────────────────────

def load_predictions() -> list[dict]:
    """Load all prediction JSON files, sorted by date."""
    preds = []
    for f in sorted(PREDICTIONS_DIR.glob("*.json")):
        with open(f) as fp:
            data = json.load(fp)
        data["_file"] = f.name
        preds.append(data)
    return preds


def analyze_prediction(pred: dict) -> dict | None:
    """Analyze a single prediction against actual observations."""
    station = pred["station"]
    date_str = pred["date"]
    tz = pred["tz"]
    offset = pred.get("offset", 0)
    pred_date = date.fromisoformat(date_str)

    if pred_date > TODAY:
        return None  # future date

    is_today = pred_date == TODAY

    print(f"  Fetching Weather.com data for {station} on {date_str}...", end=" ", flush=True)
    try:
        obs_high, hourly_obs, obs_count = get_observed_high(station, date_str, tz)
    except Exception as e:
        print(f"ERROR: {e}")
        return None
    print(f"{obs_count} observations, high = {obs_high}°F")

    models = pred.get("models", {})
    gefs = models.get("gefs") or {}
    hrrr = models.get("hrrr") or {}
    nam = models.get("nam") or {}
    twc = models.get("twc") or {}

    member_highs = gefs.get("memberHighs", [])
    gefs_median = gefs.get("medianHigh")
    gefs_p10 = gefs.get("p10High")
    gefs_p90 = gefs.get("p90High")
    gefs_mean = round(sum(member_highs) / len(member_highs), 1) if member_highs else None

    hrrr_high = hrrr.get("predictedHigh")
    nam_high = nam.get("predictedHigh")
    twc_high = twc.get("predictedHigh")

    # Lead time: hours between savedAt and start of the forecast date (local midnight)
    saved_at = datetime.fromisoformat(pred["savedAt"].replace("Z", "+00:00"))
    local_midnight = datetime(
        pred_date.year, pred_date.month, pred_date.day, tzinfo=ZoneInfo(tz),
    )
    lead_hours = (local_midnight - saved_at).total_seconds() / 3600

    result = {
        "file": pred["_file"],
        "station": station,
        "date": date_str,
        "tz": tz,
        "offset": offset,
        "is_today": is_today,
        "lead_hours": lead_hours,
        "obs_count": obs_count,
        "obs_high": obs_high,
        "obs_hourly": hourly_obs,
        "gefs_median": gefs_median,
        "gefs_mean": gefs_mean,
        "gefs_p10": gefs_p10,
        "gefs_p90": gefs_p90,
        "member_highs": member_highs,
        "hrrr_high": hrrr_high,
        "nam_high": nam_high,
        "twc_high": twc_high,
    }

    if obs_high is not None:
        result["gefs_error"] = (gefs_median - obs_high) if gefs_median is not None else None
        result["hrrr_error"] = (hrrr_high - obs_high) if hrrr_high is not None else None
        result["nam_error"] = (nam_high - obs_high) if nam_high is not None else None
        result["twc_error"] = (twc_high - obs_high) if twc_high is not None else None
        result["in_p10_p90"] = (
            gefs_p10 is not None
            and gefs_p90 is not None
            and gefs_p10 <= obs_high <= gefs_p90
        )
        if member_highs:
            n_below = sum(1 for h in member_highs if h <= obs_high)
            result["gefs_rank"] = n_below / len(member_highs)
        else:
            result["gefs_rank"] = None
    return result


# ── Display ──────────────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"


def color_error(err: int | None) -> str:
    if err is None:
        return f"{DIM}  —{RESET}"
    sign = "+" if err > 0 else ""
    if abs(err) <= 1:
        return f"{GREEN}{sign}{err:>3d}{RESET}"
    if abs(err) <= 3:
        return f"{YELLOW}{sign}{err:>3d}{RESET}"
    return f"{RED}{sign}{err:>3d}{RESET}"


def sparkline(member_highs: list[int], obs_high: int | None) -> str:
    if not member_highs:
        return ""
    lo, hi = min(member_highs), max(member_highs)
    spread = hi - lo or 1
    blocks = " ▁▂▃▄▅▆▇█"
    # Build histogram bins
    n_bins = 15
    bins = [0] * n_bins
    for h in member_highs:
        idx = min(int((h - lo) / spread * (n_bins - 1)), n_bins - 1)
        bins[idx] += 1
    max_count = max(bins) or 1
    chars = [blocks[min(int(b / max_count * 8), 8)] for b in bins]

    # Mark where obs falls
    if obs_high is not None and lo <= obs_high <= hi:
        obs_idx = min(int((obs_high - lo) / spread * (n_bins - 1)), n_bins - 1)
        chars[obs_idx] = f"{RED}▼{RESET}"

    return f"{lo}°F [{''.join(chars)}] {hi}°F"


def print_hourly_comparison(result: dict) -> None:
    """Print hourly forecast vs observed comparison for a single prediction."""
    obs_hourly = result.get("obs_hourly", {})
    if not obs_hourly:
        return

    # Get model hourly data from the prediction file
    file_path = PREDICTIONS_DIR / result["file"]
    with open(file_path) as fp:
        pred = json.load(fp)

    models = pred.get("models", {})
    gefs_hourly = {s["hour"]: s for s in (models.get("gefs") or {}).get("hourlyStats", [])}
    hrrr_hourly = (models.get("hrrr") or {}).get("hourlyTemps", {})
    nam_hourly = (models.get("nam") or {}).get("hourlyTemps", {})
    twc_hourly = (models.get("twc") or {}).get("hourlyTemps", {})

    print(f"\n  {'Hour':>6s}  {'Obs':>5s}  {'GEFS':>6s}  {'HRRR':>5s}  {'NAM':>5s}  {'TWC':>5s}")
    print(f"  {'─'*6}  {'─'*5}  {'─'*6}  {'─'*5}  {'─'*5}  {'─'*5}")
    for hr in range(24):
        obs_t = obs_hourly.get(hr)
        gefs_s = gefs_hourly.get(hr)
        gefs_t = round(gefs_s["p50"]) if gefs_s else None
        hrrr_t = round(hrrr_hourly.get(str(hr), hrrr_hourly.get(hr))) if str(hr) in hrrr_hourly or hr in hrrr_hourly else None
        nam_t = round(nam_hourly.get(str(hr), nam_hourly.get(hr))) if str(hr) in nam_hourly or hr in nam_hourly else None
        twc_t = round(twc_hourly.get(str(hr), twc_hourly.get(hr))) if str(hr) in twc_hourly or hr in twc_hourly else None

        obs_str = f"{obs_t:5d}" if obs_t is not None else f"{DIM}    —{RESET}"
        gefs_str = f"{gefs_t:6d}" if gefs_t is not None else f"{DIM}     —{RESET}"
        hrrr_str = f"{hrrr_t:5d}" if hrrr_t is not None else f"{DIM}    —{RESET}"
        nam_str = f"{nam_t:5d}" if nam_t is not None else f"{DIM}    —{RESET}"
        twc_str = f"{twc_t:5d}" if twc_t is not None else f"{DIM}    —{RESET}"

        suffix = ""
        if obs_t is not None and obs_t == result.get("obs_high"):
            suffix = f"  {BOLD}← daily high{RESET}"

        print(f"  {hr:5d}h  {obs_str}  {gefs_str}  {hrrr_str}  {nam_str}  {twc_str}{suffix}")


def print_report(results: list[dict]) -> None:
    verified = [r for r in results if r["obs_high"] is not None and not r["is_today"]]
    partial = [r for r in results if r["is_today"] and r["obs_high"] is not None]

    print()
    print(f"{BOLD}{'='*78}{RESET}")
    print(f"{BOLD}  PREDICTION ACCURACY ANALYSIS{RESET}")
    print(f"{BOLD}{'='*78}{RESET}")
    print()

    # ── Per-prediction detail ────────────────────────────────────────────────
    for r in results:
        status = ""
        if r["obs_high"] is None:
            status = f" {DIM}(no observations yet){RESET}"
        elif r["is_today"]:
            status = f" {YELLOW}(today — partial day, {r['obs_count']} obs so far){RESET}"

        offset_note = f" [offset {r['offset']:+.0f}°F]" if r["offset"] else ""
        lead = f"{r['lead_hours']:.0f}h lead" if r["lead_hours"] > 0 else "same-day"

        print(f"{BOLD}{CYAN}  {r['station']} · {r['date']}{RESET}  ({lead}{offset_note}){status}")
        print(f"  File: {DIM}{r['file']}{RESET}")
        print()

        if r["obs_high"] is not None:
            print(f"    {'Observed high:':>20s}  {BOLD}{r['obs_high']}°F{RESET}  ({r['obs_count']} Weather.com obs)")
        else:
            print(f"    {'Observed high:':>20s}  {DIM}not available{RESET}")

        print(f"    {'GEFS median:':>20s}  {r['gefs_median'] or '—':>3}°F  error: {color_error(r.get('gefs_error'))}")
        if r["gefs_mean"] is not None:
            print(f"    {'GEFS mean:':>20s}  {r['gefs_mean']:>5.1f}°F")
        print(f"    {'GEFS P10–P90:':>20s}  {r['gefs_p10'] or '?'}–{r['gefs_p90'] or '?'}°F", end="")

        if r.get("in_p10_p90") is True:
            print(f"  {GREEN}✓ actual in range{RESET}")
        elif r.get("in_p10_p90") is False:
            obs = r["obs_high"]
            if obs < (r["gefs_p10"] or 0):
                print(f"  {RED}✗ actual {obs}°F below P10{RESET}")
            else:
                print(f"  {RED}✗ actual {obs}°F above P90{RESET}")
        else:
            print()

        print(f"    {'HRRR:':>20s}  {r['hrrr_high'] or '—':>3}°F  error: {color_error(r.get('hrrr_error'))}")
        print(f"    {'NAM:':>20s}  {r['nam_high'] or '—':>3}°F  error: {color_error(r.get('nam_error'))}")
        print(f"    {'TWC:':>20s}  {r['twc_high'] or '—':>3}°F  error: {color_error(r.get('twc_error'))}")

        if r.get("member_highs"):
            print(f"    {'GEFS ensemble:':>20s}  {sparkline(r['member_highs'], r.get('obs_high'))}")

        if r.get("gefs_rank") is not None:
            pct = r["gefs_rank"] * 100
            print(f"    {'GEFS rank:':>20s}  {pct:.0f}th percentile of ensemble")

        print_hourly_comparison(r)
        print()
        print(f"  {'─'*74}")
        print()

    # ── Summary statistics ───────────────────────────────────────────────────
    if not verified:
        print(f"\n  {YELLOW}Not enough fully-verified predictions for summary statistics.{RESET}\n")
        return

    print(f"\n{BOLD}{'='*78}{RESET}")
    print(f"{BOLD}  SUMMARY — {len(verified)} verified prediction(s){RESET}")
    print(f"{BOLD}{'='*78}{RESET}\n")

    model_names = ["GEFS (median)", "HRRR", "NAM", "TWC"]
    error_keys = ["gefs_error", "hrrr_error", "nam_error", "twc_error"]

    print(f"  {'Model':<16s}  {'MAE':>5s}  {'Bias':>6s}  {'RMSE':>5s}  {'Max|Err|':>8s}")
    print(f"  {'─'*16}  {'─'*5}  {'─'*6}  {'─'*5}  {'─'*8}")

    for name, key in zip(model_names, error_keys):
        errors = [r[key] for r in verified if r.get(key) is not None]
        if not errors:
            print(f"  {name:<16s}  {DIM}  —      —      —       —{RESET}")
            continue
        mae = sum(abs(e) for e in errors) / len(errors)
        bias = sum(errors) / len(errors)
        rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))
        max_abs = max(abs(e) for e in errors)

        bias_color = GREEN if abs(bias) <= 1 else (YELLOW if abs(bias) <= 3 else RED)
        mae_color = GREEN if mae <= 2 else (YELLOW if mae <= 4 else RED)

        print(
            f"  {name:<16s}  "
            f"{mae_color}{mae:5.1f}{RESET}  "
            f"{bias_color}{bias:+6.1f}{RESET}  "
            f"{rmse:5.1f}  "
            f"{max_abs:8d}"
        )

    # GEFS ensemble calibration
    in_range = sum(1 for r in verified if r.get("in_p10_p90"))
    n = len(verified)
    pct = in_range / n * 100 if n else 0
    ideal = 80.0  # P10-P90 should contain ~80% of outcomes
    cal_color = GREEN if abs(pct - ideal) <= 15 else (YELLOW if abs(pct - ideal) <= 30 else RED)

    print(f"\n  {BOLD}GEFS Ensemble Calibration{RESET}")
    print(f"  Actual in P10–P90 range:  {cal_color}{in_range}/{n} ({pct:.0f}%){RESET}  (ideal ≈ 80%)")

    if verified:
        ranks = [r["gefs_rank"] for r in verified if r.get("gefs_rank") is not None]
        if ranks:
            mean_rank = sum(ranks) / len(ranks) * 100
            rank_color = GREEN if 30 < mean_rank < 70 else YELLOW
            print(f"  Mean ensemble rank:       {rank_color}{mean_rank:.0f}th percentile{RESET}  (ideal ≈ 50th)")

    # Model ranking
    print(f"\n  {BOLD}Model Ranking (by MAE){RESET}")
    ranking = []
    for name, key in zip(model_names, error_keys):
        errors = [r[key] for r in verified if r.get(key) is not None]
        if errors:
            mae = sum(abs(e) for e in errors) / len(errors)
            ranking.append((mae, name))
    ranking.sort()
    for i, (mae, name) in enumerate(ranking):
        medal = ["🥇", "🥈", "🥉", "  "][i] if i < 4 else "  "
        print(f"  {medal} {name:<16s}  MAE = {mae:.1f}°F")

    # Per-station breakdown if multiple stations
    stations = sorted(set(r["station"] for r in verified))
    if len(stations) > 1:
        print(f"\n  {BOLD}Per-Station Breakdown{RESET}")
        for stn in stations:
            stn_results = [r for r in verified if r["station"] == stn]
            print(f"\n  {CYAN}{stn}{RESET} ({len(stn_results)} predictions)")
            for name, key in zip(model_names, error_keys):
                errors = [r[key] for r in stn_results if r.get(key) is not None]
                if errors:
                    mae = sum(abs(e) for e in errors) / len(errors)
                    bias = sum(errors) / len(errors)
                    print(f"    {name:<16s}  MAE={mae:.1f}  bias={bias:+.1f}")

    print()


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    predictions = load_predictions()
    if not predictions:
        print("No prediction files found in predictions/")
        sys.exit(1)

    print(f"\nFound {len(predictions)} prediction file(s):")
    for p in predictions:
        print(f"  {p['_file']:50s}  {p['station']}  {p['date']}")
    print()

    results = []
    for pred in predictions:
        pred_date = date.fromisoformat(pred["date"])
        if pred_date > TODAY:
            print(f"  {pred['_file']}: {DIM}future date ({pred['date']}), skipping{RESET}")
            results.append({
                "file": pred["_file"],
                "station": pred["station"],
                "date": pred["date"],
                "tz": pred["tz"],
                "offset": pred.get("offset", 0),
                "is_today": False,
                "lead_hours": 0,
                "obs_count": 0,
                "obs_high": None,
                "obs_hourly": {},
                "gefs_median": (pred.get("models", {}).get("gefs") or {}).get("medianHigh"),
                "gefs_mean": None,
                "gefs_p10": (pred.get("models", {}).get("gefs") or {}).get("p10High"),
                "gefs_p90": (pred.get("models", {}).get("gefs") or {}).get("p90High"),
                "member_highs": (pred.get("models", {}).get("gefs") or {}).get("memberHighs", []),
                "hrrr_high": (pred.get("models", {}).get("hrrr") or {}).get("predictedHigh"),
                "nam_high": (pred.get("models", {}).get("nam") or {}).get("predictedHigh"),
                "twc_high": (pred.get("models", {}).get("twc") or {}).get("predictedHigh"),
            })
            continue
        result = analyze_prediction(pred)
        if result:
            results.append(result)

    print_report(results)


if __name__ == "__main__":
    main()
