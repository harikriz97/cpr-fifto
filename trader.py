"""
v13 CPR Strategy — Live / Paper Trading Orchestrator
======================================================
Main entry point. Run this every trading day at 09:10 AM.

Usage:
    python trader.py                  # paper trade (config.PAPER_TRADE=True)
    python trader.py --live           # live trade
    python trader.py --date 20260420  # backtest single day (dry run)

Flow:
    1. Login Angel One + OpenAlgo
    2. Fetch previous day OHLC + compute CPR, EMA(20)
    3. Determine zone + signal (CE/PE sell)
    4. Wait for entry time → enter position via OpenAlgo
    5. Monitor every 5 seconds → trail stop or exit on SL/target/EOD
"""

import sys
import time
import logging
import argparse
from datetime import datetime, date

import pandas as pd
import numpy as np

from config import (BEST_PARAMS, LOT_SIZE, STRIKE_INT, EMA_PERIOD,
                    EOD_EXIT, IV_MIN, BODY_MIN, PAPER_TRADE, LOG_FILE)
from strategy  import compute_pivots, classify_zone, get_signal, get_strike, TradeState
from angelone  import AngelOneClient
from openalgo  import OpenAlgoClient

# ── Logging ───────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


def ema_series(closes, n=20):
    """Compute EMA on a list of closes. Returns last value."""
    s = pd.Series(closes)
    return round(float(s.ewm(span=n, adjust=False).mean().iloc[-1]), 2)


def wait_until(target_time_str):
    """Block until HH:MM:SS is reached."""
    today = date.today().isoformat()
    target = datetime.fromisoformat(f"{today} {target_time_str}")
    now = datetime.now()
    if now < target:
        secs = (target - now).total_seconds()
        log.info(f"Waiting {secs:.0f}s until {target_time_str}...")
        time.sleep(max(0, secs))


def run_strategy(angel: AngelOneClient, openalgo: OpenAlgoClient, dry_run=False):
    log.info("=" * 60)
    log.info(f"v13 CPR Strategy | {'PAPER' if PAPER_TRADE else 'LIVE'} | {date.today()}")
    log.info("=" * 60)

    # ── Step 1: Fetch history + compute CPR / EMA ─────────────────
    history = angel.get_nifty_ohlc_history(days=EMA_PERIOD + 10)
    if len(history) < EMA_PERIOD + 1:
        log.error("Insufficient historical data. Exiting.")
        return

    prev  = history[-2]   # previous trading day
    today = history[-1]   # today (only open is reliable before market opens)

    ph, pl, pc = prev['high'], prev['low'], prev['close']
    pop        = prev['open']
    pvt        = compute_pivots(ph, pl, pc)

    closes     = [d['close'] for d in history[:-1]]  # exclude today for EMA
    e20        = ema_series(closes, EMA_PERIOD)

    # Today's open (first tick after 09:15)
    today_open = angel.get_nifty_ltp()   # best proxy at 09:15
    log.info(f"Prev Day: H={ph} L={pl} C={pc} O={pop}")
    log.info(f"Today Open: {today_open}  EMA20: {e20}")

    atm = int(round(today_open / STRIKE_INT) * STRIKE_INT)
    log.info(f"ATM Strike: {atm}")

    # ── Step 2: CPR zone + signal ──────────────────────────────────
    prev_body = round(abs(pc - pop) / pop * 100, 3)
    bias      = 'bull' if today_open > e20 else 'bear'
    zone      = classify_zone(today_open, pvt, ph, pl)
    signal    = get_signal(zone, bias)

    log.info(f"Zone: {zone}  Bias: {bias}  Signal: {signal}  Body: {prev_body}%")

    if signal is None:
        log.info("No signal today. Exiting.")
        return
    if prev_body <= BODY_MIN:
        log.info(f"Body filter failed ({prev_body}% <= {BODY_MIN}%). Exiting.")
        return

    key = (zone, bias, signal)
    if key not in BEST_PARAMS:
        log.info(f"No params for zone {key}. Exiting.")
        return

    strike_type, entry_time, target_pct, sl_param, sl_type = BEST_PARAMS[key]
    strike = get_strike(atm, signal, strike_type)
    log.info(f"Trade plan: SELL {signal} strike={strike} ({strike_type})"
             f" entry={entry_time} tgt={target_pct:.0%} sl={sl_param}")

    # ── Step 3: IV proxy filter at 09:16 ──────────────────────────
    wait_until("09:15:30")
    # Get ATM ITM1 option price for IV proxy
    from datetime import datetime as dt
    today_str   = date.today().strftime("%d%b%Y").upper()
    # Find nearest weekly expiry (Angel One format)
    # NOTE: In production, iterate expiry list from Angel One option chain
    expiry_fmt  = today_str  # placeholder — replace with actual expiry lookup

    try:
        iv_symbol, iv_token, iv_ltp = angel.get_option_chain_ltp(
            expiry_fmt, atm, signal, 'ITM1'
        )
    except Exception as e:
        log.warning(f"IV proxy check failed: {e}. Proceeding without filter.")
        iv_ltp = today_open * IV_MIN / 100 + 1  # bypass filter

    iv_proxy = round(iv_ltp / today_open * 100, 3)
    log.info(f"IV proxy: {iv_proxy}% (min={IV_MIN}%)")
    if iv_proxy <= IV_MIN:
        log.info("IV filter failed. Exiting.")
        return

    # ── Step 4: Wait for entry time ────────────────────────────────
    wait_until(entry_time)
    log.info(f"Entry time reached: {entry_time}")

    # Get actual option price at entry
    opt_symbol = f"NIFTY{expiry_fmt}{strike}{signal}"
    try:
        opt_token = angel.search_option_token(opt_symbol)
        entry_price = angel.get_option_ltp(opt_token)
    except Exception as e:
        log.error(f"Could not fetch entry price: {e}")
        return

    if entry_price <= 0:
        log.error("Entry price is 0. Aborting.")
        return

    # ── Step 5: Place order ────────────────────────────────────────
    spot_sl_level = None
    if sl_type == 'spot':
        spot_sl_level = ph + sl_param   # PDH + buffer

    trade = TradeState(
        entry_price  = entry_price,
        target_pct   = target_pct,
        sl_param     = sl_param,
        sl_type      = sl_type,
        pdh          = ph,
        spot_sl_level= spot_sl_level
    )

    log.info(f"Entering trade: SELL {opt_symbol} @ {entry_price}")
    log.info(f"  Target={trade.target}  SL={trade.sl_level}"
             f"  ({'Spot SL @ ' + str(spot_sl_level) if sl_type == 'spot' else '%SL'})")

    if not dry_run:
        order_id = openalgo.place_sell_order(opt_symbol, LOT_SIZE)
        log.info(f"Order placed: {order_id}")
    else:
        log.info("DRY RUN — no order placed")

    # ── Step 6: Monitor position ───────────────────────────────────
    eod_time = datetime.fromisoformat(f"{date.today().isoformat()} {EOD_EXIT}")
    log.info("Monitoring position every 5 seconds...")

    while True:
        now = datetime.now()

        # EOD exit
        if now >= eod_time:
            cur_price = angel.get_option_ltp(opt_token)
            action, reason = trade.eod_exit(cur_price)
            log.info(f"EOD exit @ {cur_price} | PnL=₹{trade.pnl():,.0f}")
            if not dry_run:
                openalgo.squareoff(opt_symbol, LOT_SIZE)
            break

        # Fetch current prices
        try:
            cur_opt_price   = angel.get_option_ltp(opt_token)
            cur_spot_price  = angel.get_nifty_ltp() if sl_type == 'spot' else None
        except Exception as e:
            log.warning(f"Price fetch error: {e}. Retrying...")
            time.sleep(5)
            continue

        action, reason = trade.update(cur_opt_price, cur_spot_price)

        log.info(f"  {now.strftime('%H:%M:%S')}  Opt={cur_opt_price}"
                 f"  Decay={trade.max_decay:.1%}  SL={trade.sl_level}"
                 f"  {'→ ' + reason.upper() if action=='exit' else ''}")

        if action == 'exit':
            pnl = trade.pnl()
            log.info(f"EXIT [{reason}] @ {trade.exit_price} | PnL=₹{pnl:,.0f}")
            if not dry_run:
                openalgo.squareoff(opt_symbol, LOT_SIZE)
            break

        time.sleep(5)   # poll every 5 seconds

    log.info(f"Trade complete. Exit: {trade.exit_reason}  PnL: ₹{trade.pnl():,.0f}")
    return trade


def main():
    parser = argparse.ArgumentParser(description="v13 CPR Strategy Trader")
    parser.add_argument('--live',    action='store_true', help='Enable live trading')
    parser.add_argument('--dry-run', action='store_true', help='Simulate — no orders placed')
    args = parser.parse_args()

    if args.live and not args.dry_run:
        import config
        config.PAPER_TRADE = False
        log.warning("LIVE TRADING MODE ENABLED")

    angel    = AngelOneClient()
    openalgo = OpenAlgoClient()

    angel.login()
    log.info("Angel One connected.")

    run_strategy(angel, openalgo, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
