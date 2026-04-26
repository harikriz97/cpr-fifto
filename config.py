"""
CPR Strategy v17a + Intraday v2 — Configuration
=================================================
Fill in your API credentials before running.
"""

# ── Angel One Smart API ────────────────────────────────────────────
ANGELONE_API_KEY    = "YOUR_ANGELONE_API_KEY"
ANGELONE_CLIENT_ID  = "YOUR_CLIENT_ID"
ANGELONE_PASSWORD   = "YOUR_PASSWORD"
ANGELONE_TOTP_KEY   = "YOUR_TOTP_SECRET_KEY"

# ── OpenAlgo ───────────────────────────────────────────────────────
OPENALGO_HOST       = "http://127.0.0.1:5000"
OPENALGO_API_KEY    = "YOUR_OPENALGO_API_KEY"
OPENALGO_STRATEGY   = "V17A_CPR_PAPER"

# ── Strategy constants ─────────────────────────────────────────────
LOT_SIZE    = 75
STRIKE_INT  = 50
EMA_PERIOD  = 20
EOD_EXIT    = "15:20:00"
IV_MIN      = 0.47
BODY_MIN    = 0.10
# tc_to_pdh on DTE=1 (expiry Thursday): WR=50%, avg=-82 → skip it
# backtest shows DTE>=2 gives WR=63%, avg=+570 for this zone
TC_TO_PDH_DTE_MIN = 2

# ── Trading mode ───────────────────────────────────────────────────
PAPER_TRADE = True
LOG_FILE    = "v17a_live.log"

# ── v17a best params (from 5yr backtest grid search) ──────────────
# Format: (zone, ema_bias, opt) → (strike_type, entry_time, target_pct, sl_param, sl_type)
# sl_type 'pct' → sl_param is multiplier; 'spot' → sl_param is points above PDH
V17A_PARAMS = {
    ("above_r4",   "bull", "PE"): ("ITM1", "09:31:02", 0.50, 2.00, "pct"),
    ("below_s4",   "bear", "CE"): ("ITM1", "09:16:02", 0.20, 0.50, "pct"),
    ("pdh_to_r1",  "bear", "PE"): ("OTM1", "09:20:02", 0.50, 0.50, "pct"),
    ("pdl_to_bc",  "bull", "PE"): ("OTM1", "09:31:02", 0.20, 1.50, "pct"),
    ("pdl_to_s1",  "bear", "CE"): ("ITM1", "09:20:02", 0.20, 2.00, "pct"),
    ("r1_to_r2",   "bear", "PE"): ("ATM",  "09:20:02", 0.50, 2.00, "pct"),
    ("r1_to_r2",   "bull", "PE"): ("OTM1", "09:16:02", 0.50, 1.00, "pct"),
    ("r2_to_r3",   "bull", "PE"): ("ATM",  "09:20:02", 0.20, 1.50, "pct"),
    ("r2_to_r3",   "bear", "PE"): ("ATM",  "09:20:02", 0.20, 1.50, "pct"),
    ("r3_to_r4",   "bull", "PE"): ("ITM1", "09:25:02", 0.20, 0.50, "pct"),
    ("s1_to_s2",   "bear", "CE"): ("ATM",  "09:16:02", 0.50, 2.00, "pct"),
    ("s3_to_s4",   "bear", "CE"): ("ITM1", "09:20:02", 0.40, 0.50, "pct"),
    ("tc_to_pdh",  "bear", "PE"): ("OTM1", "09:31:02", 0.50, 25.0, "spot"),
    ("tc_to_pdh",  "bull", "PE"): ("ITM1", "09:25:02", 0.20, 75.0, "spot"),
    ("within_cpr", "bear", "CE"): ("ATM",  "09:16:02", 0.20, 2.00, "pct"),
    ("within_cpr", "bull", "PE"): ("ATM",  "09:20:02", 0.30, 2.00, "pct"),
}

# ── Intraday v2 best params (from 5yr backtest grid search) ───────
# Format: (break_level, opt) → (strike_type, target_pct, sl_pct)
INTRADAY_PARAMS = {
    ("PDL", "CE"): ("ATM",  0.30, 2.00),
    ("R1",  "PE"): ("ATM",  0.20, 0.50),
    ("R2",  "PE"): ("ITM1", 0.50, 1.00),
    ("S1",  "CE"): ("ITM1", 0.30, 1.00),
    ("S2",  "CE"): ("OTM1", 0.40, 1.00),
    # TC REMOVED: optimization backtest showed avg=-80 (negative) across all targets on OTM1.
    # All targets 0.20-0.35 = negative avg; 0.40 = +50 (breakeven). Not worth trading.
    # ("TC",  "PE"): ("OTM1", 0.20, 0.50),
}

# Intraday v2 scan window
# Optimization test (50_intraday_v2_optimize.csv):
#   10:25 baseline → 114 trades, total=+37538
#   11:20 extended → 151 trades, total=+45656  (+8118 over 5yr, WR stable at 67%)
INTRADAY_SCAN_FROM = "09:30"
INTRADAY_SCAN_TO   = "11:20"   # extended from 10:25 — adds ~37 trades/5yr, +₹8118 pnl
INTRADAY_EOD_EXIT  = "15:20:00"
