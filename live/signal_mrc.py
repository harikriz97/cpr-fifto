"""
signal_mrc.py — MRC (Mean Reversion Concept) Signal Detection
==============================================================
Strategy (from backtest script 100):
  Levels: PDH=0%, PDL=100%
    l_382 = PDH - range × 0.382   → BUY zone (HA closes above → PE sell)
    l_618 = PDH - range × 0.618   → SELL zone (HA closes below → CE sell)

  Signal: 5M Heiken Ashi
    SELL (CE): HA close < l_618 AND HA close < HA open (red)
    BUY  (PE): HA close > l_382 AND HA close > HA open (green)

  Entry: next 5M candle open + 2s

  From script 127 decision:
    MRC PE → 2 lots  (WR 80.6%, approved)
    MRC CE → 0 lots  (net negative P&L, removed)

Filter for blank days only.
"""

import logging
from indicators import build_ohlc_from_ticks, compute_ha, get_atm
from config import MRC_PE_LOTS, MRC_CE_LOTS

logger = logging.getLogger(__name__)

MAX_SIGNAL_TIME = "12:00:00"


def _next_candle_entry(candle_time: str, candle_mins: int = 5) -> str:
    parts = candle_time.split(":")
    total = int(parts[0]) * 60 + int(parts[1]) + candle_mins
    h, m = divmod(total, 60)
    return f"{h:02d}:{m:02d}:02"


class MRCScanner:
    """
    Stateful MRC scanner. One signal per day.

    Call update() with accumulated spot ticks.
    Returns signal dict when a PE signal fires, else None.
    CE signals are silently dropped (MRC CE removed from system 126/127).
    """

    IDLE         = "IDLE"
    SIGNAL_READY = "SIGNAL_READY"
    DONE         = "DONE"

    def __init__(self, pdh: float, pdl: float):
        self.pdh   = pdh
        self.pdl   = pdl
        rng        = pdh - pdl

        if rng < 50:
            logger.info(f"MRC: range too small ({rng:.0f} < 50) → scanner disabled")
            self.state = self.DONE
            return

        self.l_382  = round(pdh - rng * 0.382, 2)
        self.l_50   = round(pdh - rng * 0.500, 2)
        self.l_618  = round(pdh - rng * 0.618, 2)
        self.state  = self.IDLE
        self.signal = None
        logger.info(
            f"MRC levels: PDH={pdh} PDL={pdl} "
            f"l_382={self.l_382} l_50={self.l_50} l_618={self.l_618}"
        )

    def update(self, spot_ticks) -> dict | None:
        """
        Returns PE signal dict when fired, else None.

        Signal dict:
          entry_time, strike (ATM), opt='PE', lots=MRC_PE_LOTS,
          level_hit, ha_close, l_382
        """
        if self.state in (self.SIGNAL_READY, self.DONE):
            return self.signal if self.state == self.SIGNAL_READY else None

        import pandas as pd
        if isinstance(spot_ticks, list):
            ticks = pd.DataFrame(spot_ticks, columns=["time", "price"])
        else:
            ticks = spot_ticks.copy()

        c5_raw = build_ohlc_from_ticks(ticks, freq="5min",
                                        start="09:15:00", end="12:00:00")
        if len(c5_raw) < 3:
            return None

        ha = compute_ha(c5_raw)

        for ci in range(len(ha) - 1):
            row = ha.iloc[ci]
            ct  = row["time"]
            if ct > MAX_SIGNAL_TIME:
                break

            entry_time = _next_candle_entry(ct)

            # CE signal (SELL zone): HA red + close < l_618 → SKIP (removed)
            if row["ha_c"] < self.l_618 and row["ha_c"] < row["ha_o"]:
                if MRC_CE_LOTS == 0:
                    logger.debug(f"MRC CE signal at {ct} → skipped (removed from system)")
                    self.state = self.DONE   # one signal per day, skip rest
                    return None
                # else: would fire CE, but per config = 0 lots so skip
                self.state = self.DONE
                return None

            # PE signal (BUY zone): HA green + close > l_382 → fire
            if row["ha_c"] > self.l_382 and row["ha_c"] > row["ha_o"]:
                if MRC_PE_LOTS == 0:
                    self.state = self.DONE
                    return None

                spot_ref = row["ha_c"]
                strike   = get_atm(spot_ref)

                self.signal = {
                    "strategy":   "MRC",
                    "signal":     "PE",
                    "opt":        "PE",
                    "entry_time": entry_time,
                    "strike":     strike,
                    "level_hit":  "l382_buy",
                    "ha_close":   round(row["ha_c"], 2),
                    "l_382":      self.l_382,
                    "lots":       MRC_PE_LOTS,   # 2 lots per script 127
                }
                self.state = self.SIGNAL_READY
                logger.info(
                    f"MRC PE signal: strike={strike} @ {entry_time} "
                    f"(HA close={round(row['ha_c'],2)} > l_382={self.l_382})"
                )
                return self.signal

        return None

    def mark_done(self):
        self.state = self.DONE
