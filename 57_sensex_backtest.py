"""
SENSEX Weekly Options Backtest
================================
Trade days : Monday (DTE=3) + Tuesday (DTE=2) only
Expiry     : Thursday (BSE weekly)
Signal     : SENSEX own CPR zone + SENSEX EMA(20) bias
ATM        : computed from SENSEX spot price at entry time (fast)
Lot size   : 10  |  Strike interval : 100
Grid search: strike_type × entry_time × target_pct × sl_pct

Output: data/20260428/57_sensex_trades.csv
"""
import sys, os, warnings, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')
sys.path.insert(0, os.path.expanduser('~') + '/.claude/skills/sa-kron-chart/scripts')
os.chdir('/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')

from my_util import load_spot_data, load_tick_data, list_expiry_dates, list_trading_dates
import pandas as pd, numpy as np
from plot_util import plot_equity

OUT_DIR    = 'data/20260428'
LOT_SIZE   = 10
STRIKE_INT = 100
EMA_PERIOD = 20
BODY_MIN   = 0.10
EOD_EXIT   = '15:20:00'
YEARS      = 5
os.makedirs(OUT_DIR, exist_ok=True)

ENTRY_TIMES  = ['09:16:02', '09:20:02', '09:25:02', '09:31:02']
STRIKE_TYPES = ['ATM', 'OTM1', 'ITM1']
TARGETS      = [0.20, 0.30, 0.40, 0.50]
SL_MULTS     = [0.50, 1.00, 1.50, 2.00]

TRADE_DAYS = {0: 'Monday(DTE=3)', 1: 'Tuesday(DTE=2)'}

def r2(v): return round(float(v), 2)

def compute_pivots(h, l, c):
    pp=r2((h+l+c)/3); bc=r2((h+l)/2); tc=r2(2*pp-bc)
    r1=r2(2*pp-l); r2_=r2(pp+(h-l)); r3=r2(r1+(h-l)); r4=r2(r2_+(h-l))
    s1=r2(2*pp-h); s2_=r2(pp-(h-l)); s3=r2(s1-(h-l)); s4=r2(s2_-(h-l))
    return dict(pp=pp,bc=bc,tc=tc,r1=r1,r2=r2_,r3=r3,r4=r4,s1=s1,s2=s2_,s3=s3,s4=s4)

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

def get_signal(zone, bias):
    if zone in {'above_r4','r3_to_r4','r2_to_r3','r1_to_r2'}: return 'PE'
    if zone == 'pdh_to_r1'  and bias == 'bear': return 'PE'
    if zone == 'tc_to_pdh':                      return 'PE'
    if zone == 'within_cpr' and bias == 'bull':  return 'PE'
    if zone == 'within_cpr' and bias == 'bear':  return 'CE'
    if zone == 'pdl_to_bc'  and bias == 'bull':  return 'PE'
    if zone in {'pdl_to_s1','s1_to_s2','s3_to_s4','below_s4'} and bias=='bear': return 'CE'
    return None

def get_strike(atm, opt, stype):
    if opt == 'CE':
        return {'OTM1':atm+STRIKE_INT,'ATM':atm,'ITM1':atm-STRIKE_INT}[stype]
    return {'OTM1':atm-STRIKE_INT,'ATM':atm,'ITM1':atm+STRIKE_INT}[stype]

def sim_pct(ts, ps, ep, eod_ns, tgt_pct, sl_pct):
    tgt=r2(ep*(1-tgt_pct)); hsl=r2(ep*(1+sl_pct)); sl=hsl; md=0.0
    for i in range(len(ts)):
        t=ts[i]; p=ps[i]
        if t >= eod_ns: return r2((ep-p)*LOT_SIZE),'eod',r2(p)
        d=(ep-p)/ep
        if d>md: md=d
        if   md>=0.60: sl=min(sl,r2(ep*(1-md*0.95)))
        elif md>=0.40: sl=min(sl,r2(ep*0.80))
        elif md>=0.25: sl=min(sl,ep)
        if p<=tgt: return r2((ep-p)*LOT_SIZE),'target',r2(p)
        if p>=sl:  return r2((ep-p)*LOT_SIZE),'lockin_sl' if sl<hsl else 'hard_sl',r2(p)
    return r2((ep-ps[-1])*LOT_SIZE),'eod',r2(ps[-1])


# ═══════════════════════════════════════════════════════════════════
# Pass 0: Load SENSEX daily OHLC + EMA (spot ticks → OHLC)
# ═══════════════════════════════════════════════════════════════════
print(f"Pass 0: Loading SENSEX OHLC + EMA({EMA_PERIOD}) ({YEARS}yr)...")
t0 = time.time()

all_dates = list_trading_dates()
latest    = pd.Timestamp(all_dates[-1][:4]+'-'+all_dates[-1][4:6]+'-'+all_dates[-1][6:])
dates_5yr = [d for d in all_dates
             if pd.Timestamp(d[:4]+'-'+d[4:6]+'-'+d[6:]) >= latest - pd.DateOffset(years=YEARS)]
extra     = max(0, all_dates.index(dates_5yr[0]) - EMA_PERIOD - 20)

sensex_ohlc = {}   # date → (high, low, close, open, spot_ticks_df)
for d in all_dates[extra:]:
    tks = load_spot_data(d, 'SENSEX')
    if tks is None or tks.empty: continue
    tks_day = tks[tks['time'] <= '15:30:00']
    tks_open = tks[tks['time'] >= '09:15:00']
    if tks_open.empty: continue
    sensex_ohlc[d] = (
        float(tks_day['price'].max()),
        float(tks_day['price'].min()),
        float(tks_day['price'].iloc[-1]),
        float(tks_open['price'].iloc[0]),
    )

close_s = pd.Series({d:v[2] for d,v in sensex_ohlc.items()}).sort_index()
ema_s   = close_s.ewm(span=EMA_PERIOD, adjust=False).mean().shift(1)
print(f"  {len(sensex_ohlc)} SENSEX days loaded in {time.time()-t0:.0f}s")


# ═══════════════════════════════════════════════════════════════════
# Pass 1: Precompute SENSEX spot price at each entry time per day
# (avoids reloading spot ticks in inner loop)
# ═══════════════════════════════════════════════════════════════════
print("Pass 1: Precomputing SENSEX spot at entry times for Mon+Tue days...")
t1 = time.time()

# spot_at_entry[date][entry_time] = spot_price
spot_at_entry = {}
mon_tue_dates = [d for d in dates_5yr
                 if pd.Timestamp(d[:4]+'-'+d[4:6]+'-'+d[6:]).weekday() in TRADE_DAYS]

for date in mon_tue_dates:
    tks = load_spot_data(date, 'SENSEX')
    if tks is None or tks.empty: continue
    spot_at_entry[date] = {}
    for et in ENTRY_TIMES:
        mask = tks['time'] >= et
        if mask.any():
            spot_at_entry[date][et] = float(tks[mask].iloc[0]['price'])

print(f"  Done in {time.time()-t1:.0f}s  |  {len(spot_at_entry)} days with spot data")


# ═══════════════════════════════════════════════════════════════════
# Pass 2: Grid search
# ═══════════════════════════════════════════════════════════════════
print(f"\nPass 2: Grid search on {len(mon_tue_dates)} Mon+Tue days...")
t2 = time.time()

records = []
counts  = dict(monday=0, tuesday=0, skip_body=0, skip_signal=0,
               skip_dte0=0, no_data=0, no_expiry=0, no_opt_data=0)

for date in mon_tue_dates:
    dt = pd.Timestamp(date[:4]+'-'+date[4:6]+'-'+date[6:])
    day_label = TRADE_DAYS[dt.weekday()]

    idx = all_dates.index(date)
    if idx < 1: continue
    prev = all_dates[idx-1]
    if prev not in sensex_ohlc or date not in sensex_ohlc:
        counts['no_data'] += 1; continue
    if date not in spot_at_entry:
        counts['no_data'] += 1; continue

    ph, pl, pc, _ = sensex_ohlc[prev]
    _, _, _, today_op = sensex_ohlc[date]

    # Body filter
    prev_open = sensex_ohlc[prev][3]
    prev_body = round(abs(pc - prev_open) / prev_open * 100, 3)
    if prev_body <= BODY_MIN:
        counts['skip_body'] += 1; continue

    pvt  = compute_pivots(ph, pl, pc)
    e20  = ema_s.get(date, np.nan)
    if np.isnan(e20): continue

    bias   = 'bull' if today_op > e20 else 'bear'
    zone   = classify_zone(today_op, pvt, ph, pl)
    signal = get_signal(zone, bias)
    if signal is None:
        counts['skip_signal'] += 1; continue

    # Nearest Thursday expiry
    dstr = f'{date[:4]}-{date[4:6]}-{date[6:]}'
    exps = list_expiry_dates(date, 'SENSEX')
    if not exps:
        counts['no_expiry'] += 1; continue
    expiry = exps[0]
    exp_dt = pd.Timestamp(f'20{expiry[:2]}-{expiry[2:4]}-{expiry[4:]}')
    dte    = (exp_dt - dt).days
    if dte == 0:
        counts['skip_dte0'] += 1; continue

    eod_ns = pd.Timestamp(dstr + ' ' + EOD_EXIT).value

    if dt.weekday() == 0: counts['monday'] += 1
    else:                 counts['tuesday'] += 1

    for entry_time in ENTRY_TIMES:
        spot = spot_at_entry[date].get(entry_time)
        if spot is None: continue

        # ATM from spot price directly (fast — no file scan)
        sensex_atm = int(round(spot / STRIKE_INT) * STRIKE_INT)

        for stype in STRIKE_TYPES:
            strike = get_strike(sensex_atm, signal, stype)
            inst   = f'SENSEX{expiry}{strike}{signal}'

            tk = load_tick_data(date, inst, entry_time[:5]+':00', EOD_EXIT)
            if tk is None or tk.empty:
                counts['no_opt_data'] += 1; continue
            ep_mask = tk['time'] >= entry_time
            if not ep_mask.any(): continue
            ep = r2(float(tk[ep_mask].iloc[0]['price']))
            if ep < 10: continue

            tk_sim = tk[ep_mask]
            opt_ts = tk_sim['date_time'].values.astype('datetime64[ns]').astype('int64')
            opt_ps = tk_sim['price'].values.astype(float)

            for tgt_pct in TARGETS:
                for sl_pct in SL_MULTS:
                    pnl, reason, xp = sim_pct(opt_ts, opt_ps, ep, eod_ns, tgt_pct, sl_pct)
                    records.append(dict(
                        date=dstr, weekday=day_label, zone=zone, bias=bias,
                        opt=signal, strike_type=stype, strike=strike,
                        expiry=expiry, dte=dte, entry_time=entry_time,
                        ep=ep, xp=r2(xp), exit_reason=reason, pnl=pnl,
                        target_pct=tgt_pct, sl_pct=sl_pct,
                    ))

elapsed = time.time() - t2
print(f"  Done in {elapsed:.0f}s  |  {counts}")

if not records:
    print("No records — check SENSEX option data availability.")
    sys.exit(1)

df = pd.DataFrame(records)
df['date'] = pd.to_datetime(df['date'])

# ═══════════════════════════════════════════════════════════════════
# Results
# ═══════════════════════════════════════════════════════════════════
print("\n" + "="*75)
print("BEST CONFIGS — by total P&L (min 10 trades)")
print("="*75)
grp = df.groupby(['weekday','strike_type','entry_time','target_pct','sl_pct'])['pnl'].agg(
    n='count',
    wr=lambda x: round((x>0).mean()*100, 1),
    avg=lambda x: round(x.mean(), 0),
    total=lambda x: round(x.sum(), 0)
).reset_index()
grp = grp[grp['n'] >= 10].sort_values('total', ascending=False)
print(grp.head(20).to_string(index=False))

print("\n" + "="*75)
print("BY WEEKDAY — best config each day")
print("="*75)
for day in ['Monday(DTE=3)', 'Tuesday(DTE=2)']:
    sub = grp[grp['weekday']==day]
    if sub.empty: print(f"  {day}: no results (check data)"); continue
    best = sub.iloc[0]
    print(f"  {day}: {best.strike_type} {best.entry_time} tgt={best.target_pct:.0%} "
          f"sl={best.sl_pct}x | n={int(best.n)} WR={best.wr}% "
          f"avg=Rs{best.avg:,.0f} total=Rs{best.total:,.0f}")

print("\n" + "="*75)
print("DTE COMPARISON (2 vs 3)")
print("="*75)
dte_g = df.groupby('dte')['pnl'].agg(
    n='count', wr=lambda x: round((x>0).mean()*100,1),
    avg=lambda x: round(x.mean(),0), total=lambda x: round(x.sum(),0)
).reset_index()
print(dte_g.to_string(index=False))

print("\n" + "="*75)
print("ENTRY TIME COMPARISON")
print("="*75)
et = df.groupby('entry_time')['pnl'].agg(
    n='count', wr=lambda x: round((x>0).mean()*100,1),
    avg=lambda x: round(x.mean(),0), total=lambda x: round(x.sum(),0)
).reset_index()
print(et.to_string(index=False))

# ── Save ─────────────────────────────────────────────────────────────
out = f'{OUT_DIR}/57_sensex_trades.csv'
df.to_csv(out, index=False)
print(f"\nSaved → {out}  ({len(df)} records)")

# ── Equity curve for best config ──────────────────────────────────────
if not grp.empty:
    best = grp.iloc[0]
    print(f"\nBest config: {best.weekday} | {best.strike_type} | {best.entry_time} "
          f"| tgt={best.target_pct:.0%} | sl={best.sl_pct}x")
    mask = ((df.weekday     == best.weekday) &
            (df.strike_type == best.strike_type) &
            (df.entry_time  == best.entry_time) &
            (df.target_pct  == best.target_pct) &
            (df.sl_pct      == best.sl_pct))
    best_df = df[mask].copy().sort_values('date')
    eq  = best_df['pnl'].cumsum()
    dd  = eq - eq.cummax()
    pts = round(best_df['pnl'].sum() / YEARS / 52 / LOT_SIZE, 1)

    print(f"  Trades={len(best_df)} WR={round((best_df.pnl>0).mean()*100,1)}% "
          f"total=Rs{round(best_df.pnl.sum(),0):,.0f}/5yr "
          f"MDD=Rs{round(dd.min(),0):,.0f}  {pts}pts/wk")

    print("\nYear-wise:")
    best_df['year'] = best_df['date'].dt.year
    for yr, g in best_df.groupby('year'):
        n=len(g); wr=round((g.pnl>0).mean()*100,1); tot=round(g.pnl.sum(),0)
        print(f"  {yr}: n={n}  WR={wr}%  total=Rs{tot:,.0f}")

    plot_equity(eq, dd, '57_sensex_equity',
                title=f"SENSEX Mon+Tue | {best.strike_type} {best.entry_time} "
                      f"tgt={best.target_pct:.0%} sl={best.sl_pct}x | {pts}pts/wk")
    print("Equity chart pushed.")
