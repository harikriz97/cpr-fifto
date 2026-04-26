# CPR Strategy v17a + Intraday v2

NIFTY weekly options selling strategy using CPR zones, EMA(20) bias,
and intraday pivot break detection.

## Performance (5-year backtest)

| System | Trades/yr | WR% | Sharpe | Calmar | Max DD |
|--------|-----------|-----|--------|--------|--------|
| v17a Morning | 72 | 69.3% | 4.99 | 24.89 | ₹16,363 |
| Intraday v2  | 21 | 71.0% | 3.92 | 7.10  | ₹10,294 |
| **Combined** | **93** | **69.7%** | **5.11** | **24.84** | **₹19,342** |

5-year P&L: ₹4,80,435 (₹96,087/yr) · LOT_SIZE=75

## Files

| File | Purpose |
|------|---------|
| `config.py`    | API keys + strategy params — **fill this first** |
| `strategy.py`  | CPR pivots, zone classification, intraday break detection, TradeState |
| `angelone.py`  | Angel One Smart API — OHLC history + LTP |
| `openalgo.py`  | OpenAlgo REST — paper/live order placement |
| `trader.py`    | Main trader (v17a + intraday v2 combined) |
| `dashboard.py` | Streamlit dashboard — signal, monitor, log, performance |
| `data/`        | Historical backtest trades CSVs |

## Setup

```bash
pip install -r requirements.txt
```

Fill `config.py` with Angel One and OpenAlgo credentials.

## Running

```bash
streamlit run dashboard.py      # dashboard
python trader.py                # paper trade
python trader.py --dry-run      # signals only
python trader.py --live         # live orders
```

Cron (09:10 AM weekdays):
```
10 9 * * 1-5 cd /path/to/cpr-fifto && python trader.py >> v17a_live.log 2>&1
```

## How It Works

**Phase 1 — v17a Morning (09:10–09:31)**
Fetch 45d OHLC → compute prev-day CPR + EMA(20) → classify zone → signal → enter at zone entry time

**Phase 2 — Intraday v2 (09:35–10:30, no-signal days only)**
Scan 5-min candles for first pivot level break → enter next candle + 2s
Levels: R1 PE · R2 PE · TC PE · PDL CE · S1 CE · S2 CE

**Monitoring:** 3-tier lock-in trail (25%→BE, 40%→80%, 60%→95%) · EOD exit 15:20
