"""
signal_base.py — THOR / HULK / IRON MAN / CAPTAIN scanner
==========================================================
THOR     — V17A pivot zone + EMA bias (fixed-time entry)
HULK     — CAM L3 down-touch -> CE sell
IRON MAN — CAM H3 up-touch   -> PE sell
CAPTAIN  — IV2 PDL/R1/R2 break -> PE/CE sell

Quality cuts (backtest 127):
  - score == 6 -> skip
  - cam_h3 + tc_to_pdh zone -> direction mismatch, skip
  - PE sells with fut_basis_pts in [50, 100] -> skip
  - inside_cpr -> reduce lots by 1 (min 1)
"""
from __future__ import annotations
import logging
import pandas as pd
from indicators import get_atm, score_to_lots, build_score7_features

logger = logging.getLogger(__name__)

V17A_PARAMS = {
    ("above_r4",   "bull", "PE"): ("ITM1", "09:31:02", 0.50, 2.00),
    ("below_s4",   "bear", "CE"): ("ITM1", "09:16:02", 0.20, 0.50),
    ("pdh_to_r1",  "bear", "PE"): ("OTM1", "09:20:02", 0.50, 0.50),
    ("pdl_to_bc",  "bull", "PE"): ("OTM1", "09:31:02", 0.20, 1.50),
    ("pdl_to_s1",  "bear", "CE"): ("ITM1", "09:20:02", 0.20, 2.00),
    ("r1_to_r2",   "bear", "PE"): ("ATM",  "09:20:02", 0.50, 2.00),
    ("r1_to_r2",   "bull", "PE"): ("OTM1", "09:16:02", 0.50, 1.00),
    ("r2_to_r3",   "bull", "PE"): ("ATM",  "09:20:02", 0.20, 1.50),
    ("r2_to_r3",   "bear", "PE"): ("ATM",  "09:20:02", 0.20, 1.50),
    ("r3_to_r4",   "bull", "PE"): ("ITM1", "09:25:02", 0.20, 0.50),
    ("s1_to_s2",   "bear", "CE"): ("ATM",  "09:16:02", 0.50, 2.00),
    ("s3_to_s4",   "bear", "CE"): ("ITM1", "09:20:02", 0.40, 0.50),
    ("tc_to_pdh",  "bear", "PE"): ("OTM1", "09:31:02", 0.50, 0.50),
    ("tc_to_pdh",  "bull", "PE"): ("ITM1", "09:25:02", 0.20, 0.50),
    ("within_cpr", "bear", "CE"): ("ATM",  "09:16:02", 0.20, 2.00),
    ("within_cpr", "bull", "PE"): ("ATM",  "09:20:02", 0.30, 2.00),
}

CAM_L3_PARAMS = dict(opt="CE", stype="ITM1", tgt=0.20, sl=0.50)
CAM_H3_PARAMS = dict(opt="PE", stype="OTM1", tgt=0.50, sl=1.00)
IV2_PARAMS = {
    "PDL": dict(direction="down", opt="CE", stype="ATM", tgt=0.20, sl=0.50),
    "R1":  dict(direction="up",   opt="PE", stype="ATM", tgt=0.20, sl=0.50),
    "R2":  dict(direction="up",   opt="PE", stype="ATM", tgt=0.20, sl=0.50),
}
TOUCH_TOL_PCT = 0.0005
IRONMAN_SKIP  = ("09:20:00", "09:30:00")
IV2_START     = "09:16:00"
IV2_END       = "11:20:00"


def classify_zone(today_open: float, pvt: dict, pdh: float, pdl: float) -> str:
    """
    Zone classification. Works with compute_cpr() which returns up to r3/s3.
    r4/s4 are computed on-the-fly if missing (r4 = r2 + (r2-r1), etc.).
    Also handles 'pivot' key as alias for 'pvt'.
    """
    op = today_open
    # Compute r4/s4 if not present
    r4 = pvt.get("r4") or pvt.get("r3", 0) + (pvt.get("r2", 0) - pvt.get("r1", 0))
    s4 = pvt.get("s4") or pvt.get("s3", 0) - (pvt.get("s1", 0) - pvt.get("s2", 0))
    if   op > r4:            return "above_r4"
    elif op > pvt["r3"]:     return "r3_to_r4"
    elif op > pvt["r2"]:     return "r2_to_r3"
    elif op > pvt["r1"]:     return "r1_to_r2"
    elif op > pdh:            return "pdh_to_r1"
    elif op > pvt["tc"]:     return "tc_to_pdh"
    elif op >= pvt["bc"]:    return "within_cpr"
    elif op > pdl:            return "pdl_to_bc"
    elif op > pvt["s1"]:     return "pdl_to_s1"
    elif op > pvt["s2"]:     return "s1_to_s2"
    elif op > pvt["s3"]:     return "s2_to_s3"
    elif op > s4:             return "s3_to_s4"
    else:                     return "below_s4"


def _get_strike(spot: float, opt: str, stype: str, si: int = 50) -> int:
    atm = get_atm(spot, si)
    if opt == "CE":
        return {"OTM1": atm+si, "ATM": atm, "ITM1": atm-si}[stype]
    return {"OTM1": atm-si, "ATM": atm, "ITM1": atm+si}[stype]


def _to_df(ticks) -> pd.DataFrame:
    if isinstance(ticks, pd.DataFrame):
        return ticks.copy()
    return pd.DataFrame(ticks, columns=["time", "price"])


def _detect_touch(df: pd.DataFrame, level: float, direction: str,
                  t0: str = "09:16:00", t1: str = "15:15:00"):
    tol  = level * TOUCH_TOL_PCT
    rows = df[(df["time"] >= t0) & (df["time"] <= t1)]
    if len(rows) < 2:
        return None, 0.0
    prices, times = rows["price"].values, rows["time"].values
    for i in range(1, len(prices)):
        p, c = prices[i-1], prices[i]
        if direction == "down" and p > level and c <= level + tol:
            return times[i], c
        if direction == "up"   and p < level and c >= level - tol:
            return times[i], c
    return None, 0.0


def _entry_after(touch_time: str, secs: int = 2) -> str:
    h, m, s = map(int, touch_time.split(":"))
    tot = h*3600 + m*60 + s + secs
    hh, rem = divmod(tot, 3600)
    mm, ss  = divmod(rem, 60)
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


class BaseScanner:
    """THOR / HULK / IRON MAN / CAPTAIN stateful scanner."""

    def __init__(self, levels: dict, ema20: float, today_open: float,
                 fut_basis_pts: float,
                 prev_close: float = 0.0, prev_open: float = 0.0,
                 vix_today: float = 0.0, vix_ma20: float = 15.0,
                 dte: int = 4, ohlc_history: pd.DataFrame = None):
        self.levels        = levels
        self.ema20         = ema20
        self.today_open    = today_open
        self.fut_basis_pts = fut_basis_pts
        self.prev_close    = prev_close
        self.prev_open     = prev_open
        self.vix_today     = vix_today
        self.vix_ma20      = vix_ma20
        self.dte           = dte
        self.ohlc_history  = ohlc_history if ohlc_history is not None else pd.DataFrame()
        self._done         = False
        self._hulk_done    = False
        self._ironman_done = False
        self._iv2_done     = {k: False for k in IV2_PARAMS}
        self._captain_done = False
        self._thor_signal  = None
        self._thor_sched   = None
        self._zone         = None
        self._bias         = None
        self._prev_body    = (abs(prev_close - prev_open) / prev_open * 100
                              if prev_open > 0 else 1.0)
        self._compute_thor()
        logger.info("BaseScanner init: zone=%s bias=%s open=%.2f body=%.2f%%",
                    self._zone, self._bias, today_open, self._prev_body)

    def set_ib(self, ib_high: float, ib_low: float):
        self.levels["ib_high"] = ib_high
        self.levels["ib_low"]  = ib_low

    def is_done(self) -> bool:
        return self._done

    def get_lots(self, direction: str) -> int:
        f = self._score7(direction)
        return score_to_lots(f.get("score", 0), f.get("inside_cpr", False))

    def update(self, spot_ticks) -> dict | None:
        if self._done:
            return None
        df  = _to_df(spot_ticks)
        if df.empty:
            return None
        now = df["time"].iloc[-1]

        # THOR: scheduled entry
        if self._thor_signal and now >= self._thor_sched:
            sig = dict(self._thor_signal)
            sig["lots"]  = self.get_lots(sig["opt"])
            sig["score"] = self._score7(sig["opt"]).get("score", 0)
            logger.info("THOR: %s %s entry=%s lots=%d",
                        sig["zone"], sig["opt"], sig["entry_time"], sig["lots"])
            self._done = True
            return sig

        # HULK: CAM L3 down
        if not self._hulk_done:
            t, p = _detect_touch(df, self.levels["cam_l3"], "down")
            if t:
                self._hulk_done = True
                et = _entry_after(t)
                if now >= et:
                    opt, st = CAM_L3_PARAMS["opt"], CAM_L3_PARAMS["stype"]
                    sig = dict(strategy="cam_l3", signal="HULK", opt=opt,
                               entry_time=et, strike=_get_strike(p, opt, st),
                               lots=self.get_lots(opt),
                               score=self._score7(opt).get("score", 0),
                               tgt_pct=CAM_L3_PARAMS["tgt"],
                               sl_pct=CAM_L3_PARAMS["sl"], zone=self._zone or "")
                    logger.info("HULK: L3=%.2f @ %s lots=%d",
                                self.levels["cam_l3"], t, sig["lots"])
                    self._done = True
                    return sig

        # IRON MAN: CAM H3 up (skip if tc_to_pdh zone)
        if not self._ironman_done and self._zone != "tc_to_pdh":
            t, p = _detect_touch(df, self.levels["cam_h3"], "up")
            if t:
                self._ironman_done = True
                if IRONMAN_SKIP[0] <= t <= IRONMAN_SKIP[1]:
                    logger.info("IRON MAN: skip window %s", t)
                elif 50 <= self.fut_basis_pts <= 100:
                    logger.info("IRON MAN: PE basis skip %.1f", self.fut_basis_pts)
                else:
                    et = _entry_after(t)
                    if now >= et:
                        opt, st = CAM_H3_PARAMS["opt"], CAM_H3_PARAMS["stype"]
                        sig = dict(strategy="cam_h3", signal="IRON MAN", opt=opt,
                                   entry_time=et, strike=_get_strike(p, opt, st),
                                   lots=self.get_lots(opt),
                                   score=self._score7(opt).get("score", 0),
                                   tgt_pct=CAM_H3_PARAMS["tgt"],
                                   sl_pct=CAM_H3_PARAMS["sl"], zone=self._zone or "")
                        logger.info("IRON MAN: H3=%.2f @ %s lots=%d",
                                    self.levels["cam_h3"], t, sig["lots"])
                        self._done = True
                        return sig

        # CAPTAIN: IV2 breaks
        if not self._captain_done:
            lmap = {"PDL": self.levels.get("pdl", 0),
                    "R1":  self.levels.get("r1",  0),
                    "R2":  self.levels.get("r2",  0)}
            for lvl, params in IV2_PARAMS.items():
                if self._iv2_done.get(lvl) or not lmap[lvl]:
                    continue
                if not (IV2_START <= now <= IV2_END):
                    continue
                t, p = _detect_touch(df, lmap[lvl], params["direction"],
                                     IV2_START, IV2_END)
                if not t:
                    continue
                self._iv2_done[lvl] = True
                opt = params["opt"]
                if opt == "PE" and 50 <= self.fut_basis_pts <= 100:
                    logger.info("CAPTAIN %s: PE basis skip", lvl)
                    continue
                et = _entry_after(t)
                if now >= et:
                    sig = dict(strategy=f"iv2_{lvl.lower()}", signal="CAPTAIN",
                               opt=opt, entry_time=et,
                               strike=_get_strike(p, opt, params["stype"]),
                               lots=self.get_lots(opt),
                               score=self._score7(opt).get("score", 0),
                               tgt_pct=params["tgt"], sl_pct=params["sl"],
                               zone=self._zone or "")
                    logger.info("CAPTAIN: %s=%.2f @ %s %s lots=%d",
                                lvl, lmap[lvl], t, opt, sig["lots"])
                    self._done = True
                    self._captain_done = True
                    return sig

        return None

    def _compute_thor(self):
        if self._prev_body <= 0.10:
            logger.info("THOR: doji day (body=%.2f%%) skip", self._prev_body)
            return
        pvt        = self.levels
        self._zone = classify_zone(self.today_open, pvt,
                                   pvt.get("pdh", 0), pvt.get("pdl", 0))
        self._bias = "bull" if self.today_open > self.ema20 else "bear"
        for opt in ("PE", "CE"):
            key = (self._zone, self._bias, opt)
            if key not in V17A_PARAMS:
                continue
            stype, etime, tgt, sl = V17A_PARAMS[key]
            score = self._score7(opt).get("score", 0)
            if score == 6:
                logger.info("THOR: score==6 skip")
                continue
            if opt == "PE" and 50 <= self.fut_basis_pts <= 100:
                logger.info("THOR: PE basis skip %.1f", self.fut_basis_pts)
                continue
            strike = _get_strike(self.today_open, opt, stype)
            self._thor_signal = dict(strategy="v17a", signal="THOR", opt=opt,
                                     entry_time=etime, strike=strike,
                                     lots=1, score=score,
                                     tgt_pct=tgt, sl_pct=sl,
                                     zone=self._zone, bias=self._bias)
            self._thor_sched = etime
            logger.info("THOR: zone=%s %s %s entry=%s score=%d",
                        self._zone, self._bias, opt, etime, score)
            return
        logger.info("THOR: no rule for zone=%s bias=%s", self._zone, self._bias)

    def _score7(self, direction: str) -> dict:
        if self.ohlc_history.empty:
            return {"score": 0, "inside_cpr": False}
        try:
            return build_score7_features(
                ohlc_history=self.ohlc_history,
                today_open=self.today_open,
                direction=direction,
                vix_today=self.vix_today or None,
                vix_ma20=self.vix_ma20  or None,
                dte=self.dte,
            )
        except Exception as e:
            logger.warning("score7 failed: %s", e)
            return {"score": 0, "inside_cpr": False}
