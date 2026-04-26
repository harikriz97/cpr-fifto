"""
CPR Strategy v17a + Intraday v2 — Core Logic
=============================================
Zone classification, signal generation, intraday break detection,
and live trade state management.
"""

import numpy as np
import pandas as pd
from datetime import timedelta
from config import STRIKE_INT, LOT_SIZE, V17A_PARAMS, INTRADAY_PARAMS


def r2(v):
    return round(float(v), 2)


# ── Pivot / CPR calculation ────────────────────────────────────────
def compute_pivots(h, l, c):
    pp  = r2((h + l + c) / 3)
    bc  = r2((h + l) / 2)
    tc  = r2(2 * pp - bc)
    r1  = r2(2 * pp - l)
    r2_ = r2(pp + (h - l))
    r3  = r2(r1 + (h - l))
    r4  = r2(r2_ + (h - l))
    s1  = r2(2 * pp - h)
    s2_ = r2(pp - (h - l))
    s3  = r2(s1 - (h - l))
    s4  = r2(s2_ - (h - l))
    return dict(pp=pp, bc=bc, tc=tc, r1=r1, r2=r2_, r3=r3, r4=r4,
                s1=s1, s2=s2_, s3=s3, s4=s4)


def compute_ema(closes, period=20):
    """Compute EMA on a list/array of closes. Returns last value."""
    s = pd.Series(closes)
    return round(s.ewm(span=period, adjust=False).mean().iloc[-1], 2)


def classify_zone(open_price, pvt, pdh, pdl):
    if   open_price > pvt['r4']: return 'above_r4'
    elif open_price > pvt['r3']: return 'r3_to_r4'
    elif open_price > pvt['r2']: return 'r2_to_r3'
    elif open_price > pvt['r1']: return 'r1_to_r2'
    elif open_price > pdh:       return 'pdh_to_r1'
    elif open_price > pvt['tc']: return 'tc_to_pdh'
    elif open_price >= pvt['bc']:return 'within_cpr'
    elif open_price > pdl:       return 'pdl_to_bc'
    elif open_price > pvt['s1']: return 'pdl_to_s1'
    elif open_price > pvt['s2']: return 's1_to_s2'
    elif open_price > pvt['s3']: return 's2_to_s3'
    elif open_price > pvt['s4']: return 's3_to_s4'
    else:                        return 'below_s4'


def get_v17a_signal(zone, ema_bias):
    """Returns 'CE', 'PE', or None based on v17a rules."""
    if zone in {'above_r4', 'r3_to_r4', 'r2_to_r3', 'r1_to_r2'}:
        return 'PE'
    if zone == 'pdh_to_r1'  and ema_bias == 'bear': return 'PE'
    if zone == 'tc_to_pdh':                          return 'PE'
    if zone == 'within_cpr' and ema_bias == 'bull':  return 'PE'
    if zone == 'within_cpr' and ema_bias == 'bear':  return 'CE'
    if zone == 'pdl_to_bc'  and ema_bias == 'bull':  return 'PE'
    if zone in {'pdl_to_s1', 's1_to_s2', 's3_to_s4', 'below_s4'} and ema_bias == 'bear':
        return 'CE'
    return None


def get_strike(atm, opt_type, stype):
    if opt_type == 'CE':
        return {'OTM1': atm + STRIKE_INT, 'ATM': atm, 'ITM1': atm - STRIKE_INT}[stype]
    return {'OTM1': atm - STRIKE_INT, 'ATM': atm, 'ITM1': atm + STRIKE_INT}[stype]


# ── Intraday v2: 5-min break detection ────────────────────────────
def detect_intraday_break(ohlc_5m, pvt, pdh, pdl, scan_from='09:30', scan_to='10:25'):
    """
    Scan 5-min OHLC for first pivot level break in window.
    Break confirmed when candle CLOSE crosses level for first time.
    Previous candle must NOT have been beyond level.

    ohlc_5m: DataFrame with DatetimeIndex and columns [open, high, low, close]

    Returns dict {entry_dt, opt, level, level_name} or None.
    """
    up_levels = [
        ('R1', pvt['r1'], 'PE'),
        ('R2', pvt['r2'], 'PE'),
        # TC removed: optimization backtest showed negative avg across all targets on OTM1
    ]
    dn_levels = [
        ('PDL', pdl,       'CE'),
        ('S1',  pvt['s1'], 'CE'),
        ('S2',  pvt['s2'], 'CE'),
    ]

    scan = ohlc_5m.between_time(scan_from, scan_to)
    if len(scan) < 2:
        return None

    candles = scan.reset_index()
    ts_col  = candles.columns[0]   # name of the reset index column (robust)
    for idx in range(1, len(candles)):
        row      = candles.iloc[idx]
        prev_row = candles.iloc[idx - 1]
        c_close  = row['close']
        p_close  = prev_row['close']
        c_time   = row[ts_col]

        entry_dt = c_time + timedelta(minutes=5, seconds=2)

        for name, level, opt in up_levels:
            if p_close <= level < c_close:
                return dict(entry_dt=entry_dt, opt=opt, level=level, level_name=name)

        for name, level, opt in dn_levels:
            if p_close >= level > c_close:
                return dict(entry_dt=entry_dt, opt=opt, level=level, level_name=name)

    return None


# ── 3-tier lock-in trailing stop ───────────────────────────────────
class TradeState:
    """Manages a single open short option position."""

    def __init__(self, entry_price, target_pct, sl_param, sl_type,
                 spot_sl_level=None):
        self.entry_price   = entry_price
        self.target        = r2(entry_price * (1 - target_pct))
        self.sl_type       = sl_type
        self.hard_sl       = r2(entry_price * (1 + sl_param)) if sl_type == 'pct' \
                             else r2(entry_price * 5.0)
        self.sl_level      = self.hard_sl
        self.max_decay     = 0.0
        self.spot_sl_level  = spot_sl_level
        self.is_open        = True
        self.exit_reason    = None
        self.exit_price     = None
        self.trail_tier     = 0    # 0=none, 1=BE(25%), 2=80%(40%), 3=95%(60%)
        self._current_price = None # initialise so unrealised_pnl never raises AttributeError

    @property
    def pnl(self):
        if self.exit_price is None:
            return None
        return r2((self.entry_price - self.exit_price) * LOT_SIZE)

    @property
    def unrealised_pnl(self):
        """P&L if closed at current price (call update first)."""
        if self._current_price is None:
            return None
        return r2((self.entry_price - self._current_price) * LOT_SIZE)

    def update(self, option_price, spot_price=None):
        """
        Call on each tick/poll. Returns ('hold', None) or ('exit', reason).
        """
        self._current_price = option_price
        if not self.is_open:
            return 'hold', None

        decay = (self.entry_price - option_price) / self.entry_price
        if decay > self.max_decay:
            self.max_decay = decay

        # Update trailing SL tier
        if self.max_decay >= 0.60:
            self.trail_tier = 3
            self.sl_level = min(self.sl_level,
                                r2(self.entry_price * (1 - self.max_decay * 0.95)))
        elif self.max_decay >= 0.40:
            self.trail_tier = max(self.trail_tier, 2)
            self.sl_level = min(self.sl_level, r2(self.entry_price * 0.80))
        elif self.max_decay >= 0.25:
            self.trail_tier = max(self.trail_tier, 1)
            self.sl_level = min(self.sl_level, self.entry_price)

        # Target
        if option_price <= self.target:
            return self._close(option_price, 'target')

        # Option SL
        if option_price >= self.sl_level:
            rsn = 'lockin_sl' if self.sl_level < self.hard_sl else 'hard_sl'
            return self._close(option_price, rsn)

        # Spot SL (tc_to_pdh bear PE)
        if (self.sl_type == 'spot' and spot_price is not None
                and self.spot_sl_level is not None
                and spot_price >= self.spot_sl_level):
            return self._close(option_price, 'spot_sl')

        return 'hold', None

    def eod_exit(self, option_price):
        return self._close(option_price, 'eod')

    def _close(self, price, reason):
        self.is_open     = False
        self.exit_price  = price
        self.exit_reason = reason
        return 'exit', reason

    def trail_label(self):
        return {0: 'None', 1: 'Break-even (25%)',
                2: '80% lock (40%)', 3: '95% lock (60%)'}[self.trail_tier]

    def sl_pct_from_entry(self):
        return r2((self.sl_level / self.entry_price - 1) * 100)
