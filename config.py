"""
config.py — Hari CPR Intraday Strategy — Central Configuration
"""
import os
from datetime import time as dtime

# ── Angel One Smart API ──────────────────────────────────────────────────────
ANGELONE_API_KEY   = "k6S2VzNN"
ANGELONE_CLIENT_ID = "pvip1030"
ANGELONE_PASSWORD  = "5131"
ANGELONE_TOTP_KEY  = "UJ2OEF4RVJQG3Q7JLRGKH4NZ3A"

# ── OpenAlgo ─────────────────────────────────────────────────────────────────
OPENALGO_HOST    = os.getenv("OPENALGO_HOST",    "http://127.0.0.1:5000")
OPENALGO_API_KEY = os.getenv("OPENALGO_API_KEY", "4292034230005c316ef7b477ea6e61685f04875817966f0b547706cfe520880d")
OPENALGO_STRATEGY = "hari-cpr"

# ── Index config ──────────────────────────────────────────────────────────────
INDICES = {
    "NIFTY": {
        "symbol":     "NIFTY",
        "lot_size":   65,
        "strike_int": 50,
        "exchange":   "NFO",
        "spot_sym":   "Nifty 50",
    },
    "SENSEX": {
        "symbol":     "SENSEX",
        "lot_size":   10,
        "strike_int": 100,
        "exchange":   "BFO",
        "spot_sym":   "SENSEX",
    },
}

# SENSEX dual mode — set to {2, 3} when SENSEX token configured in angelone.py
DUAL_INDEX_DAYS = set()   # disabled until SENSEX history source configured

# ── Strategy params ───────────────────────────────────────────────────────────
EMA_PERIOD     = 20
EMA_SEED       = 40
CAM_RATIO      = 1.1682
VIX_MAX        = 20.0
CPR_NARROW_PCT = 0.002
CPR_GAP_PCT    = 0.005
BODY_MIN       = 0.10

# Legacy (used by old trader.py / strategy.py)
LOT_SIZE           = 65
STRIKE_INT         = 50
IV_MIN             = 0.47
TC_TO_PDH_DTE_MIN  = 2
LOT_HIGH_DTE_MIN   = 3
LOT_HIGH_EP_MIN    = 80.0
LOT_HIGH_MULT      = 3
PAPER_TRADE        = True
LOG_FILE           = "v17a_live.log"

# ── V17A params: zone → (opt, strike_type, target_pct, sl_pct, entry_time) ───
# Zone names match core/levels.py classify_zone output exactly.
# Zones from classify_zone: r2_plus, r1_to_r2, pdh_to_r1, tc_to_pdh,
#   within_cpr, pdl_to_bc, pdl_to_s1, s1_to_s2, s2_to_s3, s3_to_s4, below_s4
V17A_PARAMS = {
    "r2_plus":    ("PE", "OTM1", 0.20, 1.00, "09:16:02"),  # above R2
    "r1_to_r2":   ("PE", "OTM1", 0.20, 1.00, "09:20:02"),
    "pdh_to_r1":  ("PE", "OTM1", 0.20, 1.00, "09:20:02"),  # PDH to R1
    "tc_to_pdh":  ("PE", "ITM1", 0.20, 1.00, "09:25:02"),
    "within_cpr": (None, "ATM",  0.20, 1.00, "09:20:02"),  # opt from bias
    "pdl_to_bc":  ("PE", "OTM1", 0.20, 1.00, "09:31:02"),
    "pdl_to_s1":  ("CE", "ITM1", 0.20, 1.00, "09:20:02"),
    "s1_to_s2":   ("CE", "ATM",  0.20, 1.00, "09:16:02"),
    "s2_to_s3":   ("CE", "ATM",  0.20, 1.00, "09:20:02"),
    "s3_to_s4":   ("CE", "ITM1", 0.20, 1.00, "09:20:02"),
    "below_s4":   ("CE", "ITM1", 0.20, 1.00, "09:16:02"),
}

# ── Camarilla params ──────────────────────────────────────────────────────────
CAM_L3_PARAMS = ("CE", "ITM1", 0.20, 1.00)
CAM_H3_PARAMS = ("PE", "OTM1", 0.20, 1.00)

# ── Intraday v2 params ────────────────────────────────────────────────────────
IV2_SCAN_START = dtime(9, 30)
IV2_SCAN_END   = dtime(11, 20)
IV2_PARAMS = {
    "R1":  ("PE", "ATM",  0.20, 0.50),
    "R2":  ("PE", "ITM1", 0.50, 1.00),
    "PDL": ("CE", "ATM",  0.30, 2.00),
    "S1":  ("CE", "ITM1", 0.30, 1.00),
    "S2":  ("CE", "OTM1", 0.40, 1.00),
}

# Legacy intraday params (used by old trader.py)
INTRADAY_PARAMS = {
    ("PDL", "CE"): ("ATM",  0.30, 2.00),
    ("R1",  "PE"): ("ATM",  0.20, 0.50),
    ("R2",  "PE"): ("ITM1", 0.50, 1.00),
    ("S1",  "CE"): ("ITM1", 0.30, 1.00),
    ("S2",  "CE"): ("OTM1", 0.40, 1.00),
}
INTRADAY_SCAN_FROM = "09:30"
INTRADAY_SCAN_TO   = "11:20"
INTRADAY_EOD_EXIT  = "15:20:00"

# ── 3-tier trailing SL ────────────────────────────────────────────────────────
LOCKIN_TIERS = [
    (0.60, lambda md: None),
    (0.40, 0.80),
    (0.25, 1.00),
]

# ── Trade management ──────────────────────────────────────────────────────────
EOD_EXIT_TIME = dtime(15, 20)

# ── Data paths ────────────────────────────────────────────────────────────────
DATA_DIR        = os.path.join(os.path.dirname(__file__), "data")
LIVE_TRADES_CSV = os.path.join(DATA_DIR, "live_trades.csv")
REPORT_DIR      = os.path.join(DATA_DIR, "reports")

# ── Conviction score → lots ───────────────────────────────────────────────────
def score_to_lots(score: int, inside_cpr: bool) -> int:
    if score <= 1:   base = 1
    elif score <= 3: base = 2
    else:            base = 3
    return max(1, base - int(inside_cpr))
