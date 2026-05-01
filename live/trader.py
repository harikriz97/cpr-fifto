"""
live/trader.py — Main live trading orchestrator
================================================
Runs daily.  Each trading day:

  Pre-market (before 09:15):
    - Compute CPR, EMA, Camarilla, conviction features for NIFTY
    - Wednesday/Thursday: also compute for SENSEX

  09:15 open:
    - Check v17a zone signal for NIFTY
    - If signal → place trade (NIFTY + SENSEX on Wed/Thu)

  During day (if no v17a signal):
    - Monitor NIFTY spot ticks for cam_l3 / cam_h3 touch
    - If cam touch → place trade (NIFTY + SENSEX on Wed/Thu)

  09:30–11:20 (if still no signal):
    - Monitor R1/R2/PDL break for intraday v2 (NIFTY only)

  Position management:
    - 3-tier trailing SL on every option price tick
    - Exit on target / SL / EOD 15:20

  All trades written to data/live_trades.csv (appended).
  Dashboard reads the same CSV for real-time display.

Usage:
    python live/trader.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import logging
import csv
from datetime import datetime, date, timedelta, time as dtime
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    INDICES, DUAL_INDEX_DAYS, EMA_PERIOD, EMA_SEED,
    EOD_EXIT_TIME, LIVE_TRADES_CSV, DATA_DIR,
    V17A_PARAMS, IV2_SCAN_START, IV2_SCAN_END,
    score_to_lots,
)
from core import (
    compute_cpr, compute_camarilla, compute_ema_series, compute_features,
    classify_zone, ema_bias, r2,
    v17a_signal, cam_signal, iv2_signal,
    v17a_params, cam_params, iv2_params,
)
from live.orders import (
    TrailingSL, place_sell_order, place_buy_order,
    get_strike, build_option_symbol,
)

# ── Setup ─────────────────────────────────────────────────────────────────────
os.makedirs(DATA_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(DATA_DIR, "trader.log"), mode="a"),
    ],
)
log = logging.getLogger("trader")
IST = ZoneInfo("Asia/Kolkata")

TICK_SLEEP   = 1.0
MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# ── AngelOne client (used for all market data) ─────────────────────────────────
_angel = None

def get_angel():
    global _angel
    if _angel is None:
        from angelone import AngelOneClient
        _angel = AngelOneClient()
        _angel.login()
    return _angel

def get_spot_ltp(index: str) -> float | None:
    """Get NIFTY/SENSEX spot via AngelOne."""
    try:
        a = get_angel()
        return a.get_nifty_ltp()
    except Exception as e:
        log.warning("get_spot_ltp failed: %s", e)
        return None

def get_ltp(index: str, expiry: str, strike: int, opt: str) -> float | None:
    """Get option LTP via AngelOne token search."""
    try:
        a  = get_angel()
        # expiry is YYYYMMDD, need DDMMMYY for Angel One
        from datetime import datetime as _dt
        exp_ao = _dt.strptime(expiry, '%Y%m%d').strftime('%d%b%y').upper()
        sym    = f"{index}{exp_ao}{strike}{opt}"
        tok    = a.search_option_token(sym)
        return a.get_option_ltp(tok)
    except Exception as e:
        log.warning("get_ltp failed (%s%s): %s", strike, opt, e)
        return None


# ── CSV trade log ─────────────────────────────────────────────────────────────
TRADE_COLS = [
    "date", "index", "strategy", "zone", "opt", "strike", "expiry",
    "dte", "lots", "score", "entry_time", "entry_price",
    "exit_time", "exit_price", "exit_reason",
    "pnl", "win",
    "vix_ok", "cpr_trend_aligned", "consec_aligned", "cpr_gap_aligned",
    "dte_sweet", "cpr_narrow", "cpr_dir_aligned", "inside_cpr",
]


def append_trade(row: dict):
    """Append one completed trade to live_trades.csv."""
    write_header = not os.path.exists(LIVE_TRADES_CSV)
    with open(LIVE_TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in TRADE_COLS})
    log.info("Trade saved: %s", row)


# ── Historical data loader via AngelOne ──────────────────────────────────────
def load_daily_ohlc(index: str, n_bars: int = EMA_SEED + 10) -> pd.DataFrame:
    """
    Load recent daily OHLC via AngelOne Smart API.
    Returns DataFrame with columns:
      date, open, high, low, close, vix, tc, bc, pvt, cpr_mid, ema
    Sorted ascending by date.
    """
    try:
        import sys, os as _os
        sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
        from angelone import AngelOneClient
        a = AngelOneClient()
        a.login()
        history = a.get_nifty_ohlc_history(days=n_bars + 20)
        if len(history) < 10:
            raise RuntimeError("Insufficient bars from AngelOne")

        # Remove today's partial bar if present
        today_str = date.today().strftime('%Y-%m-%d')
        if str(history[-1].get('date','')).startswith(today_str):
            history = history[:-1]
        history = history[-n_bars:]

        rows = []
        for h in history:
            cpr = compute_cpr(h['high'], h['low'], h['close'])
            rows.append(dict(
                date  = str(h['date'])[:10].replace('-',''),
                open  = float(h['open']),
                high  = float(h['high']),
                low   = float(h['low']),
                close = float(h['close']),
                vix   = 0.0,
                **cpr
            ))
        df = pd.DataFrame(rows)
        df["ema"]     = compute_ema_series(df["close"], EMA_PERIOD).shift(1)
        df["cpr_mid"] = (df["tc"] + df["bc"]) / 2
        return df.dropna(subset=["ema"]).reset_index(drop=True)

    except Exception as e:
        log.error("load_daily_ohlc failed: %s", e)
        return pd.DataFrame(columns=["date","open","high","low","close","vix",
                                      "tc","bc","pvt","cpr_mid","ema"])


def get_expiry(index: str) -> str:
    """
    Return nearest valid weekly expiry (YYYYMMDD).
    Validates by searching Angel One for a real contract token.
    NIFTY: Tuesday (Mon if holiday). SENSEX: Friday.
    """
    today = date.today()
    a     = get_angel()
    si    = 50  # strike interval for validation
    # rough ATM estimate
    try:
        spot = a.get_nifty_ltp()
        atm  = int(round(spot / si) * si)
    except Exception:
        atm  = 24000

    target_days = (1, 0) if index == "NIFTY" else (4,)
    for delta in range(1, 14):
        d = today + timedelta(days=delta)
        if d.weekday() not in target_days:
            continue
        expiry_yyyymmdd = d.strftime("%Y%m%d")
        expiry_ddmmyy   = d.strftime("%d%b%y").upper()
        sym = f"{index}{expiry_ddmmyy}{atm}CE"
        try:
            a.search_option_token(sym)
            return expiry_yyyymmdd   # contract exists — valid expiry
        except Exception:
            continue                 # no contract — try next week
    # fallback: 7 days ahead
    return (today + timedelta(days=7)).strftime("%Y%m%d")


# ── Position tracker ──────────────────────────────────────────────────────────
class Position:
    """Tracks a single open position + trailing SL."""
    def __init__(self, index: str, strategy: str, zone: str, opt: str,
                 strike: int, expiry: str, lots: int, score: int,
                 entry_price: float, entry_time: str, dte: int,
                 features: dict, sl_pct: float, tgt_pct: float):
        self.index      = index
        self.strategy   = strategy
        self.zone       = zone
        self.opt        = opt
        self.strike     = strike
        self.expiry     = expiry
        self.lots       = lots
        self.score      = score
        self.ep         = entry_price
        self.entry_time = entry_time
        self.dte        = dte
        self.features   = features
        self.trail      = TrailingSL(entry_price, sl_pct, tgt_pct)
        log.info("Position open: %s %s %s%s ep=%.2f lots=%d score=%d sl=%.2f tgt=%.2f",
                 index, strategy, strike, opt, entry_price, lots,
                 score, self.trail.sl, self.trail.target)

    def on_tick(self, ltp: float, now: dtime) -> str | None:
        """Update trailing SL. Returns exit reason or None."""
        if now >= EOD_EXIT_TIME:
            return "eod"
        return self.trail.update(ltp)

    def close(self, exit_price: float, exit_reason: str, exit_time: str) -> dict:
        """Build trade record for CSV."""
        lot_size = INDICES[self.index]["lot_size"]
        pnl      = r2((self.ep - exit_price) * lot_size * self.lots)
        return dict(
            date         = date.today().strftime("%Y%m%d"),
            index        = self.index,
            strategy     = self.strategy,
            zone         = self.zone,
            opt          = self.opt,
            strike       = self.strike,
            expiry       = self.expiry,
            dte          = self.dte,
            lots         = self.lots,
            score        = self.score,
            entry_time   = self.entry_time,
            entry_price  = self.ep,
            exit_time    = exit_time,
            exit_price   = exit_price,
            exit_reason  = exit_reason,
            pnl          = pnl,
            win          = int(pnl > 0),
            **{k: int(v) for k, v in self.features.items() if k != "score"},
        )


# ── Main trading day ──────────────────────────────────────────────────────────
def run_trading_day():
    today     = date.today()
    weekday   = today.weekday()   # 0=Mon … 6=Sun
    dual_mode = weekday in DUAL_INDEX_DAYS
    indices   = ["NIFTY", "SENSEX"] if dual_mode else ["NIFTY"]

    log.info("=" * 60)
    log.info("Trading day: %s  indices: %s", today, indices)

    # ── Pre-market: build features for each index ─────────────────────────────
    daily = {}
    feats = {}
    pvts  = {}
    cams  = {}
    expiries = {}

    for idx in ["NIFTY"]:   # features always from NIFTY
        df = load_daily_ohlc(idx)
        if df.empty or len(df) < 4:
            log.error("Insufficient historical data for %s. Aborting.", idx)
            return
        daily[idx]   = df
        today_i      = len(df) - 1
        feats[idx]   = compute_features(df, today_i)
        prev         = df.iloc[today_i - 1]
        pvts[idx]    = compute_cpr(prev["high"], prev["low"], prev["close"])
        cams[idx]    = compute_camarilla(prev["high"], prev["low"], prev["close"])
        expiries[idx]= get_expiry(idx)

    if dual_mode:
        df_s = load_daily_ohlc("SENSEX")
        if not df_s.empty and len(df_s) >= 4:
            si           = len(df_s) - 1
            pvts["SENSEX"]  = compute_cpr(df_s.iloc[si-1]["high"],
                                           df_s.iloc[si-1]["low"],
                                           df_s.iloc[si-1]["close"])
            cams["SENSEX"]  = compute_camarilla(df_s.iloc[si-1]["high"],
                                                 df_s.iloc[si-1]["low"],
                                                 df_s.iloc[si-1]["close"])
            expiries["SENSEX"] = get_expiry("SENSEX")
        else:
            log.warning("SENSEX data insufficient — trading NIFTY only today")
            dual_mode = False

    nifty_feats = feats["NIFTY"]
    log.info("Conviction features: score=%d inside_cpr=%s",
             nifty_feats["score"], nifty_feats["inside_cpr"])

    # ── State tracking ────────────────────────────────────────────────────────
    positions: list[Position] = []   # open positions (max 1 per index)
    signal_taken = False             # NIFTY v17a/cam/iv2 fired today
    cam_l3_done  = False
    cam_h3_done  = False
    iv2_r1_done  = False
    iv2_r2_done  = False
    iv2_pdl_done = False

    def enter_trade(index: str, strategy: str, zone: str, opt: str,
                    stype: str, tgt_pct: float, sl_pct: float):
        """Place order and open Position tracker."""
        spot = get_spot_ltp(index)
        if spot is None:
            log.error("Cannot get spot for %s — skipping entry", index)
            return None
        expiry = expiries.get(index, get_expiry(index))
        strike = get_strike(spot, index, opt, stype)
        lots   = score_to_lots(nifty_feats["score"], nifty_feats["inside_cpr"])
        try:
            resp = place_sell_order(index, expiry, strike, opt, lots,
                                    strategy_tag=f"hari-{strategy}")
        except Exception as e:
            log.error("Order placement failed for %s: %s", index, e)
            return None
        # Get entry price
        entry_price = get_ltp(index, expiry, strike, opt) or spot * 0.02
        dte_val     = (datetime.strptime(expiry, "%Y%m%d").date() - today).days
        pos = Position(
            index=index, strategy=strategy, zone=zone, opt=opt,
            strike=strike, expiry=expiry, lots=lots,
            score=nifty_feats["score"],
            entry_price=entry_price,
            entry_time=datetime.now(IST).strftime("%H:%M:%S"),
            dte=dte_val, features=nifty_feats,
            sl_pct=sl_pct, tgt_pct=tgt_pct,
        )
        return pos

    def handle_signal(strategy: str, zone: str, opt: str,
                      stype: str, tgt_pct: float, sl_pct: float):
        """Enter trade on NIFTY. signal_taken=True only on successful order."""
        nonlocal signal_taken
        log.info("SIGNAL: %s zone=%s opt=%s stype=%s", strategy, zone, opt, stype)
        pos_n = enter_trade("NIFTY", strategy, zone, opt, stype, tgt_pct, sl_pct)
        if pos_n:
            positions.append(pos_n)
            signal_taken = True   # only set if order succeeded
        if dual_mode and strategy in ("v17a", "cam_l3", "cam_h3"):
            pos_s = enter_trade("SENSEX", strategy, zone, opt, stype, tgt_pct, sl_pct)
            if pos_s:
                positions.append(pos_s)

    # ── Wait for market open ──────────────────────────────────────────────────
    now_check = datetime.now(IST).time()
    if now_check >= EOD_EXIT_TIME:
        log.info("Already past EOD (%s). Not trading today.", now_check)
        return
    log.info("Waiting for market open (09:15)...")
    while True:
        now = datetime.now(IST).time()
        if now >= MARKET_OPEN:
            break
        time.sleep(5)

    # ── 09:15 — check v17a signal ─────────────────────────────────────────────
    spot = get_spot_ltp("NIFTY")
    if spot is None:
        log.error("Cannot get NIFTY spot at open. Aborting.")
        return

    pvt_n   = pvts["NIFTY"]
    prev_df = daily["NIFTY"].iloc[-2]
    pdh     = prev_df["high"]
    pdl     = prev_df["low"]
    ema_p   = prev_df["ema"]

    opt, zone, etime = v17a_signal(spot, pvt_n, pdh, pdl, ema_p)
    if opt and zone in V17A_PARAMS:
        # Wait for entry time
        entry_dt = datetime.strptime(etime, "%H:%M:%S").time()
        while datetime.now(IST).time() < entry_dt:
            time.sleep(1)
        _, stype, tgt, sl, _ = V17A_PARAMS[zone]
        if zone == "within_cpr":
            bias = ema_bias(ema_p, spot)
            opt  = "PE" if bias == "bull" else "CE"
        handle_signal("v17a", zone, opt, stype, tgt, sl)

    # ── Intraday loop ─────────────────────────────────────────────────────────
    log.info("Entering intraday loop...")
    while True:
        now_dt  = datetime.now(IST)
        now_t   = now_dt.time()
        now_str = now_dt.strftime("%H:%M:%S")

        if now_t > MARKET_CLOSE:
            break

        # ── Manage open positions ─────────────────────────────────────────────
        closed_positions = []
        for pos in positions:
            ltp = get_ltp(pos.index, pos.expiry, pos.strike, pos.opt)
            if ltp is None:
                continue
            reason = pos.on_tick(ltp, now_t)
            if reason:
                log.info("EXIT %s %s%s @ %.2f reason=%s",
                         pos.index, pos.strike, pos.opt, ltp, reason)
                try:
                    place_buy_order(pos.index, pos.expiry, pos.strike,
                                    pos.opt, pos.lots)
                except Exception as e:
                    log.error("Exit order failed: %s", e)
                trade = pos.close(ltp, reason, now_str)
                append_trade(trade)
                closed_positions.append(pos)

        for p in closed_positions:
            positions.remove(p)

        # ── New signals (only if no NIFTY signal yet) ─────────────────────────
        nifty_open = any(p.index == "NIFTY" for p in positions)
        if not signal_taken and not nifty_open:
            spot = get_spot_ltp("NIFTY")
            if spot:
                # Camarilla check
                cam_n = cams["NIFTY"]
                c_opt, c_lvl = cam_signal(spot, cam_n["cam_l3"], cam_n["cam_h3"],
                                          cam_l3_done, cam_h3_done)
                if c_opt:
                    o, stype, tgt, sl = cam_params(c_lvl)
                    handle_signal(c_lvl, c_lvl, c_opt, stype, tgt, sl)
                    cam_l3_done = c_lvl == "cam_l3" or cam_l3_done
                    cam_h3_done = c_lvl == "cam_h3" or cam_h3_done

                # Intraday v2 (09:30–11:20)
                elif IV2_SCAN_START <= now_t <= IV2_SCAN_END:
                    r1_lvl = pvt_n["r1"]
                    r2_lvl = pvt_n["r2"]
                    i2_opt, i2_lvl = iv2_signal(spot, r1_lvl, r2_lvl, pdl,
                                                 iv2_r1_done, iv2_r2_done, iv2_pdl_done)
                    if i2_opt:
                        o, stype, tgt, sl = iv2_params(i2_lvl)
                        handle_signal(f"iv2_{i2_lvl.lower()}", i2_lvl, i2_opt, stype, tgt, sl)
                        iv2_r1_done  = i2_lvl == "R1"  or iv2_r1_done
                        iv2_r2_done  = i2_lvl == "R2"  or iv2_r2_done
                        iv2_pdl_done = i2_lvl == "PDL" or iv2_pdl_done

        # ── EOD force exit ────────────────────────────────────────────────────
        if now_t >= EOD_EXIT_TIME and positions:
            for pos in list(positions):
                ltp = get_ltp(pos.index, pos.expiry, pos.strike, pos.opt) or pos.ep
                log.info("EOD EXIT %s %s%s @ %.2f", pos.index, pos.strike, pos.opt, ltp)
                try:
                    place_buy_order(pos.index, pos.expiry, pos.strike, pos.opt, pos.lots)
                except Exception as e:
                    log.error("EOD exit order failed: %s", e)
                trade = pos.close(ltp, "eod", now_str)
                append_trade(trade)
                positions.remove(pos)
            break

        time.sleep(TICK_SLEEP)

    log.info("Day complete. Positions closed: %d", len(positions) == 0)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_trading_day()
