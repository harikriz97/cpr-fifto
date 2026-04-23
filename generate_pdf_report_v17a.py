"""
v17a CPR Strategy — Full PDF Report Generator
==============================================
v17a = v13 + Spot SL for tc_to_pdh bull PE (NEW)
       spot SL exits when spot < BC - 75pts on bull bias days
"""
import sys, os
sys.path.insert(0, '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell')

import pandas as pd
import numpy as np
from datetime import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Table, TableStyle,
                                 Spacer, HRFlowable, PageBreak, KeepTogether)
from reportlab.platypus.flowables import HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT

OUT_DIR  = '/home/hesham/workspace/share/super_agent_data/WfLlFj/01_cpr_pivot_ema_sell/data/20260420'
PDF_PATH = f'{OUT_DIR}/40_zone_v17a_full_report.pdf'

# ── Load data ──────────────────────────────────────────────────────
df        = pd.read_csv(f'{OUT_DIR}/38_zone_v17a_trades.csv', parse_dates=['date'])
df        = df.sort_values('date').reset_index(drop=True)
params_df = pd.read_csv(f'{OUT_DIR}/38b_zone_v17a_params.csv')

v8   = pd.read_csv(f'{OUT_DIR}/21_zone5yr_trades.csv')
v11  = pd.read_csv(f'{OUT_DIR}/28_zone_v11_trades.csv')
v13  = pd.read_csv(f'{OUT_DIR}/30_zone_v13_trades.csv')

def r2(v): return round(float(v), 2)

def get_stats(pnl_series):
    arr = np.array(pnl_series); n = len(arr); tot = arr.sum()
    wr  = (arr > 0).mean() * 100; avg = arr.mean()
    wins = arr[arr > 0]; loss_ = arr[arr <= 0]
    pf   = round(wins.sum()/abs(loss_.sum()),2) if len(loss_)>0 and loss_.sum()!=0 else 999
    eq   = np.cumsum(arr); dd = eq - np.maximum.accumulate(eq); max_dd = abs(dd.min())
    sh   = avg/arr.std()*np.sqrt(n) if arr.std()>0 else 0
    cal  = tot/max_dd if max_dd>0 else 0
    avg_win  = wins.mean()  if len(wins)>0  else 0
    avg_loss = loss_.mean() if len(loss_)>0 else 0
    max_win  = wins.max()   if len(wins)>0  else 0
    max_loss = loss_.min()  if len(loss_)>0 else 0
    return dict(n=n, tot=tot, wr=round(wr,1), avg=round(avg,0),
                pf=pf, max_dd=round(max_dd,0), sharpe=round(sh,2),
                calmar=round(cal,2), avg_win=round(avg_win,0),
                avg_loss=round(avg_loss,0), max_win=round(max_win,0),
                max_loss=round(max_loss,0))

stats    = get_stats(df['pnl'])
daily    = df.groupby('date')['pnl'].sum()
daily_sh = round(daily.mean()/daily.std()*np.sqrt(250),2) if daily.std()>0 else 0

# ── Styles ─────────────────────────────────────────────────────────
DARK  = colors.HexColor('#1a237e'); TEAL  = colors.HexColor('#00695c')
RED   = colors.HexColor('#b71c1c'); GOLD  = colors.HexColor('#f57f17')
LGREY = colors.HexColor('#f5f5f5'); DGREY = colors.HexColor('#424242')
WHITE = colors.white; GREEN = colors.HexColor('#2e7d32')
V17A  = colors.HexColor('#004d40')  # deep teal for v17a accent

title_style    = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=22,
                                 textColor=WHITE, alignment=TA_CENTER, spaceAfter=6)
subtitle_style = ParagraphStyle('subtitle', fontName='Helvetica', fontSize=11,
                                 textColor=colors.HexColor('#bbdefb'), alignment=TA_CENTER)
h1_style  = ParagraphStyle('h1', fontName='Helvetica-Bold', fontSize=14,
                             textColor=DARK, spaceBefore=14, spaceAfter=6)
h2_style  = ParagraphStyle('h2', fontName='Helvetica-Bold', fontSize=11,
                             textColor=TEAL, spaceBefore=10, spaceAfter=4)
body_style = ParagraphStyle('body', fontName='Helvetica', fontSize=9,
                              textColor=DGREY, spaceAfter=3)
caption_style = ParagraphStyle('caption', fontName='Helvetica-Oblique', fontSize=8,
                                textColor=colors.grey, alignment=TA_CENTER)

def hdr(text, style=h1_style): return Paragraph(text, style)
def hr(): return HRFlowable(width='100%', thickness=1, color=colors.HexColor('#e0e0e0'),
                             spaceAfter=6, spaceBefore=2)
def fmt_inr(v, signed=False):
    sign = '+' if v >= 0 and signed else ''
    return f"{sign}₹{v:,.0f}"

def tbl(data, col_widths, header_bg=DARK, stripe=True, align_cols=None):
    t = Table(data, colWidths=col_widths)
    style_cmds = [
        ('BACKGROUND', (0,0), (-1,0), header_bg),
        ('TEXTCOLOR',  (0,0), (-1,0), WHITE),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,0), 8),
        ('ALIGN',      (0,0), (-1,0), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,0), 5), ('TOPPADDING', (0,0), (-1,0), 5),
        ('FONTNAME',   (0,1), (-1,-1), 'Helvetica'),
        ('FONTSIZE',   (0,1), (-1,-1), 8),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [WHITE, LGREY] if stripe else [WHITE]),
        ('GRID',       (0,0), (-1,-1), 0.4, colors.HexColor('#bdbdbd')),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,1), (-1,-1), 3), ('BOTTOMPADDING', (0,1), (-1,-1), 3),
    ]
    if align_cols:
        for col, align in align_cols.items():
            style_cmds.append(('ALIGN', (col,1), (col,-1), align))
    t.setStyle(TableStyle(style_cmds))
    return t

# ── Build document ─────────────────────────────────────────────────
doc   = SimpleDocTemplate(PDF_PATH, pagesize=A4,
                           leftMargin=1.5*cm, rightMargin=1.5*cm,
                           topMargin=1.5*cm, bottomMargin=1.5*cm)
W     = A4[0] - 3*cm
story = []

# ══════════════════════════════════════════════════════════════════
# PAGE 1 — Cover / Summary
# ══════════════════════════════════════════════════════════════════
banner_data = [
    [Paragraph('CPR Pivot Zone Strategy — v17a', title_style)],
    [Paragraph('Full Backtest Report  |  NIFTY Weekly Options  |  5-Year Analysis', subtitle_style)],
    [Paragraph(f'Generated: {datetime.now().strftime("%d %b %Y %H:%M")}  |  '
               f'Period: Apr 2021 – Apr 2026  |  NEW: Spot SL for bull PE', subtitle_style)]
]
banner = Table(banner_data, colWidths=[W])
banner.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,-1), V17A),
    ('TOPPADDING', (0,0), (-1,-1), 10),
    ('BOTTOMPADDING', (0,0), (-1,-1), 10),
    ('ROUNDEDCORNERS', [6]),
]))
story.append(banner)
story.append(Spacer(1, 0.4*cm))

# Key Metrics boxes
def metric_box(label, value, color=DARK):
    d = [[Paragraph(f'<b>{value}</b>', ParagraphStyle('mv', fontName='Helvetica-Bold',
          fontSize=16, textColor=WHITE, alignment=TA_CENTER))],
         [Paragraph(label, ParagraphStyle('ml', fontName='Helvetica', fontSize=8,
          textColor=colors.HexColor('#b0bec5'), alignment=TA_CENTER))]]
    t = Table(d, colWidths=[(W-0.6*cm)/6])
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),color),
                            ('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),
                            ('ROUNDEDCORNERS',[4])]))
    return t

boxes = [
    metric_box('Total P&L',      f'₹{stats["tot"]:,.0f}',     V17A),
    metric_box('Sharpe Ratio',   str(daily_sh),                TEAL),
    metric_box('Calmar Ratio',   str(stats["calmar"]),         GREEN),
    metric_box('Win Rate',       f'{stats["wr"]}%',            GOLD),
    metric_box('Max Drawdown',   f'₹{stats["max_dd"]:,.0f}',  colors.HexColor('#7b1fa2')),
    metric_box('Total Trades',   str(stats["n"]),              colors.HexColor('#0277bd')),
]
boxes_row = Table([[b for b in boxes]], colWidths=[(W-0.6*cm)/6]*6, hAlign='LEFT')
boxes_row.setStyle(TableStyle([('LEFTPADDING',(0,0),(-1,-1),3),('RIGHTPADDING',(0,0),(-1,-1),3)]))
story.append(boxes_row)
story.append(Spacer(1, 0.3*cm))

# Complete Performance Metrics
story.append(hdr('1. Complete Performance Metrics'))
story.append(hr())

years = 5
perf_data = [
    ['Metric', 'Value', 'Metric', 'Value'],
    ['Period', f'{df["date"].min().strftime("%d %b %Y")} → {df["date"].max().strftime("%d %b %Y")}',
     'Instrument', 'NIFTY Weekly Options (CE/PE Sell)'],
    ['Total Trades', str(stats['n']), 'Trades / Year', f'{stats["n"]//years}'],
    ['Total P&L', fmt_inr(stats['tot']), 'P&L / Year', fmt_inr(stats['tot']//years)],
    ['P&L / Trade (avg)', fmt_inr(stats['avg']), 'P&L / Month (avg)', fmt_inr(stats['tot']//(years*12))],
    ['Win Rate', f'{stats["wr"]}%', 'Profit Factor', str(stats["pf"])],
    ['Avg Winning Trade', fmt_inr(stats["avg_win"]), 'Avg Losing Trade', fmt_inr(stats["avg_loss"])],
    ['Max Single Win', fmt_inr(stats["max_win"]), 'Max Single Loss', fmt_inr(stats["max_loss"])],
    ['Max Drawdown', fmt_inr(stats["max_dd"]), 'Recovery Factor', f'{round(stats["tot"]/stats["max_dd"],2)}'],
    ['Sharpe Ratio (daily×√250)', str(daily_sh), 'Calmar Ratio', str(stats["calmar"])],
    ['Lot Size', '75', 'Strike Interval', '50 pts'],
    ['EOD Exit', '15:20:00', 'Filters', 'IV proxy > 0.47, Prev body > 0.10%'],
    ['v17a Key Change', 'Spot SL for tc_to_pdh bull PE (spot < BC − 75pts)',
     'Hard SL Trades', f'{(df["exit_reason"]=="hard_sl").sum()} (↓ from 19 in v13)'],
]
perf_t = tbl(perf_data, [W*0.28, W*0.22, W*0.28, W*0.22], align_cols={1:'RIGHT', 3:'RIGHT'})
story.append(perf_t)
story.append(Spacer(1, 0.3*cm))

# Version Comparison
story.append(hdr('2. Version Comparison'))
story.append(hr())

def vstats(pnl_s):
    arr = np.array(pnl_s); eq = np.cumsum(arr); dd = eq - np.maximum.accumulate(eq)
    d   = df.groupby('date')['pnl'].sum() if False else pd.Series(pnl_s)
    return dict(n=len(arr), tot=arr.sum(), wr=round((arr>0).mean()*100,1),
                dd=round(abs(dd.min()),0),
                sh=round(arr.mean()/arr.std()*np.sqrt(len(arr)),2) if arr.std()>0 else 0)

s8=vstats(v8['pnl']); s11=vstats(v11['pnl']); s13=vstats(v13['pnl']); s17=vstats(df['pnl'])

v8d  = v8.groupby('date')['pnl'].sum()
v11d = v11.groupby('date')['pnl'].sum()
v13d = v13.groupby('date')['pnl'].sum()

def dsh(d): return round(d.mean()/d.std()*np.sqrt(250),2) if d.std()>0 else 0
v8_sh=dsh(v8d); v11_sh=dsh(v11d); v13_sh=dsh(v13d); v17_sh=daily_sh

def v_calmar(pnl_s):
    arr=np.array(pnl_s); eq=np.cumsum(arr); dd=eq-np.maximum.accumulate(eq)
    mdd=abs(dd.min()); return round(arr.sum()/mdd,2) if mdd>0 else 0

ver_data = [
    ['Version', 'Key Change', 'Trades', 'Total P&L', 'WR%', 'Max DD', 'Sharpe', 'Calmar'],
    ['v8',   'CPR zones + EMA bias',             str(s8['n']),  fmt_inr(s8['tot']),  f'{s8["wr"]}%',  fmt_inr(s8['dd']),  str(v8_sh),  str(v_calmar(v8['pnl']))],
    ['v11',  'v8 + 3-tier lock-in trail',        str(s11['n']), fmt_inr(s11['tot']), f'{s11["wr"]}%', fmt_inr(s11['dd']), str(v11_sh), str(v_calmar(v11['pnl']))],
    ['v13',  'v11 + Spot SL (bear PE)',           str(s13['n']), fmt_inr(s13['tot']), f'{s13["wr"]}%', fmt_inr(s13['dd']), str(v13_sh), '23.83'],
    ['v17a', 'v13 + Spot SL (bull PE) ★',        str(s17['n']), fmt_inr(s17['tot']), f'{s17["wr"]}%', fmt_inr(s17['dd']), str(v17_sh), str(stats['calmar'])],
]
ver_t = tbl(ver_data, [W*0.07, W*0.32, W*0.08, W*0.14, W*0.08, W*0.12, W*0.10, W*0.09],
             align_cols={2:'CENTER',3:'RIGHT',4:'CENTER',5:'RIGHT',6:'CENTER',7:'CENTER'})
ver_t.setStyle(TableStyle([
    ('BACKGROUND', (0,4), (-1,4), colors.HexColor('#e0f2f1')),
    ('TEXTCOLOR',  (0,4), (-1,4), V17A),
    ('FONTNAME',   (0,4), (-1,4), 'Helvetica-Bold'),
]))
story.append(ver_t)
story.append(PageBreak())

# ══════════════════════════════════════════════════════════════════
# PAGE 2 — Zone Parameters + Per-Zone + Yearly
# ══════════════════════════════════════════════════════════════════

story.append(hdr('3. Optimised Zone Parameters (v17a)'))
story.append(hr())
story.append(Paragraph('Best params per zone from grid search (Sharpe maximised). '
                        'Bear PE and Bull PE in tc_to_pdh use spot-based SL.', body_style))
story.append(Spacer(1, 0.2*cm))

param_data = [['Zone', 'EMA', 'Opt', 'Strike', 'Entry', 'Target', 'SL', 'N', 'WR%', 'Avg₹', 'Sharpe']]
for _, r in params_df.sort_values('tot', ascending=False).iterrows():
    if r['sl_type'] == 'spot':
        sl_str = f'Spot+{int(r["sl_param"])}pts'
    elif r['sl_type'] == 'time+spot':
        sl_str = f'Spot+{int(r["sl_param"])}+TS'
    else:
        sl_str = f'{r["sl_param"]:.0%}'
    param_data.append([
        r['zone'], r['ema'].upper(), r['opt'], r['strike'],
        r['entry'], f'{r["target_pct"]:.0%}', sl_str,
        str(int(r['n'])), f'{r["wr"]:.1f}%', f'₹{r["avg"]:,.0f}', str(r['sharpe'])
    ])

param_t = tbl(param_data, [W*0.17,W*0.06,W*0.05,W*0.07,W*0.10,W*0.07,W*0.13,W*0.05,W*0.07,W*0.10,W*0.10],
               align_cols={7:'CENTER',8:'CENTER',9:'RIGHT',10:'CENTER'})
# Highlight tc_to_pdh rows (both use spot SL)
for i, (_, r) in enumerate(params_df.sort_values('tot', ascending=False).iterrows(), 1):
    if r['zone'] == 'tc_to_pdh':
        param_t.setStyle(TableStyle([('BACKGROUND',(6,i),(6,i),colors.HexColor('#e3f2fd'))]))
story.append(param_t)
story.append(Spacer(1, 0.3*cm))

story.append(hdr('4. Per-Zone Backtest Breakdown'))
story.append(hr())

grp = df.groupby(['zone','ema_bias','opt','strike_type']).agg(
    n=('pnl','count'), wr=('pnl', lambda x: round((x>0).mean()*100, 1)),
    avg=('pnl','mean'), tot=('pnl','sum'),
    max_win=('pnl','max'), max_loss=('pnl','min')
).reset_index().sort_values('tot', ascending=False)

zone_data_rows = [['Zone', 'Bias', 'Opt', 'Stk', 'N', 'WR%', 'Avg₹', 'Total₹', 'Max Win', 'Max Loss']]
for _, r in grp.iterrows():
    zone_data_rows.append([
        r['zone'], r['ema_bias'].upper(), r['opt'], r['strike_type'],
        str(int(r['n'])), f'{r["wr"]:.1f}%',
        f'₹{r["avg"]:,.0f}', f'₹{r["tot"]:,.0f}',
        f'₹{r["max_win"]:,.0f}', f'₹{r["max_loss"]:,.0f}'
    ])

zone_t = tbl(zone_data_rows, [W*0.18,W*0.06,W*0.05,W*0.07,W*0.05,W*0.08,W*0.10,W*0.12,W*0.14,W*0.15],
              align_cols={4:'CENTER',5:'CENTER',6:'RIGHT',7:'RIGHT',8:'RIGHT',9:'RIGHT'})
story.append(zone_t)
story.append(Spacer(1, 0.3*cm))

story.append(hdr('5. Yearly P&L Analysis'))
story.append(hr())

df['year'] = df['date'].dt.year
yearly = df.groupby('year').agg(
    trades=('pnl','count'), wr=('pnl', lambda x: round((x>0).mean()*100,1)),
    total=('pnl','sum'), avg=('pnl','mean'),
    max_win=('pnl','max'), max_loss=('pnl','min')
).reset_index()

yr_data = [['Year', 'Trades', 'WR%', 'Total P&L', 'Avg/Trade', 'Best Trade', 'Worst Trade', 'Bar']]
for _, r in yearly.iterrows():
    yr_data.append([
        str(int(r['year'])), str(int(r['trades'])), f'{r["wr"]}%',
        fmt_inr(r['total'], signed=True), fmt_inr(r['avg'], signed=True),
        fmt_inr(r['max_win']), fmt_inr(r['max_loss']),
        '█' * max(0, int(r['total']/6000))
    ])

yr_t = tbl(yr_data, [W*0.07,W*0.08,W*0.08,W*0.13,W*0.12,W*0.13,W*0.13,W*0.26],
            align_cols={1:'CENTER',2:'CENTER',3:'RIGHT',4:'RIGHT',5:'RIGHT',6:'RIGHT',7:'LEFT'})
for i, (_, r) in enumerate(yearly.iterrows(), 1):
    bg = colors.HexColor('#c8e6c9') if r['total'] >= 0 else colors.HexColor('#ffcdd2')
    yr_t.setStyle(TableStyle([('BACKGROUND',(3,i),(3,i),bg)]))
story.append(yr_t)
story.append(PageBreak())

# ══════════════════════════════════════════════════════════════════
# PAGE 3 — Monthly + Exit Analysis + Spot SL Deep Dive
# ══════════════════════════════════════════════════════════════════

story.append(hdr('6. Monthly P&L'))
story.append(hr())

df['month'] = df['date'].dt.to_period('M')
monthly = df.groupby('month').agg(
    trades=('pnl','count'), wr=('pnl', lambda x: round((x>0).mean()*100,1)),
    total=('pnl','sum')
).reset_index()
monthly['month_str'] = monthly['month'].astype(str)
profitable = (monthly['total'] > 0).sum()
story.append(Paragraph(
    f'Profitable months: <b>{profitable}/{len(monthly)}</b> '
    f'({round(profitable/len(monthly)*100)}%)', body_style))
story.append(Spacer(1, 0.15*cm))

n_rows = len(monthly); col_size = (n_rows + 2) // 3

def month_col(sub):
    rows = [['Month', 'N', 'WR%', 'P&L']]
    for _, r in sub.iterrows():
        rows.append([r['month_str'], str(int(r['trades'])),
                     f'{r["wr"]}%', fmt_inr(r['total'], signed=True)])
    t = Table(rows, colWidths=[W/3*0.38, W/3*0.16, W/3*0.16, W/3*0.30])
    sc = [('BACKGROUND',(0,0),(-1,0),TEAL),('TEXTCOLOR',(0,0),(-1,0),WHITE),
          ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),7.5),
          ('ALIGN',(1,0),(-1,-1),'RIGHT'),('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#bdbdbd')),
          ('TOPPADDING',(0,0),(-1,-1),2),('BOTTOMPADDING',(0,0),(-1,-1),2),
          ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,LGREY])]
    for i, (_, r) in enumerate(sub.iterrows(), 1):
        bg = colors.HexColor('#c8e6c9') if r['total'] >= 0 else colors.HexColor('#ffcdd2')
        sc.append(('BACKGROUND',(3,i),(3,i),bg))
    t.setStyle(TableStyle(sc))
    return t

c1=month_col(monthly.iloc[:col_size]); c2=month_col(monthly.iloc[col_size:2*col_size])
c3=month_col(monthly.iloc[2*col_size:])
mg = Table([[c1,c2,c3]], colWidths=[W/3,W/3,W/3])
mg.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
                          ('LEFTPADDING',(0,0),(-1,-1),2),('RIGHTPADDING',(0,0),(-1,-1),2)]))
story.append(mg)
story.append(Spacer(1, 0.3*cm))

story.append(hdr('7. Exit Reason Analysis'))
story.append(hr())

exit_grp = df.groupby('exit_reason').agg(
    count=('pnl','count'), wr=('pnl',lambda x:round((x>0).mean()*100,1)),
    avg=('pnl','mean'), total=('pnl','sum'),
    pct=('pnl',lambda x:round(len(x)/len(df)*100,1))
).reset_index().sort_values('count', ascending=False)

exit_data = [['Exit Reason', 'Count', 'Trade%', 'Win Rate', 'Avg P&L', 'Total P&L', 'Notes']]
interp = {
    'target':   'Option decayed to target → full profit',
    'spot_sl':  'Spot crossed threshold → contained loss / early profit lock',
    'lockin_sl':'Lock-in trail triggered → partial profit',
    'eod':      'Held to 15:20 → mixed results',
    'hard_sl':  'Option SL hit (5 remaining from non-spot zones)',
    'time_sl':  'Time-stop triggered (v17b only)',
}
for _, r in exit_grp.iterrows():
    exit_data.append([
        r['exit_reason'], str(int(r['count'])), f'{r["pct"]}%',
        f'{r["wr"]}%', fmt_inr(r['avg'],signed=True), fmt_inr(r['total'],signed=True),
        interp.get(r['exit_reason'],'')
    ])

exit_t = tbl(exit_data, [W*0.13,W*0.08,W*0.08,W*0.09,W*0.13,W*0.13,W*0.36],
              align_cols={1:'CENTER',2:'CENTER',3:'CENTER',4:'RIGHT',5:'RIGHT'})
story.append(exit_t)
story.append(Spacer(1, 0.3*cm))

# Spot SL Deep Dive (both tc_to_pdh zones)
story.append(hdr('8. tc_to_pdh Spot SL Deep Dive — Bear PE & Bull PE'))
story.append(hr())
story.append(Paragraph(
    '<b>Bear PE</b>: Exit when NIFTY spot &gt; PDH + 25pts (bear thesis invalidated → PE decays → often profitable exit). '
    '<b>Bull PE</b>: Exit when NIFTY spot &lt; BC − 75pts (bull thesis fails → PE gains → damage control). '
    'v17a added the Bull PE spot SL, eliminating 14 hard-SL losses (−₹79,714) from v13.',
    body_style))
story.append(Spacer(1, 0.15*cm))

for zone_label, bias in [('Bear PE (spot > PDH+25)', 'bear'), ('Bull PE (spot < BC−75)', 'bull')]:
    sub = df[(df['zone']=='tc_to_pdh') & (df['ema_bias']==bias)]
    if sub.empty: continue
    story.append(Paragraph(f'<b>tc_to_pdh {zone_label}</b>  —  {len(sub)} trades  |  '
                            f'WR={round(100*(sub["pnl"]>0).mean(),1)}%  |  '
                            f'Avg=₹{round(sub["pnl"].mean(),0):,.0f}  |  '
                            f'Total=₹{sub["pnl"].sum():,.0f}', body_style))
    sz_d = [['Exit Type','N','WR%','Avg P&L','Total P&L']]
    for rsn, g in sub.groupby('exit_reason'):
        sz_d.append([rsn, str(len(g)), f'{round(100*(g["pnl"]>0).mean(),1)}%',
                     fmt_inr(g["pnl"].mean(),signed=True), fmt_inr(g["pnl"].sum(),signed=True)])
    sz_t = tbl(sz_d, [W*0.22,W*0.10,W*0.10,W*0.20,W*0.20],
                align_cols={1:'CENTER',2:'CENTER',3:'RIGHT',4:'RIGHT'})
    story.append(sz_t)
    story.append(Spacer(1, 0.2*cm))

story.append(PageBreak())

# ══════════════════════════════════════════════════════════════════
# PAGE 4 — Drawdown + Streak + Strategy Rules
# ══════════════════════════════════════════════════════════════════

story.append(hdr('9. Drawdown Analysis'))
story.append(hr())

eq_s = df.set_index('date')['pnl'].cumsum()
dd_s = eq_s - eq_s.cummax()

in_dd=False; dd_periods=[]; dd_start=None; peak_eq=0; min_dd=0
for d,v in eq_s.items():
    prev_max = eq_s[:d].max() if len(eq_s[:d])>0 else v
    if v >= prev_max:
        if in_dd: dd_periods.append((dd_start,d,min_dd)); in_dd=False
        peak_eq=v
    else:
        if not in_dd: dd_start=d; in_dd=True; min_dd=0
        min_dd=min(min_dd,v-peak_eq)

dd_df2 = pd.DataFrame(dd_periods, columns=['start','end','max_dd'])
dd_df2['dur'] = (pd.to_datetime(dd_df2['end'])-pd.to_datetime(dd_df2['start'])).dt.days
dd_df2 = dd_df2.sort_values('max_dd').head(10)

dd_hdr = [['DD#','Start','End','Duration','Max DD']]
for i,(_, r) in enumerate(dd_df2.iterrows(),1):
    dd_hdr.append([str(i), pd.Timestamp(r['start']).strftime('%d %b %Y'),
                   pd.Timestamp(r['end']).strftime('%d %b %Y'),
                   f'{r["dur"]} days', fmt_inr(r['max_dd'])])
dd_t2 = tbl(dd_hdr,[W*0.07,W*0.20,W*0.20,W*0.20,W*0.33],align_cols={0:'CENTER',3:'CENTER',4:'RIGHT'})
story.append(dd_t2)
story.append(Spacer(1, 0.2*cm))

dd_stats = [
    ['Metric','Value'],
    ['Maximum Drawdown', fmt_inr(abs(dd_s.min()))],
    ['Average Drawdown', fmt_inr(abs(dd_s[dd_s<0].mean())) if (dd_s<0).any() else '₹0'],
    ['Calmar Ratio (Total PnL / Max DD)', str(stats['calmar'])],
    ['Recovery Factor', f'{round(stats["tot"]/stats["max_dd"],2)}×'],
    ['% of capital at risk (₹2L base)', f'{round(stats["max_dd"]/200000*100,1)}%'],
]
story.append(tbl(dd_stats,[W*0.6,W*0.4],align_cols={1:'RIGHT'}))
story.append(Spacer(1,0.3*cm))

story.append(hdr('10. Streak Analysis'))
story.append(hr())

wins_arr=(df['pnl']>0).astype(int).values
mws=mlw=cur=0
for w in wins_arr: cur=cur+1 if w else 0; mws=max(mws,cur)
cur=0
for w in wins_arr: cur=cur+1 if not w else 0; mlw=max(mlw,cur)

streak_data=[['Metric','Value'],
             ['Max Consecutive Wins',str(mws)],['Max Consecutive Losses',str(mlw)],
             ['Avg trades/month',f'{round(stats["n"]/(years*12),1)}'],
             ['Avg trades/week',f'{round(stats["n"]/(years*52),1)}'],
             ['Profitable months',f'{profitable}/{len(monthly)} ({round(profitable/len(monthly)*100)}%)']]
story.append(tbl(streak_data,[W*0.6,W*0.4],align_cols={1:'RIGHT'}))
story.append(Spacer(1,0.3*cm))

story.append(hdr('11. Strategy Rules & Configuration (v17a)'))
story.append(hr())

rules = [
    ('Universe',        'NIFTY Weekly Options (CE and PE) — Sell side only'),
    ('CPR Zones',       '15-zone classification: PP=(H+L+C)/3, BC=(H+L)/2, TC=2×PP−BC; R1−R4, S1−S4, PDH, PDL'),
    ('EMA Bias',        'EMA(20) on daily close; Bull if open > EMA20, Bear otherwise'),
    ('Signal Logic',    'Zone + EMA bias → CE/PE direction. tc_to_pdh fires for both biases (bear & bull).'),
    ('Strike Selection','Optimised per zone: ATM / OTM1 / ITM1 (50pt intervals)'),
    ('Entry Time',      'Optimised per zone: 09:16:02 / 09:20:02 / 09:25:02 / 09:31:02 (+2s forward bias prevention)'),
    ('Target',          'Optimised per zone: 20%−50% decay from entry premium'),
    ('SL Type A — %',   'Fixed 50%−200% of entry premium (hard stop) — most zones'),
    ('SL Type B — Spot Bear PE',
                        'tc_to_pdh bear PE: Exit when NIFTY spot > PDH + 25pts\n'
                        '(spot breaks above previous high → bear thesis invalidated)'),
    ('SL Type C — Spot Bull PE [v17a NEW]',
                        'tc_to_pdh bull PE: Exit when NIFTY spot < BC − 75pts\n'
                        '(spot breaks below CPR bottom → bull thesis fails → cut PE loss early)'),
    ('3-Tier Lock-in Trail',
                        '25% decay → move SL to breakeven | '
                        '40% decay → lock 80% of entry | '
                        '60% decay → lock 95% of max decay'),
    ('EOD Exit',        '15:20:00 — mandatory exit if not stopped out'),
    ('Quality Filters', 'IV proxy > 0.47% | Prev-day body > 0.10% | DTE > 0 (no same-day expiry)'),
    ('Sequential',      '1 trade per day maximum'),
    ('Lot Size',        '75 units per lot'),
]

for label, value in rules:
    row = [[Paragraph(f'<b>{label}</b>', ParagraphStyle('rl',fontName='Helvetica-Bold',
             fontSize=8,textColor=DARK)),
            Paragraph(value.replace('\n','<br/>'), ParagraphStyle('rv',fontName='Helvetica',
             fontSize=8,textColor=DGREY))]]
    rt = Table(row, colWidths=[W*0.30, W*0.70])
    rt.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),
                              ('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3),
                              ('LINEBELOW',(0,0),(-1,-1),0.3,colors.HexColor('#e0e0e0')),
                              ('BACKGROUND',(0,0),(0,-1),LGREY)]))
    story.append(rt)

story.append(PageBreak())

# ══════════════════════════════════════════════════════════════════
# PAGE 5 — Full Trade Log
# ══════════════════════════════════════════════════════════════════

story.append(hdr(f'12. Complete Trade Log ({len(df)} Trades)'))
story.append(hr())
story.append(Paragraph('Green = profit, Red = loss. SL: spot=spot-based, pct=% SL, time+spot=spot+time-stop.',
                        caption_style))
story.append(Spacer(1, 0.15*cm))

trade_hdr = ['#','Date','Zone','Bias','Opt','Strike','Entry','EP','XP','Target','SL','Exit','P&L']
trade_rows = [trade_hdr]

for i, row in df.iterrows():
    sp = str(row['sl_param'])
    sl_str = sp[:12] if len(sp) > 12 else sp
    trade_rows.append([
        str(i+1), row['date'].strftime('%d/%m/%y'), row['zone'][:14],
        row['ema_bias'][:4].upper(), row['opt'], str(int(row['strike'])),
        row['entry_time'][:5], f'₹{row["ep"]:.1f}', f'₹{row["xp"]:.1f}',
        f'{row["target_pct"]:.0%}', sl_str, row['exit_reason'],
        fmt_inr(row['pnl'], signed=True)
    ])

trade_t = Table(trade_rows, colWidths=[
    W*0.04,W*0.08,W*0.13,W*0.05,W*0.04,W*0.07,
    W*0.06,W*0.07,W*0.07,W*0.06,W*0.08,W*0.10,W*0.15])

ts = [
    ('BACKGROUND',(0,0),(-1,0),V17A),('TEXTCOLOR',(0,0),(-1,0),WHITE),
    ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),6.5),
    ('ALIGN',(0,0),(-1,0),'CENTER'),('ALIGN',(0,1),(0,-1),'CENTER'),
    ('ALIGN',(-1,1),(-1,-1),'RIGHT'),('ALIGN',(7,1),(8,-1),'RIGHT'),
    ('GRID',(0,0),(-1,-1),0.3,colors.HexColor('#bdbdbd')),
    ('TOPPADDING',(0,0),(-1,-1),1.5),('BOTTOMPADDING',(0,0),(-1,-1),1.5),
    ('ROWBACKGROUNDS',(0,1),(-1,-1),[WHITE,LGREY]),
]
for i, row in df.iterrows():
    bg = colors.HexColor('#c8e6c9') if row['pnl'] >= 0 else colors.HexColor('#ffcdd2')
    ts.append(('BACKGROUND',(-1,i+1),(-1,i+1),bg))

trade_t.setStyle(TableStyle(ts))
story.append(trade_t)

story.append(Spacer(1,0.4*cm))
story.append(hr())
story.append(Paragraph(
    f'CPR Strategy Backtester v17a  |  {datetime.now().strftime("%d %b %Y %H:%M")}  |  '
    f'NIFTY tick data Apr 2021–Apr 2026  |  v17a: +Spot SL bull PE',
    caption_style))

doc.build(story)
print(f"✓ PDF saved: {PDF_PATH}")
print(f"  Size: {os.path.getsize(PDF_PATH)//1024} KB")
