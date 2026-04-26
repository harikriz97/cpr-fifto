"""
CPR Strategy v17a + Intraday v2 — Live Trader
===============================================
Run at 09:10 every trading day (weekdays).

Flow:
  1. Login Angel One + OpenAlgo
  2. Fetch 45d OHLC → compute CPR, EMA(20), zone, signal
  3. If v17a signal → enter at zone's entry time, monitor until SL/target/EOD
  4. If no signal   → scan 5-min candles 09:35–10:30 for pivot break, enter on first break

Usage:
  python trader.py             # paper trade
  python trader.py --dry-run   # signals only, no orders
  python trader.py --live      # live orders
"""

import sys, time, logging, argparse, csv, os
import pandas as pd
from datetime import datetime, timedelta, date

import config
from strategy import (
    compute_pivots, compute_ema, classify_zone,
    get_v17a_signal, get_strike, detect_intraday_break, TradeState, r2
)
from angelone import AngelOneClient
from openalgo import OpenAlgoClient

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s  %(levelname)-7s  %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE),
    ]
)
log = logging.getLogger(__name__)

POLL_SECS = 5
SCAN_SECS = 30


def wait_until(hhmm_ss: str):
    target = datetime.now().replace(
        hour=int(hhmm_ss[0:2]), minute=int(hhmm_ss[3:5]),
        second=int(hhmm_ss[6:8]) if len(hhmm_ss) > 5 else 0,
        microsecond=0
    )
    gap = (target - datetime.now()).total_seconds()
    if gap > 0:
        log.info(f"Waiting {gap:.0f}s until {hhmm_ss}...")
        time.sleep(gap)


def get_nearest_expiry(angel: AngelOneClient, spot: float) -> str:
    """Return nearest weekly expiry in Angel One format (e.g. '24APR2025').
    Skips DTE=0 (today is expiry Thursday) to avoid next-minute decay blowup.
    """
    today = date.today()
    atm   = int(round(spot / config.STRIKE_INT) * config.STRIKE_INT)
    for delta in range(0, 30):
        d = today + timedelta(days=delta)
        if d.weekday() != 3:   # Thursday
            continue
        if d == today:         # FIX: skip DTE=0 — trade on next week's expiry
            log.warning("Today is expiry Thursday (DTE=0) — skipping to next expiry")
            continue
        exp = d.strftime('%d%b%Y').upper()
        try:
            angel.search_option_token(f"NIFTY{exp}{atm}CE")
            return exp
        except Exception:
            continue
    raise RuntimeError("Could not find a valid weekly expiry")


def log_trade(**kw):
    os.makedirs('data', exist_ok=True)
    path   = 'data/live_trades.csv'
    fields = ['date','source','zone','bias','opt','symbol',
              'entry_price','exit_price','exit_reason','pnl']
    row = {f: kw.get(f, '') for f in fields}
    row['date'] = datetime.now().strftime('%Y-%m-%d')
    write_hdr = not os.path.exists(path)
    with open(path, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if write_hdr: w.writeheader()
        w.writerow(row)
    log.info(f"Trade logged → pnl={row['pnl']}")


# ── Morning setup (09:10) — OHLC only, no LTP ─────────────────────
def compute_morning_setup(angel: AngelOneClient) -> dict | None:
    """
    Fetch OHLC history and compute pivots + EMA.
    Called at 09:10 BEFORE market opens — must NOT call get_nifty_ltp() here
    because pre-market LTP = yesterday's close, giving wrong zone.
    """
    history = angel.get_nifty_ohlc_history(days=50)   # FIX: 50d for EMA seed
    if len(history) < 22:
        log.error("Insufficient OHLC history"); return None

    # FIX: validate history[-1] is today or skip it
    today_str = date.today().strftime('%Y-%m-%d')
    last_date = str(history[-1].get('date', ''))
    if today_str in last_date:
        # Angel One returned a partial today bar — use history[-2] as prev
        prev    = history[-2]
        closes  = [d['close'] for d in history[:-1]]
    else:
        # history[-1] is yesterday — normal case
        prev    = history[-1]
        closes  = [d['close'] for d in history]

    pvt       = compute_pivots(prev['high'], prev['low'], prev['close'])
    pdh       = r2(prev['high']); pdl = r2(prev['low'])
    e20       = compute_ema(closes, config.EMA_PERIOD)
    prev_body = r2(abs(prev['close'] - prev['open']) / prev['open'] * 100)

    log.info(f"Setup  PDH={pdh}  PDL={pdl}  PP={pvt['pp']}  "
             f"TC={pvt['tc']}  EMA={e20}  Body={prev_body}%")

    return dict(pvt=pvt, pdh=pdh, pdl=pdl, e20=e20, prev_body=prev_body)


# ── Signal computation (09:15:02) — needs real open price ──────────
def compute_signal(setup: dict, spot_open: float) -> dict:
    """
    Compute zone/bias/signal using actual 09:15 open price.
    Called AFTER market opens to avoid pre-market LTP forward bias.
    """
    bias   = 'bull' if spot_open > setup['e20'] else 'bear'
    zone   = classify_zone(spot_open, setup['pvt'], setup['pdh'], setup['pdl'])
    signal = get_v17a_signal(zone, bias)

    if setup['prev_body'] <= config.BODY_MIN:
        log.info(f"Body filter fail: {setup['prev_body']}% — no signal")
        signal = None

    log.info(f"Zone={zone}  Bias={bias}  Signal={signal}  "
             f"Open={spot_open:.2f}  EMA={setup['e20']:.2f}  Body={setup['prev_body']}%")

    return dict(zone=zone, bias=bias, signal=signal,
                pvt=setup['pvt'], pdh=setup['pdh'], pdl=setup['pdl'],
                spot_open=r2(spot_open), e20=setup['e20'])


# ── Monitor loop (shared by v17a + intraday v2) ────────────────────
def monitor_trade(angel, oa, symbol, token, state: TradeState,
                  sl_type, dry_run):
    eod = datetime.now().replace(hour=15, minute=20, second=0, microsecond=0)
    api_errors = 0
    MAX_API_ERRORS = 5

    while True:
        time.sleep(POLL_SECS)
        now = datetime.now()

        try:
            cp   = angel.get_option_ltp(token)
            spot = angel.get_nifty_ltp() if sl_type == 'spot' else None
            api_errors = 0  # reset on success
        except Exception as e:
            api_errors += 1
            log.warning(f"API error #{api_errors}: {e}")
            if api_errors >= MAX_API_ERRORS:
                log.error(f"Too many API errors — forcing EOD exit {symbol}")
                break
            continue

        if now >= eod:
            state.eod_exit(cp)
            log.info(f"EOD exit {symbol}  cp={cp}  pnl=₹{state.pnl:,.0f}")
            if not dry_run: oa.squareoff(symbol, config.LOT_SIZE)
            break

        act, reason = state.update(cp, spot)

        log.debug(f"{symbol}  cp={cp}  trail={state.trail_label()}"
                  f"  sl={state.sl_level}  upnl=₹{state.unrealised_pnl:,.0f}")

        if act == 'exit':
            log.info(f"Exit [{reason}] {symbol}  cp={cp}  pnl=₹{state.pnl:,.0f}")
            if not dry_run: oa.squareoff(symbol, config.LOT_SIZE)
            break


# ── v17a morning trade ─────────────────────────────────────────────
def run_v17a(angel, oa, ctx, dry_run):
    key = (ctx['zone'], ctx['bias'], ctx['signal'])
    if key not in config.V17A_PARAMS:
        log.error(f"No params for {key}"); return

    stype, entry_time, tgt_pct, sl_param, sl_type = config.V17A_PARAMS[key]
    wait_until(entry_time)

    spot    = angel.get_nifty_ltp()
    atm     = int(round(spot / config.STRIKE_INT) * config.STRIKE_INT)
    strike  = get_strike(atm, ctx['signal'], stype)
    expiry  = get_nearest_expiry(angel, spot)
    symbol  = f"NIFTY{expiry}{strike}{ctx['signal']}"
    token   = angel.search_option_token(symbol)
    ep      = angel.get_option_ltp(token)

    iv = ep / spot * 100
    if iv <= config.IV_MIN:
        log.info(f"IV filter fail: {iv:.3f}%"); return

    spot_sl = r2(ctx['pdh'] + sl_param) if sl_type == 'spot' else None
    state   = TradeState(ep, tgt_pct, sl_param, sl_type, spot_sl)

    log.info(f"v17a SELL {symbol}  ep={ep}  target={state.target}"
             f"  sl={state.hard_sl}  spot_sl={spot_sl}")
    if not dry_run: oa.place_sell_order(symbol, config.LOT_SIZE)

    monitor_trade(angel, oa, symbol, token, state, sl_type, dry_run)

    log_trade(source='v17a', zone=ctx['zone'], bias=ctx['bias'],
              opt=ctx['signal'], symbol=symbol,
              entry_price=ep, exit_price=state.exit_price,
              exit_reason=state.exit_reason, pnl=state.pnl)


# ── Intraday v2 scan ───────────────────────────────────────────────
def run_intraday_v2(angel, oa, ctx, dry_run):
    log.info("No v17a signal → starting intraday v2 scan (09:35→10:30)")
    wait_until('09:30:00')
    deadline = datetime.now().replace(hour=10, minute=30, second=5, microsecond=0)

    brk = None
    while datetime.now() < deadline:
        time.sleep(SCAN_SECS)
        now = datetime.now()
        from_dt = now.replace(hour=9, minute=15, second=0, microsecond=0)
        bars = angel.get_nifty_1min_ohlc(from_dt, now)
        if not bars: continue

        df = pd.DataFrame(bars, columns=['ts','open','high','low','close','vol'])
        df['ts'] = pd.to_datetime(df['ts'])
        df = df.set_index('ts')[['open','high','low','close']].astype(float)
        ohlc5 = df.resample('5min', closed='left', label='left').agg(
            open='first', high='max', low='min', close='last').dropna()

        brk = detect_intraday_break(ohlc5, ctx['pvt'], ctx['pdh'], ctx['pdl'],
                                    config.INTRADAY_SCAN_FROM, config.INTRADAY_SCAN_TO)
        if brk:
            log.info(f"Break: {brk['level_name']} {brk['opt']}  entry={brk['entry_dt']}")
            break

    if not brk:
        log.info("No intraday break found before 10:30. No trade."); return

    key = (brk['level_name'], brk['opt'])
    if key not in config.INTRADAY_PARAMS:
        log.error(f"No intraday params for {key}"); return

    stype, tgt_pct, sl_pct = config.INTRADAY_PARAMS[key]
    wait_until(brk['entry_dt'].strftime('%H:%M:%S'))

    spot   = angel.get_nifty_ltp()
    atm    = int(round(spot / config.STRIKE_INT) * config.STRIKE_INT)
    strike = get_strike(atm, brk['opt'], stype)
    expiry = get_nearest_expiry(angel, spot)
    symbol = f"NIFTY{expiry}{strike}{brk['opt']}"
    token  = angel.search_option_token(symbol)
    ep     = angel.get_option_ltp(token)

    state  = TradeState(ep, tgt_pct, sl_pct, 'pct')
    log.info(f"Intraday SELL {symbol}  ep={ep}  target={state.target}  sl={state.hard_sl}")
    if not dry_run: oa.place_sell_order(symbol, config.LOT_SIZE)

    monitor_trade(angel, oa, symbol, token, state, 'pct', dry_run)

    log_trade(source='intraday_v2', zone=f"{brk['level_name']}_break", bias='—',
              opt=brk['opt'], symbol=symbol,
              entry_price=ep, exit_price=state.exit_price,
              exit_reason=state.exit_reason, pnl=state.pnl)


# ── Main ───────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--live',    action='store_true')
    args = parser.parse_args()

    if args.live:
        config.PAPER_TRADE = False
        log.warning("LIVE MODE — real orders will be placed")

    log.info(f"=== CPR v17a + Intraday v2 | "
             f"{'DRY-RUN' if args.dry_run else 'PAPER' if config.PAPER_TRADE else 'LIVE'}"
             f" | {date.today()} ===")

    angel = AngelOneClient()
    angel.login()
    oa = OpenAlgoClient()

    wait_until('09:10:00')
    setup = compute_morning_setup(angel)   # OHLC + EMA only — no LTP here
    if setup is None: return

    wait_until('09:15:02')
    spot_open = angel.get_nifty_ltp()     # FIX: real open after market starts
    ctx = compute_signal(setup, spot_open)

    if ctx['signal']:
        run_v17a(angel, oa, ctx, args.dry_run)
    else:
        run_intraday_v2(angel, oa, ctx, args.dry_run)

    log.info("=== Session complete ===")


if __name__ == '__main__':
    main()
