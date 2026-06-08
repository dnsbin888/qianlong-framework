"""TP1宽+TP2紧 — 减少震仓+锁利润"""
import struct, os, numpy as np, pandas as pd, random
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 2000

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

stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

codes = list(stocks.keys()); random.seed(42)
if len(codes) > N: codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}

entries = []
for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        sc = s['c'][i]; sv = s['v'][i]
        if sc < 15 or sv*sc < 1e8: continue
        if sv < np.mean(s['v'][max(0,i-20):i+1])*2.0: continue
        ni = i+1; no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no < lu-0.01 and not(s['o'][ni] == s['h'][ni] == s['c'][ni]):
            entries.append({'code': code, 'idx': ni, 'ep': no})

def sim(tp1_drop, tp2_drop):
    ts = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = lh = False
        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if zt: lh = True; continue
            r, sp = None, 0
            if lh and not zt: lh = False
            if not h7 and pp >= 0.05 and (p-peak)/ep <= -tp1_drop: r, sp = "TP1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -tp2_drop: r, sp = "TP2", remain
            elif pnl <= -0.055: r, sp = "SL", remain
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Drop", remain
            elif dc >= 5 and pnl < 0.01: r, sp = "Time", remain
            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break
    if not ts: return (0, 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return (len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts))

TESTS = [
    (0.010, 0.010, "sym_tight_1.0_1.0"),
    (0.015, 0.010, "reverse_1.5_1.0"),
    (0.015, 0.015, "sym_wide_1.5_1.5"),
    (0.020, 0.010, "reverse_2.0_1.0"),
    (0.020, 0.015, "reverse_2.0_1.5"),
    (0.025, 0.010, "reverse_2.5_1.0"),
    (0.025, 0.015, "reverse_2.5_1.5"),
]

print(f"Entries: {len(entries)}\n")
print(f"  {'TP1/TP2':<20} {'Trd':>5} {'WR':>6} {'PF':>6} {'PnL':>10}")
for d1, d2, name in TESTS:
    nt, wr, aw, al, pf, pnl = sim(d1, d2)
    if nt > 0:
        print(f"  {name:<20} {nt:>5} {wr:>5.1%} {pf:>6.2f} {pnl:>+10.2f}")

# Find best
results = []
for d1, d2, name in TESTS:
    r = sim(d1, d2)
    if r[0] > 0: results.append((name, d1, d2, *r))
results.sort(key=lambda x: -x[6])
print(f"\n  Best: {results[0][0]} PF={results[0][6]:.2f} WR={results[0][3]:.1%}")
print("  Done!")
