"""
Parse raw METAR data and output it in Weather.com's historical observation JSON format.

Fetches from aviationweather.gov (free, no API key) and converts using the same
logic Weather.com/IBM uses: T-group precision temps, NWS wind chill & heat index
formulas, Magnus-formula RH, etc.

Usage:
    python metar_to_weathercom.py                          # KSEA, last 24h
    python metar_to_weathercom.py --station KJFK           # Different station
    python metar_to_weathercom.py --station KSEA --hours 6 # Last 6 hours
    python metar_to_weathercom.py --compare                # Side-by-side with Weather.com
"""

import argparse
import json
import math
import re
import sys
import urllib.request
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# METAR parser
# ---------------------------------------------------------------------------

# Weather phenomena lookup
WX_PHENOMENA = {
    "DZ": "Drizzle", "RA": "Rain", "SN": "Snow", "SG": "Snow Grains",
    "IC": "Ice Crystals", "PL": "Ice Pellets", "GR": "Hail",
    "GS": "Small Hail", "UP": "Unknown Precip",
    "BR": "Mist", "FG": "Fog", "FU": "Smoke", "VA": "Volcanic Ash",
    "DU": "Dust", "SA": "Sand", "HZ": "Haze", "PY": "Spray",
    "PO": "Dust Whirls", "SQ": "Squall", "FC": "Funnel Cloud",
    "SS": "Sandstorm", "DS": "Dust Storm",
}

WX_DESCRIPTORS = {
    "MI": "Shallow", "BC": "Patches of", "PR": "Partial",
    "DR": "Drifting", "BL": "Blowing", "SH": "Showers",
    "TS": "Thunderstorm", "FZ": "Freezing",
}

WX_INTENSITY = {"-": "Light", "+": "Heavy", "": ""}

PRESSURE_TEND_CODE = {
    0: ("Rising then falling", None),
    1: ("Rising then steady", "Rising"),
    2: ("Rising", "Rising"),
    3: ("Steady or rising then falling", "Rising"),
    4: ("Steady", "Steady"),
    5: ("Falling then rising", "Rising Rapidly"),
    6: ("Falling then steady", "Falling"),
    7: ("Falling", "Falling"),
    8: ("Steady or falling then rising", "Falling Rapidly"),
}

CARDINAL_DIRS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def deg_to_cardinal(deg):
    if deg is None:
        return None
    return CARDINAL_DIRS[round(deg / 22.5) % 16]


def parse_metar(raw):
    """Parse a raw METAR string into a structured dict."""
    m = {
        "raw": raw.strip(),
        "type": None,        # METAR or SPECI
        "station": None,
        "time_utc": None,    # datetime
        "wind_dir": None,    # degrees (int) or "VRB"
        "wind_speed_kt": None,
        "wind_gust_kt": None,
        "visibility_sm": None,
        "wx_codes": [],      # list of (intensity, descriptor, phenomenon, vicinity)
        "cloud_layers": [],  # list of (cover, base_ft)
        "temp_c": None,      # integer from main body
        "dewp_c": None,
        "temp_precise_c": None,   # from T-group (tenths)
        "dewp_precise_c": None,
        "altimeter_inhg": None,
        "slp_hpa": None,
        "precip_1hr_in": None,    # Pxxxx group
        "precip_6hr_in": None,    # 6xxxx group
        "precip_24hr_in": None,   # 7xxxx group
        "max_temp_6hr_c": None,   # 1xxxx group
        "min_temp_6hr_c": None,   # 2xxxx group
        "pressure_tend": None,    # (code, change_hpa)
        "peak_wind_dir": None,
        "peak_wind_kt": None,
        "auto": False,
    }

    tokens = raw.strip().split()
    i = 0

    # Type
    if tokens[i] in ("METAR", "SPECI"):
        m["type"] = tokens[i]
        i += 1

    # Station
    if i < len(tokens) and re.match(r"^[A-Z]{4}$", tokens[i]):
        m["station"] = tokens[i]
        i += 1

    # Time group: DDHHMMz
    if i < len(tokens):
        tm = re.match(r"^(\d{2})(\d{2})(\d{2})Z$", tokens[i])
        if tm:
            now = datetime.now(timezone.utc)
            day, hour, minute = int(tm.group(1)), int(tm.group(2)), int(tm.group(3))
            try:
                m["time_utc"] = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
                if m["time_utc"] > now:
                    month = now.month - 1 or 12
                    year = now.year if month != 12 else now.year - 1
                    m["time_utc"] = m["time_utc"].replace(month=month, year=year)
            except ValueError:
                pass
            i += 1

    # AUTO
    if i < len(tokens) and tokens[i] == "AUTO":
        m["auto"] = True
        i += 1

    # Wind group
    if i < len(tokens):
        wm = re.match(r"^(VRB|\d{3})(\d{2,3})(G(\d{2,3}))?KT$", tokens[i])
        if wm:
            m["wind_dir"] = None if wm.group(1) == "VRB" else int(wm.group(1))
            m["wind_speed_kt"] = int(wm.group(2))
            m["wind_gust_kt"] = int(wm.group(4)) if wm.group(4) else None
            i += 1

    # Variable wind direction (ignored for our purposes)
    if i < len(tokens) and re.match(r"^\d{3}V\d{3}$", tokens[i]):
        i += 1

    # Visibility
    if i < len(tokens):
        vis_match = re.match(r"^(\d+)SM$", tokens[i])
        frac_match = re.match(r"^(\d+)/(\d+)SM$", tokens[i])
        mixed_match = re.match(r"^(\d+)\s+(\d+)/(\d+)SM$", " ".join(tokens[i:i+2]))

        if tokens[i] == "M1/4SM":
            m["visibility_sm"] = 0.25
            i += 1
        elif mixed_match:
            m["visibility_sm"] = int(mixed_match.group(1)) + int(mixed_match.group(2)) / int(mixed_match.group(3))
            i += 2
        elif frac_match:
            m["visibility_sm"] = int(frac_match.group(1)) / int(frac_match.group(2))
            i += 1
        elif vis_match:
            m["visibility_sm"] = int(vis_match.group(1))
            i += 1

    # Weather phenomena and cloud layers (everything before RMK)
    while i < len(tokens) and tokens[i] != "RMK":
        tok = tokens[i]

        # Weather phenomena
        wx_match = re.match(
            r"^([+-]|VC)?"
            r"(MI|BC|PR|DR|BL|SH|TS|FZ)?"
            r"(DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)$",
            tok
        )
        if wx_match:
            intensity = wx_match.group(1) or ""
            vicinity = intensity == "VC"
            if vicinity:
                intensity = ""
            m["wx_codes"].append({
                "intensity": intensity,
                "descriptor": wx_match.group(2) or "",
                "phenomenon": wx_match.group(3) or "",
                "vicinity": vicinity,
            })
            i += 1
            continue

        # Cloud layers
        cld_match = re.match(r"^(FEW|SCT|BKN|OVC|CLR|SKC|VV)(\d{3})?$", tok)
        if cld_match:
            cover = cld_match.group(1)
            base = int(cld_match.group(2)) * 100 if cld_match.group(2) else None
            m["cloud_layers"].append({"cover": cover, "base_ft": base})
            i += 1
            continue

        # Temp/Dew group
        td_match = re.match(r"^(M?\d{1,2})/(M?\d{1,2})?$", tok)
        if td_match:
            t_str = td_match.group(1)
            d_str = td_match.group(2)
            m["temp_c"] = -int(t_str[1:]) if t_str.startswith("M") else int(t_str)
            if d_str:
                m["dewp_c"] = -int(d_str[1:]) if d_str.startswith("M") else int(d_str)
            i += 1
            continue

        # Altimeter
        alt_match = re.match(r"^A(\d{4})$", tok)
        if alt_match:
            m["altimeter_inhg"] = int(alt_match.group(1)) / 100.0
            i += 1
            continue

        i += 1

    # --- Remarks section ---
    rmk_start = None
    for j, tok in enumerate(tokens):
        if tok == "RMK":
            rmk_start = j + 1
            break

    if rmk_start is not None:
        rmk_text = " ".join(tokens[rmk_start:])

        # T-group: precise temp/dewpoint to tenths of °C
        t_match = re.search(r"T(\d)(\d{3})(\d)(\d{3})", rmk_text)
        if t_match:
            t_sign = -1 if t_match.group(1) == "1" else 1
            t_val = int(t_match.group(2)) / 10.0 * t_sign
            d_sign = -1 if t_match.group(3) == "1" else 1
            d_val = int(t_match.group(4)) / 10.0 * d_sign
            m["temp_precise_c"] = t_val
            m["dewp_precise_c"] = d_val

        # SLP group
        slp_match = re.search(r"SLP(\d{3})", rmk_text)
        if slp_match:
            slp_raw = int(slp_match.group(1))
            m["slp_hpa"] = (1000.0 + slp_raw / 10.0) if slp_raw < 500 else (900.0 + slp_raw / 10.0)

        # Precip last hour: Pxxxx (hundredths of inch)
        p_match = re.search(r"P(\d{4})", rmk_text)
        if p_match:
            m["precip_1hr_in"] = int(p_match.group(1)) / 100.0

        # Precip 3/6 hour: 6xxxx (hundredths of inch) — at 00, 06, 12, 18Z
        p6_match = re.search(r"(?<!\d)6(\d{4})(?!\d)", rmk_text)
        if p6_match:
            m["precip_6hr_in"] = int(p6_match.group(1)) / 100.0

        # Precip 24 hour: 7xxxx (hundredths of inch) — at 12Z
        p24_match = re.search(r"(?<!\d)7(\d{4})(?!\d)", rmk_text)
        if p24_match:
            m["precip_24hr_in"] = int(p24_match.group(1)) / 100.0

        # Max temp 6hr: 1snTTT
        max_match = re.search(r"(?<!\d)1(\d)(\d{3})(?!\d)", rmk_text)
        if max_match:
            sign = -1 if max_match.group(1) == "1" else 1
            m["max_temp_6hr_c"] = int(max_match.group(2)) / 10.0 * sign

        # Min temp 6hr: 2snTTT
        min_match = re.search(r"(?<!\d)2(\d)(\d{3})(?!\d)", rmk_text)
        if min_match:
            sign = -1 if min_match.group(1) == "1" else 1
            m["min_temp_6hr_c"] = int(min_match.group(2)) / 10.0 * sign

        # Pressure tendency: 5appp
        ptend_match = re.search(r"(?<!\d)5(\d)(\d{3})(?!\d)", rmk_text)
        if ptend_match:
            code = int(ptend_match.group(1))
            change = int(ptend_match.group(2)) / 10.0
            m["pressure_tend"] = (code, change)

        # Peak wind: PK WND dddff(f)/hhmm
        pk_match = re.search(r"PK WND (\d{3})(\d{2,3})/(\d{2,4})", rmk_text)
        if pk_match:
            m["peak_wind_dir"] = int(pk_match.group(1))
            m["peak_wind_kt"] = int(pk_match.group(2))

    return m


# ---------------------------------------------------------------------------
# Unit conversions (matching Weather.com's rounding behavior)
# ---------------------------------------------------------------------------

def c_to_f(c):
    """Celsius to Fahrenheit, rounded to integer like Weather.com."""
    if c is None:
        return None
    return round(c * 9.0 / 5.0 + 32.0)


def kt_to_mph(kt):
    """Knots to mph, rounded to integer."""
    if kt is None:
        return None
    return round(kt * 1.15078)


def calc_rh(temp_c, dewp_c):
    """Relative humidity via Magnus formula, matching Weather.com."""
    if temp_c is None or dewp_c is None:
        return None
    a, b = 17.625, 243.04
    rh = 100.0 * math.exp((a * dewp_c) / (b + dewp_c)) / math.exp((a * temp_c) / (b + temp_c))
    return round(min(100, max(0, rh)))


def calc_wind_chill(temp_f, wind_mph):
    """NWS wind chill formula. Valid when temp <= 50°F and wind > 3 mph."""
    if temp_f is None or wind_mph is None:
        return None
    if temp_f > 50 or wind_mph <= 3:
        return None
    wc = (35.74 + 0.6215 * temp_f
          - 35.75 * (wind_mph ** 0.16)
          + 0.4275 * temp_f * (wind_mph ** 0.16))
    return round(wc)


def calc_heat_index(temp_f, rh):
    """NWS heat index formula. Valid when temp >= 80°F."""
    if temp_f is None or rh is None:
        return None
    if temp_f < 80:
        return None
    hi = (-42.379 + 2.04901523 * temp_f + 10.14333127 * rh
          - 0.22475541 * temp_f * rh - 0.00683783 * temp_f**2
          - 0.05481717 * rh**2 + 0.00122874 * temp_f**2 * rh
          + 0.00085282 * temp_f * rh**2 - 0.00000199 * temp_f**2 * rh**2)
    return round(hi)


def calc_feels_like(temp_f, wind_mph, rh):
    """Weather.com 'feels_like': wind chill when cold, heat index when hot, temp otherwise."""
    wc = calc_wind_chill(temp_f, wind_mph)
    if wc is not None:
        return wc
    hi = calc_heat_index(temp_f, rh)
    if hi is not None:
        return hi
    return temp_f


def altimeter_to_station_pressure(alt_inhg, elevation_m):
    """
    Convert altimeter setting to station-level barometric pressure.
    This is what Weather.com reports in their historical `pressure` field —
    NOT the altimeter setting or SLP, but the actual pressure at station elevation.
    Uses the hypsometric equation with standard atmosphere lapse rate.
    """
    if alt_inhg is None or elevation_m is None:
        return None
    Ts = 288.15  # standard sea-level temp (K)
    L = 0.0065   # lapse rate (K/m)
    exp = 5.2559  # g / (L * R)
    ratio = ((Ts - L * elevation_m) / Ts) ** exp
    return round(alt_inhg * ratio, 2)


def get_ceiling(cloud_layers):
    """Ceiling = lowest BKN, OVC, or VV layer height in feet."""
    for layer in cloud_layers:
        if layer["cover"] in ("BKN", "OVC", "VV") and layer["base_ft"] is not None:
            return layer["base_ft"]
    return None


def get_sky_cover(cloud_layers):
    """Highest cloud coverage code for the sky_cover description."""
    priority = {"OVC": 4, "BKN": 3, "SCT": 2, "FEW": 1, "VV": 4, "CLR": 0, "SKC": 0}
    best = "CLR"
    for layer in cloud_layers:
        if priority.get(layer["cover"], 0) > priority.get(best, 0):
            best = layer["cover"]
    return best


# ---------------------------------------------------------------------------
# Weather phrase generation (replicating Weather.com's wx_phrase)
# ---------------------------------------------------------------------------

def build_wx_phrase(parsed, wind_mph):
    """Build a Weather.com-style weather phrase from parsed METAR data."""

    # If there are active weather phenomena, describe them
    if parsed["wx_codes"]:
        parts = []
        for wx in parsed["wx_codes"]:
            phrase_parts = []
            intensity = WX_INTENSITY.get(wx["intensity"], "")
            if intensity:
                phrase_parts.append(intensity)
            if wx["descriptor"] and wx["descriptor"] in WX_DESCRIPTORS:
                desc = WX_DESCRIPTORS[wx["descriptor"]]
                if wx["descriptor"] == "FZ":
                    phrase_parts.append("Freezing")
                elif wx["descriptor"] == "SH":
                    if not wx["phenomenon"]:
                        phrase_parts.append("Showers")
                elif wx["descriptor"] == "TS":
                    phrase_parts.append("Thunderstorm")
                else:
                    phrase_parts.append(desc)
            if wx["phenomenon"] in WX_PHENOMENA:
                phen = WX_PHENOMENA[wx["phenomenon"]]
                if wx["descriptor"] == "SH" and wx["phenomenon"] == "RA":
                    phen = "Rain Showers"
                elif wx["descriptor"] == "SH" and wx["phenomenon"] == "SN":
                    phen = "Snow Showers"
                elif wx["descriptor"] == "FZ" and wx["phenomenon"] == "RA":
                    phen = "Freezing Rain"
                elif wx["descriptor"] == "FZ" and wx["phenomenon"] == "DZ":
                    phen = "Freezing Drizzle"
                phrase_parts.append(phen)
            if wx["vicinity"]:
                parts.append(" ".join(phrase_parts) + " in Vicinity")
            else:
                parts.append(" ".join(phrase_parts))

        phrase = " and ".join(parts) if len(parts) <= 2 else parts[0]
    else:
        # No weather — describe by cloud cover
        sky = get_sky_cover(parsed["cloud_layers"])
        sky_phrases = {
            "OVC": "Cloudy", "BKN": "Mostly Cloudy", "SCT": "Partly Cloudy",
            "FEW": "Mostly Clear", "CLR": "Clear", "SKC": "Clear", "VV": "Cloudy",
        }
        phrase = sky_phrases.get(sky, "Fair")

    # Weather.com appends " / Windy" when sustained wind >= 22 mph
    if wind_mph is not None and wind_mph >= 22:
        phrase += " / Windy"

    return phrase


# ---------------------------------------------------------------------------
# Day/Night indicator
# ---------------------------------------------------------------------------

def get_day_ind(dt_utc, station):
    """Approximate day/night. Uses rough sunrise/sunset for known stations."""
    # For a proper implementation you'd use solar position calculation.
    # This uses a simple UTC hour approximation for US stations.
    hour_utc = dt_utc.hour
    SUNRISE_SUNSET_UTC = {
        "KSEA": (14, 2),   # ~7am PDT (14Z) to ~7pm PDT (02Z next day)
        "KJFK": (11, 23),  # ~6am EDT (11Z) to ~7pm EDT (23Z)
        "KORD": (12, 0),   # ~6am CDT (12Z) to ~7pm CDT (00Z)
        "KLAX": (14, 2),   # ~7am PDT (14Z) to ~7pm PDT (02Z)
    }
    rise, sset = SUNRISE_SUNSET_UTC.get(station, (12, 0))
    if rise < sset:
        return "D" if rise <= hour_utc < sset else "N"
    else:
        return "N" if sset <= hour_utc < rise else "D"


# ---------------------------------------------------------------------------
# Weather.com icon mapping (approximate)
# ---------------------------------------------------------------------------

WX_ICON_MAP = {
    ("Clear", "D"): (32, 3200), ("Clear", "N"): (31, 3100),
    ("Mostly Clear", "D"): (34, 3400), ("Mostly Clear", "N"): (33, 3300),
    ("Partly Cloudy", "D"): (30, 3000), ("Partly Cloudy", "N"): (29, 2900),
    ("Mostly Cloudy", "D"): (28, 2800), ("Mostly Cloudy", "N"): (27, 2700),
    ("Cloudy", "D"): (26, 2600), ("Cloudy", "N"): (26, 2600),
    ("Light Rain", "D"): (11, 1100), ("Light Rain", "N"): (11, 1100),
    ("Rain", "D"): (12, 1200), ("Rain", "N"): (12, 1200),
    ("Heavy Rain", "D"): (40, 4000), ("Heavy Rain", "N"): (40, 4000),
    ("Light Snow", "D"): (14, 1400), ("Light Snow", "N"): (14, 1400),
    ("Snow", "D"): (16, 1600), ("Snow", "N"): (16, 1600),
    ("Heavy Snow", "D"): (43, 4300), ("Heavy Snow", "N"): (43, 4300),
    ("Thunderstorm", "D"): (4, 400), ("Thunderstorm", "N"): (4, 400),
    ("Fog", "D"): (20, 2000), ("Fog", "N"): (20, 2000),
    ("Mist", "D"): (20, 2000), ("Mist", "N"): (20, 2000),
    ("Haze", "D"): (21, 2100), ("Haze", "N"): (21, 2100),
    ("Freezing Rain", "D"): (8, 800), ("Freezing Rain", "N"): (8, 800),
}


def get_wx_icon(phrase_base, day_ind):
    """Approximate Weather.com icon code from phrase (before '/ Windy')."""
    base = phrase_base.replace(" / Windy", "").strip()
    key = (base, day_ind)
    if key in WX_ICON_MAP:
        icon, extd = WX_ICON_MAP[key]
        if "/ Windy" in phrase_base:
            extd = extd + 90  # Weather.com adds 90 for windy variants
        return icon, extd
    return 26, 2600  # Default to cloudy


# ---------------------------------------------------------------------------
# Convert parsed METAR → Weather.com format
# ---------------------------------------------------------------------------

def to_weathercom(parsed, elevation_m=None):
    """Convert a parsed METAR dict to Weather.com historical observation format.

    Args:
        parsed: Output of parse_metar()
        elevation_m: Station elevation in meters (needed for pressure conversion).
                     If None, altimeter setting is used as-is.
    """

    # Prefer T-group precision, fall back to main body
    temp_c = parsed["temp_precise_c"] if parsed["temp_precise_c"] is not None else parsed["temp_c"]
    dewp_c = parsed["dewp_precise_c"] if parsed["dewp_precise_c"] is not None else parsed["dewp_c"]

    temp_f = c_to_f(temp_c)
    dewp_f = c_to_f(dewp_c)
    wspd_mph = kt_to_mph(parsed["wind_speed_kt"])
    gust_mph = kt_to_mph(parsed["wind_gust_kt"])
    rh = calc_rh(temp_c, dewp_c)
    wc = calc_wind_chill(temp_f, wspd_mph)
    hi = calc_heat_index(temp_f, rh)
    feels = calc_feels_like(temp_f, wspd_mph, rh)

    epoch = int(parsed["time_utc"].timestamp()) if parsed["time_utc"] else None
    day_ind = get_day_ind(parsed["time_utc"], parsed["station"]) if parsed["time_utc"] else None

    wx_phrase = build_wx_phrase(parsed, wspd_mph)
    wx_icon, icon_extd = get_wx_icon(wx_phrase, day_ind)

    sky = get_sky_cover(parsed["cloud_layers"])

    # Weather.com pressure = station-level barometric pressure, not altimeter setting
    pressure = altimeter_to_station_pressure(parsed["altimeter_inhg"], elevation_m) \
        if elevation_m is not None else parsed["altimeter_inhg"]

    # Pressure tendency
    p_tend = None
    p_desc = None
    if parsed["pressure_tend"]:
        code, change = parsed["pressure_tend"]
        p_tend = code
        _, p_desc = PRESSURE_TEND_CODE.get(code, (None, None))

    # Max/min temps from 6-hour groups
    max_temp = c_to_f(parsed["max_temp_6hr_c"])
    min_temp = c_to_f(parsed["min_temp_6hr_c"])

    return {
        "key": parsed["station"],
        "class": "observation",
        "expire_time_gmt": (epoch + 1800) if epoch else None,
        "obs_id": parsed["station"],
        "obs_name": parsed["station"],
        "valid_time_gmt": epoch,
        "day_ind": day_ind,
        "temp": temp_f,
        "wx_icon": wx_icon,
        "icon_extd": icon_extd,
        "wx_phrase": wx_phrase,
        "pressure_tend": p_tend,
        "pressure_desc": p_desc,
        "dewPt": dewp_f,
        "heat_index": hi if hi is not None else temp_f,
        "rh": rh,
        "pressure": pressure,
        "vis": parsed["visibility_sm"],
        "wc": wc if wc is not None else temp_f,
        "wdir": parsed["wind_dir"],
        "wdir_cardinal": deg_to_cardinal(parsed["wind_dir"]),
        "gust": gust_mph,
        "wspd": wspd_mph,
        "max_temp": max_temp,
        "min_temp": min_temp,
        "precip_total": None,
        "precip_hrly": parsed["precip_1hr_in"] if parsed["precip_1hr_in"] is not None else 0.0,
        "snow_hrly": None,
        "uv_desc": "Low",
        "feels_like": feels,
        "uv_index": 0,
        "qualifier": None,
        "qualifier_svrty": None,
        "blunt_phrase": None,
        "terse_phrase": None,
        "clds": sky if sky not in ("CLR", "SKC") else "CLR",
        "water_temp": None,
        "primary_wave_period": None,
        "primary_wave_height": None,
        "primary_swell_period": None,
        "primary_swell_height": None,
        "primary_swell_direction": None,
        "secondary_swell_period": None,
        "secondary_swell_height": None,
        "secondary_swell_direction": None,
    }


# ---------------------------------------------------------------------------
# Fetch METARs from aviationweather.gov
# ---------------------------------------------------------------------------

# Known field elevations (meters) for stations where the FAA-published elevation
# differs from what metadata APIs return. These are the values Weather.com uses.
STATION_ELEVATIONS_M = {
    "KSEA": 132.0,  # 433 ft — FAA published field elevation
}


def fetch_station_elevation(station):
    """Fetch station elevation in meters from aviationweather.gov or NWS."""
    if station in STATION_ELEVATIONS_M:
        return STATION_ELEVATIONS_M[station]
    try:
        url = f"https://aviationweather.gov/api/data/metar?ids={station}&hours=1&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "MetarParser/1.0"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data and "elev" in data[0]:
            return data[0]["elev"]
    except Exception:
        pass
    return None


def fetch_metars_raw(station, hours=24):
    """Fetch raw METAR strings from aviationweather.gov."""
    url = f"https://aviationweather.gov/api/data/metar?ids={station}&hours={hours}&format=raw"
    req = urllib.request.Request(url, headers={"User-Agent": "MetarParser/1.0"})
    with urllib.request.urlopen(req) as resp:
        text = resp.read().decode("utf-8")
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]
    return lines


def fetch_weathercom(station, date_str=None):
    """Fetch Weather.com historical data for comparison. Returns observations list."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    else:
        date_str = date_str.replace("-", "")
    url = (
        f"https://api.weather.com/v1/location/{station}:9:US/observations/historical.json"
        f"?apiKey=e1f10a1e78da46f5b10a1e78da96f525&units=e"
        f"&startDate={date_str}&endDate={date_str}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "MetarParser/1.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("observations", [])


# ---------------------------------------------------------------------------
# Thinning: replicate Weather.com's ~hourly sampling from the firehose
# ---------------------------------------------------------------------------

def thin_to_hourly(observations, parsed_list=None):
    """
    Weather.com keeps one standard hourly METAR per hour (closest to :53)
    PLUS any SPECI (special) reports issued between hours when conditions
    change significantly. We replicate that behavior.
    """
    by_hour = {}
    specis = []
    for i, obs in enumerate(observations):
        if obs["valid_time_gmt"] is None:
            continue
        # Check if this was a SPECI by looking at the parsed data
        is_speci = False
        if parsed_list and i < len(parsed_list):
            is_speci = parsed_list[i].get("type") == "SPECI"
        elif obs.get("_metar_raw", "").startswith("SPECI"):
            is_speci = True

        if is_speci:
            specis.append(obs)
            continue

        dt = datetime.fromtimestamp(obs["valid_time_gmt"], tz=timezone.utc)
        hour_key = dt.strftime("%Y-%m-%d %H")
        if hour_key not in by_hour:
            by_hour[hour_key] = obs
        else:
            existing_dt = datetime.fromtimestamp(by_hour[hour_key]["valid_time_gmt"], tz=timezone.utc)
            if abs(dt.minute - 53) < abs(existing_dt.minute - 53):
                by_hour[hour_key] = obs
    result = sorted(list(by_hour.values()) + specis, key=lambda o: o["valid_time_gmt"])
    return result


# ---------------------------------------------------------------------------
# Comparison display
# ---------------------------------------------------------------------------

def compare_side_by_side(station, hours=24):
    """Fetch both sources and print a side-by-side comparison."""
    elev = fetch_station_elevation(station)
    print(f"\nStation {station} elevation: {elev}m ({elev * 3.28084:.0f} ft)" if elev else "")
    print(f"Fetching raw METARs for {station} (last {hours}h)...")
    raw_lines = fetch_metars_raw(station, hours)
    parsed_list = [parse_metar(line) for line in raw_lines]
    parsed_obs = [to_weathercom(p, elevation_m=elev) for p in parsed_list]
    parsed_obs = thin_to_hourly(parsed_obs, parsed_list)

    print(f"Fetching Weather.com data for {station}...")
    today = datetime.now().strftime("%Y%m%d")
    wc_obs = fetch_weathercom(station, today)

    # Index Weather.com obs by epoch
    wc_by_epoch = {o["valid_time_gmt"]: o for o in wc_obs}

    fields = ["temp", "dewPt", "rh", "wspd", "gust", "pressure", "vis",
              "wc", "feels_like", "wx_phrase", "clds", "precip_hrly"]

    print(f"\n{'Time UTC':>12}  {'Field':>12}  {'METAR→Parse':>14}  {'Weather.com':>14}  {'Match':>5}")
    print("-" * 65)

    matches = 0
    total = 0

    for obs in parsed_obs:
        epoch = obs["valid_time_gmt"]
        # Find closest Weather.com observation (within 5 min)
        wc = None
        for wc_epoch, wc_candidate in wc_by_epoch.items():
            if abs(wc_epoch - epoch) < 300:
                wc = wc_candidate
                break

        if wc is None:
            continue

        dt_str = datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%H:%M")
        for field in fields:
            ours = obs.get(field)
            theirs = wc.get(field)
            is_match = ours == theirs
            if is_match:
                matches += 1
            total += 1
            flag = "✓" if is_match else "✗"
            print(f"{dt_str:>12}  {field:>12}  {str(ours):>14}  {str(theirs):>14}  {flag:>5}")
        print()

    if total:
        print(f"Match rate: {matches}/{total} ({100*matches/total:.1f}%)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Parse raw METAR → Weather.com JSON format"
    )
    parser.add_argument("--station", default="KSEA", help="ICAO station ID (default: KSEA)")
    parser.add_argument("--hours", type=int, default=24, help="Hours of history (default: 24)")
    parser.add_argument("--compare", action="store_true", help="Side-by-side comparison with Weather.com")
    parser.add_argument("--raw", action="store_true", help="Also include parsed METAR details")
    parser.add_argument("--thin", action="store_true", help="Thin to ~hourly like Weather.com")
    args = parser.parse_args()

    if args.compare:
        compare_side_by_side(args.station, args.hours)
        return

    elev = fetch_station_elevation(args.station)
    raw_lines = fetch_metars_raw(args.station, args.hours)
    parsed_list = []
    all_obs = []
    for line in raw_lines:
        parsed = parse_metar(line)
        parsed_list.append(parsed)
        obs = to_weathercom(parsed, elevation_m=elev)
        if args.raw:
            obs["_metar_raw"] = parsed["raw"]
            obs["_temp_c_precise"] = parsed["temp_precise_c"]
            obs["_temp_c_body"] = parsed["temp_c"]
        all_obs.append(obs)

    # Reverse so oldest is first (Weather.com order)
    all_obs.reverse()
    parsed_list.reverse()

    if args.thin:
        all_obs = thin_to_hourly(all_obs, parsed_list)

    output = {
        "metadata": {
            "language": "en-US",
            "version": "1",
            "location_id": f"{args.station}:9:US",
            "units": "e",
            "status_code": 200,
            "source": "aviationweather.gov METAR → Weather.com format converter",
        },
        "observations": all_obs,
    }

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
