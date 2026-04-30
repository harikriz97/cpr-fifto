"""
core/levels.py — CPR, Pivot, R/S, Camarilla, EMA calculations
All use PREVIOUS day data only — no forward bias.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from config import EMA_PERIOD, CAM_RATIO, CPR_NARROW_PCT, CPR_GAP_PCT


def r2(v) -> float:
    return round(float(v), 2)


# ── CPR + standard pivots ────────────────────────────────────────────────────
def compute_cpr(high: float, low: float, close: float) -> dict:
    """Return pivot, TC, BC, R1-R4, S1-S4 from previous day HLC."""
    pvt = r2((high + low + close) / 3)
    bc  = r2((high + low) / 2)
    tc  = r2(pvt + (pvt - bc))
    r1  = r2(2 * pvt - low)
    r2_ = r2(pvt + (high - low))
    r3  = r2(high + 2 * (pvt - low))
    r4  = r2(r3 + (high - low))
    s1  = r2(2 * pvt - high)
    s2  = r2(pvt - (high - low))
    s3  = r2(low - 2 * (high - pvt))
    s4  = r2(s3 - (high - low))
    return dict(pvt=pvt, tc=tc, bc=bc,
                r1=r1, r2=r2_, r3=r3, r4=r4,
                s1=s1, s2=s2, s3=s3, s4=s4)


# ── Camarilla levels ──────────────────────────────────────────────────────────
def compute_camarilla(high: float, low: float, close: float) -> dict:
    """Return cam_h3, cam_l3 from previous day HLC."""
    rng  = high - low
    h3   = r2(close + rng * CAM_RATIO)
    l3   = r2(close - rng * CAM_RATIO)
    return dict(cam_h3=h3, cam_l3=l3)


# ── EMA (shifted — no forward bias) ─────────────────────────────────────────
def compute_ema_series(close_series: pd.Series, period: int = EMA_PERIOD) -> pd.Series:
    """
    Returns EMA series.  Caller must shift(1) before using as a signal feature
    so today's EMA is computed from yesterday's close.
    Requires at least EMA_SEED bars seeded before the first signal date.
    """
    return close_series.ewm(span=period, adjust=False).mean().round(2)


def ema_bias(ema_prev: float, open_price: float) -> str:
    """'bull' if open > prev EMA, 'bear' otherwise."""
    return "bull" if open_price > ema_prev else "bear"


# ── Zone classification ───────────────────────────────────────────────────────
def classify_zone(open_price: float, pvt: dict, pdh: float, pdl: float) -> str:
    """
    Map today's open price to a v17a zone using yesterday's pivot levels.
    Returns zone string.
    """
    op = open_price
    if   op > pvt["r4"]:        return "r2_plus"    # collapse r4+ → r2_plus
    elif op > pvt["r3"]:        return "r2_plus"
    elif op > pvt["r2"]:        return "r2_plus"
    elif op > pvt["r1"]:        return "r1_to_r2"
    elif op > pdh:               return "pdh_to_r1"  # open between PDH and R1
    elif op > pvt["tc"]:        return "tc_to_pdh"
    elif op >= pvt["bc"]:       return "within_cpr"
    elif op > pdl:               return "pdl_to_bc"
    elif op > pvt["s1"]:        return "pdl_to_s1"
    elif op > pvt["s2"]:        return "s1_to_s2"
    elif op > pvt["s3"]:        return "s2_to_s3"
    elif op > pvt["s4"]:        return "s3_to_s4"
    else:                        return "below_s4"


# ── Conviction features ───────────────────────────────────────────────────────
def compute_features(daily_df: pd.DataFrame, today_idx: int) -> dict:
    """
    Compute all 7 conviction features + inside_cpr for the trade day at today_idx.
    daily_df must be sorted ascending with columns:
      date, open, high, low, close, vix, tc, bc, pvt, cpr_mid, ema
    All features use shift — today_idx row is NOT used.

    Returns dict of feature bools + score + inside_cpr.
    """
    if today_idx < 3:
        return _zero_features()

    df   = daily_df
    i    = today_idx
    prev = df.iloc[i - 1]     # yesterday
    pp   = df.iloc[i - 2]     # day before yesterday
    ppp  = df.iloc[i - 3]     # 3 days ago
    tod  = df.iloc[i]         # today (only open used for ema_bias — already shifted)

    # 1. vix_ok: prev day VIX < VIX_MAX
    from config import VIX_MAX
    vix_ok = bool(prev["vix"] < VIX_MAX) if prev["vix"] > 0 else False

    # 2. cpr_trend_aligned: prev close relative to prev CPR midpoint
    cpr_mid_prev = (prev["tc"] + prev["bc"]) / 2
    cpr_trend_aligned = bool(
        (tod["open"] > cpr_mid_prev) or (tod["open"] < cpr_mid_prev)
    )  # always True — refined: open same side as EMA bias vs CPR
    bias = ema_bias(prev["ema"], tod["open"])
    cpr_trend_aligned = bool(
        (bias == "bull" and tod["open"] > cpr_mid_prev) or
        (bias == "bear" and tod["open"] < cpr_mid_prev)
    )

    # 3. consec_aligned: 2 consecutive prev closes on same side of prev EMA
    consec_aligned = bool(
        (prev["close"] > prev["ema"] and pp["close"] > pp["ema"]) or
        (prev["close"] < prev["ema"] and pp["close"] < pp["ema"])
    )

    # 4. cpr_gap_aligned: open far from pivot (gap day)
    gap_pct = abs(tod["open"] - prev["pvt"]) / prev["pvt"]
    cpr_gap_aligned = bool(gap_pct > CPR_GAP_PCT)

    # 5. dte_sweet: DTE in [2, 6]  (caller fills tod["dte"])
    dte = int(tod.get("dte", 0))
    dte_sweet = bool(2 <= dte <= 6)

    # 6. cpr_narrow: TC-BC / spot < threshold
    cpr_range_pct = abs(prev["tc"] - prev["bc"]) / prev["close"] if prev["close"] > 0 else 1
    cpr_narrow = bool(cpr_range_pct < CPR_NARROW_PCT)

    # 7. cpr_dir_aligned: CPR midpoint ascending 3 days (bull) or descending (bear)
    mid_1 = (prev["tc"] + prev["bc"]) / 2
    mid_2 = (pp["tc"]   + pp["bc"])   / 2
    mid_3 = (ppp["tc"]  + ppp["bc"])  / 2
    asc   = mid_1 > mid_2 > mid_3
    desc  = mid_1 < mid_2 < mid_3
    cpr_dir_aligned = bool(
        (bias == "bull" and asc) or
        (bias == "bear" and desc)
    )

    # inside_cpr (negative): yesterday's CPR inside day-before's CPR
    inside_cpr = bool(
        prev["tc"] < pp["tc"] and prev["bc"] > pp["bc"]
    )

    score = sum([vix_ok, cpr_trend_aligned, consec_aligned,
                 cpr_gap_aligned, dte_sweet, cpr_narrow, cpr_dir_aligned])

    return dict(
        vix_ok=vix_ok,
        cpr_trend_aligned=cpr_trend_aligned,
        consec_aligned=consec_aligned,
        cpr_gap_aligned=cpr_gap_aligned,
        dte_sweet=dte_sweet,
        cpr_narrow=cpr_narrow,
        cpr_dir_aligned=cpr_dir_aligned,
        inside_cpr=inside_cpr,
        score=score,
    )


def _zero_features() -> dict:
    return dict(vix_ok=False, cpr_trend_aligned=False, consec_aligned=False,
                cpr_gap_aligned=False, dte_sweet=False, cpr_narrow=False,
                cpr_dir_aligned=False, inside_cpr=False, score=0)
