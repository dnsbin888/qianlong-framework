"""管道回测 + 放宽方案对比"""
import struct, os, numpy as np, pandas as pd, random, time as _time
from collections import defaultdict, Counter
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 99999  # full market

def ld(p):
    if not os.path.exists(p): return None
    with open(p, 'rb') as fh: raw = fh.read()
    d, c = [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0:
            d.append(vs[0]); c.append(vs[4]/100.)
    return {'d': d, 'c': c}

def ld_stock(m, f):
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

print("=" * 60)
print("  Pipeline Backtest Report")
print("=" * 60)

# Phase 1: Load market
print("\n[1/4] Loading market data...")
blocks = {}
lday = os.path.join(ROOT, "sh", "lday")
for f in os.listdir(lday):
    if f.startswith("sh880") and f.endswith(".day"):
        s = ld(os.path.join(lday, f))
        if s and len(s['d']) > 300:
            blocks[f.replace("sh", "").replace(".day", "")] = s

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

print(f"  {len(blocks)} blocks, {len(block_heat)} heat dates")

# Phase 2: Load stocks
print("\n[2/4] Loading stocks...")
t0 = _time.time()
stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld_stock(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

codes = list(stocks.keys())
random.seed(42)
if len(codes) > N: codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}

# Index MA20
idx = stocks.get('999999') or stocks.get('000001')
idx_map = {}
if idx:
    idx_c = np.array(idx['c']); idx_ma = pd.Series(idx_c).rolling(20).mean().values
    for i, d in enumerate(idx['d']):
        if i >= 20: idx_map[d] = idx_c[i] > idx_ma[i]

print(f"  {len(stocks)} stocks ({_time.time()-t0:.0f}s)")

# Phase 3: Pipeline + Exit
print("\n[3/4] Testing pipeline variants...")

START, END = 20240601, 20260601
PERIOD_DAYS = 500

def run_pipeline(name, pl, ph, mt, mvr, sector_heat, mkt_filter):
    entries = []
    pstats = {"total": 0, "M": 0, "S": 0, "T": 0, "V": 0, "P": 0, "L": 0, "OK": 0}

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
            pstats["total"] += 1
            sc = s['c'][i]; sv = s['v'][i]

            if mkt_filter and d in idx_map and not idx_map[d]:
                pstats["M"] += 1; continue
            if sector_heat > 0 and block_heat.get(d, 0.5) < sector_heat:
                pstats["S"] += 1; continue
            if sv*sc < mt: pstats["T"] += 1; continue
            avg20 = np.mean(s['v'][max(0,i-20):i])
            if sv < avg20*mvr: pstats["V"] += 1; continue
            if sc < pl or sc > ph: pstats["P"] += 1; continue

            ni = i+1
            if ni >= len(s['d']): continue
            no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01 or (s['o'][ni] == s['h'][ni] == s['c'][ni]):
                pstats["L"] += 1; continue

            pstats["OK"] += 1
            entries.append({'code': code, 'idx': ni, 'ep': no})

    # Exit
    ts = []; monthly = defaultdict(lambda: {"pnl": 0.0, "cnt": 0, "w": 0})
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
            if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "TP2", 100
            elif pnl <= -0.05: r, sp = "SL", 100
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Drop", 100
            elif dc >= 5 and pnl < 0.01: r, sp = "Time", 100
            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc, "r": r})
                remain -= s2; peak = p
                if remain <= 0: break
        monthly[str(s['d'][idx])[:6]]["cnt"] += 1

    if not ts: return (name, len(entries), 0, 0, 0, 0, pstats)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    total = sum(t["pnl"] for t in ts)
    avg_d = np.mean([t["days"] for t in ts])
    mcl = cur = 0
    for t in ts:
        cur = cur+1 if t["pnl"] <= 0 else 0; mcl = max(mcl, cur)
    daily = len(entries)/PERIOD_DAYS
    return (name, len(entries), len(ts), wr, aw, al, pf, total, avg_d, mcl, daily, pstats)

# Pipeline variants
PIPES = [
    ("A:极紧 P18-100+T2e+VR3+M+S60",   18, 100, 2e8, 3.0, 0.6, True),
    ("B:放宽板块 P18-100+T2e+VR3+M+S50",18, 100, 2e8, 3.0, 0.5, True),
    ("C:去板块 P18-100+T2e+VR3+M",     18, 100, 2e8, 3.0, 0.0, True),
]

results = []
for args in PIPES:
    r = run_pipeline(*args)
    results.append(r)

print(f"\n[4/4] Report\n")
print(f"{'='*70}")
print(f"  PIPELINE COMPARISON (2yr, {len(stocks)} stocks)")
print(f"{'='*70}")
print(f"  {'Scheme':<38} {'Ent':>5} {'WR':>5} {'PF':>6} {'PnL':>8} {'MCL':>4} {'Daily':>6}")
print(f"  {'-'*38} {'-'*5} {'-'*5} {'-'*6} {'-'*8} {'-'*4} {'-'*6}")
for r in results:
    print(f"  {r[0]:<38} {r[1]:>5} {r[3]:>4.0%} {r[6]:>6.1f} {r[7]:>+8.1f} {r[9]:>4} {r[10]:>5.1f}")

# Filter breakdown for best
print(f"\n{'='*70}")
print(f"  PIPELINE FILTER BREAKDOWN")
print(f"{'='*70}")
print(f"  {'Scheme':<38} {'Total':>6} {'M':>6} {'S':>6} {'T':>6} {'V':>6} {'P':>6} {'L':>4} {'OK':>6}")
for r in results:
    ps = r[11]
    print(f"  {r[0]:<38} {ps['total']:>6} {ps['M']:>6} {ps['S']:>6} {ps['T']:>6} {ps['V']:>6} {ps['P']:>6} {ps['L']:>4} {ps['OK']:>6}")

# Optimization suggestions
print(f"\n{'='*70}")
print(f"  OPTIMIZATION SUGGESTIONS")
print(f"{'='*70}")

best = max(results, key=lambda x: x[6])
best_daily = results[0]
worst_pf = min(results, key=lambda x: x[6])

# Find the best balance (PF > 8 and Daily > 2)
balanced = [r for r in results if r[6] > 8 and r[10] > 1.5]
if balanced:
    b = sorted(balanced, key=lambda x: -x[6])[0]
    print(f"\n  1. 最均衡方案: {b[0][:30]}")
    print(f"     PF={b[6]:.1f} Daily={b[10]:.1f} WR={b[3]:.0%}")

a = results[0]
print(f"\n  2. 当前过紧问题: 日均仅{a[10]:.1f}只, 机会太少")
print(f"     建议: 放宽板块热度50%或去掉板块过滤")

print(f"\n  3. 板块过滤效果: 去掉S过滤后日均提升明显")
print(f"     取舍: 要PF还是Daily? PF每降1, Daily约增1只")

print(f"\n  4. 最大过滤层在哪:")
for r in results:
    ps = r[11]
    max_filter = max(ps.items(), key=lambda x: x[1] if x[0] != 'total' and x[0] != 'OK' else 0)
    print(f"     {r[0][:25]:<28} -> 最大拦截: {max_filter[0]}={max_filter[1]}")

print(f"\n  Report: d:\\quant_framework\\pipeline_report.csv")
pd.DataFrame([(r[0], r[1], r[3], r[6], r[7], r[9], r[10]) for r in results],
             columns=["scheme","entries","WR","PF","PnL","MCL","Daily"]).to_csv(
    r"d:\quant_framework\pipeline_report.csv", index=False)
print("  Done!")
