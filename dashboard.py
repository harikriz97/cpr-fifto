"""
FIFTO CPR Strategy v17a — Dashboard
Run: streamlit run dashboard.py
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, date
import os, time, requests as rq

st.set_page_config(page_title="FIFTO | CPR v17a", layout="wide",
                   initial_sidebar_state="collapsed")

# ── Global CSS ────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600;700&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif !important;}
[data-testid="stApp"]{background:#0d1117 !important;}
[data-testid="stHeader"]{display:none !important;}
[data-testid="stSidebar"]{display:none !important;}
.block-container{padding:0 !important;max-width:100% !important;}
footer{display:none !important;}
section[data-testid="stSidebar"]{display:none;}
div[data-testid="stHorizontalBlock"]{gap:0 !important;}
[data-testid="column"]{padding:0 !important;}
/* Metric overrides */
[data-testid="stMetricValue"]{font-family:'JetBrains Mono',monospace !important;font-size:1.4rem !important;color:#f0f6fc !important;}
[data-testid="stMetricLabel"]{font-size:.65rem !important;color:#6e7681 !important;text-transform:uppercase;letter-spacing:.08em;}
[data-testid="stMetricDelta"]{font-size:.72rem !important;}
div[data-testid="metric-container"]{background:#161b22;border:1px solid #21262d;border-radius:10px;padding:.8rem !important;}
/* Plotly */
.js-plotly-plot .plotly{background:#161b22 !important;}
/* Button */
.stButton>button{background:#161b22 !important;color:#58a6ff !important;border:1px solid #21262d !important;
  border-radius:8px !important;font-family:'JetBrains Mono',monospace !important;font-size:.75rem !important;
  padding:.4rem 1rem !important;width:100% !important;}
.stButton>button:hover{border-color:#58a6ff !important;}
/* Dataframe */
[data-testid="stDataFrame"]{border:1px solid #21262d !important;border-radius:8px !important;}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────
@st.cache_resource(show_spinner=False)
def get_angel():
    try:
        from angelone import AngelOneClient
        a = AngelOneClient(); a.login(); return a, None
    except Exception as e: return None, str(e)

@st.cache_data(ttl=30, show_spinner=False)
def get_data(_t=None):
    angel, err = get_angel()
    if not angel: return None, err
    try:
        from trader import compute_morning_setup, compute_signal, get_nearest_expiry
        from strategy import get_strike
        import config
        spot   = angel.get_nifty_ltp()
        setup  = compute_morning_setup(angel)
        ctx    = compute_signal(setup, spot)
        expiry = get_nearest_expiry(angel, spot)
        atm    = int(round(spot / 50) * 50)
        ti     = None
        if ctx['signal']:
            key = (ctx['zone'], ctx['bias'], ctx['signal'])
            if key in config.V17A_PARAMS:
                stype, etime, tgt, sl, sltype = config.V17A_PARAMS[key]
                expiry_dt = datetime.strptime(expiry, '%d%b%y').date()
                dte  = (expiry_dt - date.today()).days
                skip = ctx['zone'] == 'tc_to_pdh' and dte < config.TC_TO_PDH_DTE_MIN
                strike = get_strike(atm, ctx['signal'], stype)
                sym  = f"NIFTY{expiry}{strike}{ctx['signal']}"
                ltp  = None
                if not skip:
                    try:
                        time.sleep(1)
                        tok = angel.search_option_token(sym)
                        ltp = angel.get_option_ltp(tok)
                    except: pass
                ti = dict(stype=stype, etime=etime, tgt=tgt, sl=sl, sltype=sltype,
                          strike=strike, sym=sym, ltp=ltp, dte=dte, skip=skip)
        return dict(spot=spot, setup=setup, ctx=ctx, pvt=ctx['pvt'],
                    expiry=expiry, atm=atm, ti=ti), None
    except Exception as e: return None, str(e)

@st.cache_data(ttl=60, show_spinner=False)
def load_trades():
    p = os.path.join(os.path.dirname(__file__), 'data', 'live_trades.csv')
    if not os.path.exists(p): return pd.DataFrame()
    return pd.read_csv(p, parse_dates=['date'])

def make_gauge(val, label, sub, color, bgcolor="#161b22"):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number={"suffix":"%","font":{"size":22,"family":"JetBrains Mono","color":"#f0f6fc"}},
        title={"text":f"<span style='font-size:11px;color:#8b949e;font-weight:600;text-transform:uppercase;letter-spacing:.08em'>{label}</span><br><span style='font-size:9px;color:#6e7681'>{sub}</span>"},
        gauge={
            "axis":{"range":[0,100],"showticklabels":False,"tickwidth":0},
            "bar":{"color":color,"thickness":0.55},
            "bgcolor":"#21262d","borderwidth":0,
            "steps":[{"range":[0,100],"color":"#0d1117"}],
            "threshold":{"line":{"color":color,"width":3},"thickness":0.8,"value":val},
            "shape":"angular",
        },
        domain={"x":[0,1],"y":[0,1]},
    ))
    fig.update_layout(height=160, margin=dict(l=20,r=20,t=55,b=5),
        paper_bgcolor=bgcolor, plot_bgcolor=bgcolor,
        font=dict(family="JetBrains Mono", color="#c9d1d9"))
    return fig

def card(content, title="", height=None):
    h = f'height:{height}px;' if height else ''
    hdr = f'<div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;margin-bottom:.6rem;padding-bottom:.4rem;border-bottom:1px solid #21262d">{title}</div>' if title else ''
    st.markdown(f'<div style="background:#161b22;border:1px solid #21262d;border-radius:10px;padding:.8rem;{h}">{hdr}{content}</div>', unsafe_allow_html=True)

ZONES = ["above_r4","r3_to_r4","r2_to_r3","r1_to_r2","pdh_to_r1",
         "tc_to_pdh","within_cpr","pdl_to_bc","pdl_to_s1",
         "s1_to_s2","s2_to_s3","s3_to_s4","below_s4"]


# ── Fetch data ────────────────────────────────────────────────────────
data, err = get_data()
live_df   = load_trades()
now_str   = datetime.now().strftime('%H:%M:%S')
engine    = "SCANNING" if 9 <= datetime.now().hour < 15 else "CLOSED"

spot  = data['spot']   if data else 0.0
setup = data['setup']  if data else {}
ctx   = data['ctx']    if data else {}
pvt   = data['pvt']    if data else {}
ti    = data['ti']     if data else None
expiry= data['expiry'] if data else "--"
atm   = data['atm']    if data else 0

pnl_today = 0.0
if not live_df.empty and 'pnl' in live_df.columns:
    td = live_df[live_df['date'].astype(str) == date.today().isoformat()]
    if not td.empty: pnl_today = float(td['pnl'].sum())

try: oa_ok = rq.get("http://127.0.0.1:5000", timeout=2).status_code == 200
except: oa_ok = False

ao_col   = "#3fb950" if not err else "#f85149"
oa_col   = "#3fb950" if oa_ok else "#f85149"
pnl_col  = "#3fb950" if pnl_today >= 0 else "#f85149"
pnl_sign = "+" if pnl_today >= 0 else ""
bias_col = "#3fb950" if ctx.get('bias') == 'bull' else "#f85149"
zone_t   = ctx.get('zone','')
bias_t   = ctx.get('bias','')
sig_t    = ctx.get('signal','') or ''
ema_val  = setup.get('e20', 0)
pdh      = setup.get('pdh', 0)
pdl      = setup.get('pdl', 0)

if ti and not ti.get('skip') and sig_t:
    sc = "#3fb950" if sig_t=="PE" else "#f85149"
    bg = "#0d2818" if sig_t=="PE" else "#2d1111"
    sig_html = f'<span style="background:{bg};color:{sc};border:1px solid {sc};padding:.2rem .75rem;border-radius:20px;font-weight:700;font-size:.75rem;font-family:\'JetBrains Mono\',monospace">SELL {sig_t}</span>'
elif ti and ti.get('skip'):
    sig_html = '<span style="background:#271d08;color:#e3b341;border:1px solid #9e6a03;padding:.2rem .75rem;border-radius:20px;font-size:.75rem">DTE SKIP &rarr; Intraday v2</span>'
else:
    sig_html = '<span style="background:#161b22;color:#8b949e;border:1px solid #30363d;padding:.2rem .75rem;border-radius:20px;font-size:.75rem">NO SIGNAL &rarr; Intraday v2</span>'


# ══ TOP BAR ═══════════════════════════════════════════════════════════
st.markdown(f"""
<div style="background:#161b22;border-bottom:1px solid #21262d;padding:.55rem 1.5rem;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem">
  <div style="display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap">
    <span style="background:#0d2137;color:#58a6ff;border:1px solid #1f6feb;
      padding:.2rem .8rem;border-radius:20px;font-family:'JetBrains Mono',monospace;
      font-size:.72rem;font-weight:700">&#9679; AUTO TRADING: {engine}</span>
    <span style="font-size:.75rem;color:#6e7681">Spot <b style="color:#f0f6fc;font-family:'JetBrains Mono',monospace">{spot:,.2f}</b></span>
    <span style="color:#30363d">|</span>
    <span style="font-size:.75rem;color:#6e7681">{date.today().strftime("%d %b %Y")}</span>
    <span style="color:#30363d">|</span>
    <span style="font-size:.75rem;color:#6e7681">P&L <b style="color:{pnl_col};font-family:'JetBrains Mono',monospace">Rs.{pnl_sign}{pnl_today:,.0f}</b></span>
  </div>
  <div style="display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap">
    <span style="font-size:.73rem">
      <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{ao_col};
        box-shadow:0 0 5px {ao_col};margin-right:5px;vertical-align:middle"></span>
      <span style="color:#6e7681">AngelOne</span>
      <span style="font-family:'JetBrains Mono',monospace;color:#f0f6fc;margin-left:4px">PVIP1030</span>
    </span>
    <span style="font-size:.73rem">
      <span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{oa_col};
        box-shadow:0 0 5px {oa_col};margin-right:5px;vertical-align:middle"></span>
      <span style="color:#6e7681">OpenAlgo</span>
      <span style="background:{'#0d2818' if oa_ok else '#2d1111'};color:{oa_col};
        border:1px solid {oa_col};padding:.1rem .5rem;border-radius:12px;
        font-family:'JetBrains Mono',monospace;font-size:.65rem;margin-left:4px">
        {'ONLINE' if oa_ok else 'OFFLINE'}</span>
    </span>
    <span style="font-size:.73rem;color:#6e7681">Lot
      <span style="background:#0d2137;color:#58a6ff;border:1px solid #1f6feb;
        padding:.1rem .5rem;border-radius:12px;font-family:'JetBrains Mono',monospace;
        font-size:.65rem;margin-left:4px">65</span>
    </span>
    <span style="font-size:.7rem;color:#6e7681">&#128339; {now_str}</span>
  </div>
</div>
""", unsafe_allow_html=True)

# ══ METRICS BAR ═══════════════════════════════════════════════════════
st.markdown(f"""
<div style="background:#0d1117;border-bottom:1px solid #21262d;padding:.4rem 1.5rem;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.8rem">
  <div style="display:flex;align-items:center;gap:2rem;flex-wrap:wrap">
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">NIFTY</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.88rem;font-weight:700;color:#f0f6fc">{spot:,.2f}</span>
    </div>
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">EMA(20)</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.88rem;font-weight:700;color:#58a6ff">{ema_val:,.2f}</span>
    </div>
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">BIAS</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.88rem;font-weight:700;color:{bias_col}">{bias_t.upper()}</span>
    </div>
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">ZONE</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.78rem;font-weight:700;color:#e3b341">{zone_t.replace('_',' ').upper()}</span>
    </div>
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">SIGNAL</span>
      {sig_html}
    </div>
    <div>
      <span style="font-size:.6rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681;margin-right:.4rem">EXPIRY</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.78rem;color:#8b949e">{expiry}</span>
    </div>
  </div>
  <div style="display:flex;gap:.6rem">
    <div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:.25rem .9rem">
      <span style="font-size:.6rem;color:#6e7681;margin-right:.4rem">ENGINE</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#e3b341">{engine}</span>
    </div>
    <div style="background:#161b22;border:1px solid #21262d;border-radius:6px;padding:.25rem .9rem">
      <span style="font-size:.6rem;color:#6e7681;margin-right:.4rem">P&L</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:{pnl_col}">Rs.{pnl_sign}{pnl_today:,.0f}</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)


# ══ MAIN 3-COLUMN LAYOUT ══════════════════════════════════════════════
left, center, right = st.columns([1.1, 4, 1.6], gap="small")

# ── LEFT — Zone sidebar ───────────────────────────────────────────────
with left:
    st.markdown(f"""
    <div style="background:#161b22;border:1px solid #21262d;border-radius:10px;
      overflow:hidden;min-height:calc(100vh - 80px);margin:.5rem 0 0 .5rem">
      <div style="padding:.75rem .9rem;border-bottom:1px solid #21262d;display:flex;align-items:center;gap:.6rem">
        <div style="width:30px;height:30px;background:#58a6ff;border-radius:8px;
          display:flex;align-items:center;justify-content:center;font-weight:700;color:#0d1117;font-size:.85rem;flex-shrink:0">T</div>
        <div>
          <div style="font-weight:700;font-size:.82rem;color:#f0f6fc;letter-spacing:.05em">FIFTO</div>
          <div style="font-size:.58rem;color:#6e7681;text-transform:uppercase;letter-spacing:.12em">Intra Selling</div>
        </div>
      </div>
      <div style="padding:.6rem .7rem">
        <div style="font-size:.58rem;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;
          padding:.15rem .3rem;margin-bottom:.4rem">v17a Zones</div>
        {"".join(
            f'<div style="padding:.32rem .6rem;font-size:.69rem;border-radius:4px;margin:.1rem 0;'
            f'border-left:2px solid {"#58a6ff" if z==zone_t else "transparent"};'
            f'background:{"#1f6feb18" if z==zone_t else "transparent"};'
            f'color:{"#58a6ff" if z==zone_t else "#6e7681"};'
            f'font-weight:{"600" if z==zone_t else "400"};'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
            f'{">> " if z==zone_t else ""}{z.replace("_"," ").title()}</div>'
            for z in ZONES
        )}
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── CENTER ────────────────────────────────────────────────────────────
with center:
    st.markdown('<div style="padding:.5rem .3rem 0">', unsafe_allow_html=True)

    # Row 1: Levels + Trade details
    c1, c2 = st.columns([1.35, 1], gap="small")

    with c1:
        # Build levels table
        if pvt and setup and spot:
            levels = [
                ("R4", pvt.get('r4',0), "#f85149"), ("R3", pvt.get('r3',0), "#f85149"),
                ("R2", pvt.get('r2',0), "#f85149"), ("R1", pvt.get('r1',0), "#f85149"),
                ("PDH", pdh, "#e3b341"),
                ("TC", pvt.get('tc',0), "#58a6ff"), ("PP", pvt.get('pp',0), "#58a6ff"),
                ("BC", pvt.get('bc',0), "#58a6ff"),
                ("PDL", pdl, "#e3b341"),
                ("S1", pvt.get('s1',0), "#3fb950"), ("S2", pvt.get('s2',0), "#3fb950"),
                ("S3", pvt.get('s3',0), "#3fb950"), ("S4", pvt.get('s4',0), "#3fb950"),
            ]
            rows = ""
            for name, val, col in levels:
                dist  = round(spot - val, 1)
                dcol  = "#3fb950" if dist > 0 else "#f85149"
                near  = abs(dist) < 60
                bg    = "background:#1c2128;" if near else ""
                mark  = "<span style='color:#58a6ff;font-size:.62rem'>&#8592; SPOT</span>" if near else ""
                sign  = "+" if dist > 0 else ""
                rows += (f'<tr style="{bg}">'
                         f'<td style="color:{col};font-weight:600;padding:.28rem .5rem">{name}</td>'
                         f'<td style="color:#c9d1d9;padding:.28rem .5rem;font-family:\'JetBrains Mono\',monospace">{val:,.2f}</td>'
                         f'<td style="color:{dcol};padding:.28rem .5rem;font-family:\'JetBrains Mono\',monospace">{sign}{dist:,.1f}</td>'
                         f'<td style="padding:.28rem .5rem">{mark}</td></tr>')
            tbl = f"""
            <table style="width:100%;border-collapse:collapse;font-size:.75rem">
              <thead><tr>
                <th style="color:#6e7681;font-size:.6rem;text-transform:uppercase;letter-spacing:.06em;padding:.28rem .5rem;border-bottom:1px solid #21262d;text-align:left;font-weight:600">Level</th>
                <th style="color:#6e7681;font-size:.6rem;text-transform:uppercase;letter-spacing:.06em;padding:.28rem .5rem;border-bottom:1px solid #21262d;text-align:left;font-weight:600">Price</th>
                <th style="color:#6e7681;font-size:.6rem;text-transform:uppercase;letter-spacing:.06em;padding:.28rem .5rem;border-bottom:1px solid #21262d;text-align:left;font-weight:600">From Spot</th>
                <th style="border-bottom:1px solid #21262d"></th>
              </tr></thead>
              <tbody>{rows}</tbody>
            </table>"""
            card(tbl, "CPR & Pivot Levels")
        else:
            card(f'<div style="color:#f85149;font-size:.8rem">&#9888; {err}</div>', "CPR & Pivot Levels")

    with c2:
        if ti and not ti.get('skip') and sig_t:
            ltp_s  = f"Rs.{ti['ltp']:.2f}" if ti.get('ltp') else "N/A"
            sl_s   = f"Spot &gt; {pdh+ti['sl']:,.0f}" if ti['sltype']=='spot' else f"{ti['sl']}x premium"
            tgt_rs = round(ti['ltp']*ti['tgt']/100*65,0) if ti.get('ltp') else 0
            rows   = [("Symbol",ti['sym']),("Strike",f"{ti['strike']} ({ti['stype']})"),
                      ("Entry Time",ti['etime']),("LTP",ltp_s),
                      (f"Target",f"{ti['tgt']*100:.0f}% &rarr; Rs.{tgt_rs:,.0f}"),
                      ("Stop Loss",sl_s),(f"Expiry",f"{expiry} (DTE {ti['dte']})")]
            det = "".join(f'<tr><td style="color:#6e7681;font-size:.68rem;padding:.28rem .3rem;white-space:nowrap">{k}</td>'
                         f'<td style="font-family:\'JetBrains Mono\',monospace;font-size:.7rem;color:#f0f6fc;padding:.28rem .3rem">{v}</td></tr>'
                         for k,v in rows)
            card(f'<table style="width:100%;border-collapse:collapse">{det}</table>', "Trade Details")
        elif ti and ti.get('skip'):
            card(f"""
            <div style="text-align:center;padding:.8rem 0">
              <div style="font-size:1.8rem;margin-bottom:.5rem">&#9889;</div>
              <div style="color:#e3b341;font-weight:700;font-size:.85rem;margin-bottom:.6rem">Intraday v2 Active</div>
              <div style="color:#6e7681;font-size:.72rem;line-height:1.9">
                tc_to_pdh DTE={ti['dte']} (min=2)<br>
                Scan: 09:30 &ndash; 11:20<br>
                PDL / R1 / R2 / S1 / S2
              </div>
            </div>""", "Trade Details")
        else:
            card("""
            <div style="text-align:center;padding:.8rem 0">
              <div style="font-size:1.8rem;margin-bottom:.5rem">&#128269;</div>
              <div style="color:#8b949e;font-weight:700;font-size:.85rem;margin-bottom:.6rem">Intraday v2 Scan</div>
              <div style="color:#6e7681;font-size:.72rem;line-height:1.9">
                No v17a signal today<br>
                Watching: PDL / R1 / R2 / S1 / S2<br>
                Window: 09:30 &ndash; 11:20
              </div>
            </div>""", "Trade Details")

    # Row 2: Gauges
    st.markdown('<div style="margin-top:.6rem">', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:#161b22;border:1px solid #21262d;border-radius:10px;padding:.6rem .9rem .2rem">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.2rem">
        <span style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681">
          &#9679; Strategy Compass</span>
        <span style="font-size:.72rem;color:#c9d1d9">{zone_t.replace('_',' ').upper()} | {bias_t.upper()}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    trend_v = 72 if any(x in zone_t for x in ['r3','r4','above']) else 55 if 'r1' in zone_t or 'r2' in zone_t else 35
    side_v  = 65 if any(x in zone_t for x in ['cpr','pdh','pdl','within']) else 28
    rev_v   = 78 if any(x in zone_t for x in ['below','s4','s3']) else 40
    wr_v    = 68 if sig_t else 48

    g1,g2,g3,g4 = st.columns(4, gap="small")
    with g1: st.plotly_chart(make_gauge(trend_v,"Trend","of sessions","#2979ff"), use_container_width=True, config={"displayModeBar":False})
    with g2: st.plotly_chart(make_gauge(side_v,"Sideways","stable range","#42a5f5"), use_container_width=True, config={"displayModeBar":False})
    with g3: st.plotly_chart(make_gauge(rev_v,"Reversal","snap-back","#e53935"), use_container_width=True, config={"displayModeBar":False})
    with g4: st.plotly_chart(make_gauge(wr_v,"Win Rate","backtest est.","#43a047"), use_container_width=True, config={"displayModeBar":False})

    st.markdown('</div>', unsafe_allow_html=True)


# ── RIGHT — Strike + Positions + P&L ─────────────────────────────────
with right:
    st.markdown('<div style="padding:.5rem .5rem 0 0">', unsafe_allow_html=True)

    stype_d  = ti['stype'] if ti else "ATM"
    strike_d = str(ti['strike']) if ti and not ti.get('skip') else str(atm)
    opt_d    = sig_t if sig_t else "PE"
    ltp_d    = f"Rs.{ti['ltp']:.2f}" if ti and ti.get('ltp') else "--"

    # Strike card
    st.markdown(f"""
    <div style="background:#161b22;border:1px solid #21262d;border-radius:10px;padding:.8rem;margin-bottom:.55rem">
      <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;margin-bottom:.55rem">Strike Selection</div>
      <div style="background:linear-gradient(135deg,#0d2137,#1f3a5f22);border:1px solid #1f6feb44;
        border-radius:10px;padding:.9rem;text-align:center;margin-bottom:.5rem">
        <div style="font-size:.63rem;color:#8b949e">{stype_d}</div>
        <div style="font-family:'JetBrains Mono',monospace;font-size:2rem;font-weight:700;color:#58a6ff;line-height:1.1">{strike_d}</div>
        <div style="font-size:.78rem;font-weight:700;color:#58a6ff">{opt_d}</div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.35rem">
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.38rem;text-align:center">
          <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.15rem">LTP</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#e3b341">{ltp_d}</div>
        </div>
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.38rem;text-align:center">
          <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.15rem">LOT</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#58a6ff">65</div>
        </div>
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.38rem;text-align:center">
          <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.15rem">MODE</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#3fb950">PAPER</div>
        </div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.35rem;margin-top:.35rem">
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.38rem;text-align:center">
          <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.15rem">Exchange</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#58a6ff">NFO</div>
        </div>
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:.38rem;text-align:center">
          <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.15rem">Product</div>
          <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:#e3b341">MIS</div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # Live positions
    pos_rows = ""
    if not live_df.empty:
        td2 = live_df[live_df['date'].astype(str) == date.today().isoformat()]
        if not td2.empty:
            for _, r in td2.iterrows():
                sym  = str(r.get('symbol','--'))
                ep   = r.get('entry_price','--')
                pnl  = float(r.get('pnl', 0) or 0)
                rea  = str(r.get('exit_reason','open'))
                pc   = "#3fb950" if pnl >= 0 else "#f85149"
                ps   = "+" if pnl >= 0 else ""
                pos_rows += f"""
                <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;
                  padding:.4rem .6rem;margin-bottom:.28rem;display:flex;align-items:center;justify-content:space-between">
                  <div>
                    <div style="font-family:'JetBrains Mono',monospace;font-size:.63rem;color:#c9d1d9;
                      white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:120px">{sym[-16:]}</div>
                    <div style="font-size:.57rem;color:#6e7681">EP:{ep} | {rea}</div>
                  </div>
                  <div style="font-family:'JetBrains Mono',monospace;font-size:.72rem;font-weight:700;color:{pc};white-space:nowrap">{ps}Rs.{pnl:,.0f}</div>
                </div>"""

    if not pos_rows:
        pos_rows = '<div style="color:#6e7681;font-size:.72rem;text-align:center;padding:.6rem 0">No trades today</div>'

    total = float(live_df['pnl'].sum()) if not live_df.empty and 'pnl' in live_df.columns else 0
    best  = float(live_df['pnl'].max()) if not live_df.empty and 'pnl' in live_df.columns else 0
    worst = float(live_df['pnl'].min()) if not live_df.empty and 'pnl' in live_df.columns else 0

    st.markdown(f"""
    <div style="background:#161b22;border:1px solid #21262d;border-radius:10px;padding:.8rem">
      <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;margin-bottom:.55rem">Live Positions</div>
      {pos_rows}
      <div style="margin-top:.55rem;padding-top:.5rem;border-top:1px solid #21262d">
        <div style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;margin-bottom:.4rem">P&L Summary</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.35rem">
          <div style="background:#0d2818;border:1px solid #23863633;border-radius:8px;padding:.55rem .3rem;text-align:center">
            <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.2rem">Total</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:700;color:#3fb950">Rs.{total:,.0f}</div>
          </div>
          <div style="background:#2d1111;border:1px solid #da363333;border-radius:8px;padding:.55rem .3rem;text-align:center">
            <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.2rem">Worst</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:700;color:#f85149">Rs.{worst:,.0f}</div>
          </div>
          <div style="background:#0d2137;border:1px solid #1f6feb33;border-radius:8px;padding:.55rem .3rem;text-align:center">
            <div style="font-size:.55rem;text-transform:uppercase;color:#6e7681;margin-bottom:.2rem">Best</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:700;color:#58a6ff">Rs.{best:,.0f}</div>
          </div>
        </div>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div style="margin-top:.5rem"></div>', unsafe_allow_html=True)
    if st.button("&#8635; Refresh", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown('</div>', unsafe_allow_html=True)
