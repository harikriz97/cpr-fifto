"""
report/generate.py — Full Trading Report Generator
====================================================
Reads:  data/live_trades.csv  (or any trades CSV with required columns)
Writes: data/reports/report_YYYYMMDD.pdf
        data/reports/report_YYYYMMDD.csv  (summary stats)

Charts included (matplotlib, embedded in PDF):
  1. Flat vs Conviction equity curve
  2. Conviction equity + drawdown
  3. Per-strategy equity curves
  4. Year-wise bar chart
  5. Score distribution bar chart
  6. Win/Loss distribution

Usage:
    python report/generate.py [--csv path/to/trades.csv] [--out output_dir]
"""
from __future__ import annotations
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.ticker import FuncFormatter
from datetime import datetime

from config import LIVE_TRADES_CSV, REPORT_DIR

try:
    from fpdf import FPDF
    HAS_FPDF = True
except ImportError:
    HAS_FPDF = False
    print("WARNING: fpdf2 not installed. PDF will not be generated. Run: pip install fpdf2")


# ── Helpers ───────────────────────────────────────────────────────────────────
def r2(v): return round(float(v), 2)
def fmt_inr(v): return f"Rs.{v:,.0f}"
def fmt_pct(v): return f"{v:.1f}%"
def _p(s): return str(s).replace("₹", "Rs.").replace("—", "-").replace("\u2014", "-")
plt.rcParams.update({"font.family": "DejaVu Sans", "axes.facecolor": "#0d1117",
                     "figure.facecolor": "#0d1117", "axes.edgecolor": "#30363d",
                     "text.color": "#e6edf3", "axes.labelcolor": "#e6edf3",
                     "xtick.color": "#8b949e", "ytick.color": "#8b949e",
                     "grid.color": "#21262d", "grid.linestyle": "--", "grid.alpha": 0.5})


# ── Load trades ───────────────────────────────────────────────────────────────
def load_trades(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    # Normalize column names
    df.columns = [c.lower().strip() for c in df.columns]

    # Detect P&L column (live_trades uses 'pnl', backtest uses 'pnl_conv'/'pnl_final')
    if "pnl_conv" in df.columns:
        df["pnl"] = df["pnl_conv"]
    elif "pnl_final" in df.columns:
        df["pnl"] = df["pnl_final"]

    if "pnl_flat" not in df.columns:
        df["pnl_flat"] = df.get("pnl_65", df["pnl"])

    # Normalize date
    df["date"] = pd.to_datetime(df["date"].astype(str), format="mixed")
    df = df.sort_values("date").reset_index(drop=True)

    if "win" not in df.columns:
        df["win"] = (df["pnl"] > 0).astype(int)
    if "year" not in df.columns:
        df["year"] = df["date"].dt.year.astype(str)
    if "strategy" not in df.columns:
        df["strategy"] = "unknown"

    return df


# ── Compute summary metrics ───────────────────────────────────────────────────
def compute_metrics(df: pd.DataFrame) -> dict:
    pnl     = df["pnl"]
    wins    = df["win"]
    eq      = pnl.cumsum()
    dd      = eq - eq.cummax()

    total_pnl     = r2(pnl.sum())
    flat_pnl      = r2(df["pnl_flat"].sum())
    total_trades  = len(df)
    win_rate      = r2(wins.mean() * 100)
    avg_win       = r2(pnl[wins == 1].mean()) if wins.sum() > 0 else 0
    avg_loss      = r2(pnl[wins == 0].mean()) if (wins == 0).sum() > 0 else 0
    profit_factor = r2(pnl[wins == 1].sum() / abs(pnl[wins == 0].sum())) if (wins==0).sum()>0 else 0
    max_dd        = r2(dd.min())
    max_dd_pct    = r2(dd.min() / eq.cummax().max() * 100) if eq.max() > 0 else 0
    avg_pnl       = r2(pnl.mean())

    # Consecutive losses
    streak = loss_streak = 0
    for w in wins:
        if w == 0:
            streak += 1
            loss_streak = max(loss_streak, streak)
        else:
            streak = 0

    # Weekly stats
    df2 = df.copy()
    df2["week"] = df2["date"].dt.to_period("W")
    weekly_pnl = df2.groupby("week")["pnl"].sum()
    avg_week   = r2(weekly_pnl.mean())

    # Calmar ratio (annualised return / max_dd)
    years = max((df["date"].max() - df["date"].min()).days / 365, 0.1)
    annual = total_pnl / years
    calmar = r2(annual / abs(max_dd)) if max_dd != 0 else 0

    return dict(
        total_pnl=total_pnl, flat_pnl=flat_pnl,
        total_trades=total_trades, win_rate=win_rate,
        avg_win=avg_win, avg_loss=avg_loss,
        profit_factor=profit_factor, max_dd=max_dd, max_dd_pct=max_dd_pct,
        avg_pnl=avg_pnl, avg_week=avg_week, max_cons_loss=loss_streak,
        calmar=calmar, years=r2(years),
    )


# ── Charts ────────────────────────────────────────────────────────────────────
def color_strategy(s):
    m = {"v17a": "#1e88e5", "cam_l3": "#26a69a", "cam_h3": "#ef5350",
         "iv2_r1": "#ff9800", "iv2_r2": "#ab47bc", "iv2_pdl": "#78909c"}
    return m.get(s, "#888888")


def chart_equity(df: pd.DataFrame) -> plt.Figure:
    """Flat vs Conviction equity curve + drawdown."""
    fig = plt.figure(figsize=(14, 8))
    gs  = gridspec.GridSpec(2, 1, height_ratios=[3, 1], hspace=0.05)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)

    dates = df["date"]
    eq_flat = df["pnl_flat"].cumsum()
    eq_conv = df["pnl"].cumsum()
    dd      = eq_conv - eq_conv.cummax()

    ax1.plot(dates, eq_flat / 1e5, color="#888888", lw=1.5, label="Flat (1 lot)")
    ax1.plot(dates, eq_conv / 1e5, color="#26a69a", lw=2.0, label="Conviction sizing")
    ax1.fill_between(dates, eq_conv / 1e5, 0, alpha=0.08, color="#26a69a")
    ax1.set_ylabel("Cumulative P&L (₹L)")
    ax1.legend(loc="upper left", framealpha=0.3)
    ax1.set_title("Equity Curve - Flat vs Conviction (LOT=65)", pad=10)
    ax1.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:.1f}L"))
    ax1.grid(True)

    ax2.fill_between(dates, dd / 1e5, 0, color="#ef5350", alpha=0.5, label="Drawdown")
    ax2.set_ylabel("Drawdown (₹L)")
    ax2.set_xlabel("Date")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:.1f}L"))
    ax2.grid(True)

    plt.setp(ax1.get_xticklabels(), visible=False)
    fig.tight_layout()
    return fig


def chart_per_strategy(df: pd.DataFrame) -> plt.Figure:
    """Per-strategy cumulative P&L."""
    fig, ax = plt.subplots(figsize=(14, 6))
    for strat in sorted(df["strategy"].unique()):
        g  = df[df["strategy"] == strat].sort_values("date")
        eq = g["pnl"].cumsum()
        ax.plot(g["date"], eq / 1e5, color=color_strategy(strat),
                lw=1.8, label=strat, marker="", alpha=0.85)
    ax.set_title("Per-Strategy Conviction Equity", pad=10)
    ax.set_ylabel("Cumulative P&L (₹L)")
    ax.set_xlabel("Date")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:.1f}L"))
    ax.legend(loc="upper left", framealpha=0.3)
    ax.grid(True)
    fig.tight_layout()
    return fig


def chart_yearwise(df: pd.DataFrame) -> plt.Figure:
    """Year-wise P&L bar chart."""
    yr  = df.groupby("year")["pnl"].sum() / 1e5
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#26a69a" if v >= 0 else "#ef5350" for v in yr.values]
    bars = ax.bar(yr.index, yr.values, color=colors, width=0.6, edgecolor="#21262d")
    for bar, val in zip(bars, yr.values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.02,
                f"₹{val:.2f}L", ha="center", va="bottom", fontsize=9, color="#e6edf3")
    ax.set_title("Year-wise Conviction P&L")
    ax.set_ylabel("P&L (₹L)")
    ax.set_xlabel("Year")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:.1f}L"))
    ax.axhline(0, color="#8b949e", lw=0.8)
    ax.grid(True, axis="y")
    fig.tight_layout()
    return fig


def chart_score_dist(df: pd.DataFrame) -> plt.Figure:
    """Score vs Win Rate + total P&L."""
    if "score" not in df.columns:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No score data", ha="center", transform=ax.transAxes)
        return fig

    scored = df[df["score"] >= 0]
    scores = sorted(scored["score"].unique())
    wr_vals = [scored[scored["score"] == s]["win"].mean() * 100 for s in scores]
    pnl_vals = [scored[scored["score"] == s]["pnl"].sum() / 1e5 for s in scores]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

    colors_wr = ["#26a69a" if w >= 70 else "#ff9800" if w >= 55 else "#ef5350" for w in wr_vals]
    ax1.bar(scores, wr_vals, color=colors_wr, width=0.6, edgecolor="#21262d")
    ax1.axhline(70, color="#26a69a", lw=1, ls="--", alpha=0.5, label="70% WR")
    ax1.set_title("Win Rate by Conviction Score")
    ax1.set_xlabel("Score"); ax1.set_ylabel("Win Rate (%)")
    ax1.set_ylim(0, 110); ax1.grid(True, axis="y"); ax1.legend()

    colors_pnl = ["#26a69a" if v >= 0 else "#ef5350" for v in pnl_vals]
    ax2.bar(scores, pnl_vals, color=colors_pnl, width=0.6, edgecolor="#21262d")
    ax2.set_title("Total P&L by Conviction Score")
    ax2.set_xlabel("Score"); ax2.set_ylabel("P&L (₹L)")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:.2f}L"))
    ax2.grid(True, axis="y")

    fig.tight_layout()
    return fig


def chart_pnl_dist(df: pd.DataFrame) -> plt.Figure:
    """P&L distribution histogram."""
    fig, ax = plt.subplots(figsize=(10, 5))
    wins_  = df[df["win"] == 1]["pnl"]
    losses = df[df["win"] == 0]["pnl"]
    ax.hist(wins_,  bins=30, color="#26a69a", alpha=0.7, label=f"Wins ({len(wins_)})")
    ax.hist(losses, bins=30, color="#ef5350", alpha=0.7, label=f"Losses ({len(losses)})")
    ax.axvline(0, color="#e6edf3", lw=1, ls="--")
    ax.set_title("P&L Distribution per Trade")
    ax.set_xlabel("P&L (₹)"); ax.set_ylabel("Frequency")
    ax.legend(framealpha=0.3)
    ax.grid(True, axis="y")
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"₹{v:,.0f}"))
    fig.tight_layout()
    return fig


def fig_to_bytes(fig: plt.Figure) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    data = buf.read()
    plt.close(fig)
    return data


# ── PDF builder ───────────────────────────────────────────────────────────────
class ReportPDF(FPDF if HAS_FPDF else object):

    def header(self):
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(38, 166, 154)
        self.cell(0, 10, "Hari CPR Intraday Strategy - Performance Report", new_x="LMARGIN", new_y="NEXT", align="C")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(139, 148, 158)
        self.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                  new_x="LMARGIN", new_y="NEXT", align="C")
        self.ln(4)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(139, 148, 158)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")

    def section_title(self, title: str):
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(38, 166, 154)
        self.cell(0, 8, _p(title), new_x="LMARGIN", new_y="NEXT")
        self.set_draw_color(38, 166, 154)
        self.line(self.get_x(), self.get_y(), self.get_x() + 190, self.get_y())
        self.ln(3)

    def kv_row(self, label: str, value: str, highlight=False):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(139, 148, 158)
        self.cell(75, 7, _p(label))
        if highlight:
            self.set_font("Helvetica", "B", 10)
            self.set_text_color(38, 166, 154)
        else:
            self.set_text_color(230, 237, 243)
        self.cell(0, 7, _p(str(value)), new_x="LMARGIN", new_y="NEXT")

    def table_header(self, cols: list[tuple]):
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(33, 38, 45)
        self.set_text_color(139, 148, 158)
        for label, w in cols:
            self.cell(w, 7, label, border=0, fill=True, align="C")
        self.ln()

    def table_row(self, values: list, cols: list[tuple], win: bool = True):
        self.set_font("Helvetica", "", 9)
        self.set_text_color(230, 237, 243)
        if not win:
            self.set_text_color(239, 83, 80)
        for val, (_, w) in zip(values, cols):
            self.cell(w, 6, _p(str(val)), border=0, align="C")
        self.ln()

    def add_image_bytes(self, img_bytes: bytes, w: int = 190):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp.write(img_bytes)
            tmp_path = tmp.name
        self.image(tmp_path, x=self.get_x(), y=self.get_y(), w=w)
        os.unlink(tmp_path)
        self.ln(5)


# ── Main report function ──────────────────────────────────────────────────────
def generate_report(csv_path: str = None, out_dir: str = None):
    csv_path = csv_path or LIVE_TRADES_CSV
    out_dir  = out_dir  or REPORT_DIR
    os.makedirs(out_dir, exist_ok=True)

    if not os.path.exists(csv_path):
        print(f"ERROR: trades CSV not found: {csv_path}")
        return

    print(f"Loading trades from {csv_path}...")
    df = load_trades(csv_path)
    print(f"  {len(df)} trades loaded")

    m  = compute_metrics(df)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    # ── Strategy breakdown ─────────────────────────────────────────────────
    strat_df = df.groupby("strategy").agg(
        trades=("pnl", "count"),
        wr=("win", lambda x: round(x.mean() * 100, 1)),
        flat_pnl=("pnl_flat", "sum"),
        conv_pnl=("pnl", "sum"),
    ).reset_index()

    # ── Year-wise breakdown ────────────────────────────────────────────────
    year_df = df.groupby("year").agg(
        trades=("pnl", "count"),
        wr=("win", lambda x: round(x.mean() * 100, 1)),
        pnl=("pnl", "sum"),
    ).reset_index()

    # ── Score breakdown ────────────────────────────────────────────────────
    score_df = None
    if "score" in df.columns:
        score_df = df[df["score"] >= 0].groupby("score").agg(
            trades=("pnl", "count"),
            wr=("win", lambda x: round(x.mean() * 100, 1)),
            avg_pnl=("pnl", "mean"),
            total_pnl=("pnl", "sum"),
        ).reset_index()

    # ── Save summary CSV ───────────────────────────────────────────────────
    csv_out = os.path.join(out_dir, f"report_summary_{ts}.csv")
    summary_rows = [
        ("Total P&L (conviction)", m["total_pnl"]),
        ("Flat P&L (1 lot)",       m["flat_pnl"]),
        ("Total Trades",           m["total_trades"]),
        ("Win Rate (%)",           m["win_rate"]),
        ("Avg Win (₹)",            m["avg_win"]),
        ("Avg Loss (₹)",           m["avg_loss"]),
        ("Profit Factor",          m["profit_factor"]),
        ("Max Drawdown (₹)",       m["max_dd"]),
        ("Max Drawdown (%)",       m["max_dd_pct"]),
        ("Avg P&L/Trade",          m["avg_pnl"]),
        ("Avg P&L/Week",           m["avg_week"]),
        ("Max Consec Losses",      m["max_cons_loss"]),
        ("Calmar Ratio",           m["calmar"]),
        ("Years",                  m["years"]),
    ]
    pd.DataFrame(summary_rows, columns=["Metric", "Value"]).to_csv(csv_out, index=False)
    print(f"  Summary CSV → {csv_out}")

    # ── Generate charts ────────────────────────────────────────────────────
    print("  Generating charts...")
    charts = {
        "equity":       fig_to_bytes(chart_equity(df)),
        "per_strategy": fig_to_bytes(chart_per_strategy(df)),
        "yearwise":     fig_to_bytes(chart_yearwise(df)),
        "score_dist":   fig_to_bytes(chart_score_dist(df)),
        "pnl_dist":     fig_to_bytes(chart_pnl_dist(df)),
    }

    if not HAS_FPDF:
        # Save PNGs only
        for name, img in charts.items():
            p = os.path.join(out_dir, f"chart_{name}_{ts}.png")
            with open(p, "wb") as f:
                f.write(img)
            print(f"  Chart → {p}")
        return

    # ── Build PDF ──────────────────────────────────────────────────────────
    pdf = ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(10, 15, 10)
    pdf.set_fill_color(13, 17, 23)
    pdf.add_page()

    # Page 1 — Summary metrics
    pdf.section_title("1. Summary Metrics")
    left = [
        ("Total Conviction P&L",  fmt_inr(m["total_pnl"])),
        ("Flat P&L (1 lot)",      fmt_inr(m["flat_pnl"])),
        ("Conviction uplift",     f"+{fmt_inr(m['total_pnl']-m['flat_pnl'])}  "
                                  f"(+{(m['total_pnl']/m['flat_pnl']-1)*100:.0f}%)"
                                  if m["flat_pnl"] else "N/A"),
        ("Total Trades",          str(m["total_trades"])),
        ("Win Rate",              fmt_pct(m["win_rate"])),
        ("Avg Win",               fmt_inr(m["avg_win"])),
        ("Avg Loss",              fmt_inr(m["avg_loss"])),
    ]
    right = [
        ("Profit Factor",         f"{m['profit_factor']:.2f}"),
        ("Max Drawdown",          fmt_inr(m["max_dd"])),
        ("Max Drawdown %",        fmt_pct(m["max_dd_pct"])),
        ("Avg P&L / Trade",       fmt_inr(m["avg_pnl"])),
        ("Avg P&L / Week",        fmt_inr(m["avg_week"])),
        ("Max Consec Losses",     str(m["max_cons_loss"])),
        ("Calmar Ratio",          f"{m['calmar']:.2f}"),
    ]
    for (l, lv), (r, rv) in zip(left, right):
        pdf.kv_row(l, lv, highlight=("P&L" in l or "Win Rate" in l))
        # simple side-by-side not easy in fpdf — just stack
        pdf.kv_row(r, rv, highlight=("Factor" in r))
    pdf.ln(4)

    # Strategy breakdown table
    pdf.section_title("2. Strategy Breakdown")
    cols = [("Strategy", 45), ("Trades", 25), ("Win Rate", 30),
            ("Flat P&L", 45), ("Conv P&L", 45)]
    pdf.table_header(cols)
    for _, row in strat_df.iterrows():
        pdf.table_row([row["strategy"], int(row["trades"]),
                       fmt_pct(row["wr"]), fmt_inr(row["flat_pnl"]),
                       fmt_inr(row["conv_pnl"])], cols,
                      win=row["conv_pnl"] >= 0)
    # Total row
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(38, 166, 154)
    pdf.table_row(["TOTAL", m["total_trades"], fmt_pct(m["win_rate"]),
                   fmt_inr(m["flat_pnl"]), fmt_inr(m["total_pnl"])], cols, win=True)
    pdf.ln(4)

    # Year-wise table
    pdf.section_title("3. Year-wise Breakdown")
    cols2 = [("Year", 30), ("Trades", 25), ("Win Rate", 30), ("Conv P&L", 50)]
    pdf.table_header(cols2)
    for _, row in year_df.iterrows():
        pdf.table_row([row["year"], int(row["trades"]),
                       fmt_pct(row["wr"]), fmt_inr(row["pnl"])],
                      cols2, win=row["pnl"] >= 0)
    pdf.ln(4)

    # Score breakdown
    if score_df is not None:
        pdf.section_title("4. Conviction Score Breakdown")
        cols3 = [("Score", 25), ("Trades", 25), ("Win Rate", 30),
                 ("Avg P&L", 45), ("Total P&L", 45)]
        pdf.table_header(cols3)
        for _, row in score_df.iterrows():
            pdf.table_row([int(row["score"]), int(row["trades"]),
                           fmt_pct(row["wr"]), fmt_inr(row["avg_pnl"]),
                           fmt_inr(row["total_pnl"])],
                          cols3, win=row["total_pnl"] >= 0)
        pdf.ln(4)

    # Page 2 — Equity chart
    pdf.add_page()
    pdf.section_title("5. Equity Curve — Flat vs Conviction + Drawdown")
    pdf.add_image_bytes(charts["equity"], w=185)

    # Page 3 — Per-strategy + year-wise
    pdf.add_page()
    pdf.section_title("6. Per-Strategy Equity Curves")
    pdf.add_image_bytes(charts["per_strategy"], w=185)
    pdf.ln(2)
    pdf.section_title("7. Year-wise P&L")
    pdf.add_image_bytes(charts["yearwise"], w=185)

    # Page 4 — Score dist + P&L dist
    pdf.add_page()
    pdf.section_title("8. Conviction Score Analysis")
    pdf.add_image_bytes(charts["score_dist"], w=185)
    pdf.ln(2)
    pdf.section_title("9. P&L Distribution per Trade")
    pdf.add_image_bytes(charts["pnl_dist"], w=185)

    pdf_out = os.path.join(out_dir, f"report_{ts}.pdf")
    pdf.output(pdf_out)
    print(f"  PDF  → {pdf_out}")
    print("Done.")
    return pdf_out


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate trading performance report")
    parser.add_argument("--csv", default=None, help="Path to trades CSV")
    parser.add_argument("--out", default=None, help="Output directory")
    args = parser.parse_args()
    generate_report(csv_path=args.csv, out_dir=args.out)
