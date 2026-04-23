"""
Zone v17a — Spot SL for BOTH tc_to_pdh bear PE AND tc_to_pdh bull PE
======================================================================
Changes from v13:
  1. tc_to_pdh bear PE  : spot SL when spot > PDH + buffer  [SAME AS v13]
  2. tc_to_pdh bull PE  : spot SL when spot < BC  - buffer  [NEW v17a]
                          Bull thesis fails when spot breaks below CPR bottom (BC)
                          Grid: buffer [0, 25, 50, 75, 100]
  3. All else unchanged from v13

Motivation: tc_to_pdh bull PE had 14 hard-SL losses = -₹79,714 in v13
            (option doubled when spot fell through CPR on "bull" days)
"""
import sys, os, time, itertools
sys.path.insert(0, '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')
sys.path.insert(0, os.path.expanduser('~') + '/.claude/skills/sa-kron-chart/scripts')
os.chdir('/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')

from plot_util import plot_equity
from my_util import load_tick_data, load_spot_data, list_expiry_dates, list_trading_dates
import pandas as pd, numpy as np

FOLDER   = '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell'
OUT_DIR  = f'{FOLDER}/data/20260420'
LOT_SIZE = 75; STRIKE_INT = 50; EMA_PERIOD = 20; EOD_EXIT = '15:20:00'; YEARS = 5

IV_MIN   = 0.47
BODY_MIN = 0.10

STRIKE_TYPES = ['OTM1', 'ATM', 'ITM1']
ENTRY_TIMES  = ['09:16:02', '09:20:02', '09:25:02', '09:31:02']
TARGET_PCTS  = [0.20, 0.30, 0.40, 0.50]
SL_PCTS      = [0.50, 1.00, 1.50, 2.00]
SPOT_BUFFERS = [0, 25, 50, 75, 100]    # points above PDH for spot SL

# Zones that get spot-based SL treatment
SPOT_SL_KEY     = ('tc_to_pdh', 'bear', 'PE')   # exit when spot > PDH + buffer
SPOT_SL_KEY_BUL = ('tc_to_pdh', 'bull', 'PE')   # exit when spot < BC  - buffer  [v17a NEW]
SPOT_SL_KEYS    = {SPOT_SL_KEY, SPOT_SL_KEY_BUL}

def r2(v): return round(float(v), 2)

def get_strike(atm, opt_type, stype):
    if opt_type == 'CE':
        return {'OTM1': atm+STRIKE_INT, 'ATM': atm, 'ITM1': atm-STRIKE_INT}[stype]
    return {'OTM1': atm-STRIKE_INT, 'ATM': atm, 'ITM1': atm+STRIKE_INT}[stype]

def compute_pivots(h, l, c):
    pp=r2((h+l+c)/3); bc=r2((h+l)/2); tc=r2(2*pp-bc)
    r1=r2(2*pp-l); r2_=r2(pp+(h-l)); r3=r2(r1+(h-l)); r4=r2(r2_+(h-l))
    s1=r2(2*pp-h); s2_=r2(pp-(h-l)); s3=r2(s1-(h-l)); s4=r2(s2_-(h-l))
    return dict(pp=pp,bc=bc,tc=tc,r1=r1,r2=r2_,r3=r3,r4=r4,s1=s1,s2=s2_,s3=s3,s4=s4)

def classify_zone(op, pvt, pdh, pdl):
    r4=pvt['r4']; r3=pvt['r3']; r2=pvt['r2']; r1=pvt['r1']
    tc=pvt['tc']; bc=pvt['bc']
    s1=pvt['s1']; s2=pvt['s2']; s3=pvt['s3']; s4=pvt['s4']
    if   op>r4:  return 'above_r4'
    elif op>r3:  return 'r3_to_r4'
    elif op>r2:  return 'r2_to_r3'
    elif op>r1:  return 'r1_to_r2'
    elif op>pdh: return 'pdh_to_r1'
    elif op>tc:  return 'tc_to_pdh'
    elif op>=bc: return 'within_cpr'
    elif op>pdl: return 'pdl_to_bc'
    elif op>s1:  return 'pdl_to_s1'
    elif op>s2:  return 's1_to_s2'
    elif op>s3:  return 's2_to_s3'
    elif op>s4:  return 's3_to_s4'
    else:        return 'below_s4'

def get_signal(zone, ema_bias):
    if zone in {'above_r4','r3_to_r4','r2_to_r3','r1_to_r2'}: return 'PE'
    if zone == 'pdh_to_r1' and ema_bias == 'bear': return 'PE'
    if zone == 'tc_to_pdh': return 'PE'                        # both biases (spot SL for bear)
    if zone == 'within_cpr' and ema_bias == 'bull': return 'PE'
    if zone == 'within_cpr' and ema_bias == 'bear': return 'CE'
    if zone == 'pdl_to_bc'  and ema_bias == 'bull': return 'PE'  # bear CE DROPPED
    if zone in {'pdl_to_s1','s1_to_s2','s3_to_s4','below_s4'} and ema_bias == 'bear': return 'CE'
    return None

def sim(ts, ps, ep, eod_ns, tgt_pct, sl_pct):
    """Standard % SL with 3-tier lock-in. Returns (pnl, reason, xp, exit_ts)."""
    tgt      = r2(ep*(1-tgt_pct))
    hsl      = r2(ep*(1+sl_pct))
    sl_level = hsl
    max_decay = 0.0
    for i in range(len(ts)):
        t = ts[i]; p = ps[i]
        if t >= eod_ns:
            return r2((ep-p)*LOT_SIZE), 'eod', p, t
        decay = (ep-p)/ep
        if decay > max_decay: max_decay = decay
        if   max_decay >= 0.60: sl_level = min(sl_level, r2(ep*(1-max_decay*0.95)))
        elif max_decay >= 0.40: sl_level = min(sl_level, r2(ep*0.80))
        elif max_decay >= 0.25: sl_level = min(sl_level, ep)
        if p <= tgt:
            return r2((ep-p)*LOT_SIZE), 'target', p, t
        if p >= sl_level:
            rsn = 'lockin_sl' if sl_level < hsl else 'hard_sl'
            return r2((ep-p)*LOT_SIZE), rsn, p, t
    return r2((ep-ps[-1])*LOT_SIZE), 'eod', ps[-1], ts[-1]

def sim_spot_sl(opt_ts, opt_ps, spot_ts, spot_ps, ep, eod_ns, tgt_pct, sl_spot_level,
                sl_trigger='above'):
    """Spot-based SL for PE sell.
    sl_trigger='above': exit when spot >= sl_spot_level (bear PE: PDH+buf)
    sl_trigger='below': exit when spot <= sl_spot_level (bull PE: BC-buf)
    Lock-in trail still active for profit protection.
    Uses two-pointer (O(n)) instead of per-tick searchsorted (O(n log m)).
    """
    tgt      = r2(ep*(1-tgt_pct))
    hsl_wide = r2(ep*5.0)
    sl_level = hsl_wide
    max_decay = 0.0
    spot_i   = 0
    n_spot   = len(spot_ts)
    for i in range(len(opt_ts)):
        t = opt_ts[i]; p = opt_ps[i]
        if t >= eod_ns:
            return r2((ep-p)*LOT_SIZE), 'eod', p, t
        decay = (ep-p)/ep
        if decay > max_decay: max_decay = decay
        if   max_decay >= 0.60: sl_level = min(sl_level, r2(ep*(1-max_decay*0.95)))
        elif max_decay >= 0.40: sl_level = min(sl_level, r2(ep*0.80))
        elif max_decay >= 0.25: sl_level = min(sl_level, ep)
        if p <= tgt:
            return r2((ep-p)*LOT_SIZE), 'target', p, t
        if p >= sl_level:
            return r2((ep-p)*LOT_SIZE), 'lockin_sl', p, t
        # advance spot pointer (two-pointer: both arrays sorted)
        while spot_i < n_spot - 1 and spot_ts[spot_i + 1] <= t:
            spot_i += 1
        sp = spot_ps[spot_i]
        if (sl_trigger == 'above' and sp >= sl_spot_level) or \
           (sl_trigger == 'below' and sp <= sl_spot_level):
            return r2((ep-p)*LOT_SIZE), 'spot_sl', p, t
    return r2((ep-opt_ps[-1])*LOT_SIZE), 'eod', opt_ps[-1], opt_ts[-1]

# ── Pass 1: OHLC + EMA ────────────────────────────────────────────
print(f"Pass 1: daily OHLC + EMA({EMA_PERIOD}) ({YEARS}yr)...")
all_dates = list_trading_dates()
latest    = pd.Timestamp(all_dates[-1][:4]+'-'+all_dates[-1][4:6]+'-'+all_dates[-1][6:])
dates_Nyr = [d for d in all_dates
             if pd.Timestamp(d[:4]+'-'+d[4:6]+'-'+d[6:]) >= latest-pd.DateOffset(years=YEARS)]

extra = max(0, all_dates.index(dates_Nyr[0]) - EMA_PERIOD - 5)
t0 = time.time()
daily_ohlc = {}
for d in all_dates[extra:]:
    tks = load_spot_data(d, 'NIFTY')
    if tks is None: continue
    daily_ohlc[d] = (round(tks['price'].max(),2), round(tks['price'].min(),2),
                     round(tks[tks['time']<='15:30:00']['price'].iloc[-1],2),
                     round(tks[tks['time']>='09:15:00']['price'].iloc[0],2))

close_s = pd.Series({d:v[2] for d,v in daily_ohlc.items()}).sort_index()
ema_s   = close_s.ewm(span=EMA_PERIOD, adjust=False).mean()
print(f"  {len(daily_ohlc)} days in {time.time()-t0:.0f}s")

# ── Pass 2: collect tick data ──────────────────────────────────────
print("Pass 2: collecting tick data...")
zone_data = {}
t1 = time.time(); total_q = 0

for i, date in enumerate(dates_Nyr):
    idx = all_dates.index(date)
    if idx < 1: continue
    prev = all_dates[idx-1]
    if prev not in daily_ohlc or date not in daily_ohlc: continue

    ph,pl,pc,pop = daily_ohlc[prev]
    pvt       = compute_pivots(ph,pl,pc)
    prev_body = round(abs(pc-pop)/pop*100, 3)

    _,_,_,today_op = daily_ohlc[date]
    e20  = ema_s.get(date, np.nan)
    if np.isnan(e20): continue
    bias = 'bull' if today_op > e20 else 'bear'

    zone = classify_zone(today_op, pvt, ph, pl)
    opt  = get_signal(zone, bias)
    if opt is None: continue

    dstr     = f'{date[:4]}-{date[4:6]}-{date[6:]}'
    expiries = list_expiry_dates(date)
    if not expiries: continue
    expiry   = expiries[0]
    exp_dt   = pd.Timestamp(f'20{expiry[:2]}-{expiry[2:4]}-{expiry[4:]}')
    dte      = (exp_dt - pd.Timestamp(dstr)).days
    if dte == 0: continue

    atm    = int(round(today_op/STRIKE_INT)*STRIKE_INT)
    eod_ns = pd.Timestamp(dstr+' '+EOD_EXIT).value

    # Quality filter
    ref_strike = get_strike(atm, opt, 'ITM1')
    ot_ref = load_tick_data(date, f'NIFTY{expiry}{ref_strike}{opt}', '09:15:00', '15:30:00')
    if ot_ref is None or ot_ref.empty: continue
    ot_ref['dt'] = pd.to_datetime(dstr+' '+ot_ref['time'])
    ref_e = ot_ref[ot_ref['dt'] >= pd.Timestamp(dstr+' 09:16:02')]
    if ref_e.empty: continue
    ep_ref = float(ref_e['price'].iloc[0])
    if ep_ref <= 0: continue
    iv_proxy = round(ep_ref/today_op*100, 3)
    if iv_proxy <= IV_MIN: continue
    if prev_body <= BODY_MIN: continue

    # Load 3 strike types
    entry_ns = {et: pd.Timestamp(dstr+' '+et).value for et in ENTRY_TIMES}
    strike_ticks = {}
    for st in STRIKE_TYPES:
        strike = get_strike(atm, opt, st)
        ot = load_tick_data(date, f'NIFTY{expiry}{strike}{opt}', '09:15:00', '15:30:00')
        if ot is None or ot.empty: continue
        ot['dt'] = pd.to_datetime(dstr+' '+ot['time'])
        strike_ticks[st] = (
            ot['dt'].values.astype('datetime64[ns]').astype('int64'),
            ot['price'].values.astype(float)
        )
    if not strike_ticks: continue

    key = (zone, bias, opt)

    # For SPOT_SL_KEYS: load spot tick data
    spot_arrays = None
    if key in SPOT_SL_KEYS:
        sp_tks = load_spot_data(date, 'NIFTY')
        if sp_tks is not None and not sp_tks.empty:
            sp_tks['dt'] = pd.to_datetime(dstr+' '+sp_tks['time'])
            spot_arrays = (
                sp_tks['dt'].values.astype('datetime64[ns]').astype('int64'),
                sp_tks['price'].values.astype(float)
            )

    zone_data.setdefault(key, []).append(dict(
        dstr=dstr, atm=atm, expiry=expiry, eod_ns=eod_ns,
        entry_ns=entry_ns, strike_ticks=strike_ticks,
        today_op=today_op, prev_body=prev_body, dte=dte,
        iv_proxy=iv_proxy, pdh=ph, pdl=pl, bc=pvt['bc'],
        spot_arrays=spot_arrays
    ))
    total_q += 1

    if (i+1) % 200 == 0:
        print(f"  {i+1}/{len(dates_Nyr)}  groups={len(zone_data)}  q={total_q}  ({time.time()-t1:.0f}s)")

print(f"\nGroups: {len(zone_data)}  Total qualifying: {total_q}")
for k,v in sorted(zone_data.items()):
    print(f"  {k[0]:20} {k[1]:4} {k[2]}  N={len(v)}")

# ── Pass 3: grid search ────────────────────────────────────────────
std_combos  = list(itertools.product(STRIKE_TYPES, ENTRY_TIMES, TARGET_PCTS, SL_PCTS))
spot_combos = list(itertools.product(STRIKE_TYPES, ENTRY_TIMES, TARGET_PCTS, SPOT_BUFFERS))

print(f"\nPass 3: grid search — std={len(std_combos)} / spot={len(spot_combos)} combos per zone...")

best_params = {}
opt_report  = []

for key, days in sorted(zone_data.items()):
    zone, bias, opt = key
    best_sh = -999; best_combo = None; best_stats = None
    use_spot = (key in SPOT_SL_KEYS)
    combos   = spot_combos if use_spot else std_combos

    for combo in combos:
        st, et, tp, sp_param = combo
        pnls = []
        for day in days:
            if st not in day['strike_ticks']: continue
            all_ts, all_ps = day['strike_ticks'][st]
            ens  = day['entry_ns'][et]
            mask = all_ts >= ens
            if not mask.any(): continue
            i0   = int(np.argmax(mask))
            ep   = r2(all_ps[i0])
            if ep <= 0: continue

            if use_spot:
                # bear PE: exit when spot > PDH + buffer
                # bull PE: exit when spot < BC  - buffer
                if key == SPOT_SL_KEY:
                    sl_spot = day['pdh'] + sp_param
                else:
                    sl_spot = day['bc'] - sp_param
                spot_arr = day['spot_arrays']
                if spot_arr is None: continue
                sp_ts, sp_ps = spot_arr
                sl_trigger = 'above' if key == SPOT_SL_KEY else 'below'
                pnl,_,_,_ = sim_spot_sl(all_ts[i0:], all_ps[i0:], sp_ts, sp_ps,
                                         ep, day['eod_ns'], tp, sl_spot,
                                         sl_trigger=sl_trigger)
            else:
                pnl,_,_,_ = sim(all_ts[i0:], all_ps[i0:], ep, day['eod_ns'], tp, sp_param)
            pnls.append(pnl)

        if len(pnls) < 3: continue
        arr = np.array(pnls)
        avg = arr.mean(); std = arr.std()
        wr  = (arr>0).mean()*100
        pf  = round(arr[arr>0].sum()/abs(arr[arr<=0].sum()),2) if (arr<=0).any() else 999
        sh  = avg/std*np.sqrt(len(arr)) if std>0 else 0

        if sh > best_sh or (sh==best_sh and avg>(best_stats['avg'] if best_stats else -999)):
            best_sh = sh; best_combo = combo
            best_stats = dict(n=len(pnls), wr=round(wr,1), pf=pf,
                              avg=round(avg,0), tot=round(arr.sum(),0), sharpe=round(sh,2))

    if best_combo:
        best_params[key] = best_combo
        st,et,tp,sp_param = best_combo
        sl_label = f'spot+{sp_param}' if use_spot else f'{sp_param:.0%}'
        opt_report.append(dict(zone=zone, ema=bias, opt=opt,
                               strike=st, entry=et, target_pct=tp, sl_param=sp_param,
                               sl_type='spot' if use_spot else 'pct', **best_stats))
        print(f"  {zone:20}|{bias:4}|{opt}  n={best_stats['n']:3}"
              f"  stk={st:4} et={et} tgt={tp:.0%} sl={sl_label}"
              f"  WR={best_stats['wr']:5.1f}%  PF={best_stats['pf']:4.2f}"
              f"  avg=₹{best_stats['avg']:>7,.0f}  sh={best_stats['sharpe']:.2f}")

# ── Pass 4: final backtest ─────────────────────────────────────────
print("\nPass 4: Final backtest...")
trades_out = []

for date in dates_Nyr:
    idx = all_dates.index(date)
    if idx < 1: continue
    prev = all_dates[idx-1]
    if prev not in daily_ohlc or date not in daily_ohlc: continue

    ph,pl,pc,pop = daily_ohlc[prev]
    pvt       = compute_pivots(ph,pl,pc)
    prev_body = round(abs(pc-pop)/pop*100, 3)

    _,_,_,today_op = daily_ohlc[date]
    e20  = ema_s.get(date, np.nan)
    if np.isnan(e20): continue
    bias = 'bull' if today_op > e20 else 'bear'
    zone = classify_zone(today_op, pvt, ph, pl)
    opt  = get_signal(zone, bias)
    if opt is None: continue

    key = (zone, bias, opt)
    if key not in best_params: continue
    st, et, tp, sp_param = best_params[key]
    use_spot = (key in SPOT_SL_KEYS)

    dstr     = f'{date[:4]}-{date[4:6]}-{date[6:]}'
    expiries = list_expiry_dates(date)
    if not expiries: continue
    expiry   = expiries[0]
    exp_dt   = pd.Timestamp(f'20{expiry[:2]}-{expiry[2:4]}-{expiry[4:]}')
    if (exp_dt - pd.Timestamp(dstr)).days == 0: continue

    atm    = int(round(today_op/STRIKE_INT)*STRIKE_INT)
    strike = get_strike(atm, opt, st)
    ot = load_tick_data(date, f'NIFTY{expiry}{strike}{opt}', '09:15:00', '15:30:00')
    if ot is None or ot.empty: continue
    ot['dt'] = pd.to_datetime(dstr+' '+ot['time'])
    ot_e = ot[ot['dt'] >= pd.Timestamp(dstr+' '+et)].reset_index(drop=True)
    if ot_e.empty: continue

    ts = ot_e['dt'].values.astype('datetime64[ns]').astype('int64')
    ps = ot_e['price'].values.astype(float)
    ep = r2(ps[0])
    if ep <= 0: continue

    iv_proxy = round(ep/today_op*100, 3)
    if iv_proxy <= IV_MIN: continue
    if prev_body <= BODY_MIN: continue

    eod_ns = pd.Timestamp(dstr+' '+EOD_EXIT).value

    if use_spot:
        sp_tks = load_spot_data(date, 'NIFTY')
        if sp_tks is None or sp_tks.empty: continue
        sp_tks['dt'] = pd.to_datetime(dstr+' '+sp_tks['time'])
        sp_ts = sp_tks['dt'].values.astype('datetime64[ns]').astype('int64')
        sp_ps = sp_tks['price'].values.astype(float)
        pvt = compute_pivots(ph, pl, pc)
        if key == SPOT_SL_KEY:
            sl_spot = ph + sp_param
            sl_trigger = 'above'
        else:
            sl_spot = pvt['bc'] - sp_param
            sl_trigger = 'below'
        pnl, rsn, xp, _ = sim_spot_sl(ts, ps, sp_ts, sp_ps, ep, eod_ns, tp, sl_spot,
                                        sl_trigger=sl_trigger)
    else:
        pnl, rsn, xp, _ = sim(ts, ps, ep, eod_ns, tp, sp_param)

    trades_out.append(dict(
        date=dstr, zone=zone, ema_bias=bias, opt=opt,
        atm=atm, strike=strike, strike_type=st,
        entry_time=et, target_pct=tp, sl_param=sp_param,
        sl_type='spot' if use_spot else 'pct',
        ep=ep, xp=r2(xp), exit_reason=rsn, pnl=pnl,
        iv_proxy=iv_proxy, prev_body=prev_body, dte=(exp_dt-pd.Timestamp(dstr)).days
    ))

df = pd.DataFrame(trades_out)
df['date'] = pd.to_datetime(df['date'])
df = df.sort_values('date').reset_index(drop=True)
df.to_csv(f'{OUT_DIR}/38_zone_v17a_trades.csv', index=False)

# ── Results ───────────────────────────────────────────────────────
n     = len(df); total = df['pnl'].sum()
wr    = round((df['pnl']>0).mean()*100, 1)
wins  = df[df['pnl']>0]['pnl'].sum()
loss_ = abs(df[df['pnl']<=0]['pnl'].sum())
pf    = round(wins/loss_, 2) if loss_>0 else 999
eq    = df.set_index('date')['pnl'].cumsum()
dds   = eq - eq.cummax(); max_dd = round(abs(dds.min()), 0)
daily = df.groupby('date')['pnl'].sum()
sh    = round(daily.mean()/daily.std()*np.sqrt(250), 2) if daily.std()>0 else 0
cal   = round(total/max_dd, 2) if max_dd>0 else 0

print(f"\n{'═'*68}")
print(f"  Zone v17a — Spot SL for tc_to_pdh bear+bull PE")
print(f"{'═'*68}")
print(f"  Period  : {dates_Nyr[0]} → {dates_Nyr[-1]}")
print(f"  Trades  : {n}  ({n//YEARS}/yr)")
print(f"  Total   : ₹{total:,.0f}  (₹{total/YEARS:,.0f}/yr)")
print(f"  Win%    : {wr}%   PF: {pf}   Avg/trade: ₹{total/n:,.0f}")
print(f"  Max DD  : ₹{max_dd:,.0f}")
print(f"  Sharpe  : {sh}   Calmar: {cal}")
print(f"{'═'*68}")

# Comparison
v11 = pd.read_csv(f'{OUT_DIR}/28_zone_v11_trades.csv')
v11d = v11.groupby('date')['pnl'].sum()
v11sh = round(v11d.mean()/v11d.std()*np.sqrt(250), 2)
v11eq = v11['pnl'].cumsum(); v11dd = round((v11eq.cummax()-v11eq).max(), 0)
v8 = pd.read_csv(f'{OUT_DIR}/21_zone5yr_trades.csv')
v8d = v8.groupby('date')['pnl'].sum()
v8sh = round(v8d.mean()/v8d.std()*np.sqrt(250), 2)
v8eq = v8['pnl'].cumsum(); v8dd = round((v8eq.cummax()-v8eq).max(), 0)
print(f"\n  v8  : {len(v8):>3} trades/5yr ({len(v8)//5}/yr)  ₹{v8['pnl'].sum():>9,.0f}  WR={100*(v8['pnl']>0).mean():.1f}%  DD=₹{v8dd:,.0f}  Sh={v8sh}")
print(f"  v11 : {len(v11):>3} trades/5yr ({len(v11)//5}/yr)  ₹{v11['pnl'].sum():>9,.0f}  WR={100*(v11['pnl']>0).mean():.1f}%  DD=₹{v11dd:,.0f}  Sh={v11sh}")
print(f"  v13 : 314 trades/5yr (62/yr)  ₹  384,574  WR=72.6%  DD=₹16,140  Sh=5.53  Cal=23.83")
print(f"  v17a: {n:>3} trades/5yr ({n//YEARS}/yr)  ₹{total:>9,.0f}  WR={wr}%  DD=₹{max_dd:,.0f}  Sh={sh}  Cal={cal}")

# tc_to_pdh bear PE spot_sl stats
spot_zone = df[df['zone']=='tc_to_pdh'][df['ema_bias']=='bear']
if len(spot_zone)>0:
    print(f"\n  tc_to_pdh bear PE (spot SL): {len(spot_zone)} trades | WR={100*(spot_zone['pnl']>0).mean():.1f}% | avg=₹{spot_zone['pnl'].mean():,.0f}")
    print(f"  Exit breakdown:")
    for rsn, g in spot_zone.groupby('exit_reason'):
        print(f"    {rsn:<12}: {len(g):>3}  avg=₹{g['pnl'].mean():>7,.0f}")

# Per zone
print("\nPer-zone breakdown:")
print(f"  {'Zone':<20} {'EMA':<5} {'Opt':<4} {'Stk':<5} {'N':>4} {'WR%':>6} {'Avg₹':>8} {'Tot₹':>10}")
print("  "+"─"*65)
grp = df.groupby(['zone','ema_bias','opt','strike_type']).agg(
    n=('pnl','count'), wr=('pnl',lambda x:round((x>0).mean()*100,1)),
    avg=('pnl','mean'), tot=('pnl','sum')
).reset_index().sort_values('tot', ascending=False)
for _, r in grp.iterrows():
    print(f"  {r['zone']:<20} {r['ema_bias']:<5} {r['opt']:<4} {r['strike_type']:<5}"
          f" {r['n']:>4} {r['wr']:>5.1f}% {r['avg']:>8,.0f} {r['tot']:>10,.0f}")

# Yearly
print(f"\nYearly P&L:")
df['year'] = df['date'].dt.year
for yr, g in df.groupby('year'):
    bar = '█' * max(0, int(g['pnl'].sum()/8000))
    print(f"  {yr}  {g['pnl'].sum():>+10,.0f}  ({len(g)} trades, WR={100*(g['pnl']>0).mean():.0f}%)  {bar}")

# Monthly
df['month'] = df['date'].dt.to_period('M')
monthly = df.groupby('month')['pnl'].sum()
print(f"\nMonthly P&L ({len(monthly)} months, profitable: {(monthly>0).sum()}/{len(monthly)}):")
for m, v in monthly.items():
    bar  = '█' * max(0, int(abs(v)/4000))
    sign = '+' if v >= 0 else ''
    print(f"  {m}  {sign}{v:>9,.0f}  {bar}")

# Exit reasons
print("\nExit reasons:")
for rsn, g in df.groupby('exit_reason'):
    print(f"  {rsn:<12}: {len(g):>4}  WR={100*(g['pnl']>0).mean():.0f}%  avg=₹{g['pnl'].mean():>7,.0f}")

# Equity chart
plot_equity(eq, dds, '38_zone_v17a_equity', folder_path=OUT_DIR,
    title=f'Zone v17a — {n} trades | ₹{total:,.0f} | Sh={sh} | DD ₹{max_dd:,.0f}')

pd.DataFrame(opt_report).to_csv(f'{OUT_DIR}/38b_zone_v17a_params.csv', index=False)
print(f"\n✓ Saved: 38_zone_v17a_trades.csv  38b_zone_v17a_params.csv")
