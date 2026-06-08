"""超紧过滤 — 追求 PF>8, 日均0-8信号"""
import struct, os, numpy as np, pandas as pd, random
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 4000  # 快速测试

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
print(f"{len(codes)} stocks\n")

def run(min_p, min_t, min_vr):
    entries = []
    for code in codes:
        s = stocks[code]
        df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                           'close': s['c'], 'volume': s['v']})
        try: ff = f1(df)
        except: continue
        for i in range(250, len(df)-1):
            if not ff[i]: continue
            sc = s['c'][i]; sv = s['v'][i]; so = s['o'][i]; sh = s['h'][i]; sl = s['l'][i]
            if sc < min_p or sv*sc < min_t: continue
            if sv < np.mean(s['v'][max(0,i-20):i+1])*min_vr: continue
            # Signal close in upper half of day's range
            if (sc-so)/(sh-sl+0.001) < 0.4: continue
            ni = i+1
            if ni >= len(s['d']): continue
            no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01: continue
            if s['o'][ni] == s['h'][ni] == s['c'][ni]: continue
            entries.append({'code': code, 'idx': ni, 'ep': no})

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
            if lh and not zt: lh = False
            r, sp = None, 0
            if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "T1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "T2", 100
            elif pnl <= -0.05: r, sp = "SL", 100
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Dp", 100
            elif dc >= 5 and pnl < 0.01: r, sp = "Tm", 100
            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break

    if not ts: return (0, 0, 0, 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    mcl = cur = 0
    for t in ts:
        if t["pnl"] <= 0: cur += 1
        else: cur = 0
        mcl = max(mcl, cur)
    daily = len(entries)/1500
    return (len(entries), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts), mcl, daily)

TESTS = [
    ("P20+T2e8+VR2.5(base)", 20, 2e8, 2.5),
    ("P25+T3e8+VR3",         25, 3e8, 3.0),
    ("P30+T5e8+VR3.5",       30, 5e8, 3.5),
    ("P35+T5e8+VR4",         35, 5e8, 4.0),
    ("P30+T5e8+VR3",         30, 5e8, 3.0),
]

print(f"{'Filter':<25} {'Ent':>6} {'Trd':>6} {'WR':>5} {'PF':>6} {'PnL':>10} {'MCL':>4} {'Daily':>6}")
for name, mp, mt, mvr in TESTS:
    ne, nt, wr, aw, al, pf, pnl, mcl, daily = run(mp, mt, mvr)
    print(f"{name:<25} {ne:>6} {nt:>6} {wr:>4.0%} {pf:>6.2f} {pnl:>+10.1f} {mcl:>4} {daily:>5.1f}")

# Find best
best_pf = max(TESTS, key=lambda x: run(x[1], x[2], x[3])[5])
print(f"\nBest PF candidate: {best_pf[0]} PF={run(best_pf[1],best_pf[2],best_pf[3])[5]:.2f}")
print("Done!")
