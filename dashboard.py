"""
CPR Strategy v17a + Intraday v2 — Dashboard
============================================
Run: streamlit run dashboard.py
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
from datetime import datetime, date
import os, glob

# ── Page config ────────────────────────────────────────────────────
st.set_page_config(
    page_title="CPR Strategy",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS — dark Lovable-style theme ──────────────────────────
st.markdown("""
<style>
  /* Base */
  [data-testid="stApp"] { background:#0d0f14; color:#e2e8f0; }
  [data-testid="stSidebar"] { background:#0a0c10; border-right:1px solid #1e2530; }
  [data-testid="stSidebar"] * { color:#e2e8f0 !important; }

  /* Remove default padding */
  .block-container { padding-top:1.5rem; padding-bottom:1rem; }

  /* Cards */
  .card {
    background:#131720; border:1px solid #1e2530;
    border-radius:12px; padding:1.2rem 1.4rem;
    margin-bottom:0.8rem;
  }
  .card-title { color:#64748b; font-size:.75rem; font-weight:600;
                letter-spacing:.05em; text-transform:uppercase; margin-bottom:.4rem; }
  .card-value { font-size:1.6rem; font-weight:700; color:#f1f5f9; line-height:1; }
  .card-sub   { font-size:.8rem; color:#64748b; margin-top:.3rem; }

  /* Signal badge */
  .badge-pe { background:#1a3a2a; color:#4ade80; border:1px solid #166534;
              padding:.25rem .75rem; border-radius:6px; font-weight:700;
              font-size:.9rem; display:inline-block; }
  .badge-ce { background:#3a1a1a; color:#f87171; border:1px solid #991b1b;
              padding:.25rem .75rem; border-radius:6px; font-weight:700;
              font-size:.9rem; display:inline-block; }
  .badge-none { background:#1e2530; color:#94a3b8; border:1px solid #334155;
                padding:.25rem .75rem; border-radius:6px; font-size:.9rem;
                display:inline-block; }

  /* Level table */
  .level-table { width:100%; border-collapse:collapse; font-size:.85rem; }
  .level-table th { color:#64748b; font-weight:600; padding:.4rem .6rem;
                    border-bottom:1px solid #1e2530; text-align:left; }
  .level-table td { padding:.35rem .6rem; border-bottom:1px solid #131720; color:#e2e8f0; }
  .level-table tr:hover td { background:#1e2530; }
  .lvl-r { color:#f87171; } .lvl-s { color:#4ade80; }
  .lvl-cpr { color:#60a5fa; } .lvl-pdx { color:#fbbf24; }

  /* P&L colors */
  .pnl-pos { color:#4ade80; } .pnl-neg { color:#f87171; }

  /* Divider */
  hr.sect { border:none; border-top:1px solid #1e2530; margin:1rem 0; }

  /* Sidebar nav items */
  [data-testid="stSidebarNav"] a { color:#94a3b8 !important; }
  [data-testid="stSidebarNav"] a:hover { color:#e2e8f0 !important; }

  /* Metric override */
  [data-testid="stMetricValue"] { color:#f1f5f9 !important; font-size:1.4rem !important; }
  [data-testid="stMetricDelta"] { font-size:.8rem !important; }
  [data-testid="stMetricLabel"] { color:#64748b !important; font-size:.75rem !important; }

  /* Table */
  [data-testid="stDataFrame"] { border:1px solid #1e2530; border-radius:8px; }
  thead tr th { background:#131720 !important; color:#64748b !important; }
  tbody tr td { background:#0d0f14 !important; color:#e2e8f0 !important; }
  tbody tr:hover td { background:#131720 !important; }
</style>
""", unsafe_allow_html=True)


# ── Data helpers ───────────────────────────────────────────────────
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

@st.cache_data(ttl=60)
def load_backtest_trades():
    # Search dated subfolders (e.g. data/20260420/38_zone_v17a_trades.csv)
    # then fall back to flat data/ folder for backwards compatibility
    def latest(pattern):
        hits = sorted(glob.glob(os.path.join(DATA_DIR, '*', pattern)))
        flat = os.path.join(DATA_DIR, pattern.lstrip('*'))
        if os.path.exists(flat):
            hits.append(flat)
        return hits[-1] if hits else None

    v17a_path  = latest('*v17a_trades.csv')
    intra_path = latest('*intraday_v2_trades.csv')
    dfs = []
    if v17a_path:
        df = pd.read_csv(v17a_path, parse_dates=['date'])
        df['source'] = 'v17a'
        cols = ['date','source','zone','opt','entry_time','ep','xp','exit_reason','pnl']
        if 'dte' in df.columns: cols.append('dte')
        dfs.append(df[cols])
    if intra_path:
        df = pd.read_csv(intra_path, parse_dates=['date'])
        df['source'] = 'intraday_v2'
        df = df.rename(columns={'break_name':'zone'})
        cols = ['date','source','zone','opt','entry_time','ep','xp','exit_reason','pnl']
        if 'dte' in df.columns: cols.append('dte')
        dfs.append(df[cols])
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True).sort_values('date').reset_index(drop=True)

@st.cache_data(ttl=60)
def load_live_trades():
    path = os.path.join(DATA_DIR, 'live_trades.csv')
    if not os.path.exists(path):
        return pd.DataFrame()
    return pd.read_csv(path, parse_dates=['date'])

def compute_stats(df):
    if df.empty: return {}
    n     = len(df)
    total = round(df['pnl'].sum(), 0)
    wr    = round((df['pnl'] > 0).mean() * 100, 1)
    wins  = df[df['pnl'] > 0]['pnl'].sum()
    loss  = abs(df[df['pnl'] <= 0]['pnl'].sum())
    pf    = round(wins / loss, 2) if loss > 0 else 999
    eq    = df.sort_values('date').set_index('date')['pnl'].cumsum()
    dds   = eq - eq.cummax()
    dd    = round(abs(dds.min()), 0)
    daily = df.groupby('date')['pnl'].sum()
    sh    = round(daily.mean() / daily.std() * np.sqrt(250), 2) if daily.std() > 0 else 0
    yrs   = max((df['date'].max() - df['date'].min()).days / 365.25, 0.1)
    cal   = round(total / dd, 2) if dd > 0 else 0
    return dict(n=n, total=total, wr=wr, pf=pf, dd=dd, sharpe=sh, calmar=cal, yrs=yrs, eq=eq, dds=dds)


# ── Sidebar ────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📈 CPR Strategy")
    st.markdown('<hr class="sect">', unsafe_allow_html=True)
    page = st.radio(
        "",
        ["Today's Signal", "Live Monitor", "Trade Log", "Performance"],
        label_visibility="collapsed"
    )
    st.markdown('<hr class="sect">', unsafe_allow_html=True)
    st.markdown(f"<div style='color:#64748b;font-size:.75rem'>"
                f"v17a + Intraday v2<br>"
                f"LOT: 75 · NIFTY Weekly<br>"
                f"{date.today().strftime('%d %b %Y')}</div>",
                unsafe_allow_html=True)
    st.markdown('<hr class="sect">', unsafe_allow_html=True)

    # Quick live stats
    df_all = load_backtest_trades()
    if not df_all.empty:
        s = compute_stats(df_all)
        st.markdown(f"""
        <div style='font-size:.8rem;color:#94a3b8'>
          <div style='margin-bottom:.3rem'>Trades: <b style='color:#e2e8f0'>{s['n']}</b></div>
          <div style='margin-bottom:.3rem'>Win%: <b style='color:#4ade80'>{s['wr']}%</b></div>
          <div style='margin-bottom:.3rem'>Sharpe: <b style='color:#60a5fa'>{s['sharpe']}</b></div>
          <div>Max DD: <b style='color:#f87171'>₹{s['dd']:,.0f}</b></div>
        </div>""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 1 — TODAY'S SIGNAL
# ═══════════════════════════════════════════════════════════════════
if page == "Today's Signal":
    st.markdown("## Today's Signal")
    st.markdown("*Computed from previous day OHLC + EMA(20). Connect Angel One to enable live data.*")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown('<div class="card-title">Data Source</div>', unsafe_allow_html=True)
        mode = st.radio("", ["Manual (enter levels)", "Live (Angel One)"],
                        horizontal=True, label_visibility="collapsed")
        st.markdown('</div>', unsafe_allow_html=True)

    if mode == "Manual (enter levels)":
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### Previous Day Levels")
        c1, c2, c3 = st.columns(3)
        with c1:
            pdh = st.number_input("PDH", value=24500.0, step=0.5)
            pdl = st.number_input("PDL", value=24100.0, step=0.5)
        with c2:
            pc  = st.number_input("Prev Close", value=24300.0, step=0.5)
            ema = st.number_input("EMA(20)", value=24250.0, step=0.5)
        with c3:
            today_open = st.number_input("Today Open (09:15)", value=24350.0, step=0.5)

        from strategy import compute_pivots, classify_zone, get_v17a_signal, r2
        pvt  = compute_pivots(pdh, pdl, pc)
        bias = 'bull' if today_open > ema else 'bear'
        zone = classify_zone(today_open, pvt, pdh, pdl)
        sig  = get_v17a_signal(zone, bias)

        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        col_a, col_b, col_c, col_d = st.columns(4)

        with col_a:
            st.markdown(f"""<div class="card">
              <div class="card-title">Zone</div>
              <div class="card-value" style="font-size:1.1rem">{zone.replace('_',' ').title()}</div>
            </div>""", unsafe_allow_html=True)
        with col_b:
            bias_col = '#4ade80' if bias == 'bull' else '#f87171'
            st.markdown(f"""<div class="card">
              <div class="card-title">EMA Bias</div>
              <div class="card-value" style="color:{bias_col}">{bias.upper()}</div>
              <div class="card-sub">Open {today_open:.0f} vs EMA {ema:.0f}</div>
            </div>""", unsafe_allow_html=True)
        with col_c:
            badge = f'<span class="badge-pe">SELL PE</span>' if sig == 'PE' \
                    else f'<span class="badge-ce">SELL CE</span>' if sig == 'CE' \
                    else '<span class="badge-none">NO SIGNAL</span>'
            st.markdown(f"""<div class="card">
              <div class="card-title">v17a Signal</div>
              <div style="margin-top:.5rem">{badge}</div>
            </div>""", unsafe_allow_html=True)
        with col_d:
            from config import V17A_PARAMS
            params_disp = "—"
            if sig:
                key = (zone, bias, sig)
                if key in V17A_PARAMS:
                    st_, et, tp, sp, slt = V17A_PARAMS[key]
                    params_disp = f"{st_} @ {et[:5]}<br>Tgt {tp:.0%} · SL {sp}"
            st.markdown(f"""<div class="card">
              <div class="card-title">Trade Params</div>
              <div style="font-size:.9rem;margin-top:.3rem;color:#e2e8f0">{params_disp}</div>
            </div>""", unsafe_allow_html=True)

        # CPR Levels table
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### CPR & Pivot Levels")
        levels = [
            ("R4", pvt['r4'], "lvl-r"), ("R3", pvt['r3'], "lvl-r"),
            ("R2", pvt['r2'], "lvl-r"), ("R1", pvt['r1'], "lvl-r"),
            ("PDH", pdh, "lvl-pdx"),
            ("TC",  pvt['tc'], "lvl-cpr"), ("PP", pvt['pp'], "lvl-cpr"),
            ("BC",  pvt['bc'], "lvl-cpr"),
            ("PDL", pdl, "lvl-pdx"),
            ("S1", pvt['s1'], "lvl-s"), ("S2", pvt['s2'], "lvl-s"),
            ("S3", pvt['s3'], "lvl-s"), ("S4", pvt['s4'], "lvl-s"),
        ]
        rows_html = ""
        for name, val, cls in levels:
            dist    = round(today_open - val, 1)
            arrow   = "▲" if dist > 0 else "▼"
            dist_c  = "#4ade80" if dist > 0 else "#f87171"
            marker  = " ← OPEN" if abs(dist) < 30 else ""
            rows_html += f"""<tr>
              <td class="{cls}"><b>{name}</b></td>
              <td>{val:.2f}</td>
              <td style="color:{dist_c}">{arrow} {abs(dist):.1f}</td>
              <td style="color:#60a5fa">{marker}</td>
            </tr>"""
        st.markdown(f"""<table class="level-table">
          <thead><tr><th>Level</th><th>Value</th><th>Dist from Open</th><th></th></tr></thead>
          <tbody>{rows_html}</tbody>
        </table>""", unsafe_allow_html=True)

    else:
        st.info("Angel One connection required. Fill `config.py` credentials and restart.")


# ═══════════════════════════════════════════════════════════════════
# PAGE 2 — LIVE MONITOR
# ═══════════════════════════════════════════════════════════════════
elif page == "Live Monitor":
    st.markdown("## Live Monitor")

    live_df = load_live_trades()

    # Today's trade
    today_str = date.today().isoformat()
    today_trades = live_df[live_df['date'].astype(str) == today_str] \
                   if not live_df.empty else pd.DataFrame()

    col1, col2, col3, col4 = st.columns(4)

    if today_trades.empty:
        with col1:
            st.markdown("""<div class="card">
              <div class="card-title">Status</div>
              <div class="card-value" style="font-size:1rem;color:#fbbf24">Waiting</div>
              <div class="card-sub">No trade today yet</div>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown("""<div class="card">
              <div class="card-title">Today P&L</div>
              <div class="card-value">₹ —</div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown("""<div class="card">
              <div class="card-title">Symbol</div>
              <div class="card-value" style="font-size:1rem">—</div>
            </div>""", unsafe_allow_html=True)
        with col4:
            st.markdown("""<div class="card">
              <div class="card-title">Source</div>
              <div class="card-value" style="font-size:1rem">—</div>
            </div>""", unsafe_allow_html=True)
    else:
        t = today_trades.iloc[-1]
        pnl_val  = t.get('pnl', 0)
        pnl_col  = '#4ade80' if pnl_val >= 0 else '#f87171'
        status   = "Closed" if pd.notna(t.get('exit_reason')) else "Open"
        src_col  = '#60a5fa' if t.get('source') == 'v17a' else '#a78bfa'

        with col1:
            st.markdown(f"""<div class="card">
              <div class="card-title">Status</div>
              <div class="card-value" style="font-size:1.1rem;color:{'#4ade80' if status=='Closed' else '#fbbf24'}">{status}</div>
              <div class="card-sub">{t.get('exit_reason','—')}</div>
            </div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="card">
              <div class="card-title">Today P&L</div>
              <div class="card-value" style="color:{pnl_col}">₹{pnl_val:,.0f}</div>
            </div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="card">
              <div class="card-title">Symbol</div>
              <div class="card-value" style="font-size:.9rem">{t.get('symbol','—')}</div>
              <div class="card-sub">Entry ₹{t.get('entry_price','—')} → Exit ₹{t.get('exit_price','—')}</div>
            </div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class="card">
              <div class="card-title">Source</div>
              <div class="card-value" style="color:{src_col};font-size:1rem">{t.get('source','—').replace('_',' ').upper()}</div>
              <div class="card-sub">{t.get('zone','—')}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown('<hr class="sect">', unsafe_allow_html=True)

    # Recent live trades
    st.markdown("### Recent Live Trades")
    if live_df.empty:
        st.info("No live trades recorded yet. Run `python trader.py` to start.")
    else:
        disp = live_df.sort_values('date', ascending=False).head(20).copy()
        disp['pnl'] = disp['pnl'].apply(lambda x: f"₹{x:,.0f}" if pd.notna(x) else "—")
        st.dataframe(disp, use_container_width=True, hide_index=True)

    st.markdown('<hr class="sect">', unsafe_allow_html=True)
    st.markdown("### Trail Stop Reference")
    st.markdown("""
    <table class="level-table">
      <thead><tr><th>Tier</th><th>Trigger</th><th>SL Moves To</th><th>Effect</th></tr></thead>
      <tbody>
        <tr><td>1</td><td>25% premium decay</td><td>Entry price (break-even)</td><td>Guaranteed no-loss</td></tr>
        <tr><td>2</td><td>40% premium decay</td><td>80% of entry</td><td>Lock 20% gain</td></tr>
        <tr><td>3</td><td>60% premium decay</td><td>95% of max decay</td><td>Lock 95% of best</td></tr>
      </tbody>
    </table>
    """, unsafe_allow_html=True)

    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# PAGE 3 — TRADE LOG
# ═══════════════════════════════════════════════════════════════════
elif page == "Trade Log":
    st.markdown("## Trade Log")
    df = load_backtest_trades()

    if df.empty:
        st.info("No backtest trade data found in `data/` folder.")
    else:
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            src_filter = st.multiselect("Source", ['v17a','intraday_v2'],
                                        default=['v17a','intraday_v2'])
        with col2:
            opt_filter = st.multiselect("Option", ['PE','CE'], default=['PE','CE'])
        with col3:
            exit_filter = st.multiselect("Exit Reason",
                          df['exit_reason'].unique().tolist(),
                          default=df['exit_reason'].unique().tolist())

        filtered = df[df['source'].isin(src_filter) &
                      df['opt'].isin(opt_filter) &
                      df['exit_reason'].isin(exit_filter)].copy()

        # Summary row
        if not filtered.empty:
            s = compute_stats(filtered)
            c1,c2,c3,c4,c5 = st.columns(5)
            c1.metric("Trades", s['n'])
            c2.metric("Total P&L", f"₹{s['total']:,.0f}")
            c3.metric("Win%", f"{s['wr']}%")
            c4.metric("Sharpe", s['sharpe'])
            c5.metric("Max DD", f"₹{s['dd']:,.0f}")

        st.markdown('<hr class="sect">', unsafe_allow_html=True)

        # Table
        disp = filtered.sort_values('date', ascending=False).copy()
        disp['pnl_disp'] = disp['pnl'].apply(lambda x: f"₹{x:,.0f}")
        disp['date_str']  = disp['date'].dt.strftime('%Y-%m-%d')
        st.dataframe(
            disp[['date_str','source','zone','opt','entry_time','ep','xp','exit_reason','pnl']],
            column_config={
                'date_str':    st.column_config.TextColumn('Date'),
                'source':      st.column_config.TextColumn('Source'),
                'zone':        st.column_config.TextColumn('Zone'),
                'opt':         st.column_config.TextColumn('Opt'),
                'entry_time':  st.column_config.TextColumn('Entry Time'),
                'ep':          st.column_config.NumberColumn('Entry ₹', format="%.2f"),
                'xp':          st.column_config.NumberColumn('Exit ₹',  format="%.2f"),
                'exit_reason': st.column_config.TextColumn('Exit'),
                'pnl':         st.column_config.NumberColumn('P&L ₹',   format="%.0f"),
            },
            use_container_width=True, hide_index=True
        )

        # By zone
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### By Zone")
        grp = filtered.groupby(['source','zone']).agg(
            n=('pnl','count'), wr=('pnl', lambda x: round((x>0).mean()*100,1)),
            total=('pnl','sum'), avg=('pnl','mean')
        ).reset_index().sort_values('total', ascending=False)
        st.dataframe(grp, use_container_width=True, hide_index=True)


# ═══════════════════════════════════════════════════════════════════
# PAGE 4 — PERFORMANCE
# ═══════════════════════════════════════════════════════════════════
elif page == "Performance":
    st.markdown("## Performance")
    df = load_backtest_trades()

    if df.empty:
        st.info("No backtest trade data found in `data/` folder.")
    else:
        s = compute_stats(df)

        # KPI row
        c1,c2,c3,c4,c5,c6,c7 = st.columns(7)
        c1.metric("Total P&L",    f"₹{s['total']:,.0f}")
        c2.metric("Trades",       f"{s['n']} ({s['n']/s['yrs']:.0f}/yr)")
        c3.metric("Win Rate",     f"{s['wr']}%")
        c4.metric("Profit Factor",f"{s['pf']}")
        c5.metric("Sharpe",       f"{s['sharpe']}")
        c6.metric("Calmar",       f"{s['calmar']}")
        c7.metric("Max Drawdown", f"₹{s['dd']:,.0f}")

        st.markdown('<hr class="sect">', unsafe_allow_html=True)

        # ── Equity curve ──────────────────────────────────────────
        eq  = s['eq']
        dds = s['dds']

        fig_eq = make_subplots(rows=2, cols=1, row_heights=[0.7, 0.3],
                               shared_xaxes=True, vertical_spacing=0.04)
        fig_eq.add_trace(go.Scatter(
            x=eq.index, y=eq.values,
            mode='lines', name='Equity',
            line=dict(color='#60a5fa', width=2),
            fill='tozeroy', fillcolor='rgba(96,165,250,0.08)'
        ), row=1, col=1)
        fig_eq.add_trace(go.Scatter(
            x=dds.index, y=dds.values,
            mode='lines', name='Drawdown',
            line=dict(color='#f87171', width=1.5),
            fill='tozeroy', fillcolor='rgba(248,113,113,0.1)'
        ), row=2, col=1)
        fig_eq.update_layout(
            height=420, showlegend=False,
            plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
            font=dict(color='#94a3b8', size=11),
            margin=dict(l=10, r=10, t=30, b=10),
            title=dict(text='Equity Curve & Drawdown', font=dict(color='#e2e8f0', size=13))
        )
        fig_eq.update_xaxes(gridcolor='#1e2530', showgrid=True, zeroline=False)
        fig_eq.update_yaxes(gridcolor='#1e2530', showgrid=True, zeroline=False,
                            tickprefix='₹', tickformat=',.0f')
        st.plotly_chart(fig_eq, use_container_width=True)

        # ── Yearly P&L ────────────────────────────────────────────
        col_l, col_r = st.columns(2)

        with col_l:
            df['year'] = df['date'].dt.year
            yearly = df.groupby('year')['pnl'].sum().reset_index()
            fig_yr = px.bar(yearly, x='year', y='pnl',
                            color=yearly['pnl'].apply(lambda x: 'Profit' if x >= 0 else 'Loss'),
                            color_discrete_map={'Profit':'#4ade80','Loss':'#f87171'},
                            title='Yearly P&L')
            fig_yr.update_layout(
                height=300, showlegend=False,
                plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
                font=dict(color='#94a3b8'),
                margin=dict(l=10,r=10,t=40,b=10),
                title_font=dict(color='#e2e8f0', size=13)
            )
            fig_yr.update_yaxes(tickprefix='₹', tickformat=',.0f', gridcolor='#1e2530')
            fig_yr.update_xaxes(gridcolor='#1e2530')
            st.plotly_chart(fig_yr, use_container_width=True)

        with col_r:
            # Exit reason breakdown
            exit_grp = df.groupby('exit_reason')['pnl'].agg(['sum','count']).reset_index()
            exit_grp.columns = ['reason','total','n']
            fig_ex = px.pie(exit_grp, values='n', names='reason',
                            title='Exit Reason Mix',
                            color_discrete_sequence=['#60a5fa','#4ade80','#f87171','#fbbf24','#a78bfa'])
            fig_ex.update_layout(
                height=300, plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
                font=dict(color='#94a3b8'), margin=dict(l=10,r=10,t=40,b=10),
                title_font=dict(color='#e2e8f0', size=13)
            )
            st.plotly_chart(fig_ex, use_container_width=True)

        # ── Monthly heatmap ───────────────────────────────────────
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### Monthly P&L Heatmap")
        _mnames = {1:'Jan',2:'Feb',3:'Mar',4:'Apr',5:'May',6:'Jun',
                   7:'Jul',8:'Aug',9:'Sep',10:'Oct',11:'Nov',12:'Dec'}
        df['month'] = df['date'].dt.month
        monthly = df.groupby(['year','month'])['pnl'].sum().reset_index()
        pivot   = monthly.pivot(index='year', columns='month', values='pnl').fillna(0)
        pivot.columns = [_mnames[m] for m in pivot.columns]  # fix: map by actual month number

        fig_hm = go.Figure(go.Heatmap(
            z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
            colorscale=[[0,'#7f1d1d'],[0.5,'#131720'],[1,'#14532d']],
            zmid=0, text=pivot.values,
            texttemplate='₹%{text:,.0f}', textfont=dict(size=9),
            hovertemplate='%{y} %{x}: ₹%{z:,.0f}<extra></extra>'
        ))
        fig_hm.update_layout(
            height=250, plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
            font=dict(color='#94a3b8'), margin=dict(l=10,r=10,t=20,b=10)
        )
        st.plotly_chart(fig_hm, use_container_width=True)

        # ── Source breakdown ──────────────────────────────────────
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### Strategy Breakdown")
        src_grp = df.groupby('source').agg(
            n=('pnl','count'),
            total=('pnl','sum'),
            wr=('pnl', lambda x: round((x>0).mean()*100,1)),
            avg=('pnl','mean'),
            sharpe=('pnl', lambda x: round(x.mean()/x.std()*np.sqrt(len(x)),2) if x.std()>0 else 0)
        ).reset_index()
        src_grp['total'] = src_grp['total'].apply(lambda x: f"₹{x:,.0f}")
        src_grp['avg']   = src_grp['avg'].apply(lambda x: f"₹{x:,.0f}")
        src_grp['wr']    = src_grp['wr'].apply(lambda x: f"{x}%")
        st.dataframe(src_grp, use_container_width=True, hide_index=True)

        # ── Zone performance bar chart ────────────────────────────
        st.markdown('<hr class="sect">', unsafe_allow_html=True)
        st.markdown("### Zone Performance")
        zone_grp = df.groupby('zone').agg(
            n=('pnl','count'),
            total=('pnl','sum'),
            avg=('pnl','mean'),
            wr=('pnl', lambda x: round((x>0).mean()*100,1))
        ).reset_index().sort_values('total', ascending=True)

        col_zl, col_zr = st.columns(2)
        with col_zl:
            fig_z = go.Figure(go.Bar(
                x=zone_grp['total'], y=zone_grp['zone'],
                orientation='h',
                marker_color=['#4ade80' if v >= 0 else '#f87171' for v in zone_grp['total']],
                text=zone_grp['total'].apply(lambda x: f"₹{x:,.0f}"),
                textposition='outside',
            ))
            fig_z.update_layout(
                title=dict(text='Total P&L by Zone', font=dict(color='#e2e8f0', size=13)),
                height=max(300, len(zone_grp) * 30),
                plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
                font=dict(color='#94a3b8', size=10),
                margin=dict(l=10, r=80, t=40, b=10),
                xaxis=dict(tickprefix='₹', tickformat=',.0f', gridcolor='#1e2530'),
                yaxis=dict(gridcolor='#1e2530'),
                showlegend=False,
            )
            st.plotly_chart(fig_z, use_container_width=True)

        with col_zr:
            zone_grp_wr = zone_grp.sort_values('wr', ascending=True)
            fig_wr = go.Figure(go.Bar(
                x=zone_grp_wr['wr'], y=zone_grp_wr['zone'],
                orientation='h',
                marker_color=['#4ade80' if v >= 65 else '#fbbf24' if v >= 50 else '#f87171'
                              for v in zone_grp_wr['wr']],
                text=zone_grp_wr['wr'].apply(lambda x: f"{x:.0f}%"),
                textposition='outside',
            ))
            fig_wr.update_layout(
                title=dict(text='Win Rate by Zone', font=dict(color='#e2e8f0', size=13)),
                height=max(300, len(zone_grp_wr) * 30),
                plot_bgcolor='#0d0f14', paper_bgcolor='#131720',
                font=dict(color='#94a3b8', size=10),
                margin=dict(l=10, r=60, t=40, b=10),
                xaxis=dict(ticksuffix='%', gridcolor='#1e2530', range=[0, 110]),
                yaxis=dict(gridcolor='#1e2530'),
                showlegend=False,
            )
            st.plotly_chart(fig_wr, use_container_width=True)
