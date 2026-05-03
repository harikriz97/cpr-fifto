"""
signal_crt.py — CRT (Candle Range Theory) Signal Detection
===========================================================
Strategy (from backtest script 91, approach D — best version):
  1. Build 15M candles from NIFTY spot ticks
  2. Scan for 3-candle bearish CRT pattern at TC or R1:
       C2 high  > TC (or R1)
       C3 close < TC (or R1)         ← resistance rejected, swept back below
  3. After C3 closes, scan 5M Heiken Ashi for LTF bearish Turtle Soup:
       HA candle: high > open (wick up) AND close < open (red body)
       Window: C3 close time + 30 minutes
  4. Entry: next 5M candle after LTF confirmation + 2s
  5. Opt: CE sell (ATM, not OTM1 — as in backtest script 103)
  6. FILTER (from script 126):
       - Remove if IB already expanded UP before entry_time (real-time check)
       - Remove if fut_basis_pts < -50 or > 100

Filter for blank days only: fires only if no base strategy signal on this date.
"""

import logging
from indicators import build_ohlc_from_ticks, compute_ha, get_atm

logger = logging.getLogger(__name__)

# Window: search LTF confirmation within 30 min of C3 close
LTF_WINDOW_MINS = 30
MAX_C3_TIME     = "12:00:00"     # C3 must close by 12:00
MIN_ENTRY_TIME  = "09:30:00"     # earliest possible entry


def _mins_to_time(total_minutes: int) -> str:
    h = total_minutes // 60
    m = total_minutes % 60
    return f"{h:02d}:{m:02d}:00"

def _time_to_mins(t: str) -> int:
    parts = t.split(":")
    return int(parts[0]) * 60 + int(parts[1])

def _next_candle_entry(candle_time: str, candle_mins: int = 5) -> str:
    """Next candle open + 2s after given candle close time."""
    mins = _time_to_mins(candle_time) + candle_mins
    h, m = divmod(mins, 60)
    return f"{h:02d}:{m:02d}:02"


class CRTScanner:
    """
    Stateful scanner: call update() with latest spot ticks each time
    new 15M or 5M candle completes.

    State machine:
      IDLE          → watching for 15M CRT pattern at TC/R1
      CRT_FOUND     → 15M pattern confirmed, now watching for 5M LTF
      SIGNAL_READY  → both confirmed, entry_time is set
      DONE          → signal fired for today (one per day)
    """

    IDLE         = "IDLE"
    CRT_FOUND    = "CRT_FOUND"
    SIGNAL_READY = "SIGNAL_READY"
    DONE         = "DONE"

    def __init__(self, tc: float, r1: float, ib_high: float = None,
                 fut_basis_pts: float = 0.0):
        self.tc            = tc
        self.r1            = r1
        self.ib_high       = ib_high
        self.fut_basis_pts = fut_basis_pts
        self.state         = self.IDLE
        self.signal        = None    # populated when SIGNAL_READY

        # basis filter: remove if extreme
        self._basis_ok = not (fut_basis_pts < -50 or fut_basis_pts > 100)
        if not self._basis_ok:
            logger.info(f"CRT: basis {fut_basis_pts} outside range → scanner disabled")

        # CRT pattern tracking
        self._crt_c3_close_time  = None   # time C3 candle closed (HH:MM:SS)
        self._crt_level          = None   # 'TC' or 'R1'
        self._ltf_window_end     = None   # time string: c3_close + 30 min

    def update(self, spot_ticks: list) -> dict | None:
        """
        Call periodically with accumulated spot ticks for today.
        spot_ticks: list of (time_str, price_float) tuples or
                    DataFrame with ['time','price'] — both accepted.

        Returns signal dict when ready, else None.
        Signal dict:
          entry_time, strike (ATM), opt='CE', level='TC'/'R1',
          ib_already_up (bool)
        """
        if self.state in (self.SIGNAL_READY, self.DONE):
            return self.signal if self.state == self.SIGNAL_READY else None
        if not self._basis_ok:
            return None

        import pandas as pd
        if isinstance(spot_ticks, list):
            ticks = pd.DataFrame(spot_ticks, columns=["time", "price"])
        else:
            ticks = spot_ticks.copy()

        # Build 15M candles
        c15 = build_ohlc_from_ticks(ticks, freq="15min",
                                     start="09:15:00", end="15:00:00")
        if len(c15) < 3:
            return None

        # Build 5M HA candles
        c5_raw = build_ohlc_from_ticks(ticks, freq="5min",
                                        start="09:15:00", end="13:00:00")
        c5_ha  = compute_ha(c5_raw) if len(c5_raw) >= 3 else pd.DataFrame()

        # ── IDLE: scan 15M for CRT pattern ────────────────────────────────────
        if self.state == self.IDLE:
            for ci in range(1, len(c15) - 1):
                c1_row = c15.iloc[ci - 1]
                c2_row = c15.iloc[ci]
                c3_row = c15.iloc[ci + 1]

                c3_time = c3_row["time"]
                if c3_time > MAX_C3_TIME:
                    break

                c2h = c2_row["h"]
                c3c = c3_row["c"]

                # CRT at TC
                crt_tc = (c2h > self.tc and c3c < self.tc)
                # CRT at R1
                crt_r1 = (c2h > self.r1 and c3c < self.r1)

                if crt_tc or crt_r1:
                    self._crt_c3_close_time = c3_time
                    self._crt_level = "TC" if crt_tc else "R1"
                    c3_close_mins = _time_to_mins(c3_time) + 15
                    ltf_end_mins  = c3_close_mins + LTF_WINDOW_MINS
                    self._ltf_window_start = _mins_to_time(c3_close_mins)
                    self._ltf_window_end   = _mins_to_time(ltf_end_mins)
                    self._c3_spot_price    = c3c
                    self.state = self.CRT_FOUND
                    logger.info(f"CRT pattern at {self._crt_level}, C3 close {c3_time}")
                    break   # one pattern per day

        # ── CRT_FOUND: scan 5M HA for LTF Turtle Soup confirmation ────────────
        if self.state == self.CRT_FOUND and not c5_ha.empty:
            win = c5_ha[
                (c5_ha["time"] > self._ltf_window_start) &
                (c5_ha["time"] <= self._ltf_window_end)
            ].reset_index(drop=True)

            for _, row in win.iterrows():
                # Bearish Turtle Soup: wick up + close down
                if row["ha_h"] > row["ha_o"] and row["ha_c"] < row["ha_o"]:
                    entry_time = _next_candle_entry(row["time"], candle_mins=5)

                    # Real-time IB expansion check
                    ib_already_up = False
                    if self.ib_high is not None:
                        from indicators import is_ib_expanded_up
                        ib_already_up = is_ib_expanded_up(
                            ticks, self.ib_high, entry_time
                        )
                        if ib_already_up:
                            logger.info(
                                f"CRT: IB already expanded UP before {entry_time} → skip"
                            )
                            self.state = self.DONE
                            return None

                    spot_at_entry = self._c3_spot_price
                    strike = get_atm(spot_at_entry)

                    self.signal = {
                        "strategy":     "CRT",
                        "signal":       "CE",
                        "opt":          "CE",
                        "entry_time":   entry_time,
                        "strike":       strike,
                        "level":        self._crt_level,
                        "ib_already_up": ib_already_up,
                        "lots":         1,     # CRT CE = 1 lot
                    }
                    self.state = self.SIGNAL_READY
                    logger.info(
                        f"CRT signal: CE {strike} @ {entry_time} "
                        f"(level={self._crt_level})"
                    )
                    return self.signal

        return None

    def mark_done(self):
        """Call after trade entry to prevent re-firing."""
        self.state = self.DONE
