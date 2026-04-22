# v13 CPR Strategy — Setup Guide

## Strategy Summary
- **Instrument**: NIFTY Weekly Options (Sell)
- **Timeframe**: Daily CPR zones + EMA(20) bias
- **Trades**: ~62/year | WR: 72.8% | Sharpe: 5.54 | Max DD: ₹16,140
- **5-Year P&L**: ₹3,84,866 (₹76,973/yr)

---

## Files
| File | Purpose |
|------|---------|
| `backtest_report.html` | Open in browser — full backtest results |
| `config.py` | **Fill your API keys here first** |
| `strategy.py` | CPR zones, signal logic, trailing stop |
| `angelone.py` | Angel One Smart API data fetching |
| `openalgo.py` | OpenAlgo paper/live order placement |
| `trader.py` | Main trading script |
| `requirements.txt` | Python dependencies |

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Angel One Smart API setup
1. Login to `smartapi.angelone.in`
2. Create new app → get API Key
3. Enable TOTP in security settings → save TOTP secret
4. Fill in `config.py`:
```python
ANGELONE_API_KEY   = "your_api_key"
ANGELONE_CLIENT_ID = "your_client_id"    # e.g. A123456
ANGELONE_PASSWORD  = "your_password"
ANGELONE_TOTP_KEY  = "your_totp_secret"  # 32-char base32 string
```

### 3. OpenAlgo setup (paper trading)
1. Install OpenAlgo: `pip install openalgo` or clone from GitHub
2. Start OpenAlgo server: `python openalgo.py` (runs on localhost:5000)
3. Get API key from OpenAlgo dashboard
4. Fill in `config.py`:
```python
OPENALGO_HOST    = "http://127.0.0.1:5000"
OPENALGO_API_KEY = "your_openalgo_key"
```

---

## Running

### Paper trade (safe — no real orders)
```bash
python trader.py
```

### Dry run (no orders at all — just logging)
```bash
python trader.py --dry-run
```

### Live trade (real orders — use with caution)
```bash
python trader.py --live
```

---

## Daily Routine
Run at **09:10 AM** every trading day:
```bash
# Add to cron (09:10 AM weekdays)
10 9 * * 1-5 cd /path/to/v13 && python trader.py >> logs/trade.log 2>&1
```

---

## How It Works

### Morning (09:10–09:15)
1. Fetch previous 25 days of NIFTY daily OHLC from Angel One
2. Compute CPR levels (PP, BC, TC, R1-R4, S1-S4) from prev day H/L/C
3. Compute EMA(20) on last 20 closing prices
4. Get today's opening price at 09:15
5. Classify zone (where does open fall relative to CPR levels?)
6. Determine signal: SELL CE or SELL PE based on zone + EMA bias

### Entry (09:16–09:31)
- Wait until zone's optimal entry time (from backtest grid)
- Check IV filter: ITM1 option premium > 0.47% of spot
- Check body filter: prev day |close-open| > 0.10% of open
- Place SELL order via OpenAlgo

### Monitoring (every 5 seconds)
- **3-tier trailing stop**:
  - 25% premium decay → stop moves to breakeven
  - 40% decay → stop moves to 80% of entry
  - 60% decay → stop locks at 95% of max decay
- **Spot SL** (for tc_to_pdh + bear EMA only): exit when NIFTY spot > PDH + 25 points
- **Target**: exit when premium decays to target %

### EOD exit: 15:20:00 unconditionally

---

## Zone Signal Rules

| Zone | EMA Bias | Signal | SL Type |
|------|----------|--------|---------|
| above_r4, r3_r4, r2_r3, r1_r2 | any | SELL PE | % SL |
| pdh_to_r1 | bear | SELL PE | % SL |
| tc_to_pdh | bear | SELL PE | **Spot SL (PDH+25)** |
| tc_to_pdh | bull | SELL PE | % SL |
| within_cpr | bull | SELL PE | % SL |
| within_cpr | bear | SELL CE | % SL |
| pdl_to_bc | bull | SELL PE | % SL |
| pdl_to_s1, s1_s2, s3_s4, below_s4 | bear | SELL CE | % SL |

---

## Important Notes
- **One trade per day** — strategy exits before looking for new signals
- **Lot size**: 75 (NIFTY standard)
- **Product**: MIS (intraday margin)
- **Never trade on expiry day** (DTE=0 filtered out)
- Always test with `PAPER_TRADE=True` for at least 1 month before going live
