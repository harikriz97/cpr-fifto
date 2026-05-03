"""
trade_manager.py — Trade state machine (entry / trail SL / exit)
=================================================================
Implements EXACT same SL trail logic as backtest scripts 91/100/103/127:
  - Target: 30% of entry price
  - Trail SL:
      max_decline ≥ 25% → move SL to breakeven (entry price)
      max_decline ≥ 40% → move SL to 80% of entry (lock 20% gain)
      max_decline ≥ 60% → trail SL at 95% of max decline (lock most)
  - Hard SL: 100% of entry price (price doubles → exit)
  - EOD exit: 15:20:00

Paper trade: logs to CSV, prints to console, no real orders.
"""

import logging
import csv
import os
from datetime import datetime
from config import TGT_PCT, EOD_EXIT_TIME, LOT_SIZE, LOG_CSV, PAPER_TRADE

logger = logging.getLogger(__name__)


def r2(v): return round(float(v), 2)


class TradeManager:
    """
    Manages one active option sell position.
    Call on_tick(time_str, ltp) for each option price tick.

    Usage:
        tm = TradeManager(signal, ep)
        result = tm.on_tick(time, price)
        if result:
            # trade exited
    """

    IDLE    = "IDLE"
    ACTIVE  = "ACTIVE"
    EXITED  = "EXITED"

    def __init__(self, signal: dict, entry_price: float):
        """
        signal: dict from CRTScanner/MRCScanner/BaseScanner
          keys: strategy, signal, opt, entry_time, strike, lots
        entry_price: actual option LTP at entry
        """
        self.signal       = signal
        self.lots         = signal.get("lots", 1)
        self.ep           = r2(entry_price)
        self.tgt          = r2(self.ep * (1 - TGT_PCT))
        self.hsl          = r2(self.ep * 2.0)       # 100% above entry = unreachable hard cap
        self.sl           = self.hsl                  # starts at hard SL
        self.max_decline  = 0.0                       # max (ep - price) / ep so far
        self.state        = self.ACTIVE
        self.exit_price   = None
        self.exit_reason  = None
        self.exit_time    = None
        self.pnl          = None

        logger.info(
            f"ENTRY: {signal['strategy']} {signal['opt']} "
            f"strike={signal['strike']} ep={self.ep} "
            f"tgt={self.tgt} hsl={self.hsl} lots={self.lots}"
        )
        print(
            f"\n[TRADE ENTRY] {signal['strategy']} {signal['opt']} "
            f"strike={signal['strike']} | ep={self.ep} | "
            f"tgt={self.tgt} | lots={self.lots}"
        )

    def on_tick(self, time_str: str, ltp: float) -> dict | None:
        """
        Process a single option LTP tick.
        Returns exit dict if trade closed, else None.

        Exit dict: entry_price, exit_price, pnl, exit_reason, exit_time, lots
        """
        if self.state != self.ACTIVE:
            return None

        p = r2(ltp)

        # EOD exit
        if time_str >= EOD_EXIT_TIME:
            return self._exit(p, time_str, "eod")

        # Update max decline
        d = (self.ep - p) / self.ep
        if d > self.max_decline:
            self.max_decline = d

        # Trail SL logic (exactly as backtest)
        md = self.max_decline
        if   md >= 0.60:
            self.sl = min(self.sl, r2(self.ep * (1 - md * 0.95)))
        elif md >= 0.40:
            self.sl = min(self.sl, r2(self.ep * 0.80))
        elif md >= 0.25:
            self.sl = min(self.sl, self.ep)

        # Target hit
        if p <= self.tgt:
            return self._exit(p, time_str, "target")

        # SL hit
        if p >= self.sl:
            reason = "lockin_sl" if self.sl < self.hsl else "hard_sl"
            return self._exit(p, time_str, reason)

        return None

    def _exit(self, exit_price: float, exit_time: str, reason: str) -> dict:
        self.exit_price  = r2(exit_price)
        self.exit_reason = reason
        self.exit_time   = exit_time
        self.pnl         = r2((self.ep - self.exit_price) * LOT_SIZE * self.lots)
        self.state       = self.EXITED

        win_str = "WIN" if self.pnl > 0 else "LOSS"
        print(
            f"\n[TRADE EXIT] {self.signal['strategy']} {self.signal['opt']} "
            f"@ {exit_time} | reason={reason} | "
            f"ep={self.ep} xp={self.exit_price} | "
            f"P&L=Rs.{self.pnl:,.0f} [{win_str}]"
        )
        logger.info(
            f"EXIT: {reason} xp={self.exit_price} pnl={self.pnl} @ {exit_time}"
        )

        result = {
            "date":         datetime.today().strftime("%Y%m%d"),
            "strategy":     self.signal.get("strategy"),
            "signal":       self.signal.get("signal"),
            "opt":          self.signal.get("opt"),
            "entry_time":   self.signal.get("entry_time"),
            "exit_time":    exit_time,
            "strike":       self.signal.get("strike"),
            "ep":           self.ep,
            "xp":           self.exit_price,
            "lots":         self.lots,
            "pnl":          self.pnl,
            "win":          self.pnl > 0,
            "exit_reason":  reason,
        }

        if PAPER_TRADE:
            _log_paper_trade(result)

        return result


# ── Paper trade logger ─────────────────────────────────────────────────────────
_LOG_HEADER = [
    "date", "strategy", "signal", "opt",
    "entry_time", "exit_time", "strike",
    "ep", "xp", "lots", "pnl", "win", "exit_reason",
]

def _log_paper_trade(trade: dict):
    """Append trade to paper_trades.csv. Creates file with header if needed."""
    file_exists = os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_LOG_HEADER)
        if not file_exists:
            writer.writeheader()
        writer.writerow({k: trade.get(k, "") for k in _LOG_HEADER})
    logger.info(f"Paper trade logged: {trade['date']} {trade['strategy']} "
                f"P&L={trade['pnl']}")


def print_paper_summary():
    """Print summary of all paper trades logged today."""
    import pandas as pd
    if not os.path.exists(LOG_CSV):
        print("No paper trades yet.")
        return
    df = pd.read_csv(LOG_CSV)
    today = datetime.today().strftime("%Y%m%d")
    today_df = df[df["date"].astype(str) == today]
    if today_df.empty:
        print(f"No paper trades for {today}")
        return
    print(f"\n{'='*55}")
    print(f"PAPER TRADE SUMMARY — {today}")
    print(f"{'='*55}")
    for _, r in today_df.iterrows():
        win_s = "WIN " if r["win"] else "LOSS"
        print(f"  {r['strategy']:<8} {r['opt']} | "
              f"ep={r['ep']} xp={r['xp']} | lots={r['lots']} | "
              f"P&L Rs.{r['pnl']:>8,.0f} [{win_s}] | {r['exit_reason']}")
    total = today_df["pnl"].sum()
    wr    = today_df["win"].mean() * 100
    print(f"{'─'*55}")
    print(f"  Total: Rs.{total:,.0f} | WR: {wr:.0f}% | Trades: {len(today_df)}")
    print(f"{'='*55}\n")
