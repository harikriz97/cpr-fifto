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
from angel_client import AngelClient, NIFTY_SPOT_TOKEN, NIFTY_WS_TOKEN, NSE, NFO
from indicators  import (
    compute_cpr, compute_camarilla, compute_mrc_levels,
    compute_ema20, compute_ib, build_ohlc_from_ticks,
    get_atm, score_to_lots, build_score7_features,
)
from signal_crt     import CRTScanner
from signal_mrc     import MRCScanner
from signal_base    import BaseScanner
from trade_manager  import TradeManager, print_paper_summary
from telegram_alert import (
    alert_entry, alert_exit, alert_pnl_update,
    alert_s4_watching, alert_contra_watching, alert_eod_summary,
    start_pnl_updates, stop_pnl_updates,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper_trader")

# ── Strategy constants ────────────────────────────────────────────────────────
S4_PB_LO        = 0.60       # S4 pullback: option at 60%–75% of entry price
S4_PB_HI        = 0.75
REENTRY_CUT     = "14:00:00" # no new S4 / contra entries after this time
CONTRA_PULL_TOL = 30         # spot within 30 pts of SL exit = contra trigger
CONTRA_CUT      = "14:00:00"


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

        # S4 re-entry state (monitor option price after target exit)
        self.s4_watching     = False
        self.s4_ep           = 0.0   # original entry price of the winning trade
        self.s4_opt          = ""    # CE / PE
        self.s4_strike       = 0
        self.s4_exit_time    = ""

        # Contra trade state (monitor spot price after hard_sl)
        self.contra_watching        = False
        self.contra_opt             = ""    # opposite option (CE/PE)
        self.contra_spot_at_exit    = 0.0
        self.contra_start_time      = ""

        # Last known spot price (used for contra entry reference)
        self.last_spot_price = 0.0


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
    # Skip today's partial bar if present (open==close or date==today)
    today_str = datetime.today().strftime("%Y%m%d")
    last_date = str(ohlc.iloc[-1]["date"])
    if last_date >= today_str or float(ohlc.iloc[-1]["open"]) == float(ohlc.iloc[-1]["close"]):
        prev = ohlc.iloc[-2]   # use day before
    else:
        prev = ohlc.iloc[-1]
    ph, pl, pc = float(prev["high"]), float(prev["low"]), float(prev["close"])
    state._prev_open  = float(prev["open"])
    state._prev_close = float(prev["close"])
    logger.info(f"  Prev day: {prev['date']}  O={state._prev_open} C={state._prev_close}")

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
    # Futures basis at open — find token from instrument master
    try:
        fut_token, fut_sym = client.find_futures_token("NIFTY")
        if fut_token and fut_sym:
            fut_ltp  = client.get_ltp(NFO, fut_sym, fut_token)  # Bug9: pass symbol
            spot_ltp = client.get_nifty_spot()
            state.fut_basis_pts = round(fut_ltp - spot_ltp, 2)
            logger.info(f"  Futures basis: {state.fut_basis_pts} pts ({fut_sym})")
        else:
            logger.warning("  Futures token not found — basis=0")
            state.fut_basis_pts = 0.0
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
        f"IB confirmed: high={state.ib.get('ib_high')} "
        f"low={state.ib.get('ib_low')} range={state.ib.get('ib_range')}"
    )

    # today_open: first tick or live spot fallback
    if not ticks_df.empty:
        today_open = float(ticks_df["price"].iloc[0])
    else:
        try:
            today_open = client.get_nifty_spot()
            logger.info(f"No ticks yet — using live spot as today_open: {today_open:.2f}")
        except Exception:
            today_open = float(state.ema20 or 0.0)

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

    # Init base scanner: THOR / HULK / IRON MAN / CAPTAIN
    ohlc_hist = getattr(state, "_ohlc_history", None)
    prev_row  = ohlc_hist.iloc[-1] if (ohlc_hist is not None and not ohlc_hist.empty) else None
    state.base_scanner = BaseScanner(
        levels={**state.cpr, **state.cam, "pdh": state.pdh, "pdl": state.pdl},
        ema20=float(state.ema20 or 0.0),
        today_open=today_open,
        fut_basis_pts=state.fut_basis_pts,
        prev_close=getattr(state, "_prev_close", float(prev_row["close"]) if prev_row is not None else 0.0),
        prev_open=getattr(state,  "_prev_open",  float(prev_row["open"])  if prev_row is not None else 0.0),
        vix_today=getattr(state, "_vix_today", 0.0),
        vix_ma20=getattr(state,  "_vix_ma20",  15.0),
        dte=getattr(state, "_dte", 4),
        ohlc_history=ohlc_hist,
    )
    state.base_scanner.set_ib(
        state.ib.get("ib_high") or 0.0,
        state.ib.get("ib_low")  or 0.0,
    )
    thor = state.base_scanner._thor_sched
    logger.info(
        f"BaseScanner ready: zone={state.base_scanner._zone} "
        f"bias={state.base_scanner._bias} "
        f"THOR={'@ '+thor if thor else 'no signal'}"
    )


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
    state.last_spot_price = price

    # ── IB confirmation at 09:46 ───────────────────────────────────────────────
    if not state.ib_confirmed and current_time >= "09:46:00":
        state.ib_confirmed = True
        on_ib_confirmed(client, state)

    # ── Contra spot monitoring (after hard_sl exit, waiting for pullback) ──────
    if state.contra_watching and not state.trade_entered:
        if current_time > CONTRA_CUT:
            state.contra_watching = False
            logger.info("Contra watch expired (past CONTRA_CUT)")
        elif abs(price - state.contra_spot_at_exit) <= CONTRA_PULL_TOL:
            _enter_contra(state, client, current_time, price)
        return

    # ── Signal scanning (after IB confirmed, no active trade) ─────────────────
    if state.ib_confirmed and not state.trade_entered:

        # Base scanner: THOR (v17a) / HULK (cam_l3) / IRON MAN (cam_h3) / CAPTAIN (iv2)
        bs = getattr(state, 'base_scanner', None)
        if bs and not state.base_signal_fired:
            sig = bs.update(ticks_df)
            if sig and current_time >= sig.get("entry_time", ""):
                state.base_signal_fired = True
                _enter_trade(sig, state, client, current_time)
                return

        # CRT scanner (blank days) — fires CE
        if state.crt_scanner and not state.base_signal_fired:
            sig = state.crt_scanner.update(ticks_df)
            if sig and current_time >= sig["entry_time"]:
                # Blank filter: skip CRT CE on trend-up days (proxy: bull bias + open above R1)
                bs = getattr(state, 'base_scanner', None)
                _TREND_UP_ZONES = ('r1_to_r2', 'r2_to_r3', 'r3_to_r4', 'above_r4')
                if (bs and getattr(bs, '_bias', None) == 'bull'
                        and getattr(bs, '_zone', '') in _TREND_UP_ZONES):
                    logger.info("BlankFilter: skip CRT CE — trend-up day (bias=bull zone=%s)",
                                bs._zone)
                    state.crt_scanner.mark_done()
                else:
                    _enter_trade(sig, state, client, current_time)
                return

        # MRC (blank days, only if base not fired)
        if state.mrc_scanner and not state.base_signal_fired:
            sig = state.mrc_scanner.update(ticks_df)
            if sig and current_time >= sig["entry_time"]:
                # Blank filter: skip MRC PE on normal-down days (proxy: bear bias + open below S1)
                bs = getattr(state, 'base_scanner', None)
                _TREND_DN_ZONES = ('s1_to_s2', 's2_to_s3', 's3_to_s4', 'below_s4')
                if (bs and getattr(bs, '_bias', None) == 'bear'
                        and getattr(bs, '_zone', '') in _TREND_DN_ZONES):
                    logger.info("BlankFilter: skip MRC PE — normal-down day (bias=bear zone=%s)",
                                bs._zone)
                    state.mrc_scanner.mark_done()
                else:
                    _enter_trade(sig, state, client, current_time)
                return

    # ── EOD exit if still in trade ─────────────────────────────────────────────
    if state.trade_entered and state.active_trade and current_time >= EOD_EXIT_TIME:
        if state.active_trade.state == TradeManager.ACTIVE:
            state.active_trade._exit(price, current_time, "eod")


def on_option_tick(message: dict, state: DayState, client: AngelClient):
    """
    Process option LTP tick (after entry or during S4 watch).
    After target exit  → start S4 re-entry watch (same option pulls back to 60–75% ep).
    After hard_sl exit → start contra trade watch (spot pullback to SL exit spot).
    """
    try:
        raw_price = message.get("last_traded_price", 0)
        price = raw_price / 100.0 if raw_price > 100000 else float(raw_price)
        time_str = datetime.now().strftime("%H:%M:%S")
    except Exception:
        return

    if price <= 0:
        return

    # ── S4 pullback monitoring (option price, same subscription) ─────────────
    if state.s4_watching and not state.trade_entered:
        if time_str > REENTRY_CUT:
            state.s4_watching = False
            logger.info("S4 watch expired (past REENTRY_CUT)")
            return
        lo = state.s4_ep * S4_PB_LO
        hi = state.s4_ep * S4_PB_HI
        if lo <= price <= hi:
            _enter_s4(state, client, time_str, price)
        return  # only S4 watch ticks here; no other processing

    if not state.active_trade:
        return

    # Write live state for dashboard every option tick
    _write_live_state(state, price, time_str)

    result = state.active_trade.on_tick(time_str, price)
    if result:
        state.trade_entered = False
        _clear_live_state()
        stop_pnl_updates()
        logger.info(
            f"Trade done. P&L: Rs.{result['pnl']:,.0f} | exit={result['exit_reason']}"
        )
        # Telegram exit alert
        try:
            sig = state.active_trade.signal if state.active_trade else {}
            alert_exit(
                signal  = sig.get("signal", sig.get("strategy", "")),
                opt     = result.get("opt", sig.get("opt", "")),
                strike  = result.get("strike", sig.get("strike", 0)),
                ep      = result.get("ep", 0),
                xp      = result.get("exit_price", 0),
                reason  = result.get("exit_reason", ""),
                pnl     = result.get("pnl", 0),
                lots    = sig.get("lots", 1),
            )
        except Exception: pass

        # ── After target exit: watch for S4 re-entry ─────────────────────────
        if result["exit_reason"] == "target" and time_str < REENTRY_CUT:
            state.s4_watching  = True
            state.s4_ep        = result["ep"]
            state.s4_opt       = result["opt"]
            state.s4_strike    = result["strike"]
            state.s4_exit_time = result["exit_time"]
            logger.info(
                f"S4 watching: {state.s4_opt} {state.s4_strike} ep={state.s4_ep:.0f} "
                f"window=[{state.s4_ep * S4_PB_LO:.0f}, {state.s4_ep * S4_PB_HI:.0f}]"
            )
            try:
                alert_s4_watching(state.s4_opt, state.s4_strike, state.s4_ep,
                                  state.s4_ep * S4_PB_LO, state.s4_ep * S4_PB_HI)
            except Exception: pass

        # ── After hard_sl exit: watch for contra pullback ─────────────────────
        elif result["exit_reason"] == "hard_sl" and time_str < CONTRA_CUT:
            contra_opt = "CE" if result["opt"] == "PE" else "PE"
            state.contra_watching     = True
            state.contra_opt          = contra_opt
            state.contra_spot_at_exit = state.last_spot_price
            state.contra_start_time   = time_str
            logger.info(
                f"Contra watching: {contra_opt} after hard_sl | "
                f"spot_at_exit={state.last_spot_price:.0f} "
                f"trigger=[{state.last_spot_price - CONTRA_PULL_TOL:.0f}, "
                f"{state.last_spot_price + CONTRA_PULL_TOL:.0f}]"
            )
            try:
                alert_contra_watching(contra_opt, state.last_spot_price)
            except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# LOT BOOST HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _compute_dte(state: DayState) -> int:
    """Days to nearest expiry. Uses state.expiry (format YYMMDD or YYYYMMDD)."""
    try:
        expiry = str(state.expiry).replace("-", "")
        if len(expiry) == 6:
            exp_dt = datetime.strptime("20" + expiry, "%Y%m%d")
        else:
            exp_dt = datetime.strptime(expiry[:8], "%Y%m%d")
        today_dt = datetime.strptime(state.today, "%Y%m%d")
        return max(0, (exp_dt - today_dt).days)
    except Exception:
        return 4   # safe default: mid-week


def _apply_lot_boosts(signal: dict, state: DayState) -> dict:
    """
    Apply lot boosts before trade entry (backtest-validated improvements):
      1. Basis S3: +1 lot when |fut_basis_pts| > 50 AND direction aligned
      2. DTE <= 1: +1 lot on expiry day or day before (faster theta decay)
    Max lots capped at 3.
    """
    lots = signal.get("lots", 1)
    opt  = signal.get("opt", "")
    b    = state.fut_basis_pts

    # 1. Basis S3 boost
    if abs(b) > 50:
        aligned = (opt == "PE" and b > 0) or (opt == "CE" and b < 0)
        if aligned:
            lots = min(lots + 1, 3)
            logger.info(f"  LotBoost: basis S3 ({b:+.0f} pts, {opt}) → lots={lots}")

    # 2. DTE <= 1 boost
    dte = _compute_dte(state)
    if dte <= 1:
        lots = min(lots + 1, 3)
        logger.info(f"  LotBoost: DTE={dte} → lots={lots}")

    signal = dict(signal)   # don't mutate original
    signal["lots"] = lots
    return signal


# ─────────────────────────────────────────────────────────────────────────────
# LIVE STATE — written every option tick for dashboard
# ─────────────────────────────────────────────────────────────────────────────
def _write_live_state(state: DayState, option_price: float, ts: str):
    """Write current trade state to data/live_state.json for dashboard."""
    try:
        tm  = state.active_trade
        sig = tm.signal if tm else {}
        ep  = tm.ep if tm else 0
        lots = sig.get("lots", 1) if sig else 1
        decay_pct    = round((ep - option_price) / ep * 100, 1) if ep > 0 else 0
        upnl         = round((ep - option_price) * lots * 65, 0) if ep > 0 else 0
        data = dict(
            symbol       = ("NIFTY" + state.expiry + str(sig.get("strike","")) + str(sig.get("opt",""))) if sig.get("strike") else "",
            strategy     = sig.get("strategy",""),
            signal       = sig.get("signal",""),
            status       = "open",
            entry        = ep,
            current      = option_price,
            spot         = state.last_spot_price,
            sl           = tm.sl if tm else 0,
            hard_sl      = tm.hsl if tm else 0,
            target       = tm.tgt if tm else 0,
            trail_tier   = tm.trail_tier if hasattr(tm,"trail_tier") else 0,
            trail_label  = tm.trail_label() if hasattr(tm,"trail_label") else "None",
            decay_pct    = decay_pct,
            max_decay_pct= round(tm.max_decline * 100, 1) if tm else 0,
            upnl         = upnl,
            lots         = lots,
            score        = sig.get("score", 0),
            s4_watching  = state.s4_watching,
            contra_watching = state.contra_watching,
            ts           = ts,
        )
        import json as _j
        path = os.path.join(DATA_DIR, "live_state.json")
        with open(path, "w") as f: _j.dump(data, f)
    except Exception as e:
        logger.debug("live_state write failed: %s", e)

def _clear_live_state():
    try:
        p = os.path.join(DATA_DIR, "live_state.json")
        if os.path.exists(p): os.remove(p)
    except Exception: pass


# ─────────────────────────────────────────────────────────────────────────────
# S4 RE-ENTRY + CONTRA HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _enter_s4(state: DayState, client: AngelClient, current_time: str, option_price: float):
    """
    Enter S4 re-entry: same option (strike + opt known from previous target exit).
    Triggered when option price pulls back to 60–75% of original entry price.
    """
    state.s4_watching = False
    signal = {
        "strategy":   "S4_reentry",
        "signal":     "S4_2nd",
        "opt":        state.s4_opt,
        "strike":     state.s4_strike,
        "entry_time": current_time,
        "lots":       1,
        "score":      5,
    }
    logger.info(
        f"S4 re-entry triggered: {state.s4_opt} {state.s4_strike} "
        f"@ Rs.{option_price:.0f} (ep_orig={state.s4_ep:.0f})"
    )
    _enter_trade(signal, state, client, current_time)


def _enter_contra(state: DayState, client: AngelClient, current_time: str, spot_price: float):
    """
    Enter contra trade: opposite option after hard_sl pullback.
    ATM strike computed from current spot price.
    """
    state.contra_watching = False
    atm = get_atm(spot_price)
    signal = {
        "strategy":   "contra",
        "signal":     "contra_sl",
        "opt":        state.contra_opt,
        "strike":     atm,
        "entry_time": current_time,
        "lots":       1,
        "score":      5,
    }
    logger.info(
        f"Contra entry triggered: {state.contra_opt} {atm} "
        f"spot={spot_price:.0f} (pullback to {state.contra_spot_at_exit:.0f})"
    )
    _enter_trade(signal, state, client, current_time)


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

    # Apply lot boosts (basis S3 + DTE)
    signal = _apply_lot_boosts(signal, state)

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

    # Mark all scanners done (one trade per day)
    if state.crt_scanner:
        state.crt_scanner.mark_done()
    if state.mrc_scanner:
        state.mrc_scanner.mark_done()
    if hasattr(state, 'base_scanner'):
        state.base_scanner._done = True

    # Create trade manager
    state.active_trade  = TradeManager(signal, ep)
    state.trade_entered = True
    state.option_token  = option_token

    tm = state.active_trade
    logger.info(
        f"Trade entered: {option_symbol} @ Rs.{ep} "
        f"(lots={signal.get('lots',1)})"
    )

    # Telegram entry alert
    try:
        expiry_dt = datetime.strptime(state.expiry, "%Y%m%d")
        dte_val   = (expiry_dt.date() - datetime.today().date()).days
        alert_entry(
            signal      = signal.get("signal", signal.get("strategy", "")),
            strategy    = signal.get("strategy", ""),
            opt         = opt,
            strike      = strike,
            expiry      = expiry_dt.strftime("%d%b%y").upper(),
            lots        = signal.get("lots", 1),
            ep          = ep,
            tgt         = tm.tgt,
            hsl         = tm.hsl,
            score       = signal.get("score", 0),
            zone        = signal.get("zone", ""),
            bias        = getattr(getattr(state, "base_scanner", None), "_bias", "") or "",
            dte         = dte_val,
        )
    except Exception: pass

    # Start 15-min P&L update thread
    try:
        import json as _j
        def _get_ls():
            p = os.path.join(DATA_DIR, "live_state.json")
            if os.path.exists(p):
                with open(p) as f: return _j.load(f)
            return None
        start_pnl_updates(_get_ls, interval_secs=900)
    except Exception: pass

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
        if token in (NIFTY_SPOT_TOKEN, NIFTY_WS_TOKEN):
            on_spot_tick(message, state, client)
        elif state.option_token and token == state.option_token:
            on_option_tick(message, state, client)

    client.start_websocket(
        token_list=[{"exchangeType": 1, "tokens": [NIFTY_WS_TOKEN]}],
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
        # Telegram EOD summary
        try:
            from export_excel import load_trades
            today = datetime.today().strftime("%Y%m%d")
            df_t  = load_trades(today)
            if not df_t.empty:
                trades_list = df_t.to_dict("records")
                alert_eod_summary(trades_list)
        except Exception as e:
            logger.debug("EOD telegram summary failed: %s", e)

        # Export daily Excel report
        try:
            from export_excel import export_daily_excel
            path = export_daily_excel()
            if path:
                logger.info("Daily Excel exported: %s", path)
                import subprocess
                subprocess.Popen(["start", "", path], shell=True)
        except Exception as e:
            logger.warning("Excel export failed: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# TEST HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _test_inject_levels(state: DayState):
    """Inject dummy levels for --test mode (Bug7 fix: include BaseScanner fields)."""
    state.cpr  = {"tc": 23200, "bc": 23100, "pivot": 23150,
                  "r1": 23350, "r2": 23550, "r3": 23750, "r4": 23950,
                  "s1": 22950, "s2": 22750, "s3": 22550, "s4": 22350,
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
    # Bug7 fix: BaseScanner fields needed by on_ib_confirmed
    state._prev_open   = 23000.0
    state._prev_close  = 23150.0   # body = 0.65% > 0.10% → THOR will fire
    state._vix_today   = 14.0
    state._vix_ma20    = 15.0
    state._dte         = 4
    state._ohlc_history = pd.DataFrame([
        {"date":"20260428","open":23000,"high":23400,"low":22900,"close":23150,
         "tc":23200.0,"bc":23100.0,"cpr_mid":23150.0,"ema":23100.0},
        {"date":"20260429","open":23100,"high":23500,"low":23000,"close":23200,
         "tc":23250.0,"bc":23150.0,"cpr_mid":23200.0,"ema":23120.0},
        {"date":"20260430","open":23000,"high":23400,"low":22900,"close":23150,
         "tc":23200.0,"bc":23100.0,"cpr_mid":23150.0,"ema":23100.0},
    ])


if __name__ == "__main__":
    main()
