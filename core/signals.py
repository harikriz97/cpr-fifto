"""
core/signals.py — Signal detection for v17a, Camarilla, Intraday v2
All signal functions return the option type (CE/PE) or None.
"""
from __future__ import annotations
from datetime import datetime, time as dtime
from config import V17A_PARAMS, CAM_L3_PARAMS, CAM_H3_PARAMS, IV2_PARAMS
from core.levels import classify_zone, ema_bias, r2


# ── v17a signal ───────────────────────────────────────────────────────────────
def v17a_signal(open_price: float, pvt: dict, pdh: float, pdl: float,
                ema_prev: float) -> tuple[str | None, str, str]:
    """
    Returns (opt, zone, entry_time_str) or (None, zone, '') if no signal.
    opt: 'CE' or 'PE'
    """
    zone  = classify_zone(open_price, pvt, pdh, pdl)
    bias  = ema_bias(ema_prev, open_price)
    params = V17A_PARAMS.get(zone)
    if params is None:
        return None, zone, ""

    opt, stype, tgt, sl, etime = params

    # within_cpr: opt from bias
    if zone == "within_cpr":
        opt = "PE" if bias == "bull" else "CE"

    # zones that need bear/bull confirmation
    if zone == "pdh_to_r1" and bias != "bear":
        return None, zone, ""
    if zone in {"pdl_to_bc"} and bias != "bull":
        return None, zone, ""
    if zone in {"pdl_to_s1", "s1_to_s2", "s2_to_s3", "s3_to_s4", "below_s4"} and bias != "bear":
        return None, zone, ""

    return opt, zone, etime


def v17a_params(zone: str) -> tuple:
    """Return (opt, strike_type, tgt_pct, sl_pct) for a zone."""
    p = V17A_PARAMS[zone]
    return p[0], p[1], p[2], p[3]


# ── Camarilla signal (intraday tick-level) ────────────────────────────────────
def cam_signal(price: float, cam_l3: float, cam_h3: float,
               l3_triggered: bool, h3_triggered: bool) -> tuple[str | None, str]:
    """
    Returns (opt, level_name) on first cross, else (None, '').
    Once triggered, returns None to prevent double entry.
    """
    if not l3_triggered and price < cam_l3:
        return "CE", "cam_l3"     # price fell below L3 → bearish → sell CE
    if not h3_triggered and price > cam_h3:
        return "PE", "cam_h3"     # price rose above H3 → bullish → sell PE
    return None, ""


def cam_params(level: str) -> tuple:
    """Return (opt, strike_type, tgt_pct, sl_pct)."""
    if level == "cam_l3":
        return CAM_L3_PARAMS
    return CAM_H3_PARAMS


# ── Intraday v2 signal (R1/R2/PDL break) ─────────────────────────────────────
def iv2_signal(price: float, r1: float, r2_: float, pdl: float,
               r1_done: bool, r2_done: bool, pdl_done: bool) -> tuple[str | None, str]:
    """
    Returns (opt, level_name) on break, else (None, '').
    price:  current NIFTY spot tick
    """
    if not r2_done and price > r2_:
        return "PE", "R2"
    if not r1_done and price > r1:
        return "PE", "R1"
    if not pdl_done and price < pdl:
        return "CE", "PDL"
    return None, ""


def iv2_params(level: str) -> tuple:
    """Return (opt, strike_type, tgt_pct, sl_pct)."""
    return IV2_PARAMS[level]
