"""
v13 CPR Strategy — Configuration
=================================
Fill in your API credentials before running.
"""

# ── Angel One Smart API ────────────────────────────────────────────
ANGELONE_API_KEY    = "YOUR_ANGELONE_API_KEY"
ANGELONE_CLIENT_ID  = "YOUR_CLIENT_ID"
ANGELONE_PASSWORD   = "YOUR_PASSWORD"
ANGELONE_TOTP_KEY   = "YOUR_TOTP_SECRET_KEY"   # from Smart API app registration

# ── OpenAlgo ───────────────────────────────────────────────────────
OPENALGO_HOST       = "http://127.0.0.1:5000"
OPENALGO_API_KEY    = "YOUR_OPENALGO_API_KEY"
OPENALGO_STRATEGY   = "V13_CPR_PAPER"

# ── Strategy constants ─────────────────────────────────────────────
LOT_SIZE    = 75
STRIKE_INT  = 50
EMA_PERIOD  = 20
EOD_EXIT    = "15:20:00"
IV_MIN      = 0.47       # % — minimum IV proxy filter
BODY_MIN    = 0.10       # % — minimum previous day body filter

# ── Trading mode ───────────────────────────────────────────────────
PAPER_TRADE = True       # True = paper trade via OpenAlgo, False = live
LOG_FILE    = "v13_live.log"

# ── Best params from v13 grid search (30b_zone_v13_params.csv) ─────
# Format: (zone, ema_bias, opt) → (strike_type, entry_time, target_pct, sl_param, sl_type)
BEST_PARAMS = {
    ("above_r4",   "bull", "PE"): ("ITM1", "09:31:02", 0.50, 2.00, "pct"),
    ("below_s4",   "bear", "CE"): ("ITM1", "09:16:02", 0.20, 0.50, "pct"),
    ("pdh_to_r1",  "bear", "PE"): ("OTM1", "09:20:02", 0.50, 0.50, "pct"),
    ("pdl_to_bc",  "bull", "PE"): ("OTM1", "09:31:02", 0.20, 2.00, "pct"),
    ("pdl_to_s1",  "bear", "CE"): ("ITM1", "09:20:02", 0.20, 2.00, "pct"),
    ("r1_to_r2",   "bear", "PE"): ("ATM",  "09:20:02", 0.50, 2.00, "pct"),
    ("r1_to_r2",   "bull", "PE"): ("OTM1", "09:16:02", 0.50, 1.00, "pct"),
    ("r2_to_r3",   "bull", "PE"): ("ATM",  "09:20:02", 0.20, 1.50, "pct"),
    ("r2_to_r3",   "bear", "PE"): ("ATM",  "09:20:02", 0.20, 1.50, "pct"),
    ("r3_to_r4",   "bull", "PE"): ("ITM1", "09:25:02", 0.20, 0.50, "pct"),
    ("s1_to_s2",   "bear", "CE"): ("ATM",  "09:16:02", 0.50, 2.00, "pct"),
    ("s3_to_s4",   "bear", "CE"): ("ITM1", "09:20:02", 0.40, 0.50, "pct"),
    ("tc_to_pdh",  "bear", "PE"): ("OTM1", "09:31:02", 0.50, 25,   "spot"),  # spot SL: PDH+25
    ("tc_to_pdh",  "bull", "PE"): ("ATM",  "09:25:02", 0.20, 0.50, "pct"),
    ("within_cpr", "bear", "CE"): ("ATM",  "09:16:02", 0.20, 2.00, "pct"),
    ("within_cpr", "bull", "PE"): ("ATM",  "09:20:02", 0.30, 2.00, "pct"),
}
