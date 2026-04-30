"""
dashboard/app.py — Real-time Trading Dashboard (Flask)
=======================================================
Reads data/live_trades.csv every 30 seconds.
Shows: today's trade, open P&L, overall stats, equity curve.

Run:
    python dashboard/app.py
Then open: http://localhost:8080
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import threading
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pandas as pd
from flask import Flask, render_template, jsonify

from config import LIVE_TRADES_CSV
from live.orders import get_spot_ltp

app   = Flask(__name__)
IST   = ZoneInfo("Asia/Kolkata")
CACHE = {}           # shared cache updated every 30s
LOCK  = threading.Lock()


# ── Data loader ───────────────────────────────────────────────────────────────
def load_data() -> dict:
    """Load and compute all dashboard data from live_trades.csv."""
    if not os.path.exists(LIVE_TRADES_CSV):
        return {"error": "No trades file found. Start trading first."}

    df = pd.read_csv(LIVE_TRADES_CSV)
    if df.empty:
        return {"error": "No trades recorded yet."}

    df.columns = [c.lower() for c in df.columns]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="mixed")
    df["win"]  = df["win"].astype(int)
    df = df.sort_values("date")

    today_str = date.today().strftime("%Y%m%d")
    today_df  = df[df["date"].dt.strftime("%Y%m%d") == today_str]

    # ── Overall stats ─────────────────────────────────────────────────────
    total_pnl    = round(df["pnl"].sum(), 2)
    win_rate     = round(df["win"].mean() * 100, 1)
    total_trades = len(df)
    eq           = df["pnl"].cumsum()
    dd           = eq - eq.cummax()
    max_dd       = round(dd.min(), 2)

    # ── Today's stats ──────────────────────────────────────────────────────
    today_pnl    = round(today_df["pnl"].sum(), 2) if not today_df.empty else 0
    today_trades = len(today_df)

    # ── Strategy breakdown ─────────────────────────────────────────────────
    strat = df.groupby("strategy").agg(
        trades=("pnl", "count"),
        wr=("win", lambda x: round(x.mean() * 100, 1)),
        pnl=("pnl", "sum"),
    ).reset_index().to_dict(orient="records")

    # ── Year-wise ──────────────────────────────────────────────────────────
    df["year"] = df["date"].dt.year.astype(str)
    yearly = df.groupby("year").agg(
        trades=("pnl", "count"),
        wr=("win", lambda x: round(x.mean() * 100, 1)),
        pnl=("pnl", "sum"),
    ).reset_index().to_dict(orient="records")

    # ── Equity series (for chart) ──────────────────────────────────────────
    eq_series = [
        {"date": str(d.date()), "value": round(float(v), 2)}
        for d, v in zip(df["date"], eq)
    ]

    # ── Recent trades ──────────────────────────────────────────────────────
    recent = df.tail(10)[["date", "strategy", "zone", "opt", "lots",
                          "entry_price", "exit_price", "exit_reason", "pnl", "win"]].copy()
    recent["date"] = recent["date"].dt.strftime("%Y-%m-%d")
    recent["pnl"]  = recent["pnl"].round(2)
    recent_trades  = recent.to_dict(orient="records")

    # ── Today's trades ─────────────────────────────────────────────────────
    if not today_df.empty:
        td = today_df[["strategy", "index", "opt", "strike", "lots",
                        "entry_time", "entry_price", "exit_price",
                        "exit_reason", "pnl", "win"]].copy()
        td["pnl"] = td["pnl"].round(2)
        today_trades_detail = td.to_dict(orient="records")
    else:
        today_trades_detail = []

    return dict(
        total_pnl        = total_pnl,
        win_rate         = win_rate,
        total_trades     = total_trades,
        max_dd           = max_dd,
        today_pnl        = today_pnl,
        today_trade_count= today_trades,
        strategy_breakdown = strat,
        yearly_breakdown   = yearly,
        equity_series      = eq_series,
        recent_trades      = recent_trades,
        today_trades       = today_trades_detail,
        last_updated       = datetime.now(IST).strftime("%H:%M:%S"),
    )


def refresh_cache():
    """Background thread: refresh cache every 30s."""
    while True:
        data = load_data()
        with LOCK:
            CACHE.clear()
            CACHE.update(data)
        threading.Event().wait(30)


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/data")
def api_data():
    with LOCK:
        data = dict(CACHE)
    if not data:
        data = load_data()
    return jsonify(data)


@app.route("/api/spot")
def api_spot():
    spot = get_spot_ltp("NIFTY")
    return jsonify({"nifty": spot, "time": datetime.now(IST).strftime("%H:%M:%S")})


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Initial load
    with LOCK:
        CACHE.update(load_data())

    # Start background refresh
    t = threading.Thread(target=refresh_cache, daemon=True)
    t.start()

    print("Dashboard running at http://localhost:8080")
    app.run(host="0.0.0.0", port=8080, debug=False)
