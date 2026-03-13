# Polymarket GEFS Temperature Bot

Paper-trade Polymarket "Highest temperature in {CITY} on {DATE}?" markets using GEFS ensemble forecasts.

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

## Strategy Rules

| Parameter | Default |
|---|---|
| Cities | Seattle, Chicago, London |
| Edge threshold | 10% (model prob − market price) |
| Min entry price | $0.02 |
| Stake per trade | $100 |
| Min event volume | $20,000 |

The bot picks the outcome bin with the largest edge. A trade opens only if edge ≥ 10% and entry price ≥ $0.02.

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
```
