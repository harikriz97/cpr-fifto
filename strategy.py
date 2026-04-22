"""
v13 CPR Strategy — Core Logic
==============================
All zone classification, signal generation, and trade simulation logic.
Matches backtested v13 exactly.
"""

import numpy as np
from config import STRIKE_INT, LOT_SIZE

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

def get_signal(zone, ema_bias):
    """Returns 'CE', 'PE', or None."""
    if zone in {'above_r4', 'r3_to_r4', 'r2_to_r3', 'r1_to_r2'}:
        return 'PE'
    if zone == 'pdh_to_r1'  and ema_bias == 'bear': return 'PE'
    if zone == 'tc_to_pdh':                          return 'PE'   # spot SL for bear
    if zone == 'within_cpr' and ema_bias == 'bull':  return 'PE'
    if zone == 'within_cpr' and ema_bias == 'bear':  return 'CE'
    if zone == 'pdl_to_bc'  and ema_bias == 'bull':  return 'PE'
    if zone in {'pdl_to_s1','s1_to_s2','s3_to_s4','below_s4'} and ema_bias == 'bear':
        return 'CE'
    return None

def get_strike(atm, opt_type, stype):
    if opt_type == 'CE':
        return {'OTM1': atm + STRIKE_INT, 'ATM': atm, 'ITM1': atm - STRIKE_INT}[stype]
    return {'OTM1': atm - STRIKE_INT, 'ATM': atm, 'ITM1': atm + STRIKE_INT}[stype]

# ── 3-tier lock-in trailing stop ───────────────────────────────────
def check_trail(entry_price, current_price, max_decay, sl_level):
    """
    Update trailing SL based on decay milestone.
    Returns new sl_level.
    """
    hard_sl = r2(entry_price * (1 + 0))   # placeholder, actual hsl set at entry
    if   max_decay >= 0.60:
        new_sl = r2(entry_price * (1 - max_decay * 0.95))
        sl_level = min(sl_level, new_sl)
    elif max_decay >= 0.40:
        sl_level = min(sl_level, r2(entry_price * 0.80))
    elif max_decay >= 0.25:
        sl_level = min(sl_level, entry_price)
    return sl_level

# ── Live trade state manager ───────────────────────────────────────
class TradeState:
    """Manages a single open option sell position."""

    def __init__(self, entry_price, target_pct, sl_param, sl_type,
                 pdh=None, spot_sl_level=None):
        self.entry_price    = entry_price
        self.target         = r2(entry_price * (1 - target_pct))
        self.sl_type        = sl_type

        if sl_type == 'pct':
            self.hard_sl    = r2(entry_price * (1 + sl_param))
        else:  # spot
            self.hard_sl    = r2(entry_price * 5.0)   # very wide — spot handles exit

        self.sl_level       = self.hard_sl
        self.max_decay      = 0.0
        self.spot_sl_level  = spot_sl_level   # PDH + buffer for tc_to_pdh bear PE
        self.is_open        = True
        self.exit_reason    = None
        self.exit_price     = None

    def update(self, option_price, spot_price=None):
        """
        Call each tick/bar. Returns ('hold', None) or ('exit', reason).
        """
        if not self.is_open:
            return 'hold', None

        decay = (self.entry_price - option_price) / self.entry_price
        if decay > self.max_decay:
            self.max_decay = decay

        # Update trailing SL
        self.sl_level = check_trail(self.entry_price, option_price,
                                    self.max_decay, self.sl_level)

        # Target hit
        if option_price <= self.target:
            self._close(option_price, 'target')
            return 'exit', 'target'

        # Option SL hit
        if option_price >= self.sl_level:
            rsn = 'lockin_sl' if self.sl_level < self.hard_sl else 'hard_sl'
            self._close(option_price, rsn)
            return 'exit', rsn

        # Spot-based SL (tc_to_pdh bear PE: exit when spot >= PDH + buffer)
        if self.sl_type == 'spot' and spot_price is not None:
            if spot_price >= self.spot_sl_level:
                self._close(option_price, 'spot_sl')
                return 'exit', 'spot_sl'

        return 'hold', None

    def eod_exit(self, option_price):
        self._close(option_price, 'eod')
        return 'exit', 'eod'

    def pnl(self):
        if self.exit_price is None:
            return None
        return r2((self.entry_price - self.exit_price) * LOT_SIZE)

    def _close(self, price, reason):
        self.is_open     = False
        self.exit_price  = price
        self.exit_reason = reason
