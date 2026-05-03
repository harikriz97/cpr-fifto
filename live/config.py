"""
live/config.py — Angel One credentials + strategy parameters for paper_trader
"""

# ── Angel One API Credentials ─────────────────────────────────────────────────
ANGEL_API_KEY     = "k6S2VzNN"
ANGEL_CLIENT_ID   = "pvip1030"
ANGEL_PASSWORD    = "5131"
ANGEL_TOTP_SECRET = "UJ2OEF4RVJQG3Q7JLRGKH4NZ3A"

# ── Mode ──────────────────────────────────────────────────────────────────────
PAPER_TRADE = True

# ── Strategy Parameters ───────────────────────────────────────────────────────
LOT_SIZE      = 65
STRIKE_INT    = 50
TGT_PCT       = 0.30
EOD_EXIT_TIME = "15:20:00"

# ── IB window ─────────────────────────────────────────────────────────────────
IB_START = "09:15:00"
IB_END   = "09:45:00"

# ── Signal scan windows ────────────────────────────────────────────────────────
BASE_ENTRY_START = "09:16:00"
BASE_ENTRY_END   = "15:15:00"
CRT_SCAN_START   = "09:15:00"
CRT_SCAN_END     = "12:00:00"
MRC_SCAN_START   = "09:15:00"
MRC_SCAN_END     = "12:00:00"

# ── Score7 lot sizing ─────────────────────────────────────────────────────────
SCORE_LOT_MAP = {0: 1, 1: 1, 2: 2, 3: 2, 4: 3, 5: 3, 6: 0, 7: 3}
# score 6 → 0 = SKIP TRADE (net negative over 5yr backtest)

# ── MRC lot override ──────────────────────────────────────────────────────────
MRC_PE_LOTS = 2
MRC_CE_LOTS = 0

# ── Telegram alerts ───────────────────────────────────────────────────────────
# Get token from @BotFather. Get chat_id: python live/telegram_alert.py --setup
TELEGRAM_TOKEN   = ""   # e.g. "7476365992:AAGK9c4K1..."
TELEGRAM_CHAT_ID = ""   # e.g. "123456789"

# ── Output paths ─────────────────────────────────────────────────────────────
import os
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_CSV    = os.path.join(_base, "data", "paper_trades.csv")
STATE_FILE = os.path.join(_base, "data", "live_state.json")
OHLC_CACHE = os.path.join(_base, "data", "ohlc_cache.csv")
