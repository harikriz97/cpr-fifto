"""
FIFTO Dashboard Server - Standalone HTML (port 8080)
Run: python dashboard_server.py
"""
from flask import Flask, jsonify, render_template_string
import threading, time, logging
from datetime import datetime, date
import os, sys

logging.getLogger('werkzeug').setLevel(logging.ERROR)
app = Flask(__name__)
_cache = {"data": None, "ts": 0}

def fetch():
    try:
        import time as t
        from angelone import AngelOneClient
        from trader import compute_morning_setup, compute_signal, get_nearest_expiry
        from strategy import get_strike
        import config
        a = AngelOneClient(); a.login()
        spot   = a.get_nifty_ltp()
        setup  = compute_morning_setup(a)
        ctx    = compute_signal(setup, spot)
        t.sleep(1)
        expiry = get_nearest_expiry(a, spot)
        atm    = int(round(spot/50)*50)
        pvt    = ctx['pvt']
        ti     = None
        if ctx['signal']:
            key = (ctx['zone'], ctx['bias'], ctx['signal'])
            if key in config.V17A_PARAMS:
                stype, etime, tgt, sl, sltype = config.V17A_PARAMS[key]
                expiry_dt = datetime.strptime(expiry,'%d%b%y').date()
                dte  = (expiry_dt - date.today()).days
                skip = ctx['zone']=='tc_to_pdh' and dte < config.TC_TO_PDH_DTE_MIN
                strike = get_strike(atm, ctx['signal'], stype)
                sym  = f"NIFTY{expiry}{strike}{ctx['signal']}"
                ltp  = None
                if not skip:
                    try:
                        t.sleep(1); tok = a.search_option_token(sym)
                        ltp = a.get_option_ltp(tok)
                    except: pass
                # Theory-based lot sizing preview
                lot_mult = config.LOT_HIGH_MULT if (
                    dte >= config.LOT_HIGH_DTE_MIN and ltp and ltp > config.LOT_HIGH_EP_MIN
                ) else 1
                lots_preview = config.LOT_SIZE * lot_mult
                ti = dict(stype=stype,etime=etime,tgt=tgt,sl=sl,sltype=sltype,
                          strike=strike,sym=sym,ltp=ltp,dte=dte,skip=skip,
                          lot_mult=lot_mult,lots=lots_preview)
        import pandas as pd
        path = os.path.join(os.path.dirname(__file__),'data','live_trades.csv')
        trades = []
        pnl_today = 0
        if os.path.exists(path):
            df = pd.read_csv(path)
            td = df[df['date'].astype(str)==date.today().isoformat()]
            for _,r in td.iterrows():
                pnl = float(r.get('pnl',0) or 0)
                pnl_today += pnl
                trades.append(dict(sym=str(r.get('symbol',''))[-18:],
                    ep=r.get('entry_price','--'), pnl=pnl,
                    reason=str(r.get('exit_reason','open'))))
        import requests as rq
        try: oa_ok = rq.get("http://127.0.0.1:5000",timeout=2).status_code==200
        except: oa_ok = False

        levels = [
            ("R4",pvt['r4'],"#f85149"),("R3",pvt['r3'],"#f85149"),
            ("R2",pvt['r2'],"#f85149"),("R1",pvt['r1'],"#f85149"),
            ("PDH",setup['pdh'],"#e3b341"),
            ("TC",pvt['tc'],"#58a6ff"),("PP",pvt['pp'],"#58a6ff"),("BC",pvt['bc'],"#58a6ff"),
            ("PDL",setup['pdl'],"#e3b341"),
            ("S1",pvt['s1'],"#3fb950"),("S2",pvt['s2'],"#3fb950"),
            ("S3",pvt['s3'],"#3fb950"),("S4",pvt['s4'],"#3fb950"),
        ]
        lv = [{"n":n,"v":round(v,2),"c":c,"d":round(spot-v,1)} for n,v,c in levels]

        z = ctx['zone']
        trend_v = 72 if any(x in z for x in ['r3','r4','above']) else 55 if 'r1' in z or 'r2' in z else 35
        side_v  = 65 if any(x in z for x in ['cpr','pdh','pdl','within']) else 28
        rev_v   = 78 if any(x in z for x in ['below','s4','s3']) else 40
        wr_v    = 68 if ctx['signal'] else 48

        # Fetch intraday 1-min OHLC for chart
        candles = []
        try:
            from datetime import timezone, timedelta
            IST = timezone(timedelta(hours=5, minutes=30))
            now_dt  = datetime.now()
            from_dt = now_dt.replace(hour=9, minute=15, second=0, microsecond=0)
            if now_dt.hour >= 9:
                bars = a.get_nifty_1min_ohlc(from_dt, now_dt)
                for bar in bars:
                    # Parse timestamp as IST → Unix UTC seconds
                    ts = str(bar[0]).replace('T', ' ')
                    if '+' in ts: ts = ts[:ts.index('+')]
                    ts = ts[:16]  # 'YYYY-MM-DD HH:MM'
                    dt_ist = datetime.strptime(ts, '%Y-%m-%d %H:%M').replace(tzinfo=IST)
                    unix = int(dt_ist.timestamp())
                    candles.append({
                        "time":  unix,
                        "open":  float(bar[1]),
                        "high":  float(bar[2]),
                        "low":   float(bar[3]),
                        "close": float(bar[4]),
                    })
        except: pass

        return dict(spot=spot, ema=setup['e20'], pdh=setup['pdh'], pdl=setup['pdl'],
                    zone=z, bias=ctx['bias'], signal=ctx.get('signal','') or '',
                    expiry=expiry, atm=atm, levels=lv, candles=candles,
                    ti=ti, trades=trades, pnl_today=pnl_today, oa_ok=oa_ok,
                    gauges=dict(trend=trend_v,side=side_v,rev=rev_v,wr=wr_v),
                    ts=datetime.now().strftime('%H:%M:%S'))
    except Exception as e:
        return {"error": str(e)}

def bg_fetch():
    while True:
        try:
            _cache['data'] = fetch()
            _cache['ts']   = time.time()
        except: pass
        time.sleep(30)

@app.route('/api/data')
def api_data():
    if not _cache['data'] or time.time()-_cache['ts'] > 60:
        _cache['data'] = fetch(); _cache['ts'] = time.time()
    return jsonify(_cache['data'])

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FIFTO | CPR Strategy</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap');
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;overflow:hidden;background:#0d1117;color:#c9d1d9;font-family:'Inter',sans-serif}

/* TOPBAR */
#topbar{height:40px;background:#161b22;border-bottom:1px solid #21262d;
  display:flex;align-items:center;justify-content:space-between;padding:0 1.2rem;flex-shrink:0}
.tb-l,.tb-r{display:flex;align-items:center;gap:1.2rem}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:4px;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.badge{display:inline-block;padding:.12rem .6rem;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700;border:1px solid}
.b-cyan{background:#0d2137;color:#58a6ff;border-color:#1f6feb}
.b-green{background:#0d2818;color:#3fb950;border-color:#238636}
.b-red{background:#2d1111;color:#f85149;border-color:#da3633}
.b-yellow{background:#271d08;color:#e3b341;border-color:#9e6a03}
.b-gray{background:#161b22;color:#8b949e;border-color:#30363d}
.tb-txt{font-size:.72rem;color:#6e7681}
.tb-val{font-family:'JetBrains Mono',monospace;font-size:.75rem;font-weight:600;color:#f0f6fc}
.sep{color:#30363d;margin:0 .2rem}

/* METRICS BAR */
#metbar{height:34px;background:#0d1117;border-bottom:1px solid #21262d;
  display:flex;align-items:center;justify-content:space-between;padding:0 1.2rem;flex-shrink:0}
.mi{display:flex;align-items:center;gap:.45rem}
.mi-l{font-size:.58rem;text-transform:uppercase;letter-spacing:.08em;color:#6e7681}
.mi-v{font-family:'JetBrains Mono',monospace;font-size:.82rem;font-weight:700}
.met-group{display:flex;align-items:center;gap:1.6rem}
.eng-box{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:.15rem .7rem;display:flex;align-items:center;gap:.5rem}

/* MAIN */
#main{display:grid;grid-template-columns:190px 1fr 255px;height:calc(100vh - 74px)}

/* LEFT SIDEBAR */
#lsb{background:#0d1117;border-right:1px solid #21262d;display:flex;flex-direction:column;overflow:hidden}
.sb-logo{padding:.65rem .8rem;border-bottom:1px solid #21262d;display:flex;align-items:center;gap:.55rem}
.sb-icon{width:28px;height:28px;background:#58a6ff;border-radius:7px;display:flex;align-items:center;justify-content:center;font-weight:700;color:#0d1117;font-size:.8rem;flex-shrink:0}
.sb-brand{font-weight:700;font-size:.78rem;color:#f0f6fc;letter-spacing:.04em}
.sb-sub{font-size:.55rem;color:#6e7681;text-transform:uppercase;letter-spacing:.1em}
.zone-list{flex:1;overflow-y:auto;padding:.4rem .5rem}
.zone-list::-webkit-scrollbar{width:3px}
.zone-list::-webkit-scrollbar-thumb{background:#21262d;border-radius:2px}
.zi{padding:.28rem .55rem;font-size:.67rem;border-radius:4px;margin:.08rem 0;
  border-left:2px solid transparent;color:#6e7681;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:default;transition:.15s}
.zi:hover{background:#161b22;color:#c9d1d9}
.zi.act{color:#58a6ff;background:#1f6feb14;border-left-color:#58a6ff;font-weight:600}
.zones-lbl{font-size:.56rem;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;padding:.2rem .55rem .35rem;margin-top:.25rem}

/* CENTER - chart top, levels+trade middle, compass bottom */
#center{display:flex;flex-direction:column;overflow:hidden;background:#0d1117;height:100%}
#center-body{flex:1;overflow:hidden;display:flex;flex-direction:column;min-height:0}
#center-chart{height:48%;flex-shrink:0;border-bottom:1px solid #21262d;display:flex;flex-direction:column;background:#161b22}
#center-mid{flex:1;min-height:0;display:grid;grid-template-columns:1.3fr 1fr;overflow:hidden}
#center-bottom{height:130px;flex-shrink:0;border-top:1px solid #21262d;background:#161b22;display:flex;flex-direction:column}
#chart-container{flex:1;min-height:0;position:relative}
#price-chart{width:100%;height:100%}

/* PANE headers */
.pane-hd{padding:.38rem .75rem;border-bottom:1px solid #21262d;display:flex;align-items:center;
  justify-content:space-between;background:#161b22;flex-shrink:0}
.pane-title{font-size:.58rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681}
.pane-body{padding:.45rem .65rem;overflow-y:auto;flex:1;min-height:0}
.pane-body::-webkit-scrollbar{width:3px}
.pane-body::-webkit-scrollbar-thumb{background:#21262d}
#pane-levels{border-right:1px solid #21262d;display:flex;flex-direction:column;overflow:hidden}
#pane-trade{display:flex;flex-direction:column;overflow:hidden}

/* LEVELS TABLE */
#lvl-tbl{width:100%;border-collapse:collapse;font-size:.72rem}
#lvl-tbl th{color:#6e7681;font-size:.57rem;text-transform:uppercase;letter-spacing:.06em;
  padding:.2rem .4rem;border-bottom:1px solid #21262d;text-align:left;font-weight:600;
  background:#161b22;position:sticky;top:0}
#lvl-tbl td{padding:.2rem .4rem;border-bottom:1px solid #0d1117;font-family:'JetBrains Mono',monospace}
#lvl-tbl tr:hover td{background:#1c2128}
.hl td{background:#1c2128!important;font-weight:700}

/* TRADE DETAILS */
.td-tbl{width:100%;border-collapse:collapse}
.td-tbl td{padding:.22rem .25rem;font-size:.72rem}
.td-lbl{color:#6e7681;white-space:nowrap;padding-right:.4rem!important;font-size:.67rem}
.td-val{font-family:'JetBrains Mono',monospace;color:#f0f6fc;font-size:.69rem}
.intra-box{text-align:center;padding:1rem .5rem}
.intra-icon{font-size:1.5rem;margin-bottom:.35rem}
.intra-title{font-weight:700;font-size:.8rem;margin-bottom:.4rem;color:#e3b341}
.intra-info{color:#6e7681;font-size:.68rem;line-height:1.8}

/* COMPASS - bottom strip */
.compass-strip{display:grid;grid-template-columns:auto repeat(4,1fr);align-items:center;
  padding:.35rem .7rem;gap:.5rem}
.compass-label{display:flex;flex-direction:column;gap:.15rem;padding-right:.5rem;border-right:1px solid #21262d}
.compass-gauges{display:contents}
.gauge-wrap{display:flex;align-items:center;gap:.5rem;background:#0d1117;border:1px solid #21262d;
  border-radius:6px;padding:.3rem .5rem}
.gauge-lbl{font-size:.58rem;text-transform:uppercase;letter-spacing:.07em;color:#6e7681;font-weight:600;white-space:nowrap}
.gauge-val{font-family:'JetBrains Mono',monospace;font-size:.95rem;font-weight:700;color:#f0f6fc;white-space:nowrap}
canvas.g{display:block;flex-shrink:0}

/* RIGHT PANEL */
#rpanel{background:#0d1117;border-left:1px solid #21262d;display:flex;flex-direction:column;overflow-y:auto}
#rpanel::-webkit-scrollbar{width:3px}
#rpanel::-webkit-scrollbar-thumb{background:#21262d}
.rp-sec{padding:.6rem .7rem;border-bottom:1px solid #21262d}
.rp-lbl{font-size:.56rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681;margin-bottom:.45rem}
.strike-card{background:linear-gradient(135deg,#0d2137,#1a3a6018);border:1px solid #1f6feb44;
  border-radius:8px;padding:.75rem;text-align:center;margin-bottom:.4rem;position:relative;overflow:hidden}
.strike-card::before{content:'';position:absolute;inset:0;background:linear-gradient(45deg,transparent 40%,#1f6feb08)}
.sk-type{font-size:.58rem;color:#8b949e;margin-bottom:.08rem}
.sk-val{font-family:'JetBrains Mono',monospace;font-size:1.85rem;font-weight:700;color:#58a6ff;line-height:1.05}
.sk-opt{font-size:.7rem;font-weight:700;color:#58a6ff;margin-top:.1rem}
.mini-g{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.28rem}
.mini-b{background:#161b22;border:1px solid #21262d;border-radius:5px;padding:.3rem .2rem;text-align:center}
.mini-l{font-size:.5rem;text-transform:uppercase;letter-spacing:.06em;color:#6e7681;margin-bottom:.1rem}
.mini-v{font-family:'JetBrains Mono',monospace;font-size:.66rem;font-weight:700}
.pos-row{display:flex;align-items:center;justify-content:space-between;
  background:#161b22;border-bottom:1px solid #21262d;padding:.32rem .5rem}
.pos-sym{font-family:'JetBrains Mono',monospace;font-size:.61rem;color:#c9d1d9;
  overflow:hidden;text-overflow:ellipsis;max-width:110px;white-space:nowrap}
.pos-meta{font-size:.54rem;color:#6e7681}
.pos-pnl{font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700;white-space:nowrap}
.pnl-g{display:grid;grid-template-columns:1fr 1fr 1fr;gap:.28rem;margin-top:.35rem}
.pb{border-radius:6px;padding:.45rem .2rem;text-align:center}
.pb-l{font-size:.5rem;text-transform:uppercase;letter-spacing:.06em;color:#6e7681;margin-bottom:.15rem}
.pb-v{font-family:'JetBrains Mono',monospace;font-size:.78rem;font-weight:700}
.refresh-btn{width:calc(100% - 1.2rem);margin:.45rem .6rem;padding:.4rem;background:#161b22;
  border:1px solid #21262d;border-radius:6px;color:#58a6ff;font-family:'JetBrains Mono',monospace;
  font-size:.7rem;cursor:pointer;transition:.15s}
.refresh-btn:hover{border-color:#58a6ff;background:#1f6feb12}
.loading{color:#6e7681;font-size:.72rem;text-align:center;padding:.8rem}
</style>
</head>
<body>

<div id="topbar">
  <div class="tb-l">
    <span class="badge b-cyan" id="engine-badge">&#9679; LOADING...</span>
    <span class="tb-txt">Spot <span class="tb-val" id="tb-spot">--</span></span>
    <span class="sep">|</span>
    <span class="tb-txt" id="tb-date">--</span>
    <span class="sep">|</span>
    <span class="tb-txt">P&L <b id="tb-pnl" style="font-family:'JetBrains Mono',monospace">Rs.0</b></span>
  </div>
  <div class="tb-r">
    <span><span class="dot" id="ao-dot" style="background:#6e7681"></span><span class="tb-txt">AngelOne</span> <span class="tb-val" style="margin-left:3px">PVIP1030</span></span>
    <span><span class="dot" id="oa-dot" style="background:#6e7681"></span><span class="tb-txt">OpenAlgo</span> <span class="badge b-gray" id="oa-badge" style="margin-left:3px">--</span></span>
    <span class="tb-txt">Lot <span class="badge b-cyan" style="margin-left:3px">65</span></span>
    <span class="tb-txt" id="tb-time">--</span>
  </div>
</div>

<div id="metbar">
  <div class="met-group">
    <div class="mi"><span class="mi-l">NIFTY</span><span class="mi-v" id="m-nifty" style="color:#f0f6fc">--</span></div>
    <div class="mi"><span class="mi-l">EMA(20)</span><span class="mi-v" id="m-ema" style="color:#58a6ff">--</span></div>
    <div class="mi"><span class="mi-l">Bias</span><span class="mi-v" id="m-bias">--</span></div>
    <div class="mi"><span class="mi-l">Zone</span><span class="mi-v" id="m-zone" style="color:#e3b341;font-size:.72rem">--</span></div>
    <div class="mi"><span class="mi-l">Signal</span><span id="m-signal">--</span></div>
    <div class="mi"><span class="mi-l">Expiry</span><span class="mi-v" id="m-expiry" style="color:#8b949e;font-size:.72rem">--</span></div>
  </div>
  <div style="display:flex;gap:.5rem">
    <div class="eng-box"><span class="mi-l">ENGINE</span><span class="mi-v" id="m-engine" style="color:#e3b341">--</span></div>
    <div class="eng-box"><span class="mi-l">P&L</span><span class="mi-v" id="m-pnl" style="font-size:.72rem">Rs.0</span></div>
  </div>
</div>

<div id="main">
  <div id="lsb">
    <div class="sb-logo">
      <div class="sb-icon">T</div>
      <div><div class="sb-brand">FIFTO</div><div class="sb-sub">Intra Selling</div></div>
    </div>
    <div class="zone-list">
      <div class="zones-lbl">v17a Zones</div>
      <div id="zone-list"></div>
    </div>
  </div>

  <div id="center">
    <div id="center-body">

      <!-- 1. Chart (top, full width) -->
      <div id="center-chart">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:.28rem .75rem;border-bottom:1px solid #21262d;flex-shrink:0">
          <span style="font-size:.55rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681">&#9679; NIFTY Intraday</span>
          <div style="display:flex;align-items:center;gap:.8rem">
            <span style="font-size:.58rem;color:#6e7681"><span style="display:inline-block;width:10px;height:2px;background:#f85149;margin-right:3px;vertical-align:middle"></span>Resistance</span>
            <span style="font-size:.58rem;color:#6e7681"><span style="display:inline-block;width:10px;height:2px;background:#58a6ff;margin-right:3px;vertical-align:middle"></span>CPR</span>
            <span style="font-size:.58rem;color:#6e7681"><span style="display:inline-block;width:10px;height:2px;background:#3fb950;margin-right:3px;vertical-align:middle"></span>Support</span>
            <span style="font-size:.58rem;color:#6e7681"><span style="display:inline-block;width:10px;height:2px;background:#e3b341;margin-right:3px;vertical-align:middle"></span>PDH/PDL</span>
            <span id="chart-spot" style="font-family:'JetBrains Mono',monospace;font-size:.68rem;font-weight:700;color:#f0f6fc">--</span>
          </div>
        </div>
        <div id="chart-container">
          <div id="price-chart"></div>
        </div>
      </div>

      <!-- 2. Levels (left) + Trade details (right) -->
      <div id="center-mid">
        <div id="pane-levels">
          <div class="pane-hd">
            <span class="pane-title">CPR & Pivot Levels</span>
            <span style="font-family:'JetBrains Mono',monospace;font-size:.62rem;color:#58a6ff" id="spot-tag">--</span>
          </div>
          <div class="pane-body">
            <table id="lvl-tbl">
              <thead><tr><th>Level</th><th>Price</th><th>From Spot</th><th></th></tr></thead>
              <tbody id="lvl-body"></tbody>
            </table>
          </div>
        </div>
        <div id="pane-trade">
          <div class="pane-hd"><span class="pane-title">Trade Details</span></div>
          <div class="pane-body" id="trade-details"><div class="loading">Loading...</div></div>
        </div>
      </div>

      <!-- 3. Compass (bottom strip, bigger) -->
      <div id="center-bottom">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:.28rem .75rem;border-bottom:1px solid #21262d;flex-shrink:0">
          <span style="font-size:.55rem;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:#6e7681">&#9679; Strategy Compass</span>
          <span style="font-size:.67rem;color:#c9d1d9" id="compass-title">--</span>
        </div>
        <div id="gauges-row" style="display:flex;align-items:center;justify-content:center;gap:1.5rem;flex:1;padding:0 1rem"></div>
      </div>

    </div>
  </div>

  <div id="rpanel">
    <div class="rp-sec">
      <div class="rp-lbl">Strike Selection</div>
      <div class="strike-card">
        <div class="sk-type" id="rp-stype">--</div>
        <div class="sk-val" id="rp-strike">--</div>
        <div class="sk-opt" id="rp-opt">--</div>
      </div>
      <div class="mini-g">
        <div class="mini-b"><div class="mini-l">LTP</div><div class="mini-v" id="rp-ltp" style="color:#e3b341">--</div></div>
        <div class="mini-b"><div class="mini-l">Lots</div><div class="mini-v" id="rp-lots" style="color:#58a6ff">65</div></div>
        <div class="mini-b"><div class="mini-l">Mult</div><div class="mini-v" id="rp-mult" style="color:#3fb950">1x</div></div>
      </div>
      <div class="mini-g" style="margin-top:.3rem">
        <div class="mini-b"><div class="mini-l">Exchange</div><div class="mini-v" style="color:#58a6ff">NFO</div></div>
        <div class="mini-b"><div class="mini-l">Mode</div><div class="mini-v" style="color:#3fb950">PAPER</div></div>
        <div class="mini-b"><div class="mini-l">Expiry</div><div class="mini-v" id="rp-expiry" style="color:#8b949e;font-size:.58rem">--</div></div>
      </div>
    </div>
    <div class="rp-sec" style="flex:1">
      <div class="rp-lbl">Live Positions</div>
      <div id="pos-list"><div class="loading">No trades today</div></div>
    </div>
    <div class="rp-sec">
      <div class="rp-lbl">P&L Summary</div>
      <div class="pnl-g">
        <div class="pb" style="background:#0d2818;border:1px solid #23863633">
          <div class="pb-l">Total</div><div class="pb-v" id="pnl-total" style="color:#3fb950">Rs.0</div></div>
        <div class="pb" style="background:#2d1111;border:1px solid #da363333">
          <div class="pb-l">Worst</div><div class="pb-v" id="pnl-worst" style="color:#f85149">Rs.0</div></div>
        <div class="pb" style="background:#0d2137;border:1px solid #1f6feb33">
          <div class="pb-l">Best</div><div class="pb-v" id="pnl-best" style="color:#58a6ff">Rs.0</div></div>
      </div>
    </div>
    <button class="refresh-btn" onclick="loadData()">&#8635; Refresh</button>
  </div>
</div>

<script src="https://unpkg.com/lightweight-charts@4.1.3/dist/lightweight-charts.standalone.production.js"></script>
<script>
const ZONES=['above_r4','r3_to_r4','r2_to_r3','r1_to_r2','pdh_to_r1','tc_to_pdh','within_cpr','pdl_to_bc','pdl_to_s1','s1_to_s2','s2_to_s3','s3_to_s4','below_s4'];

// Chart setup
let _chart = null, _candleSeries = null, _priceLines = [];

const IST_OFFSET = 5.5 * 3600; // IST = UTC+5:30 in seconds

function istFmt(unixUtc) {
  const d = new Date((unixUtc + IST_OFFSET) * 1000);
  return d.getUTCHours().toString().padStart(2,'0') + ':' + d.getUTCMinutes().toString().padStart(2,'0');
}

function initChart() {
  const el = document.getElementById('price-chart');
  if (!el || _chart) return;
  _chart = LightweightCharts.createChart(el, {
    layout: { background: { color: '#161b22' }, textColor: '#8b949e' },
    grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: '#21262d', textColor: '#8b949e' },
    timeScale: {
      borderColor: '#21262d', timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (t) => istFmt(t),
    },
    localization: {
      timeFormatter: (t) => istFmt(t),
    },
    handleScroll: true, handleScale: true,
  });
  _candleSeries = _chart.addCandlestickSeries({
    upColor: '#3fb950', downColor: '#f85149',
    borderUpColor: '#3fb950', borderDownColor: '#f85149',
    wickUpColor: '#3fb950', wickDownColor: '#f85149',
  });
  new ResizeObserver(() => {
    const c = document.getElementById('chart-container');
    if (c && _chart) _chart.resize(c.clientWidth, c.clientHeight);
  }).observe(document.getElementById('chart-container'));
}

function clearPriceLines() {
  _priceLines.forEach(pl => { try { _candleSeries.removePriceLine(pl); } catch(e){} });
  _priceLines = [];
}

function updateChart(d) {
  if (!_chart) initChart();
  if (!_chart) return;

  // Candles — time is already Unix UTC from Python
  if (d.candles && d.candles.length > 0) {
    const data = d.candles
      .filter(c => typeof c.time === 'number' && !isNaN(c.time))
      .sort((a,b) => a.time - b.time);
    if (data.length) _candleSeries.setData(data);
  }

  // Remove ALL old price lines before redrawing
  clearPriceLines();

  // Spot line
  _priceLines.push(_candleSeries.createPriceLine({
    price: d.spot, color: '#ffffff', lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: 'SPOT'
  }));

  // Pivot levels — one line each, no duplicates
  d.levels.forEach(lv => {
    _priceLines.push(_candleSeries.createPriceLine({
      price: lv.v, color: lv.c, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true, title: lv.n
    }));
  });

  document.getElementById('chart-spot').textContent = d.spot.toLocaleString('en-IN',{minimumFractionDigits:2});
  _chart.timeScale().fitContent();
}

function drawGauge(canvas, pct, color) {
  const ctx=canvas.getContext('2d'), w=canvas.width, h=canvas.height;
  const cx=w/2, cy=h-6, r=h*0.82;
  ctx.clearRect(0,0,w,h);
  // track
  ctx.beginPath(); ctx.arc(cx,cy,r,Math.PI,2*Math.PI);
  ctx.strokeStyle='#21262d'; ctx.lineWidth=9; ctx.lineCap='round'; ctx.stroke();
  // fill
  if(pct>0){
    const grad=ctx.createLinearGradient(cx-r,cy,cx+r,cy);
    grad.addColorStop(0,color+'88'); grad.addColorStop(1,color);
    ctx.beginPath(); ctx.arc(cx,cy,r,Math.PI,Math.PI+pct/100*Math.PI);
    ctx.strokeStyle=grad; ctx.lineWidth=9; ctx.lineCap='round'; ctx.stroke();
  }
  // glow
  ctx.shadowColor=color; ctx.shadowBlur=8;
  ctx.beginPath(); ctx.arc(cx,cy,r,Math.PI+pct/100*Math.PI-0.05,Math.PI+pct/100*Math.PI);
  ctx.strokeStyle=color; ctx.lineWidth=9; ctx.lineCap='round'; ctx.stroke();
  ctx.shadowBlur=0;
  // needle
  const ang=Math.PI+pct/100*Math.PI;
  ctx.beginPath(); ctx.moveTo(cx,cy);
  ctx.lineTo(cx+Math.cos(ang)*r*0.78, cy+Math.sin(ang)*r*0.78);
  ctx.strokeStyle='#fff'; ctx.lineWidth=1.5; ctx.lineCap='round'; ctx.stroke();
  ctx.beginPath(); ctx.arc(cx,cy,4,0,2*Math.PI); ctx.fillStyle='#fff'; ctx.fill();
  ctx.beginPath(); ctx.arc(cx,cy,2.5,0,2*Math.PI); ctx.fillStyle=color; ctx.fill();
}

function makeGauge(label,val,sub,color){
  const div=document.createElement('div');
  div.style.cssText='display:flex;align-items:center;gap:.6rem;padding:.4rem .9rem;background:#0d1117;border:1px solid #21262d;border-radius:8px;';
  div.innerHTML=`<canvas class="g" id="gc-${label}" width="86" height="54"></canvas>
    <div style="display:flex;flex-direction:column;justify-content:center">
      <div style="font-size:.6rem;text-transform:uppercase;letter-spacing:.07em;color:#6e7681;font-weight:600">${label}</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:1.3rem;font-weight:700;color:${color};line-height:1.15">${val}%</div>
      <div style="font-size:.57rem;color:#6e7681;margin-top:.08rem">${sub}</div>
    </div>`;
  return {div, draw:()=>drawGauge(div.querySelector('canvas'),val,color)};
}

function sigBadge(ti, signal) {
  if(!signal) return '<span class="badge b-gray">NO SIGNAL &rarr; Intraday v2</span>';
  if(ti && ti.skip) return '<span class="badge b-yellow">DTE SKIP &rarr; Intraday v2</span>';
  const sc=signal==='PE'?'#3fb950':'#f85149';
  const bg=signal==='PE'?'#0d2818':'#2d1111';
  return `<span style="background:${bg};color:${sc};border:1px solid ${sc};padding:.15rem .7rem;border-radius:20px;font-family:'JetBrains Mono',monospace;font-size:.7rem;font-weight:700">SELL ${signal}</span>`;
}

function render(d) {
  const now=new Date();
  document.getElementById('tb-date').textContent=now.toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
  document.getElementById('tb-time').textContent='🕐 '+d.ts;
  document.getElementById('tb-spot').textContent=d.spot.toLocaleString('en-IN',{minimumFractionDigits:2});
  const pc=d.pnl_today>=0?'#3fb950':'#f85149';
  const ps=d.pnl_today>=0?'+':'';
  document.getElementById('tb-pnl').style.color=pc;
  document.getElementById('tb-pnl').textContent=`Rs.${ps}${d.pnl_today.toFixed(0)}`;
  document.getElementById('m-pnl').style.color=pc;
  document.getElementById('m-pnl').textContent=`Rs.${ps}${d.pnl_today.toFixed(0)}`;

  const engine=new Date().getHours()>=9&&new Date().getHours()<15?'SCANNING':'CLOSED';
  document.getElementById('engine-badge').textContent=`● AUTO TRADING: ${engine}`;
  document.getElementById('m-engine').textContent=engine;

  // AO online (we got data = online)
  const aoDot=document.getElementById('ao-dot');
  aoDot.style.background='#3fb950'; aoDot.style.boxShadow='0 0 5px #3fb950';

  // OA status
  const oaDot=document.getElementById('oa-dot'), oaBadge=document.getElementById('oa-badge');
  if(d.oa_ok){
    oaDot.style.background='#3fb950'; oaDot.style.boxShadow='0 0 5px #3fb950';
    oaBadge.className='badge b-green'; oaBadge.textContent='ONLINE';
  } else {
    oaDot.style.background='#f85149'; oaDot.style.boxShadow='0 0 5px #f85149';
    oaBadge.className='badge b-red'; oaBadge.textContent='OFFLINE';
  }

  document.getElementById('spot-tag').textContent=`Spot: ${d.spot.toFixed(2)}`;
  document.getElementById('m-nifty').textContent=d.spot.toLocaleString('en-IN',{minimumFractionDigits:2});
  document.getElementById('m-ema').textContent=d.ema.toFixed(2);
  const bc=d.bias==='bull'?'#3fb950':'#f85149';
  document.getElementById('m-bias').style.color=bc;
  document.getElementById('m-bias').textContent=d.bias.toUpperCase();
  document.getElementById('m-zone').textContent=d.zone.replace(/_/g,' ').toUpperCase();
  document.getElementById('m-expiry').textContent=d.expiry;
  document.getElementById('m-signal').innerHTML=sigBadge(d.ti,d.signal);

  // Zone list
  const zl=document.getElementById('zone-list'); zl.innerHTML='';
  ZONES.forEach(z=>{
    const div=document.createElement('div');
    div.className='zi'+(z===d.zone?' act':'');
    div.textContent=(z===d.zone?'>> ':' ')+z.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
    zl.appendChild(div);
  });

  // Levels table
  const tbody=document.getElementById('lvl-body'); tbody.innerHTML='';
  d.levels.forEach(lv=>{
    const near=Math.abs(lv.d)<60;
    const dcol=lv.d>0?'#3fb950':'#f85149';
    const sign=lv.d>0?'+':'';
    const tr=document.createElement('tr');
    if(near) tr.className='hl';
    tr.innerHTML=`<td style="color:${lv.c};font-weight:600">${lv.n}</td>
      <td style="color:#c9d1d9">${lv.v.toLocaleString('en-IN',{minimumFractionDigits:2})}</td>
      <td style="color:${dcol}">${sign}${lv.d.toFixed(1)}</td>
      <td style="color:#58a6ff;font-size:.62rem">${near?'&#8592; SPOT':''}</td>`;
    tbody.appendChild(tr);
  });

  // Trade details
  const tdiv=document.getElementById('trade-details');
  if(d.ti && !d.ti.skip && d.signal){
    const ltp=d.ti.ltp?`Rs.${d.ti.ltp.toFixed(2)}`:'N/A';
    const sl=d.ti.sltype==='spot'?`Spot > ${(d.ti.sl+d.levels.find(l=>l.n==='PDH')?.v||0).toFixed(0)}`:`${d.ti.sl}x premium`;
    const tgt_rs=d.ti.ltp?Math.round(d.ti.ltp*d.ti.tgt/100*65):0;
    tdiv.innerHTML=`<table class="td-tbl">
      <tr><td class="td-lbl">Symbol</td><td class="td-val" style="font-size:.65rem">${d.ti.sym}</td></tr>
      <tr><td class="td-lbl">Strike</td><td class="td-val" style="color:#58a6ff">${d.ti.strike} (${d.ti.stype})</td></tr>
      <tr><td class="td-lbl">Entry</td><td class="td-val" style="color:#e3b341">${d.ti.etime}</td></tr>
      <tr><td class="td-lbl">LTP</td><td class="td-val">${ltp}</td></tr>
      <tr><td class="td-lbl">Target</td><td class="td-val" style="color:#3fb950">${(d.ti.tgt*100).toFixed(0)}% &rarr; Rs.${tgt_rs}</td></tr>
      <tr><td class="td-lbl">Stop Loss</td><td class="td-val" style="color:#f85149">${sl}</td></tr>
      <tr><td class="td-lbl">Expiry</td><td class="td-val" style="color:#8b949e">${d.expiry} (DTE ${d.ti.dte})</td></tr>
    </table>`;
  } else if(d.ti && d.ti.skip) {
    tdiv.innerHTML=`<div class="intra-box">
      <div class="intra-icon">&#9889;</div>
      <div class="intra-title">Intraday v2 Active</div>
      <div class="intra-info">tc_to_pdh DTE=${d.ti.dte} (min=2)<br>Scan: 09:30 &ndash; 11:20<br>PDL / R1 / R2 / S1 / S2</div>
    </div>`;
  } else {
    tdiv.innerHTML=`<div class="intra-box">
      <div class="intra-icon">&#128269;</div>
      <div class="intra-title" style="color:#8b949e">Intraday v2 Scan</div>
      <div class="intra-info">No v17a signal<br>Scan: 09:30 &ndash; 11:20<br>PDL / R1 / R2 / S1 / S2</div>
    </div>`;
  }

  // Compass
  document.getElementById('compass-title').textContent=`${d.zone.replace(/_/g,' ').toUpperCase()} | ${d.bias.toUpperCase()}`;
  const gr=document.getElementById('gauges-row'); gr.innerHTML='';
  const gauges=[
    {label:'Trend',val:d.gauges.trend,sub:'of sessions',color:'#2979ff'},
    {label:'Sideways',val:d.gauges.side,sub:'stable range',color:'#42a5f5'},
    {label:'Reversal',val:d.gauges.rev,sub:'snap-back risk',color:'#e53935'},
    {label:'Win Rate',val:d.gauges.wr,sub:'backtest est.',color:'#43a047'},
  ];
  gauges.forEach(g=>{
    const {div,draw}=makeGauge(g.label,g.val,g.sub,g.color);
    gr.appendChild(div);
    requestAnimationFrame(draw);
  });

  // Right panel
  const rp=d.ti;
  document.getElementById('rp-stype').textContent=rp?rp.stype:'ATM';
  document.getElementById('rp-strike').textContent=rp&&!rp.skip?rp.strike:d.atm;
  document.getElementById('rp-opt').textContent=d.signal||'PE';
  document.getElementById('rp-ltp').textContent=rp&&rp.ltp?`Rs.${rp.ltp.toFixed(2)}`:'--';
  document.getElementById('rp-expiry').textContent=d.expiry;
  // Lot sizing display
  const mult = rp&&rp.lot_mult?rp.lot_mult:1;
  const lots = rp&&rp.lots?rp.lots:65;
  const lotsEl = document.getElementById('rp-lots');
  const multEl = document.getElementById('rp-mult');
  if(lotsEl) { lotsEl.textContent=lots; lotsEl.style.color=mult>1?'#e3b341':'#58a6ff'; }
  if(multEl) { multEl.textContent=mult+'x'; multEl.style.color=mult>1?'#e3b341':'#3fb950'; }

  // Positions
  const pl=document.getElementById('pos-list'); pl.innerHTML='';
  if(d.trades.length===0){
    pl.innerHTML='<div class="loading">No trades today</div>';
  } else {
    d.trades.forEach(t=>{
      const pc=t.pnl>=0?'#3fb950':'#f85149', ps=t.pnl>=0?'+':'';
      const row=document.createElement('div'); row.className='pos-row';
      row.innerHTML=`<div><div class="pos-sym">${t.sym}</div><div class="pos-meta">EP:${t.ep} | ${t.reason}</div></div>
        <div class="pos-pnl" style="color:${pc}">${ps}Rs.${t.pnl.toFixed(0)}</div>`;
      pl.appendChild(row);
    });
  }

  // PnL summary
  const pnls=d.trades.map(t=>t.pnl);
  const tot=pnls.reduce((a,b)=>a+b,0);
  const best=pnls.length?Math.max(...pnls):0;
  const worst=pnls.length?Math.min(...pnls):0;
  document.getElementById('pnl-total').textContent=`Rs.${tot.toFixed(0)}`;
  document.getElementById('pnl-best').textContent=`Rs.${best.toFixed(0)}`;
  document.getElementById('pnl-worst').textContent=`Rs.${worst.toFixed(0)}`;

  // Update chart
  updateChart(d);
}

async function loadData(){
  try{
    const r=await fetch('/api/data');
    const d=await r.json();
    if(d.error) console.error(d.error);
    else render(d);
  } catch(e){ console.error(e); }
}

loadData();
setInterval(loadData, 30000);
setInterval(()=>{
  const t=document.getElementById('tb-time');
  if(t) t.textContent='🕐 '+new Date().toTimeString().slice(0,8);
},1000);
</script>
</body>
</html>"""

@app.route('/')
def index():
    return render_template_string(HTML)

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(__file__))
    t = threading.Thread(target=bg_fetch, daemon=True)
    t.start()
    print("FIFTO Dashboard: http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, debug=False)
