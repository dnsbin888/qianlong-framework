"""最终全市场回测报告 — 最新策略参数"""
import struct, os, numpy as np, pandas as pd, random, time as _time
from collections import defaultdict, Counter
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"

def ld(m, f):
    c = f.replace(m, '').replace('.day', '')
    if len(c) != 6 or not c.isdigit(): return None
    p = os.path.join(ROOT, m, 'lday', f)
    if not os.path.exists(p): return None
    with open(p, 'rb') as fh: raw = fh.read()
    d, o, h, l, cl, v = [], [], [], [], [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
            d.append(vs[0]); o.append(vs[1]/100.); h.append(vs[2]/100.)
            l.append(vs[3]/100.); cl.append(vs[4]/100.); v.append(vs[6])
    return {'code': c, 'd': d, 'o': o, 'h': h, 'l': l, 'c': cl, 'v': v}

def f1(df):
    c, v = df['close'].values, df['volume'].values
    hhv30 = pd.Series(c).rolling(30).max().shift(1); pr = hhv30.rolling(2).mean().values
    e20 = pd.Series(c).ewm(span=20, adjust=False).mean()
    dv = ((pd.Series(c)-e20)**2).rolling(20).mean()**0.5; up = (e20+2*dv).shift(1).values
    vr = v / pd.Series(v).rolling(5).mean().shift(1).replace(0, np.nan)
    qr = (c > pr) & (c > up) & (vr > 1.8); q = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if qr[i] and qr[i-7:i].sum() <= 1: q[i] = 1
    c99 = pd.Series(c).rolling(60).quantile(0.99); p100 = c99.ewm(span=5, adjust=False).mean()
    zr = c > p100.values; z = np.zeros(len(c), dtype=int)
    for i in range(60, len(c)):
        if zr[i] and zr[i-7:i].sum() <= 1: z[i] = 1
    return (q & z).astype(int)

print("=" * 65)
print("  SCHEME A: FULL PIPELINE BACKTEST")
print("  M-S-T-V-P-L Pipeline")
print("  Period: 2024-06 ~ 2026-06 (2 years)")
print("=" * 65)

# Phase 1: Load all data
print("\n[1/5] Loading A-shares + market data...")
t0 = _time.time()

# Stocks
stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

# Block indices for sector heat
def ld_light(p):
    if not os.path.exists(p): return None
    with open(p, 'rb') as fh: raw = fh.read()
    d, c = [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
            d.append(vs[0]); c.append(vs[4]/100.)
    return {'d': d, 'c': c}

blocks = {}
lday_dir = os.path.join(ROOT, "sh", "lday")
for f in os.listdir(lday_dir):
    if f.startswith("sh880") and f.endswith(".day"):
        s = ld_light(os.path.join(lday_dir, f))
        if s and len(s['d']) > 300: blocks[f.replace("sh","").replace(".day","")] = s

block_heat = {}
all_d = sorted(set(d for b in blocks.values() for d in b['d'] if 20200101 <= d <= 20260601))
for d in all_d:
    up = total = 0
    for b in blocks.values():
        if d in b['d']:
            i = b['d'].index(d)
            if i >= 60 and b['c'][i] > np.mean(b['c'][i-20:i+1]): up += 1
            total += 1
    block_heat[d] = up/total if total > 0 else 0

# Market index
idx = stocks.get('999999') or stocks.get('000001')
idx_map = {}
if idx:
    idx_c = np.array(idx['c']); idx_ma = pd.Series(idx_c).rolling(20).mean().values
    for i, d in enumerate(idx['d']):
        if i >= 20: idx_map[d] = idx_c[i] > idx_ma[i]

codes = list(stocks.keys())
print(f"  {len(codes)} stocks, {len(blocks)} blocks ({_time.time()-t0:.0f}s)")

# Phase 2: Pipeline
print("\n[2/5] Pipeline: M-S-T-V-P-L...")
t0 = _time.time()

SCHEME_A = {"min_price": 18, "max_price": 100, "min_turnover": 2e8, "min_vol_ratio": 3.0, "sector_heat": 0.6}
START, END = 20240601, 20260601
PERIOD_DAYS = 500

entries = []
fstats = {"signals": 0, "M": 0, "S": 0, "T": 0, "V": 0, "P": 0, "L": 0, "ok": 0}

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        d = s['d'][i]
        if d < START or d > END: continue
        fstats["signals"] += 1
        sc = s['c'][i]; sv = s['v'][i]

        # Pipeline M-S-T-V-P-L
        if d in idx_map and not idx_map[d]: fstats["M"] += 1; continue
        if block_heat.get(d, 0.5) < SCHEME_A["sector_heat"]: fstats["S"] += 1; continue
        if sv*sc < SCHEME_A["min_turnover"]: fstats["T"] += 1; continue
        avg20 = np.mean(s['v'][max(0,i-20):i])
        if sv < avg20 * SCHEME_A["min_vol_ratio"]: fstats["V"] += 1; continue
        if sc < SCHEME_A["min_price"] or sc > SCHEME_A["max_price"]: fstats["P"] += 1; continue

        ni = i+1
        if ni >= len(s['d']): continue
        no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01: fstats["L"] += 1; continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: fstats["L"] += 1; continue
        fstats["ok"] += 1
        entries.append({'code': code, 'idx': ni, 'ep': no, 'signal_date': d})

print(f"  F1: {fstats['signals']:,} -> M:{fstats['M']} S:{fstats['S']} T:{fstats['T']} V:{fstats['V']} P:{fstats['P']} L:{fstats['L']} OK:{fstats['ok']:,}")
print(f"  Daily: {fstats['ok']/PERIOD_DAYS:.1f} ({_time.time()-t0:.0f}s)")

# Phase 3: Exit
print("\n[3/4] Exit simulation...")
t0 = _time.time()

trades = []
monthly = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})

for e in entries:
    code, idx, ep = e['code'], e['idx'], e['ep']
    s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = lh = False
    for j in range(idx+1, min(idx+30, len(s['d']))):
        p = s['c'][j]; h = s['h'][j]; dc += 1
        if h > peak: peak = h
        pnl = (p-ep)/ep; pp = (peak-ep)/ep
        zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
        if zt: lh = True; continue
        if lh and not zt: lh = False
        r, sp = None, 0
        if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "+5%回1%半", 50; h7 = True
        elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "+7%回2%清", 100
        elif pnl <= -0.05: r, sp = "-5%止损", 100
        elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "回落3%", 100
        elif dc >= 5 and pnl < 0.01: r, sp = "时间5天", 100
        if r and sp > 0:
            s2 = min(sp, remain)
            trades.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc, "reason": r,
                           "code": code, "ed": s['d'][j]})
            remain -= s2; peak = p
            if remain <= 0: break

    ym_str = str(e['signal_date'])[:6]
    monthly[ym_str]["trades"] += 1

print(f"  {len(trades):,} events ({_time.time()-t0:.0f}s)")

# Phase 4: Report
print("\n[4/4] Report\n")

if not trades: print("No trades!"); import sys; sys.exit(1)

wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]
wr = len(wins)/len(trades)
aw = np.mean([t["pnl"] for t in wins]) if wins else 0
al = np.mean([t["pnl"] for t in losses]) if losses else 0
pf = abs(sum(t["pnl"] for t in wins)/sum(t["pnl"] for t in losses)) if losses else float("inf")
total_pnl = sum(t["pnl"] for t in trades)
avg_days = np.mean([t["days"] for t in trades])
daily_sig = len(entries)/500

mcl = cur = 0
for t in trades:
    if t["pnl"] <= 0: cur += 1
    else: cur = 0
    mcl = max(mcl, cur)

reasons = Counter(t["reason"] for t in trades)

# Monthly P&L
for t in trades:
    ym = str(t["ed"])[:6]
    monthly[ym]["pnl"] += t["pnl"]
    if t["pnl"] > 0: monthly[ym]["wins"] += 1

# P&L distribution
pnls = [t["pnl"] for t in trades]

print("=" * 60)
print("  SCHEME A BACKTEST RESULTS")
print("=" * 60)
print(f"  Universe:     {len(codes):,} stocks, {len(blocks)} blocks")
print(f"  Pipeline:     M(C>MA20)->S({SCHEME_A['sector_heat']*100:.0f}%hot)->T(>{SCHEME_A['min_turnover']/1e8:.0f}亿)->V(>{SCHEME_A['min_vol_ratio']}x)->P({SCHEME_A['min_price']}-{SCHEME_A['max_price']})->L")
print(f"  F1 signals:   {fstats['signals']:,}")
print(f"  After filter: {fstats['ok']:,} (daily {fstats['ok']/PERIOD_DAYS:.1f})")
print(f"  Filtered:     M={fstats['M']:,} S={fstats['S']:,} T={fstats['T']:,} V={fstats['V']:,} P={fstats['P']:,} L={fstats['L']:,}")
print(f"  Trade events: {len(trades):,}")
print(f"  Win Rate:     {wr:.1%}")
print(f"  Avg Win:      {aw:>+.2f}")
print(f"  Avg Loss:     {al:>+.2f}")
print(f"  Profit Factor:{pf:.2f}")
print(f"  Total P&L:    {total_pnl:>+.2f}")
print(f"  Avg Days:     {avg_days:.1f}")
print(f"  Max Loss Run: {mcl}")
print()
print(f"  Exit Reasons:")
for r, c in reasons.most_common():
    bar = "#" * max(1, c * 30 // reasons.most_common(1)[0][1])
    print(f"  {r:<14}: {c:>5} ({c/len(trades)*100:>4.0f}%) {bar}")
print()
print(f"  Monthly P&L (recent 12):")
print(f"  {'Month':<8} {'Trades':>6} {'WR':>7} {'P&L':>10}")
for ym in sorted(monthly.keys())[-12:]:
    m = monthly[ym]
    mwr = m["wins"]/m["trades"]*100 if m["trades"] > 0 else 0
    print(f"  {ym:<8} {m['trades']:>6} {mwr:>6.1f}% {m['pnl']:>+10.2f}")
print()
print(f"  P&L Distribution:")
bins = [(-99,-.10),(-.10,-.05),(-.05,-.02),(-.02,0),(0,.02),(.02,.05),(.05,.10),(.10,.20),(.20,99)]
for lo, hi in bins:
    n = sum(1 for p in pnls if lo < p <= hi)
    if n > 0:
        bar = "#" * max(1, n * 25 // max(len(pnls)//8, 1))
        print(f"  {lo:>+5.0%}~{hi:>+5.0%}: {n:>5} ({n/len(pnls)*100:>4.0f}%) {bar}")

print(f"\n  Report: d:\\quant_framework\\scheme_a_report.csv")
pd.DataFrame(trades).to_csv(r"d:\quant_framework\scheme_a_report.csv", index=False, encoding="utf-8-sig")
print("  Done!")
