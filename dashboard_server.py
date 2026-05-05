"""
FIFTO Dashboard Server - Standalone HTML (port 8080)
Run: python dashboard_server.py
"""
from flask import Flask, jsonify, render_template_string, Response
import threading, time, logging
from datetime import datetime, date
import os, sys

logging.getLogger('werkzeug').setLevel(logging.ERROR)

def _zero_feats():
    return dict(score=0, inside_cpr=False, vix_ok=False,
                cpr_trend_aligned=False, consec_aligned=False,
                cpr_gap_aligned=False, dte_sweet=False,
                cpr_narrow=False, cpr_dir_aligned=False)

def _strip_file_handlers():
    """Remove FileHandlers from root logger so dashboard doesn't write to trader log."""
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.FileHandler)]
app = Flask(__name__)
_cache  = {"data": None, "ts": 0}
_angel  = {"client": None, "ts": 0}   # reuse session, re-login only if stale

def _get_angel():
    """Return cached AngelOne client; re-login if older than 6 hours."""
    from angelone import AngelOneClient
    now = time.time()
    if _angel["client"] is None or (now - _angel["ts"]) > 21600:
        a = AngelOneClient()
        a.login()
        _angel["client"] = a
        _angel["ts"] = now
    return _angel["client"]

def fetch():
    try:
        import time as t
        import config
        import pandas as _pd
        from core.levels import compute_cpr, compute_ema_series, classify_zone, ema_bias, r2
        from core.signals import v17a_signal, v17a_params
        from live.orders import build_option_symbol, get_ltp, get_strike
        from live.trader import get_expiry

        _strip_file_handlers()

        # ── Get OHLC history from AngelOne ────────────────────────────
        a       = _get_angel()
        spot    = a.get_nifty_ltp()
        history = a.get_nifty_ohlc_history(days=50)
        now     = datetime.now()

        # Build prev-day data
        today_str = date.today().strftime('%Y-%m-%d')
        last = str(history[-1].get('date',''))
        if today_str in last:
            prev    = history[-2]
            closes  = [d['close'] for d in history[:-1]]
        else:
            prev    = history[-1]
            closes  = [d['close'] for d in history]

        pvt = compute_cpr(prev['high'], prev['low'], prev['close'])
        pdh = r2(prev['high']); pdl = r2(prev['low'])

        # EMA — use shifted value (prev EMA = signal input, no forward bias)
        s = _pd.Series([d['close'] for d in history])
        ema_series = compute_ema_series(s).shift(1)
        ema_prev   = round(float(ema_series.iloc[-1]), 2)
        prev_body  = r2(abs(prev['close'] - prev['open']) / prev['open'] * 100)

        # ── Confluence (conviction) features ──────────────────────────
        from core.levels import compute_features
        try:
            hist_rows = []
            for h in history:
                c = compute_cpr(h['high'], h['low'], h['close'])
                hist_rows.append(dict(date=str(h['date'])[:10], open=h['open'],
                    high=h['high'], low=h['low'], close=h['close'], vix=0.0,
                    **c, pvt=c['pvt'], cpr_mid=(c['tc']+c['bc'])/2))
            hist_df = _pd.DataFrame(hist_rows)
            hist_df['ema'] = compute_ema_series(hist_df['close']).shift(1)
            hist_clean = hist_df.dropna(subset=['ema']).reset_index(drop=True)
            feats = compute_features(hist_clean, len(hist_clean)-1) \
                    if len(hist_clean) >= 4 else _zero_feats()
        except Exception:
            feats = _zero_feats()
        lots_preview = config.score_to_lots(feats['score'], feats['inside_cpr'])

        # ── Signal (only after 9:15 open) ─────────────────────────────
        market_open = now.hour > 9 or (now.hour == 9 and now.minute >= 15)
        if market_open:
            zone  = classify_zone(spot, pvt, pdh, pdl)
            bias  = ema_bias(ema_prev, spot)
            opt, sig_zone, etime = v17a_signal(spot, pvt, pdh, pdl, ema_prev)
            if prev_body <= config.BODY_MIN:
                opt, sig_zone, etime = None, zone, ''
            ctx = dict(zone=zone, bias=bias, signal=opt,
                       pvt=pvt, pdh=pdh, pdl=pdl, spot_open=r2(spot), e20=ema_prev)
        else:
            ctx = dict(zone='pre_market', bias='--', signal=None,
                       pvt=pvt, pdh=pdh, pdl=pdl, spot_open=r2(spot), e20=ema_prev)

        # ── Trade info ─────────────────────────────────────────────────
        expiry = get_expiry("NIFTY")               # YYYYMMDD format
        expiry_dt = datetime.strptime(expiry, '%Y%m%d').date()
        dte       = (expiry_dt - date.today()).days
        atm       = int(round(spot / config.INDICES["NIFTY"]["strike_int"]) * config.INDICES["NIFTY"]["strike_int"])

        ti = None
        if ctx['signal']:
            zone = ctx['zone']
            if zone in config.V17A_PARAMS:
                opt_p, stype, tgt, sl, etime = config.V17A_PARAMS[zone]
                opt = ctx['signal']
                skip = zone == 'tc_to_pdh' and dte < config.TC_TO_PDH_DTE_MIN
                strike = get_strike(spot, "NIFTY", opt, stype)
                sym    = build_option_symbol("NIFTY", expiry, strike, opt)
                ltp    = None
                if not skip:
                    try: t.sleep(0.5); ltp = get_ltp("NIFTY", expiry, strike, opt)
                    except: pass
                ti = dict(stype=stype, etime=etime, tgt=tgt, sl=sl, sltype='pct',
                          strike=strike, sym=sym, ltp=ltp, dte=dte, skip=skip,
                          lot_mult=lots_preview,
                          lots=lots_preview * config.INDICES["NIFTY"]["lot_size"])
        import pandas as pd
        DATA = os.path.dirname(__file__) + '/data'
        trades = []
        pnl_today = 0
        # Read both live_trades.csv (old trader) and paper_trades.csv (paper_trader)
        for fname, ep_col, sym_col in [
            ('live_trades.csv',  'entry_price', 'symbol'),
            ('paper_trades.csv', 'entry_price', 'strategy'),
        ]:
            path = os.path.join(DATA, fname)
            if not os.path.exists(path): continue
            try:
                df = pd.read_csv(path)
                today_str = date.today().strftime('%Y%m%d')
                mask = df['date'].astype(str).str.replace('-','').str[:8] == today_str
                td = df[mask]
                for _,r in td.iterrows():
                    pnl = float(r.get('pnl',0) or 0)
                    pnl_today += pnl
                    signal_name = str(r.get('signal', r.get('source','')))
                    sym = str(r.get(sym_col,''))[-18:]
                    trades.append(dict(
                        sym=sym, ep=r.get(ep_col,'--'), pnl=pnl,
                        reason=str(r.get('exit_reason','open')),
                        signal=signal_name,
                        lots=int(r.get('lots',1) or 1),
                        score=int(r.get('score',0) or 0),
                    ))
            except Exception: pass
        import requests as rq
        try: oa_ok = rq.get("http://127.0.0.1:5000",timeout=2).status_code==200
        except: oa_ok = False

        levels = [
            ("R4",pvt['r4'],"#f85149"),("R3",pvt['r3'],"#f85149"),
            ("R2",pvt['r2'],"#f85149"),("R1",pvt['r1'],"#f85149"),
            ("PDH",pdh,"#e3b341"),
            ("TC",pvt['tc'],"#58a6ff"),("PP",pvt['pvt'],"#58a6ff"),("BC",pvt['bc'],"#58a6ff"),
            ("PDL",pdl,"#e3b341"),
            ("S1",pvt['s1'],"#3fb950"),("S2",pvt['s2'],"#3fb950"),
            ("S3",pvt['s3'],"#3fb950"),("S4",pvt['s4'],"#3fb950"),
        ]
        lv = [{"n":n,"v":round(v,2),"c":c,"d":round(spot-v,1)} for n,v,c in levels]

        z = ctx['zone']
        trend_v = 72 if any(x in z for x in ['r2_plus','r1_to_r2']) else 55 if 'pdh' in z else 35
        side_v  = 65 if any(x in z for x in ['cpr','pdh','pdl','within','bc']) else 28
        rev_v   = 78 if any(x in z for x in ['below','s4','s3','s2']) else 40
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

        # Read live trade state from trader.py
        live_state = None
        try:
            import json as _json
            sp = os.path.join(os.path.dirname(__file__), 'data', 'live_state.json')
            if os.path.exists(sp):
                with open(sp) as f: live_state = _json.load(f)
            # Enrich with OpenAlgo live position data (ground truth for lots/pnl)
            if live_state and live_state.get('status') == 'open':
                try:
                    oa_resp = rq.post(f"{config.OPENALGO_HOST}/api/v1/positionbook",
                        json={'apikey': config.OPENALGO_API_KEY}, timeout=3)
                    oa_pos = oa_resp.json().get('data', [])
                    sym = live_state.get('symbol', '')
                    for p in oa_pos:
                        if p.get('symbol') == sym and int(p.get('quantity', 0)) < 0:
                            actual_lots = abs(int(p['quantity']))
                            ltp = float(p.get('ltp', live_state.get('current', 0)))
                            ep  = float(p.get('average_price', live_state.get('entry', 0)))
                            live_state['lots']    = actual_lots
                            live_state['current'] = ltp
                            live_state['upnl']    = round((ep - ltp) * actual_lots, 0)
                            live_state['entry']   = ep
                            live_state['oa_pnl']  = round(float(p.get('pnl', 0)), 0)
                            break
                except: pass
        except: pass

        # Expiry display format (YYYYMMDD → DDMMMYY for display)
        expiry_disp = datetime.strptime(expiry,'%Y%m%d').strftime('%d%b%y').upper()

        return dict(spot=spot, ema=ema_prev, pdh=pdh, pdl=pdl,
                    zone=z, bias=ctx['bias'], signal=ctx.get('signal','') or '',
                    expiry=expiry_disp, atm=atm, levels=lv, candles=candles,
                    ti=ti, trades=trades, pnl_today=pnl_today, oa_ok=oa_ok,
                    gauges=dict(trend=trend_v,side=side_v,rev=rev_v,wr=wr_v),
                    feats=feats, lots_preview=lots_preview,
                    live_state=live_state,
                    ts=datetime.now().strftime('%H:%M:%S'),
                    market_open=market_open,
                    prev_body=prev_body)
    except Exception as e:
        return {"error": str(e)}

def bg_fetch():
    # Wait 60s before first fetch so trader gets Angel One session first
    time.sleep(60)
    while True:
        try:
            h = datetime.now().hour
            if 8 <= h < 16:
                _cache['data'] = fetch()
                _cache['ts']   = time.time()
        except: pass
        time.sleep(120)

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
#center-bottom{height:155px;flex-shrink:0;border-top:1px solid #21262d;background:#161b22;display:flex;flex-direction:column}
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
        <div id="gauges-row" style="display:grid;grid-template-columns:repeat(4,1fr);flex:1;align-items:stretch"></div>
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
    <div class="rp-sec">
      <div class="rp-lbl">Confluence Score</div>
      <div id="confluence-panel">
        <div style="color:#6e7681;font-size:.7rem;text-align:center">Loading...</div>
      </div>
    </div>
    <div class="rp-sec">
      <div class="rp-lbl">Live Trail Status</div>
      <div id="trail-panel">
        <div style="color:#6e7681;font-size:.72rem;text-align:center;padding:.4rem 0">No active trade</div>
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
let _chart = null, _candleSeries = null, _priceLines = [], _chartInitialized = false;

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
  _candleSeries = _chart.addLineSeries({
    color: '#58a6ff', lineWidth: 2,
    crosshairMarkerVisible: true,
    crosshairMarkerRadius: 4,
    lastValueVisible: true,
    priceLineVisible: false,
  });
  new ResizeObserver(() => {
    const c = document.getElementById('chart-container');
    if (c && _chart) _chart.resize(c.clientWidth, c.clientHeight);
  }).observe(document.getElementById('chart-container'));
}

function clearPriceLines() {
  if (_candleSeries)
    _priceLines.forEach(pl => { try { _candleSeries.removePriceLine(pl); } catch(e){} });
  _priceLines = [];
}

function updateChart(d) {
  if (!_chart) initChart();
  if (!_chart) return;

  // Line chart — use close price (or spot for latest bar)
  if (d.candles && d.candles.length > 0) {
    const data = d.candles
      .filter(c => typeof c.time === 'number' && !isNaN(c.time))
      .sort((a,b) => a.time - b.time)
      .map(c => ({ time: c.time, value: c.close }));  // line series needs {time, value}
    if (data.length) {
      _candleSeries.setData(data);
      // Auto zoom-out on first load only; subsequent refreshes keep user's zoom
      if (!_chartInitialized) {
        _chart.timeScale().fitContent();
        _chartInitialized = true;
      } else {
        // Just scroll to latest without resetting zoom
        _chart.timeScale().scrollToRealTime();
      }
    }
  }

  // Remove ALL old price lines before redrawing
  clearPriceLines();

  // Spot line (white dashed)
  _priceLines.push(_candleSeries.createPriceLine({
    price: d.spot, color: '#ffffff', lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true, title: 'SPOT'
  }));

  // Pivot levels
  d.levels.forEach(lv => {
    _priceLines.push(_candleSeries.createPriceLine({
      price: lv.v, color: lv.c, lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dotted,
      axisLabelVisible: true, title: lv.n
    }));
  });

  document.getElementById('chart-spot').textContent = d.spot.toLocaleString('en-IN',{minimumFractionDigits:2});
}

function makeGaugeSVG(label, val, sub, color) {
  const pct  = Math.min(Math.max(val, 0), 100);
  const w    = pct;   // bar width %
  const tier = pct >= 65 ? 'high' : pct >= 35 ? 'mid' : 'low';
  const alpha= pct >= 65 ? '33' : pct >= 35 ? '22' : '15';

  const div = document.createElement('div');
  div.style.cssText = `flex:1;padding:.45rem .65rem;border-right:1px solid #21262d;
    display:flex;flex-direction:column;justify-content:center;gap:.25rem;min-width:0`;
  div.innerHTML = `
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.1rem">
      <span style="font-size:.6rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
        color:#8b949e;white-space:nowrap">${label}</span>
      <span style="font-family:'JetBrains Mono',monospace;font-size:.9rem;font-weight:700;
        color:${color}">${val}%</span>
    </div>
    <div style="background:#0d1117;border-radius:4px;height:6px;overflow:hidden;position:relative">
      <div style="position:absolute;top:0;left:0;height:100%;width:${w}%;
        background:${color};border-radius:4px;
        box-shadow:0 0 8px ${color}88;
        transition:width .5s ease"></div>
    </div>
    <div style="font-size:.52rem;color:#6e7681">${sub}</div>`;
  return div;
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
  const preMarket = d.zone === 'pre_market';

  // Bias
  const bc = preMarket ? '#6e7681' : (d.bias==='bull'?'#3fb950':'#f85149');
  document.getElementById('m-bias').style.color = bc;
  document.getElementById('m-bias').textContent  = preMarket ? '--' : d.bias.toUpperCase();

  // Zone
  const zoneEl = document.getElementById('m-zone');
  if(preMarket){
    zoneEl.textContent  = 'PRE-MARKET';
    zoneEl.style.color  = '#6e7681';
    zoneEl.style.fontSize = '.72rem';
  } else {
    zoneEl.textContent  = d.zone.replace(/_/g,' ').toUpperCase();
    zoneEl.style.color  = '#e3b341';
  }

  document.getElementById('m-expiry').textContent = d.expiry;

  // Signal badge
  if(preMarket){
    document.getElementById('m-signal').innerHTML =
      '<span style="background:#161b22;color:#6e7681;border:1px solid #30363d;padding:.2rem .75rem;border-radius:20px;font-size:.75rem">Waiting 09:15...</span>';
  } else {
    document.getElementById('m-signal').innerHTML = sigBadge(d.ti, d.signal);
  }

  // Zone sidebar list
  const zl=document.getElementById('zone-list'); zl.innerHTML='';
  ZONES.forEach(z=>{
    const div=document.createElement('div');
    div.className='zi'+(!preMarket && z===d.zone?' act':'');
    div.textContent=(!preMarket && z===d.zone?'>> ':' ')+z.replace(/_/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
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
  if(preMarket){
    tdiv.innerHTML=`<div class="intra-box">
      <div class="intra-icon" style="font-size:1.5rem">&#128336;</div>
      <div class="intra-title" style="color:#6e7681">Pre-Market</div>
      <div class="intra-info">Signal computed after<br><b style="color:#58a6ff">09:15 AM open</b><br>Levels ready below</div>
    </div>`;
  } else if(d.ti && !d.ti.skip && d.signal){
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
  document.getElementById('compass-title').textContent = preMarket
    ? 'Waiting for market open (09:15)'
    : `${d.zone.replace(/_/g,' ').toUpperCase()} | ${d.bias.toUpperCase()}`;
  const gr=document.getElementById('gauges-row'); gr.innerHTML='';
  [
    {label:'Trend',  val:d.gauges.trend, sub:'of sessions',   color:'#2979ff'},
    {label:'Sideways',val:d.gauges.side, sub:'stable range',  color:'#42a5f5'},
    {label:'Reversal',val:d.gauges.rev,  sub:'snap-back risk',color:'#e53935'},
    {label:'Win Rate',val:d.gauges.wr,   sub:'backtest est.', color:'#43a047'},
  ].forEach(g => gr.appendChild(makeGaugeSVG(g.label, g.val, g.sub, g.color)));

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

  // Confluence score panel
  const cp = document.getElementById('confluence-panel');
  if(cp && d.feats){
    const f = d.feats;
    const score = f.score || 0;
    const scoreCol = score >= 5 ? '#3fb950' : score >= 3 ? '#e3b341' : '#f85149';
    const lots = d.lots_preview || 1;
    const lotsCol = lots >= 3 ? '#e3b341' : lots === 2 ? '#58a6ff' : '#8b949e';
    const checks = [
      ['VIX OK (<20)',        f.vix_ok],
      ['CPR Trend Aligned',   f.cpr_trend_aligned],
      ['2 Consec Closes',     f.consec_aligned],
      ['Gap Aligned',         f.cpr_gap_aligned],
      ['DTE Sweet (2-6)',     f.dte_sweet],
      ['CPR Narrow',          f.cpr_narrow],
      ['CPR Dir Aligned',     f.cpr_dir_aligned],
    ];
    const rows = checks.map(([lbl, val]) =>
      `<div style="display:flex;align-items:center;justify-content:space-between;padding:.18rem 0;border-bottom:1px solid #21262d1a">
        <span style="font-size:.6rem;color:${val?'#c9d1d9':'#6e7681'}">${lbl}</span>
        <span style="font-size:.65rem;font-weight:700;color:${val?'#3fb950':'#30363d'}">${val?'✓':'—'}</span>
      </div>`
    ).join('');
    cp.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:.4rem">
        <div>
          <span style="font-family:'JetBrains Mono',monospace;font-size:1.4rem;font-weight:700;color:${scoreCol}">${score}</span>
          <span style="font-size:.6rem;color:#6e7681">/7</span>
        </div>
        <div style="text-align:right">
          <div style="font-family:'JetBrains Mono',monospace;font-size:.85rem;font-weight:700;color:${lotsCol}">${lots}x lots</div>
          <div style="font-size:.55rem;color:#6e7681">${f.inside_cpr?'inside CPR -1':''}</div>
        </div>
      </div>
      <div style="background:#0d1117;border-radius:5px;padding:.3rem .4rem">${rows}</div>`;
  }

  // Live trail status
  const tp = document.getElementById('trail-panel');
  const ls = d.live_state;
  if(ls && ls.status === 'open'){
    const tierColors = ['#6e7681','#58a6ff','#e3b341','#3fb950'];
    const tierColor  = tierColors[ls.trail_tier] || '#6e7681';
    const upnlColor  = ls.upnl >= 0 ? '#3fb950' : '#f85149';
    const upnlSign   = ls.upnl >= 0 ? '+' : '';
    const decayColor = ls.decay_pct >= 0 ? '#3fb950' : '#f85149';
    const decaySign  = ls.decay_pct >= 0 ? '+' : '';
    const tiers = [
      {t:1, label:'Break-even (25%)', col:'#58a6ff'},
      {t:2, label:'80% Lock (40%)',   col:'#e3b341'},
      {t:3, label:'95% Lock (60%)',   col:'#3fb950'},
    ];
    const tierBars = tiers.map(x => {
      const active = ls.trail_tier >= x.t;
      const bg = active ? x.col+'22' : '#0d1117';
      const bc = active ? x.col+'88' : '#21262d';
      const tc = active ? x.col : '#6e7681';
      return `<div style="background:${bg};border:1px solid ${bc};border-radius:5px;padding:.28rem .5rem;font-size:.62rem;color:${tc};display:flex;align-items:center;gap:.35rem;margin-bottom:.25rem">
        <span style="width:6px;height:6px;border-radius:50%;background:${active?x.col:'#30363d'};display:inline-block;flex-shrink:0"></span>
        ${x.label}${active?' ✓':''}
      </div>`;
    }).join('');

    const lotsDisp  = ls.lots ? ls.lots : '?';
    const oaPnl     = ls.oa_pnl !== undefined ? ls.oa_pnl : ls.upnl;
    const oaPnlCol  = oaPnl >= 0 ? '#3fb950' : '#f85149';
    const oaPnlSign = oaPnl >= 0 ? '+' : '';

    tp.innerHTML = `
      <div style="background:#0d1117;border:1px solid #21262d;border-radius:7px;padding:.5rem .6rem;margin-bottom:.3rem">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.35rem">
          <div>
            <div style="font-size:.6rem;color:#6e7681">${ls.symbol?ls.symbol.slice(-16):''}</div>
            ${ls.signal?`<div style="font-size:.58rem;font-weight:700;color:${{THOR:'#58a6ff',HULK:'#3fb950','IRON MAN':'#e3b341',CAPTAIN:'#a78bfa',CRT:'#f97316',MRC:'#ec4899'}[ls.signal]||'#8b949e'}">${ls.signal}</div>`:''}
          </div>
          <span style="font-size:.6rem;font-family:'JetBrains Mono',monospace;color:${oaPnlCol};font-weight:700">${oaPnlSign}Rs.${oaPnl.toFixed(0)}</span>
        </div>
        <div style="background:#161b22;border:1px solid #21262d;border-radius:5px;padding:.22rem .4rem;text-align:center;margin-bottom:.3rem">
          <span style="font-size:.55rem;color:#6e7681">Lots </span>
          <span style="font-family:'JetBrains Mono',monospace;font-size:.8rem;font-weight:700;color:${lotsDisp>=195?'#e3b341':lotsDisp>=130?'#58a6ff':'#8b949e'}">${lotsDisp}</span>
          <span style="font-size:.55rem;color:#6e7681"> (${ls.score>=4?'3x':ls.score>=2?'2x':'1x'} score=${ls.score||0})</span>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:.3rem;margin-bottom:.35rem">
          <div style="background:#161b22;border-radius:5px;padding:.25rem .4rem;text-align:center">
            <div style="font-size:.52rem;color:#6e7681;margin-bottom:.1rem">Entry</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#f0f6fc">${ls.entry.toFixed(2)}</div>
          </div>
          <div style="background:#161b22;border-radius:5px;padding:.25rem .4rem;text-align:center">
            <div style="font-size:.52rem;color:#6e7681;margin-bottom:.1rem">Current</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#f0f6fc">${ls.current.toFixed(2)}</div>
          </div>
          <div style="background:#161b22;border-radius:5px;padding:.25rem .4rem;text-align:center">
            <div style="font-size:.52rem;color:#6e7681;margin-bottom:.1rem">SL Level</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#f85149">${ls.sl.toFixed(2)}</div>
          </div>
          <div style="background:#161b22;border-radius:5px;padding:.25rem .4rem;text-align:center">
            <div style="font-size:.52rem;color:#6e7681;margin-bottom:.1rem">Decay</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;color:${decayColor}">${decaySign}${ls.decay_pct}%</div>
          </div>
          <div style="background:#161b22;border-radius:5px;padding:.25rem .4rem;text-align:center">
            <div style="font-size:.52rem;color:#6e7681;margin-bottom:.1rem">Max Decay</div>
            <div style="font-family:'JetBrains Mono',monospace;font-size:.75rem;color:#58a6ff">${ls.max_decay_pct}%</div>
          </div>
        </div>
        <div style="font-size:.58rem;font-weight:700;color:${tierColor};margin-bottom:.3rem;text-align:center;
          background:${tierColor}18;border:1px solid ${tierColor}44;border-radius:5px;padding:.2rem">
          Trail: ${ls.trail_label}
        </div>
        ${tierBars}
        <div style="font-size:.55rem;color:#6e7681;text-align:right;margin-top:.2rem">${ls.ts}</div>
      </div>`;
  } else if(ls && ls.s4_watching){
    tp.innerHTML = `<div style="background:#0d2137;border:1px solid #1f6feb44;border-radius:7px;padding:.5rem .6rem;text-align:center">
      <div style="font-size:.65rem;font-weight:700;color:#58a6ff;margin-bottom:.3rem">S4 WATCHING</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:.7rem;color:#c9d1d9">
        ${ls.s4_opt||''} ${ls.s4_strike||''}</div>
      <div style="font-size:.6rem;color:#6e7681;margin-top:.2rem">
        Pullback window: [${ls.s4_ep?(ls.s4_ep*0.60).toFixed(0):'-'}, ${ls.s4_ep?(ls.s4_ep*0.75).toFixed(0):'-'}]
      </div>
      <div style="font-size:.55rem;color:#6e7681;margin-top:.2rem">Watching until 14:00</div>
    </div>`;
  } else if(ls && ls.contra_watching){
    tp.innerHTML = `<div style="background:#271d08;border:1px solid #9e6a0344;border-radius:7px;padding:.5rem .6rem;text-align:center">
      <div style="font-size:.65rem;font-weight:700;color:#e3b341;margin-bottom:.3rem">CONTRA WATCHING</div>
      <div style="font-family:'JetBrains Mono',monospace;font-size:.7rem;color:#c9d1d9">
        ${ls.contra_opt||''} entry</div>
      <div style="font-size:.6rem;color:#6e7681;margin-top:.2rem">
        Spot at exit: ${ls.contra_spot_at_exit||'--'} | tol: ±30pts
      </div>
      <div style="font-size:.55rem;color:#6e7681;margin-top:.2rem">Watching until 14:00</div>
    </div>`;
  } else {
    tp.innerHTML = '<div style="color:#6e7681;font-size:.7rem;text-align:center;padding:.4rem 0">No active trade</div>';
  }

  // Positions
  const pl=document.getElementById('pos-list'); pl.innerHTML='';
  if(d.trades.length===0){
    pl.innerHTML='<div class="loading">No trades today</div>';
  } else {
    d.trades.forEach(t=>{
      const pc=t.pnl>=0?'#3fb950':'#f85149', ps=t.pnl>=0?'+':'';
      const agentCol = {'THOR':'#58a6ff','HULK':'#3fb950','IRON MAN':'#e3b341',
                        'CAPTAIN':'#a78bfa','CRT':'#f97316','MRC':'#ec4899'}[t.signal] || '#8b949e';
      const row=document.createElement('div'); row.className='pos-row';
      row.innerHTML=`<div style="flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:.3rem">
          <span style="font-size:.56rem;font-weight:700;color:${agentCol};white-space:nowrap">${t.signal||''}</span>
          <span class="pos-sym" style="overflow:hidden;text-overflow:ellipsis">${t.sym}</span>
        </div>
        <div class="pos-meta">EP:${t.ep} | ${t.reason} | ${t.lots}L${t.score?(' s'+t.score):''}</div>
      </div>
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
    # Return HTML directly — avoids Jinja2 parsing {{ }} in JavaScript
    return Response(HTML, mimetype='text/html')

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(__file__))
    t = threading.Thread(target=bg_fetch, daemon=True)
    t.start()
    print("FIFTO Dashboard: http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, debug=False)
