"""
Artha — Missed Days Analysis
=============================
For every trading day in the 5yr backtest window, categorise why no trade happened:
  - body_filter : prev day body < 0.10% (flat day)
  - no_signal   : zone+bias combo has no v17a param, H3/L3 not inside CPR
  - dte_zero    : today is expiry day (DTE=0)
  - no_data     : NIFTY spot/option data missing
  - traded      : v17a, cam_h3, or cam_l3 signal fired

Output: stacked bar chart by month + calendar heatmap, pushed to chat
"""
import sys, os, warnings, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')
sys.path.insert(0, os.path.expanduser('~') + '/.claude/skills/sa-kron-chart/scripts')
os.chdir('/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')

from my_util import load_spot_data, list_expiry_dates, list_trading_dates
import pandas as pd, numpy as np
from plot_util import send_custom_chart

OUT_DIR    = 'artha'
EMA_PERIOD = 20
BODY_MIN   = 0.10
YEARS      = 5

V17A_PARAMS = {
    ("above_r4","bull","PE"),("below_s4","bear","CE"),("pdh_to_r1","bear","PE"),
    ("pdl_to_bc","bull","PE"),("pdl_to_s1","bear","CE"),("r1_to_r2","bear","PE"),
    ("r1_to_r2","bull","PE"),("r2_to_r3","bull","PE"),("r2_to_r3","bear","PE"),
    ("r3_to_r4","bull","PE"),("s1_to_s2","bear","CE"),("s3_to_s4","bear","CE"),
    ("tc_to_pdh","bear","PE"),("tc_to_pdh","bull","PE"),
    ("within_cpr","bear","CE"),("within_cpr","bull","PE"),
}

def r2(v): return round(float(v), 2)

def compute_pivots(h, l, c):
    pp=r2((h+l+c)/3); bc=r2((h+l)/2); tc=r2(2*pp-bc)
    r1=r2(2*pp-l); r2_=r2(pp+(h-l)); r3=r2(r1+(h-l)); r4=r2(r2_+(h-l))
    s1=r2(2*pp-h); s2_=r2(pp-(h-l)); s3=r2(s1-(h-l)); s4=r2(s2_-(h-l))
    return dict(pp=pp,bc=bc,tc=tc,r1=r1,r2=r2_,r3=r3,r4=r4,s1=s1,s2=s2_,s3=s3,s4=s4)

def compute_camarilla(h, l, c):
    rng = h - l
    return dict(h3=r2(c+rng*1.1/4), l3=r2(c-rng*1.1/4))

def classify_zone(op, pvt, pdh, pdl):
    if   op > pvt['r4']: return 'above_r4'
    elif op > pvt['r3']: return 'r3_to_r4'
    elif op > pvt['r2']: return 'r2_to_r3'
    elif op > pvt['r1']: return 'r1_to_r2'
    elif op > pdh:       return 'pdh_to_r1'
    elif op > pvt['tc']: return 'tc_to_pdh'
    elif op >= pvt['bc']:return 'within_cpr'
    elif op > pdl:       return 'pdl_to_bc'
    elif op > pvt['s1']: return 'pdl_to_s1'
    elif op > pvt['s2']: return 's1_to_s2'
    elif op > pvt['s3']: return 's2_to_s3'
    elif op > pvt['s4']: return 's3_to_s4'
    else:                return 'below_s4'

def get_v17a_signal(zone, bias):
    for opt in ('PE','CE'):
        if (zone, bias, opt) in V17A_PARAMS: return opt
    return None

# ── Load OHLC + EMA ───────────────────────────────────────────────
print("Loading NIFTY OHLC + EMA...")
all_dates = list_trading_dates()
latest    = pd.Timestamp(all_dates[-1][:4]+'-'+all_dates[-1][4:6]+'-'+all_dates[-1][6:])
dates_5yr = [d for d in all_dates
             if pd.Timestamp(d[:4]+'-'+d[4:6]+'-'+d[6:]) >= latest - pd.DateOffset(years=YEARS)]
extra     = max(0, all_dates.index(dates_5yr[0]) - EMA_PERIOD - 20)

daily_ohlc = {}
for d in all_dates[extra:]:
    tks = load_spot_data(d, 'NIFTY')
    if tks is None or tks.empty: continue
    daily_ohlc[d] = (
        float(tks['price'].max()),
        float(tks['price'].min()),
        float(tks[tks['time'] <= '15:30:00']['price'].iloc[-1]),
        float(tks[tks['time'] >= '09:15:00']['price'].iloc[0]),
    )

close_s = pd.Series({d:v[2] for d,v in daily_ohlc.items()}).sort_index()
ema_s   = close_s.ewm(span=EMA_PERIOD, adjust=False).mean().shift(1)

# Load trades from combined backtest
trades_df = pd.read_csv('data/20260428/56_combined_trades.csv', parse_dates=['date'])
traded_dates = set(trades_df['date'].dt.strftime('%Y%m%d'))

# ── Classify every day ────────────────────────────────────────────
print(f"Classifying {len(dates_5yr)} days...")
rows = []

for date in dates_5yr:
    dt  = pd.Timestamp(date[:4]+'-'+date[4:6]+'-'+date[6:])
    dstr = f'{date[:4]}-{date[4:6]}-{date[6:]}'

    # Already traded?
    if date in traded_dates:
        sub  = trades_df[trades_df['date'].dt.strftime('%Y%m%d') == date]
        strat= sub['strategy'].iloc[0]
        zone = sub['zone'].iloc[0]
        bias = sub['bias'].iloc[0]
        won  = bool(sub['pnl'].iloc[0] > 0)
        rows.append(dict(date=dstr, dt=dt, status='traded',
                         reason=f"{strat}|{zone}|{bias}", won=won,
                         zone=zone, bias=bias))
        continue

    idx = all_dates.index(date)
    if idx < 1: continue
    prev = all_dates[idx-1]

    # No data
    if prev not in daily_ohlc or date not in daily_ohlc:
        rows.append(dict(date=dstr, dt=dt, status='no_data',
                         reason='spot data missing', won=False, zone='', bias=''))
        continue

    ph, pl, pc, _ = daily_ohlc[prev]
    _, _, _, today_op = daily_ohlc[date]

    # DTE=0
    exps = list_expiry_dates(date)
    if exps:
        exp_dt = pd.Timestamp(f'20{exps[0][:2]}-{exps[0][2:4]}-{exps[0][4:]}')
        dte = (exp_dt - dt).days
        if dte == 0:
            rows.append(dict(date=dstr, dt=dt, status='dte_zero',
                             reason='expiry day DTE=0', won=False, zone='', bias=''))
            continue

    # Body filter
    prev_open = daily_ohlc[prev][3]
    prev_body = round(abs(pc - prev_open) / prev_open * 100, 3)
    if prev_body <= BODY_MIN:
        rows.append(dict(date=dstr, dt=dt, status='body_filter',
                         reason=f'body={prev_body:.2f}%≤0.10%', won=False, zone='', bias=''))
        continue

    pvt  = compute_pivots(ph, pl, pc)
    cam  = compute_camarilla(ph, pl, pc)
    e20  = ema_s.get(date, np.nan)
    if np.isnan(e20): continue

    bias   = 'bull' if today_op > e20 else 'bear'
    zone   = classify_zone(today_op, pvt, ph, pl)
    signal = get_v17a_signal(zone, bias)

    cpr_lo = min(pvt['tc'], pvt['bc'])
    cpr_hi = max(pvt['tc'], pvt['bc'])
    h3_in  = cpr_lo <= cam['h3'] <= cpr_hi
    l3_in  = cpr_lo <= cam['l3'] <= cpr_hi
    cam_possible = h3_in or l3_in

    if signal is None and not cam_possible:
        rows.append(dict(date=dstr, dt=dt, status='no_signal',
                         reason=f'{zone}|{bias}→no match', won=False, zone=zone, bias=bias))
    elif signal is None and cam_possible:
        # Cam possible but didn't fire (touch not detected — data-level miss)
        rows.append(dict(date=dstr, dt=dt, status='no_signal',
                         reason=f'cam possible {zone}|{bias} no touch', won=False, zone=zone, bias=bias))
    else:
        # Signal existed but no option data (entry price missing)
        rows.append(dict(date=dstr, dt=dt, status='no_data',
                         reason=f'option data miss {zone}|{bias}|{signal}', won=False,
                         zone=zone, bias=bias))

df = pd.DataFrame(rows)
df['dt'] = pd.to_datetime(df['dt'])
df['ym'] = df['dt'].dt.to_period('M').astype(str)

print(f"  Total days: {len(df)}")
print(df['status'].value_counts())

# ── Build monthly stacked bar ─────────────────────────────────────
months = sorted(df['ym'].unique())

STATUS_COLORS = {
    'traded_win':  '#26a69a',
    'traded_loss': '#ef5350',
    'body_filter': '#FFA726',
    'no_signal':   '#78909C',
    'dte_zero':    '#AB47BC',
    'no_data':     '#455A64',
}

def make_bar(status_key, label, color, month_counts):
    data = [{"x": m, "y": month_counts.get(m, 0)} for m in months]
    return {"id": status_key, "label": label, "color": color,
            "seriesType": "bar", "data": data}

# Split traded into win/loss
df['status2'] = df['status']
df.loc[(df['status']=='traded') & (df['won']==True),  'status2'] = 'traded_win'
df.loc[(df['status']=='traded') & (df['won']==False), 'status2'] = 'traded_loss'

monthly = df.groupby(['ym','status2']).size().unstack(fill_value=0)

bars = []
for skey, label, color in [
    ('traded_win',  'Traded — Win',    '#26a69a'),
    ('traded_loss', 'Traded — Loss',   '#ef5350'),
    ('body_filter', 'Body Filter Skip','#FFA726'),
    ('no_signal',   'No Signal',       '#78909C'),
    ('dte_zero',    'Expiry Day Skip', '#AB47BC'),
    ('no_data',     'No Data',         '#455A64'),
]:
    col = monthly[skey] if skey in monthly.columns else pd.Series(dtype=int)
    data = [{"x": m, "y": int(col.get(m, 0))} for m in months]
    bars.append({"id": skey, "label": label, "color": color,
                 "seriesType": "bar", "data": data})

# Summary counts
n_traded = len(df[df['status']=='traded'])
n_win    = len(df[df['status2']=='traded_win'])
n_body   = len(df[df['status']=='body_filter'])
n_nosig  = len(df[df['status']=='no_signal'])
n_dte0   = len(df[df['status']=='dte_zero'])
n_nodata = len(df[df['status']=='no_data'])
wr_pct   = round(n_win / n_traded * 100, 1) if n_traded else 0

tv_json = {
    "isTvFormat": False,
    "candlestick": [],
    "volume":      [],
    "lines":       bars,
}

send_custom_chart(
    "artha_missed_days",
    tv_json,
    title=(f"Artha — 5yr Day Classification | "
           f"Traded={n_traded}(WR={wr_pct}%) "
           f"Body={n_body} NoSig={n_nosig} DTE0={n_dte0} NoData={n_nodata}")
)
print("Chart pushed.")

# ── No-signal zone breakdown ──────────────────────────────────────
nosig = df[df['status']=='no_signal']
print(f"\nNo-signal days by zone ({len(nosig)} days):")
print(nosig['zone'].value_counts().head(10).to_string())

# ── Save ─────────────────────────────────────────────────────────
out = f'{OUT_DIR}/missed_days.csv'
df.to_csv(out, index=False)
print(f"\nSaved → {out}")
