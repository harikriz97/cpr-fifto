"""
paper_trader.py — Main paper trading orchestration
===================================================
Daily flow:
  08:55  connect() → fetch 45-day OHLC → compute all levels
  09:15  market open → start WebSocket subscription
  09:15  IB tracking begins
  09:45  IB confirmed → start CRT / MRC scanners (blank days)
         → start base strategy scanner (all days)
  10:00+ signals fire → enter paper trade
  15:20  EOD exit → log results

Run:
  python3 live/paper_trader.py          # live market hours
  python3 live/paper_trader.py --test   # dry-run test (no API, simulated data)

PAPER_TRADE = True in config.py  → no real orders, only logs.
"""

import sys
import time
import logging
import threading
import json
import os
import pandas as pd
import numpy as np
from datetime import datetime

# ── add live folder to path ────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET,
    PAPER_TRADE, LOT_SIZE, STRIKE_INT, EOD_EXIT_TIME,
    IB_END, LOG_CSV, STATE_FILE, OHLC_CACHE,
)
from angel_client import AngelClient, NIFTY_SPOT_TOKEN, NSE, NFO
from indicators  import (
    compute_cpr, compute_camarilla, compute_mrc_levels,
    compute_ema20, compute_ib, build_ohlc_from_ticks,
    get_atm, score_to_lots, build_score7_features,
)
from signal_crt     import CRTScanner
from signal_mrc     import MRCScanner
from trade_manager  import TradeManager, print_paper_summary

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper_trader")


# ─────────────────────────────────────────────────────────────────────────────
# DAILY STATE
# ─────────────────────────────────────────────────────────────────────────────
class DayState:
    def __init__(self):
        self.today           = datetime.today().strftime("%Y%m%d")
        self.spot_ticks      = []      # list of (time_str, price)
        self.spot_ticks_lock = threading.Lock()

        # Daily computed levels
        self.cpr   = {}       # tc, bc, pivot, r1, r2, s1, s2, s3
        self.cam   = {}       # cam_h3, cam_l3, etc.
        self.mrc   = {}       # l_382, l_618, l_50
        self.ib    = {}       # ib_high, ib_low
        self.ema20 = None

        # Features for base strategy
        self.score7_features = {}
        self.score7          = 0
        self.lots_base       = 1
        self.inside_cpr      = False
        self.fut_basis_pts   = 0.0
        self.pdh             = None
        self.pdl             = None

        # Option position
        self.active_trade    = None    # TradeManager instance
        self.option_token    = None    # subscribed option token
        self.trade_entered   = False

        # Scanners (blank day)
        self.crt_scanner     = None
        self.mrc_scanner     = None

        # Base signal fired?
        self.base_signal_fired = False

        # IB confirmed (after 09:45)
        self.ib_confirmed    = False


# ─────────────────────────────────────────────────────────────────────────────
# PRE-MARKET: fetch data + compute levels
# ─────────────────────────────────────────────────────────────────────────────
def pre_market_setup(client: AngelClient, state: DayState):
    """
    Fetch 45 days NIFTY daily OHLC, compute all levels for today.
    Call this once before 09:15.
    """
    logger.info("Pre-market setup started...")

    # ── Fetch daily OHLC ──────────────────────────────────────────────────────
    ohlc = client.get_daily_ohlc(
        symbol="Nifty 50",
        token=NIFTY_SPOT_TOKEN,
        exchange=NSE,
        n_days=45
    )
    ohlc.to_csv(OHLC_CACHE, index=False)
    logger.info(f"  OHLC fetched: {len(ohlc)} days, last={ohlc.iloc[-1]['date']}")

    # ── Compute CPR / Cam / MRC from previous day ─────────────────────────────
    prev = ohlc.iloc[-1]   # previous trading day (today not yet in daily OHLC)
    ph, pl, pc = float(prev["high"]), float(prev["low"]), float(prev["close"])

    state.pdh  = ph
    state.pdl  = pl
    state.cpr  = compute_cpr(ph, pl, pc)
    state.cam  = compute_camarilla(ph, pl, pc)
    state.mrc  = compute_mrc_levels(ph, pl)

    # ── EMA(20) on close prices ────────────────────────────────────────────────
    closes = ohlc["close"].astype(float)
    ema_series = compute_ema20(closes)
    state.ema20 = round(float(ema_series.iloc[-1]), 2) if not pd.isna(ema_series.iloc[-1]) else None

    # ── Augment OHLC with CPR columns for score7 ──────────────────────────────
    ohlc = ohlc.copy()
    _build_cpr_columns(ohlc)   # adds tc, bc, cpr_mid

    # ── Nearest expiry ─────────────────────────────────────────────────────────
    state.expiry = client.get_nearest_expiry("NIFTY")

    logger.info(
        f"  TC={state.cpr['tc']} BC={state.cpr['bc']} R1={state.cpr['r1']}\n"
        f"  CAM L3={state.cam['cam_l3']} H3={state.cam['cam_h3']}\n"
        f"  MRC l_382={state.mrc['l_382']} l_618={state.mrc['l_618']}\n"
        f"  EMA20={state.ema20} | expiry={state.expiry}"
    )

    # ── Futures basis at 09:15 (fetched right at open) ─────────────────────────
    # Will be fetched in market_open_tasks()

    state._ohlc_history = ohlc   # store for score7 feature building

    logger.info("Pre-market setup complete.")
    return state


def _build_cpr_columns(ohlc: pd.DataFrame):
    """Add tc, bc, cpr_mid columns to ohlc in-place (prev day → today's CPR)."""
    h = ohlc["high"].astype(float)
    l = ohlc["low"].astype(float)
    c = ohlc["close"].astype(float)
    pvt = (h + l + c) / 3
    bc  = (h + l) / 2
    tc  = 2 * pvt - bc
    ohlc["tc"]      = tc.round(2)
    ohlc["bc"]      = bc.round(2)
    ohlc["cpr_mid"] = ((tc + bc) / 2).round(2)


# ─────────────────────────────────────────────────────────────────────────────
# MARKET OPEN: tasks at/after 09:15
# ─────────────────────────────────────────────────────────────────────────────
def market_open_tasks(client: AngelClient, state: DayState):
    """
    Call at 09:15. Fetch futures basis, start IB tracking.
    """
    # Futures basis at open
    try:
        fut_ltp  = client.get_ltp(NFO, "NIFTYFUT", "")   # update with correct token
        spot_ltp = client.get_nifty_spot()
        state.fut_basis_pts = round(fut_ltp - spot_ltp, 2)
        logger.info(f"  Futures basis: {state.fut_basis_pts} pts")
    except Exception as e:
        logger.warning(f"  Futures basis fetch failed: {e} — using 0")
        state.fut_basis_pts = 0.0

    logger.info("Market open tasks done. IB tracking active.")


# ─────────────────────────────────────────────────────────────────────────────
# IB CONFIRMED: tasks at 09:46
# ─────────────────────────────────────────────────────────────────────────────
def on_ib_confirmed(client: AngelClient, state: DayState):
    """
    Called once at 09:46. Compute IB, init scanners.
    """
    with state.spot_ticks_lock:
        ticks_df = pd.DataFrame(state.spot_ticks, columns=["time", "price"])

    state.ib = compute_ib(ticks_df)
    logger.info(
        f"IB confirmed: high={state.ib['ib_high']} "
        f"low={state.ib['ib_low']} range={state.ib['ib_range']}"
    )

    # Score7 features (direction TBD — computed per base signal direction)
    # Stored here; direction-specific features computed when signal fires

    # Init blank day scanners (CRT + MRC)
    state.crt_scanner = CRTScanner(
        tc=state.cpr["tc"],
        r1=state.cpr["r1"],
        ib_high=state.ib.get("ib_high"),
        fut_basis_pts=state.fut_basis_pts,
    )
    state.mrc_scanner = MRCScanner(
        pdh=state.pdh,
        pdl=state.pdl,
    )
    logger.info("CRT + MRC scanners initialized.")


# ─────────────────────────────────────────────────────────────────────────────
# TICK HANDLER: called on each WebSocket message
# ─────────────────────────────────────────────────────────────────────────────
def on_spot_tick(message: dict, state: DayState, client: AngelClient):
    """
    Process a NIFTY spot tick from WebSocket.
    message: dict from Angel One WebSocket (Mode 1 LTP)
    """
    # Angel One WebSocket LTP message format:
    # {"token": "26000", "last_traded_price": 2348050, ...}
    # LTP is in paise (× 100) for indices too in some API versions
    # Safe approach: divide by 100 and check if it looks right
    try:
        raw_price = message.get("last_traded_price", 0)
        price = raw_price / 100.0 if raw_price > 100000 else float(raw_price)
        time_str = datetime.now().strftime("%H:%M:%S")
    except Exception:
        return

    if price <= 0:
        return

    # Accumulate spot ticks
    with state.spot_ticks_lock:
        state.spot_ticks.append((time_str, price))
        ticks_df = pd.DataFrame(state.spot_ticks, columns=["time", "price"])

    current_time = time_str

    # ── IB confirmation at 09:46 ───────────────────────────────────────────────
    if not state.ib_confirmed and current_time >= "09:46:00":
        state.ib_confirmed = True
        on_ib_confirmed(client, state)

    # ── If in active trade, monitor option price ───────────────────────────────
    # (Option price handled by separate option tick subscription)

    # ── Signal scanning (after IB confirmed, no active trade) ─────────────────
    if state.ib_confirmed and not state.trade_entered:

        # CRT scanner (blank days) — fires CE
        if state.crt_scanner and not state.base_signal_fired:
            sig = state.crt_scanner.update(ticks_df)
            if sig and current_time >= sig["entry_time"]:
                _enter_trade(sig, state, client, current_time)
                return

        # MRC scanner (blank days) — fires PE
        if state.mrc_scanner and not state.base_signal_fired:
            sig = state.mrc_scanner.update(ticks_df)
            if sig and current_time >= sig["entry_time"]:
                _enter_trade(sig, state, client, current_time)
                return

    # ── EOD exit if still in trade ─────────────────────────────────────────────
    if state.trade_entered and state.active_trade and current_time >= EOD_EXIT_TIME:
        if state.active_trade.state == TradeManager.ACTIVE:
            state.active_trade._exit(price, current_time, "eod")


def on_option_tick(message: dict, state: DayState):
    """
    Process option LTP tick (after entry).
    """
    if not state.active_trade:
        return
    try:
        raw_price = message.get("last_traded_price", 0)
        price = raw_price / 100.0 if raw_price > 100000 else float(raw_price)
        time_str = datetime.now().strftime("%H:%M:%S")
    except Exception:
        return

    if price <= 0:
        return

    result = state.active_trade.on_tick(time_str, price)
    if result:
        # Trade exited
        state.trade_entered = False
        logger.info(f"Trade done. P&L: Rs.{result['pnl']:,.0f}")


# ─────────────────────────────────────────────────────────────────────────────
# ENTER TRADE
# ─────────────────────────────────────────────────────────────────────────────
def _enter_trade(signal: dict, state: DayState,
                 client: AngelClient, current_time: str):
    """
    Enter paper trade: fetch option LTP, create TradeManager, subscribe option.
    """
    if state.trade_entered:
        return

    strike     = signal["strike"]
    opt        = signal["opt"]
    entry_time = signal["entry_time"]

    # Build option symbol + token
    option_symbol = client.build_option_symbol(state.expiry, strike, opt)
    try:
        option_token = client.get_nfo_token(option_symbol)
    except KeyError:
        logger.error(f"Option token not found for {option_symbol} — skipping")
        return

    # Get entry price (LTP of option at entry)
    try:
        ep = client.get_ltp(NFO, option_symbol, option_token)
    except Exception as e:
        logger.error(f"Failed to get option LTP for {option_symbol}: {e}")
        return

    if ep <= 0:
        logger.warning(f"Option LTP is 0 for {option_symbol} — skipping")
        return

    # Mark scanner as done (one trade per day)
    if state.crt_scanner:
        state.crt_scanner.mark_done()
    if state.mrc_scanner:
        state.mrc_scanner.mark_done()

    # Create trade manager
    state.active_trade  = TradeManager(signal, ep)
    state.trade_entered = True
    state.option_token  = option_token

    logger.info(
        f"Trade entered: {option_symbol} @ Rs.{ep} "
        f"(lots={signal.get('lots',1)})"
    )

    if PAPER_TRADE:
        print(f"[PAPER TRADE] {option_symbol} entry @ Rs.{ep}")
    else:
        # Place actual order (DISABLED during paper trading phase)
        # order = client.api.placeOrder({
        #     "variety": "NORMAL",
        #     "tradingsymbol": option_symbol,
        #     "symboltoken": option_token,
        #     "transactiontype": "SELL",
        #     "exchange": "NFO",
        #     "ordertype": "MARKET",
        #     "producttype": "INTRADAY",
        #     "duration": "DAY",
        #     "quantity": signal.get("lots", 1) * 75,  # use current lot size
        # })
        pass

    # Subscribe to option ticks
    client.add_subscription(
        [{"exchangeType": 2, "tokens": [option_token]}]   # 2 = NFO
    )


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    test_mode = "--test" in sys.argv
    today = datetime.today().strftime("%Y%m%d")
    logger.info(f"Paper Trader starting — date={today} | paper={PAPER_TRADE} | test={test_mode}")

    # ── Connect ────────────────────────────────────────────────────────────────
    client = AngelClient(
        ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PASSWORD, ANGEL_TOTP_SECRET
    )
    if not test_mode:
        client.connect()
    else:
        logger.info("TEST MODE: API connection skipped")

    state = DayState()

    # ── Pre-market setup ───────────────────────────────────────────────────────
    if not test_mode:
        # Wait until 08:55 if running early
        now = datetime.now().strftime("%H:%M:%S")
        if now < "08:55:00":
            wait = (datetime.strptime("08:55:00", "%H:%M:%S") -
                    datetime.strptime(now, "%H:%M:%S")).seconds
            logger.info(f"Waiting {wait}s for pre-market time...")
            time.sleep(wait)

        pre_market_setup(client, state)
    else:
        _test_inject_levels(state)

    # ── Print day plan ─────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"DAY PLAN — {today}")
    print(f"{'='*60}")
    print(f"  CPR : TC={state.cpr.get('tc')} | BC={state.cpr.get('bc')} | "
          f"R1={state.cpr.get('r1')}")
    print(f"  CAM : L3={state.cam.get('cam_l3')} | H3={state.cam.get('cam_h3')}")
    print(f"  MRC : l_382={state.mrc.get('l_382')} | l_618={state.mrc.get('l_618')}")
    print(f"  IB  : TBD (09:15-09:45)")
    print(f"  EMA20: {state.ema20}")
    print(f"  Expiry: {state.expiry}")
    print(f"{'='*60}\n")

    if test_mode:
        logger.info("Test mode: exiting after plan printout.")
        return

    # ── Wait for market open ───────────────────────────────────────────────────
    now = datetime.now().strftime("%H:%M:%S")
    if now < "09:10:00":
        wait = (datetime.strptime("09:14:55", "%H:%M:%S") -
                datetime.strptime(now, "%H:%M:%S")).seconds
        logger.info(f"Waiting {wait}s for market open...")
        time.sleep(wait)

    # ── Market open tasks ──────────────────────────────────────────────────────
    market_open_tasks(client, state)

    # ── Start WebSocket for NIFTY spot ─────────────────────────────────────────
    def tick_router(message):
        token = message.get("token", "")
        if token == NIFTY_SPOT_TOKEN:
            on_spot_tick(message, state, client)
        elif state.option_token and token == state.option_token:
            on_option_tick(message, state)

    client.start_websocket(
        token_list=[{"exchangeType": 1, "tokens": [NIFTY_SPOT_TOKEN]}],
        on_tick_callback=tick_router,
    )

    logger.info("WebSocket running. Monitoring market...")

    # ── Main loop: keep alive until EOD ───────────────────────────────────────
    try:
        while True:
            now = datetime.now().strftime("%H:%M:%S")
            if now >= "15:25:00":
                logger.info("Market closed.")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    finally:
        client.stop_websocket()
        print_paper_summary()


# ─────────────────────────────────────────────────────────────────────────────
# TEST HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _test_inject_levels(state: DayState):
    """Inject dummy levels for --test mode."""
    state.cpr  = {"tc": 23200, "bc": 23100, "pivot": 23150,
                  "r1": 23350, "r2": 23550, "s1": 22950, "s2": 22750,
                  "cpr_width_pct": 0.15}
    state.cam  = {"cam_h3": 23400, "cam_l3": 22900,
                  "cam_h4": 23600, "cam_l4": 22700}
    state.mrc  = {"l_382": 23050, "l_50": 22950, "l_618": 22850, "range": 500}
    state.ib   = {"ib_high": 23250, "ib_low": 23050, "ib_range": 200}
    state.pdh  = 23400.0
    state.pdl  = 22900.0
    state.ema20 = 23100.0
    state.expiry = "20260529"
    state.fut_basis_pts = 50.0


if __name__ == "__main__":
    main()
