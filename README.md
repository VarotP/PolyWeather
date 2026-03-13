# Polymarket GEFS Temperature Bot

Paper-trade Polymarket "Highest temperature in {CITY} on {DATE}?" markets using GEFS ensemble forecasts, with multi-model weather dashboards for analysis and prediction tuning.

## Setup

```bash
cd ~/VSC/polymarketbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Commands

### `polymarket_gefs.py` — Forecast Explorer

View GEFS ensemble probabilities vs Polymarket prices.

```bash
# Auto-discover all tomorrow's temperature markets
python3 polymarket_gefs.py

# Specific city (tomorrow by default)
python3 polymarket_gefs.py --city NYC
python3 polymarket_gefs.py --city Seattle
python3 polymarket_gefs.py --city Paris

# Specific city + date
python3 polymarket_gefs.py --city Atlanta --date 2026-03-06

# List all active temperature markets for a date
python3 polymarket_gefs.py --list
python3 polymarket_gefs.py --list --date 2026-03-07

# Verbose mode
python3 polymarket_gefs.py --city Chicago -v

# Clear cached API data
python3 polymarket_gefs.py --clear-cache
```

### `polymarket_papertrade.py` — Paper Trading

Open trades, settle them, and evaluate performance.

```bash
# Run strategy — opens up to 1 trade per city (Seattle, Chicago, London)
python3 polymarket_papertrade.py --run
python3 polymarket_papertrade.py --run --verbose

# Settle open trades using Polymarket's resolved outcome
python3 polymarket_papertrade.py --settle

# Print evaluation metrics (win rate, P&L, Brier scores, per-city breakdown)
python3 polymarket_papertrade.py --eval
```

### `server.py` — Weather Dashboard Server

Local HTTP server that serves the weather dashboards and proxies external API requests to bypass CORS.

```bash
python3 server.py          # starts on port 8080
python3 server.py 9000     # custom port
```

Dashboards available at:
- `http://localhost:8080/weather_predict.html` — Multi-model forecast dashboard
- `http://localhost:8080/weather_dashboard_metar.html` — METAR observation dashboard
- `http://localhost:8080/weather_compare.html` — METAR vs Weather.com comparison

The server also exposes:
- `GET /proxy?url=<encoded_url>` — CORS proxy (allowlisted: weather.com, aviationweather.gov, weather.gov, open-meteo.com, mesonet.agron.iastate.edu)
- `POST /save-prediction` — Save a prediction snapshot to `predictions/`
- `GET /predictions` — List saved prediction files

## Weather Dashboards

### Predictive Dashboard (`weather_predict.html`)

Multi-model temperature forecast combining five data sources on a single chart and table:

| Source | Type | Provider | Resolution |
|--------|------|----------|------------|
| **METAR** | Observations | aviationweather.gov (today) / IEM archive (historical) | Per-observation (~hourly + SPECI) |
| **GEFS** | Ensemble (31 members) | Open-Meteo | Hourly, with P10/P25/P50/P75/P90 bands |
| **HRRR** | Deterministic (3km) | Open-Meteo | Hourly |
| **NAM** | Deterministic (3km) | Open-Meteo | Hourly |
| **TWC** | Blended model | Weather.com API | Hourly (48h forecast) |

Features:
- Summary cards: current temp, observed high, and predicted high from each model
- Interactive Chart.js graph with ensemble confidence bands, model lines, and observed temperature
- Precipitation radar map (RainViewer) with animation controls (today only)
- Hourly data table with all model outputs side-by-side
- Live local time clock for the selected station
- **Save Predictions** button to capture all model data as JSON for later tuning
- Station coordinates sourced from Weather.com's `v3/location/point` API for consistent grid-point alignment
- Dynamic station lookup via aviationweather.gov for ICAO codes not in the built-in database

### METAR Standalone Dashboard (`weather_dashboard_metar.html`)

Source-of-truth observation dashboard using custom METAR parsing:
- Parses raw METAR strings with T-group precision temperatures
- Calculates feels-like (NWS wind chill / heat index), RH, station pressure
- Temperature chart (observed, feels like, dew point)
- Full historical date support via IEM ASOS archive

### Comparison Dashboard (`weather_compare.html`)

Side-by-side comparison of custom METAR parsing vs Weather.com API output:
- Validates parsing accuracy against Weather.com's production data
- Matches observations by closest timestamp
- Highlights discrepancies between the two sources

### `metar_to_weathercom.py` — METAR Parser CLI

Standalone Python script to parse raw METAR data into Weather.com's JSON format.

```bash
python3 metar_to_weathercom.py                          # KSEA, last 24h
python3 metar_to_weathercom.py --station KJFK           # Different station
python3 metar_to_weathercom.py --station KSEA --hours 6 # Last 6 hours
python3 metar_to_weathercom.py --compare                # Side-by-side with Weather.com
```

## Prediction Snapshots

Clicking **Save Predictions** in the predictive dashboard writes a JSON file to `predictions/`:

```
predictions/
├── KSEA_2026-03-13_20260313T031722Z.json
├── KLGA_2026-03-14_20260313T234622Z.json
└── ...
```

Each snapshot contains:
- Station metadata (ICAO, date, timezone, coordinates, elevation)
- All METAR observations (temp, feels like, dewpoint, RH, wind, wx, raw METAR string)
- GEFS: hourly percentile stats, member highs array, grid coordinates
- HRRR: hourly temps, predicted high, grid coordinates
- NAM: hourly temps, predicted high, grid coordinates
- TWC: hourly temps, predicted high

These snapshots are intended for offline analysis and model-tuning workflows.

## Strategy Rules

| Parameter | Default |
|---|---|
| Cities | Seattle, Chicago, London |
| Edge threshold | 10% (model prob − market price) |
| Min entry price | $0.02 |
| Stake per trade | $100 |
| Min event volume | $20,000 |

The bot picks the outcome bin with the largest edge. A trade opens only if edge >= 10% and entry price >= $0.02.

Settlement uses **Polymarket's own resolution** (Gamma API), not a weather API. Each market resolves from a specific Wunderground airport station (e.g. KSEA for Seattle, KORD for Chicago, EGLC for London).

## Cron Schedule

```cron
# Run strategy daily after 18Z GEFS becomes available (19:15 UTC)
15 19 * * * /Users/evlav/VSC/polymarketbot/.venv/bin/python3 /Users/evlav/VSC/polymarketbot/polymarket_papertrade.py --run >> /Users/evlav/VSC/polymarketbot/log_run.log 2>&1

# Settle trades hourly (markets resolve after Wunderground finalizes)
0 * * * * /Users/evlav/VSC/polymarketbot/.venv/bin/python3 /Users/evlav/VSC/polymarketbot/polymarket_papertrade.py --settle >> /Users/evlav/VSC/polymarketbot/log_settle.log 2>&1

# Evaluate daily
30 19 * * * /Users/evlav/VSC/polymarketbot/.venv/bin/python3 /Users/evlav/VSC/polymarketbot/polymarket_papertrade.py --eval >> /Users/evlav/VSC/polymarketbot/log_eval.log 2>&1
```

## File Layout

```
polymarketbot/
├── polymarket_gefs.py          # Forecast explorer CLI + shared API functions
├── polymarket_papertrade.py    # Paper trading CLI (--run / --settle / --eval)
├── config.py                   # Thresholds, city list, file paths
├── papertrade_store.py         # CSV trade log read/write
├── settle.py                   # Settlement via Polymarket Gamma API
├── eval.py                     # Evaluation metrics
├── requirements.txt            # numpy, requests
│
├── server.py                   # Local dev server + CORS proxy + prediction save
├── weather_predict.html        # Multi-model predictive dashboard
├── weather_dashboard_metar.html# METAR observation standalone dashboard
├── weather_compare.html        # METAR vs Weather.com comparison dashboard
├── metar_to_weathercom.py      # METAR → Weather.com JSON parser (Python CLI)
│
├── predictions/                # Saved prediction snapshots (JSON)
├── log_run.log                 # Cron output: strategy runs
├── log_settle.log              # Cron output: settlement
└── log_eval.log                # Cron output: evaluation
```

Trade data lives in `~/.cache/polymarket_gefs/papertrades/`:
- `trades.csv` — one row per trade (OPEN → SETTLED)
- `runs.jsonl` — diagnostics: full bin tables, skip reasons

## Monitoring

```bash
# Watch settlement log
tail -f ~/VSC/polymarketbot/log_settle.log

# Check open trades
python3 -c "from papertrade_store import read_trades; [print(f'{r[\"city\"]:>10s}  {r[\"target_date\"]}  {r[\"selected_outcome_title\"]}  {r[\"status\"]}') for r in read_trades()]"

# View raw trades CSV
column -s, -t < ~/.cache/polymarket_gefs/papertrades/trades.csv | less -S

# Start dashboards
python3 server.py
# then open http://localhost:8080/weather_predict.html
```
