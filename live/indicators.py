"""
indicators.py — All technical computations (pure Python/Pandas, no API calls)
==============================================================================
Computes: CPR, Camarilla, MRC levels, EMA(20), Heiken Ashi, IB, score7.
All formulas exactly match backtest scripts 72 / 91 / 100.
"""

import numpy as np
import pandas as pd
from config import SCORE_LOT_MAP


# ── CPR (Central Pivot Range) ──────────────────────────────────────────────────
def compute_cpr(prev_high: float, prev_low: float, prev_close: float) -> dict:
    """
    Standard CPR from previous day H/L/C.
    Returns: pivot, tc, bc, r1, r2, r3, s1, s2, s3, cpr_width_pct
    """
    pvt = (prev_high + prev_low + prev_close) / 3
    bc  = (prev_high + prev_low) / 2
    tc  = 2 * pvt - bc
    r1  = 2 * pvt - prev_low
    r2  = pvt + (prev_high - prev_low)
    r3  = prev_high + 2 * (pvt - prev_low)
    s1  = 2 * pvt - prev_high
    s2  = pvt - (prev_high - prev_low)
    s3  = prev_low - 2 * (prev_high - pvt)
    cpr_width_pct = abs(tc - bc) / pvt * 100
    return {
        "pivot": round(pvt, 2), "tc": round(tc, 2), "bc": round(bc, 2),
        "r1": round(r1, 2), "r2": round(r2, 2), "r3": round(r3, 2),
        "s1": round(s1, 2), "s2": round(s2, 2), "s3": round(s3, 2),
        "cpr_width_pct": round(cpr_width_pct, 4),
    }


# ── Camarilla Levels ───────────────────────────────────────────────────────────
def compute_camarilla(prev_high: float, prev_low: float,
                      prev_close: float) -> dict:
    """
    Camarilla pivot levels.
    H3/L3 = most commonly traded; H4/L4 = breakout/breakdown.
    """
    rng = prev_high - prev_low
    return {
        "cam_h4": round(prev_close + rng * 1.1 / 2,  2),
        "cam_h3": round(prev_close + rng * 1.1 / 4,  2),
        "cam_h2": round(prev_close + rng * 1.1 / 6,  2),
        "cam_h1": round(prev_close + rng * 1.1 / 12, 2),
        "cam_l1": round(prev_close - rng * 1.1 / 12, 2),
        "cam_l2": round(prev_close - rng * 1.1 / 6,  2),
        "cam_l3": round(prev_close - rng * 1.1 / 4,  2),
        "cam_l4": round(prev_close - rng * 1.1 / 2,  2),
    }


# ── MRC (Mean Reversion Concept) Levels ───────────────────────────────────────
def compute_mrc_levels(pdh: float, pdl: float) -> dict:
    """
    MRC levels from PDH/PDL.
    l_382: buy zone (close above → bullish signal)
    l_618: sell zone (close below → bearish signal)
    l_50:  median / SL reference
    From backtest script 100.
    """
    rng = pdh - pdl
    return {
        "l_0":   round(pdh, 2),
        "l_25":  round(pdh - rng * 0.25,  2),
        "l_382": round(pdh - rng * 0.382, 2),   # BUY above → PE sell
        "l_50":  round(pdh - rng * 0.500, 2),   # median
        "l_618": round(pdh - rng * 0.618, 2),   # SELL below → CE sell
        "l_75":  round(pdh - rng * 0.75,  2),
        "l_100": round(pdl, 2),
        "range": round(rng, 2),
    }


# ── IB (Initial Balance) ───────────────────────────────────────────────────────
def compute_ib(spot_ticks: pd.DataFrame,
               ib_start: str = "09:15:00",
               ib_end:   str = "09:45:00") -> dict:
    """
    IB = max/min of spot price from 09:15 to 09:45 inclusive.
    spot_ticks: DataFrame with columns ['time', 'price']
    Returns: ib_high, ib_low, ib_range
    """
    ib = spot_ticks[(spot_ticks["time"] >= ib_start) &
                    (spot_ticks["time"] <= ib_end)]
    if ib.empty:
        return {"ib_high": None, "ib_low": None, "ib_range": None}
    ib_h = round(ib["price"].max(), 2)
    ib_l = round(ib["price"].min(), 2)
    return {"ib_high": ib_h, "ib_low": ib_l, "ib_range": round(ib_h - ib_l, 2)}


def is_ib_expanded_up(spot_ticks: pd.DataFrame,
                      ib_high: float, entry_time: str) -> bool:
    """
    Real-time IB expansion check — same logic as script 126 rt_ib_check.
    Returns True if spot ALREADY broke above IB_H before entry_time.
    Only looks at ticks AFTER IB end (09:45) and BEFORE entry_time.
    """
    window = spot_ticks[(spot_ticks["time"] > "09:45:00") &
                        (spot_ticks["time"] < entry_time)]
    return (not window.empty) and (window["price"].max() > ib_high)


# ── EMA(20) ────────────────────────────────────────────────────────────────────
def compute_ema20(close_series: pd.Series) -> pd.Series:
    """
    EMA(20) with 40-day seed — exactly as in backtest script 72.
    First 39 values are NaN (need at least 40 bars to seed properly).
    """
    ema = close_series.ewm(span=20, adjust=False).mean()
    ema.iloc[:39] = np.nan
    return ema


# ── Heiken Ashi ────────────────────────────────────────────────────────────────
def compute_ha(ohlc_df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute Heiken Ashi on a 5M OHLC DataFrame.
    ohlc_df must have columns: o, h, l, c  (+ optionally 'time')
    Returns same df with added: ha_o, ha_c, ha_h, ha_l
    Exactly matches script 100's compute_ha().
    """
    ha = ohlc_df.copy()
    ha["ha_c"] = ((ohlc_df["o"] + ohlc_df["h"] +
                   ohlc_df["l"] + ohlc_df["c"]) / 4).round(2)
    ha_o_list = [0.0] * len(ha)
    ha_o_list[0] = round((ohlc_df["o"].iloc[0] + ohlc_df["c"].iloc[0]) / 2, 2)
    for i in range(1, len(ha)):
        ha_o_list[i] = round((ha_o_list[i-1] + ha["ha_c"].iloc[i-1]) / 2, 2)
    ha["ha_o"] = ha_o_list
    ha["ha_h"] = ha[["h", "ha_o", "ha_c"]].max(axis=1).round(2)
    ha["ha_l"] = ha[["l", "ha_o", "ha_c"]].min(axis=1).round(2)
    return ha


# ── Build OHLC from ticks ──────────────────────────────────────────────────────
def build_ohlc_from_ticks(ticks: pd.DataFrame, freq: str = "5min",
                           start: str = "09:15:00",
                           end:   str = "15:30:00") -> pd.DataFrame:
    """
    Build OHLC candles from tick data.
    ticks: DataFrame with columns ['time', 'price']  (and optionally 'date')
    freq: '5min', '15min', '1min'
    Returns: DataFrame with columns [time, o, h, l, c]
    """
    df = ticks[(ticks["time"] >= start) & (ticks["time"] <= end)].copy()
    if df.empty:
        return pd.DataFrame(columns=["time", "o", "h", "l", "c"])

    today = pd.Timestamp.today().date()
    df["ts"] = pd.to_datetime(today.strftime("%Y-%m-%d") + " " + df["time"])
    df = df.set_index("ts").sort_index()
    ohlc = df["price"].resample(freq).ohlc().dropna()
    ohlc.columns = ["o", "h", "l", "c"]
    ohlc["time"] = ohlc.index.strftime("%H:%M:%S")
    return ohlc.reset_index(drop=True)


# ── ATM Strike ────────────────────────────────────────────────────────────────
def get_atm(spot: float, strike_interval: int = 50) -> int:
    return int(round(spot / strike_interval) * strike_interval)

def get_otm1(spot: float, opt: str, strike_interval: int = 50) -> int:
    atm = get_atm(spot, strike_interval)
    return atm + strike_interval if opt == "CE" else atm - strike_interval


# ── Score7 + Lot sizing ────────────────────────────────────────────────────────
def compute_score7(features: dict) -> int:
    """
    Compute 7-feature conviction score.
    features dict keys (all bool/int 0 or 1):
      vix_ok, cpr_trend_aligned, consec_aligned, cpr_gap_aligned,
      dte_sweet, cpr_narrow, cpr_dir_aligned

    Exactly matches script 72.
    """
    keys = ["vix_ok", "cpr_trend_aligned", "consec_aligned",
            "cpr_gap_aligned", "dte_sweet", "cpr_narrow", "cpr_dir_aligned"]
    return sum(int(features.get(k, 0)) for k in keys)


def score_to_lots(score: int, inside_cpr: bool = False) -> int:
    """
    Convert score7 to lot count.
    score 0-1 → 1 lot, 2-3 → 2 lots, 4-5,7 → 3 lots
    score 6   → 0 = SKIP (structural negative over 5yr backtest)
    inside_cpr reduces by 1 (min 1, but 0 stays 0).
    """
    base_lots = SCORE_LOT_MAP.get(min(score, 7), 1)
    if base_lots == 0:
        return 0   # score==6: do not trade
    if inside_cpr:
        base_lots = max(1, base_lots - 1)
    return base_lots


# ── Score7 feature computation from daily data ─────────────────────────────────
def build_score7_features(ohlc_history: pd.DataFrame,
                           today_open: float,
                           direction: str,
                           vix_today: float = None,
                           vix_ma20:  float = None,
                           dte:       int   = None) -> dict:
    """
    Build all 7 score features for today's trade.

    ohlc_history: DataFrame with [date, open, high, low, close, tc, bc, cpr_mid]
                  sorted ascending, covering at least today
                  (tc/bc are TOMORROW's CPR based on today's H/L/C — computed externally)
    today_open: today's first tick price (09:15 open)
    direction:  'CE' or 'PE'
    vix_today:  India VIX value today
    vix_ma20:   India VIX 20-day MA
    dte:        days to expiry

    Returns dict of feature values (0/1 ints) + raw values for debugging.
    """
    h = ohlc_history

    # Needs at least 5 rows of history
    if len(h) < 5:
        return {}

    # Previous day row (index -1 relative to today = last row if today not added yet)
    prev   = h.iloc[-1]   # yesterday
    prev2  = h.iloc[-2]   # day before yesterday
    prev3  = h.iloc[-3]

    prev_tc   = prev.get("tc",  None)
    prev_bc   = prev.get("bc",  None)
    prev2_mid = prev2.get("cpr_mid", None)
    prev3_mid = prev3.get("cpr_mid", None)

    # 1. vix_ok: today's VIX < 20-day MA
    vix_ok = int(vix_today < vix_ma20) if (vix_today and vix_ma20) else 0

    # 2. cpr_trend_aligned: prev close on right side of CPR for trade direction
    prev_close = prev["close"]
    if prev_tc is not None and prev_bc is not None:
        cpr_trend_aligned = int(
            (direction == "CE" and prev_close < prev_bc) or
            (direction == "PE" and prev_close > prev_tc)
        )
    else:
        cpr_trend_aligned = 0

    # 3. consec_aligned: 2 consecutive days close above/below CPR midpoint
    if prev_tc and prev_bc and "tc" in h.columns:
        prev_mid  = (prev["tc"]  + prev["bc"])  / 2
        prev2_mid_val = (prev2["tc"] + prev2["bc"]) / 2 if "tc" in h.columns else None
        if prev2_mid_val is not None:
            d0 = int(prev_close  > prev["tc"])
            d1 = int(prev2["close"] > prev2["tc"])
            consec_aligned = int(d0 == d1)
        else:
            consec_aligned = 0
    else:
        consec_aligned = 0

    # 4. cpr_gap_aligned: today opened outside CPR in direction of trade
    if prev_tc and prev_bc:
        open_above = int(today_open > prev_tc)
        open_below = int(today_open < prev_bc)
        cpr_gap    = int(
            (prev_tc < prev_bc) or  # CPR gap up: yesterday's TC < today's BC
            (prev_bc > prev_tc)     # CPR gap down
        )
        # Simplified: gap = today opened well outside yesterday's CPR
        cpr_gap_aligned = int(
            (direction == "CE" and open_below) or
            (direction == "PE" and open_above)
        )
    else:
        cpr_gap_aligned = 0

    # 5. dte_sweet: DTE between 3 and 5 days
    dte_sweet = int(3 <= dte <= 5) if dte is not None else 0

    # 6. cpr_narrow: CPR width between 0.10% and 0.20% of spot
    cpr_width_pct = abs(prev_tc - prev_bc) / prev_tc * 100 if prev_tc else 0
    cpr_narrow = int(0.10 <= cpr_width_pct <= 0.20)

    # 7. cpr_dir_aligned: CPR midpoint trending in trade direction for 3 days
    if "cpr_mid" in h.columns and len(h) >= 4:
        m1 = h.iloc[-1].get("cpr_mid")
        m2 = h.iloc[-2].get("cpr_mid")
        m3 = h.iloc[-3].get("cpr_mid")
        asc_cpr  = int(m1 > m2 > m3) if all(x is not None for x in [m1, m2, m3]) else 0
        desc_cpr = int(m1 < m2 < m3) if all(x is not None for x in [m1, m2, m3]) else 0
        cpr_dir_aligned = int(
            (direction == "PE" and asc_cpr) or
            (direction == "CE" and desc_cpr)
        )
    else:
        cpr_dir_aligned = 0

    # inside_cpr: today's CPR inside yesterday's CPR (negative filter)
    # Today's CPR = based on YESTERDAY's H/L/C → computed by caller
    # This check needs today's tc/bc, passed separately
    # Returned as 0 here; caller should override after computing today's CPR

    return {
        "vix_ok":            vix_ok,
        "cpr_trend_aligned": cpr_trend_aligned,
        "consec_aligned":    consec_aligned,
        "cpr_gap_aligned":   cpr_gap_aligned,
        "dte_sweet":         dte_sweet,
        "cpr_narrow":        cpr_narrow,
        "cpr_dir_aligned":   cpr_dir_aligned,
        # debug
        "_prev_tc":          round(prev_tc, 2) if prev_tc else None,
        "_prev_bc":          round(prev_bc, 2) if prev_bc else None,
        "_cpr_width_pct":    round(cpr_width_pct, 4),
        "_dte":              dte,
    }
