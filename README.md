# CPR Strategy v17a + Intraday v2

NIFTY weekly options selling strategy based on CPR zone classification, EMA(20) bias, and intraday pivot break detection.

## Performance (5-year backtest, 1 lot = 65 units)

| System | Trades/yr | WR% | Sharpe | Calmar | Max DD |
|--------|-----------|-----|--------|--------|--------|
| v17a Morning | 72 | 69.3% | 4.99 | 24.89 | Rs.16,363 |
| Intraday v2  | 21 | 71.0% | 3.92 | 7.10  | Rs.10,294 |
| **Combined** | **93** | **69.7%** | **5.11** | **24.84** | **Rs.19,342** |

5-year P&L: Rs.4,80,435 (Rs.96,087/yr)

## Strategy Overview

### Phase 1 — v17a Morning (09:10–09:31)
- Fetch 50d OHLC → compute prev-day CPR + EMA(20)
- Classify today's open into 13 price zones
- EMA(20) bias (bull/bear) + zone → signal
- Body filter: skip if prev day body < 0.10%
- Enter at zone-specific time (09:16–09:31)

### Phase 2 — Intraday v2 (09:30–11:20, no-signal days only)
- Scan 5-min candles for first pivot level break
- Levels: R1 PE · R2 PE · PDL CE · S1 CE · S2 CE
- TC removed (backtest: avg=-80, negative across all targets)
- Window extended 10:25→11:20 (+Rs.8,118 over 5yr)

### Exit Logic (3-tier trailing)
- Break-even lock: 25% premium decay
- 80% lock: 40% premium decay
- 95% trail: 60% premium decay
- EOD exit: 15:20 IST

## Lot Sizing (Theory-Based)
- DTE >= 3 AND entry premium > Rs.80 → **3x lots** (theta sweet spot)
- All other cases → **1x lot**
- Never sized from backtest win rates

## Files

| File | Purpose |
|------|---------|
| `config.py` | API keys + strategy params — **fill this first** |
| `strategy.py` | CPR pivots, zone classification, TradeState |
| `angelone.py` | Angel One Smart API client |
| `openalgo.py` | OpenAlgo REST order client |
| `trader.py` | Main trader — v17a + intraday v2 + lot sizing |
| `dashboard.py` | Streamlit dashboard — signal, monitor, log, performance |
| `dashboard_server.py` | Standalone Flask dashboard with live chart (port 8080) |
| `START_ALL.bat` | Windows launcher — starts all services |
| `data/` | Live trades + backtest CSVs |
| `artha/` | Trade visualisation charts |

## Setup

```bash
pip install -r requirements.txt
```

Fill `config.py` with your credentials:
```python
ANGELONE_API_KEY   = "..."
ANGELONE_CLIENT_ID = "..."
ANGELONE_PASSWORD  = "..."
ANGELONE_TOTP_KEY  = "..."
OPENALGO_API_KEY   = "..."
```

## Running

```bash
# Windows — start everything
START_ALL.bat

# Individual
python trader.py             # paper trade
python trader.py --dry-run   # signals only
python trader.py --live      # live orders
streamlit run dashboard.py   # performance dashboard
python dashboard_server.py   # live chart dashboard (http://localhost:8080)
```

## Expiry Handling
- NIFTY weekly expiry: **Tuesday**
- DTE=0 (expiry day): auto-skips to next week
- Holiday: Monday used when Tuesday is a market holiday

## Notes
- EMA(20) requires 40+ days seed data — fetches 50d OHLC
- Body filter on prev day (not today) to avoid forward bias
- One position at a time — no overlapping trades
- tc_to_pdh zone: skipped when DTE < 2 (falls through to intraday v2)
