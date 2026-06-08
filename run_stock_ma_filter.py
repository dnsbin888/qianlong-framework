"""个股C>MA20过滤 — 提升PF"""
import struct, os, numpy as np, pandas as pd, random
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 4000

def ld_light(p):
    if not os.path.exists(p): return None
    with open(p, 'rb') as fh: raw = fh.read()
    d, c = [], []
    for i in range(len(raw)//32):
        vs = struct.unpack_from('<I I I I I f I I', raw, i*32)
        if 20100101 <= vs[0] <= 20270101 and vs[1] > 0: d.append(vs[0]); c.append(vs[4]/100.)
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

print("Loading...")
stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld_stock(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

codes = list(stocks.keys()); random.seed(42)
if len(codes) > N: codes = random.sample(codes, N)
stocks = {k: stocks[k] for k in codes}

blocks = {}
for f in os.listdir(os.path.join(ROOT, 'sh', 'lday')):
    if f.startswith('sh880') and f.endswith('.day'):
        s = ld_light(os.path.join(ROOT, 'sh', 'lday', f))
        if s and len(s['d']) > 300: blocks[f.replace('sh', '').replace('.day', '')] = s

block_heat = {}
for d in sorted(set(d for b in blocks.values() for d in b['d'] if 20200101 <= d <= 20260601)):
    up = total = 0
    for b in blocks.values():
        if d in b['d']:
            i = b['d'].index(d)
            if i >= 60 and b['c'][i] > np.mean(b['c'][i-20:i+1]): up += 1
            total += 1
    block_heat[d] = up/total if total > 0 else 0

idx = stocks.get('999999') or stocks.get('000001')
idx_map = {}
if idx:
    idx_c = np.array(idx['c']); idx_ma = pd.Series(idx_c).rolling(20).mean().values
    for i, d in enumerate(idx['d']):
        if i >= 20: idx_map[d] = idx_c[i] > idx_ma[i]

def run(pl, ph, mt, mvr, sh, stock_ma):
    entries = []
    ps = {'total': 0, 'M': 0, 'S': 0, 'T': 0, 'V': 0, 'P': 0, 'U': 0, 'L': 0, 'OK': 0}
    for code in codes:
        s = stocks[code]
        df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                           'close': s['c'], 'volume': s['v']})
        try: ff = f1(df)
        except: continue
        for i in range(250, len(df)-1):
            if not ff[i]: continue
            d = s['d'][i]
            if d < 20240601 or d > 20260601: continue
            ps['total'] += 1; sc = s['c'][i]; sv = s['v'][i]
            if d in idx_map and not idx_map[d]: ps['M'] += 1; continue
            if block_heat.get(d, 0.5) < sh: ps['S'] += 1; continue
            if sv*sc < mt: ps['T'] += 1; continue
            avg20 = np.mean(s['v'][max(0, i-20):i])
            if sv < avg20*mvr: ps['V'] += 1; continue
            if sc < pl or sc > ph: ps['P'] += 1; continue
            if stock_ma and i >= 20 and sc < np.mean(s['c'][i-20:i+1]): ps['U'] += 1; continue
            ni = i+1
            if ni >= len(s['d']): continue
            no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01 or (s['o'][ni] == s['h'][ni] == s['c'][ni]): ps['L'] += 1; continue
            ps['OK'] += 1; entries.append({'code': code, 'idx': ni, 'ep': no})

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
    if not ts: return (0, 0, 0, 0, 0, 0, 0, 0, ps)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    mcl = cur = 0
    for t in ts:
        if t["pnl"] <= 0: cur += 1
        else: cur = 0
        mcl = max(mcl, cur)
    return (len(entries), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts), mcl, len(entries)/500, ps)

TESTS = [
    ("A:BASE",             18, 100, 2e8, 3.0, 0.6, False),
    ("A+:BASE+个股C>MA20", 18, 100, 2e8, 3.0, 0.6, True),
    ("A++:P20+VR3.5+个股", 20, 100, 2e8, 3.5, 0.6, True),
    ("A+++:P22+VR3.5+S70+个股", 22, 100, 2e8, 3.5, 0.7, True),
]

print(f"Scheme                     Ent  PF    WR    Daily  MCL  PnL       M    S    U")
for name, pl, ph, mt, mvr, sh, sma in TESTS:
    ne, nt, wr, aw, al, pf, pnl, mcl, daily, ps = run(pl, ph, mt, mvr, sh, sma)
    print(f"{name:<25} {ne:>4} {pf:>5.1f} {wr:>4.0%} {daily:>5.1f} {mcl:>4} {pnl:>+8.0f} {ps['M']:>5} {ps['S']:>4} {ps.get('U',0):>4}")

print("\nDone!")
