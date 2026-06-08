"""B策略回测: 起爆点(DC14) + 强庄(BB6) — 偏低吸突破"""
import struct, os, numpy as np, pandas as pd, random, time as _time
from collections import defaultdict
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 3000

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

def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _every(c, n): return c.rolling(n, min_periods=1).min().fillna(0).astype(bool)
def _exist(c, n): return c.rolling(n, min_periods=1).max().fillna(0).astype(bool)
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()
def _barslast(c):
    r = np.full(len(c), np.nan); last = -1
    for i in range(len(c)):
        if c.iloc[i]: last = i
        r[i] = i - last if last >= 0 else np.nan
    return pd.Series(r, index=c.index)

def signal_dc14(df):
    """起爆点 DC14 — 底部放量突破"""
    c, o, h, l, v = df['close'], df['open'], df['high'], df['low'], df['volume']
    n = len(c)

    gszj1 = np.round(_ref(c, 1) * 1.10, 2)  # ZTPRICE
    gszj2 = np.round(_ref(c, 1) * 0.90, 2)  # DTPRICE
    gszj3 = c > o
    gszj4 = (h - np.maximum(c, o)) / _ref(c, 1)
    gszj6 = (c / _ref(c, 1) > 1.098) & (h == c)
    gszj8 = _every(pd.Series(gszj6), 2)
    gszj9 = _every(pd.Series(gszj6), 5)
    gszj10 = (_ref(c, 1) / _ref(c, 2) < 1.098) & (_ref(h, 1) > _ref(c, 1))
    gszj11 = ~_exist(pd.Series(gszj9), 90)
    gszj12 = ~_exist(pd.Series(gszj8), 7)
    bb_arr = (l == gszj2).values if hasattr(gszj2, 'values') else np.array(l == gszj2)

    dc = (v / _ref(v, 1) >= 2) & (v / v.rolling(100).mean() >= 2)
    dc1 = (v / _ref(v, 1) >= 3) & (v / v.rolling(100).mean() > 1.1)
    dc2 = ((dc1 | dc) & gszj11 & gszj12 & gszj11 &
           (_count(pd.Series(bb_arr), 10) < 2) & gszj10 & gszj3)

    # DC3 = FILTER(DC2, 3) - simplified
    dc3 = dc2.copy()
    last_true = -999
    for i in range(len(dc3)):
        if dc3.iloc[i] and i - last_true > 3: last_true = i
        elif i - last_true <= 3: dc3.iloc[i] = False

    dc4 = ((h / _ref(c, 1) >= 1.07) & gszj3 & gszj10 & (h > c) &
           _exist(pd.Series(dc3), 5) & (gszj4 > 0.01) & gszj12 & gszj11 &
           (v == v.rolling(5).max()))

    # DC5 = FILTER(DC4, 15)
    dc5 = dc4.copy()
    last_true = -999
    for i in range(len(dc5)):
        if dc5.iloc[i] and i - last_true > 15: last_true = i
        elif i - last_true <= 15: dc5.iloc[i] = False

    # Simplified DC14: just use DC5 (the filtered breakout signal)
    return dc5.astype(int).values

def signal_bb6(df):
    """强庄/连阳 BB6 — 底部连阳+强庄信号"""
    c, o, h, v = df['close'], df['open'], df['high'], df['volume']
    gszj1 = np.round(_ref(c, 1) * 1.10, 2)
    gszj6 = (c / _ref(c, 1) > 1.098) & (h == c)
    dc14 = signal_dc14(df)

    dc15 = _every(pd.Series(gszj6), 2) & _exist(pd.Series(c > o), 2)
    dc16 = _every(pd.Series(gszj6), 3)
    four_limit = _every(pd.Series(gszj6), 4)
    dc17 = (_count(pd.Series(gszj6 & (c > o)), 3) == 2) & (c > _ref(c, 2)) & ~_exist(pd.Series(_every(pd.Series(gszj6), 2)), 3)

    dc18 = (_every(pd.Series(c > o), 6) & (h < _hhv(h, 90)) &
            ~_exist(pd.Series(dc14 > 0), 60) & _exist(pd.Series(v > v.rolling(100).mean()), 5))

    bb1 = dc18.copy()
    last_true = -999
    for i in range(len(bb1)):
        if bb1.iloc[i] and i - last_true > 30: last_true = i
        elif i - last_true <= 30: bb1.iloc[i] = False

    bb2 = ((dc15 | dc17) & ~dc16 & ~_exist(pd.Series(four_limit), 180) &
           ~_exist(pd.Series(dc14 > 0), 60))
    bb3 = bb2.copy()
    last_true = -999
    for i in range(len(bb3)):
        if bb3.iloc[i] and i - last_true > 30: last_true = i
        elif i - last_true <= 30: bb3.iloc[i] = False

    bb4 = (_every(pd.Series(h == gszj1), 2) & _exist(pd.Series(c < gszj1), 2) &
           _every(pd.Series(c / _ref(c, 1) > 0.07), 2) &
           _every(pd.Series(v == v.rolling(60).max()), 2) &
           ~_exist(pd.Series(bb3), 3) & ~_exist(pd.Series(dc14 > 0), 60))

    bb5 = bb1 | bb3 | bb4
    # BB6 = FILTER(BB5 AND EMA(C,10) < EMA(C,32), 1)
    bb6 = (bb5 & (_ema(c, 10) < _ema(c, 32))).astype(int)
    last_true = -999
    for i in range(len(bb6)):
        if bb6.iloc[i] and i - last_true > 1: last_true = i
        elif i - last_true <= 1: bb6.iloc[i] = 0

    return bb6.values

def simulate_exit(entries):
    """A策略出场规则"""
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
            if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "TP2", 100
            elif pnl <= -0.05: r, sp = "SL", 100
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Drop", 100
            elif dc >= 5 and pnl < 0.01: r, sp = "Time", 100
            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break
    return ts

def calc(name, entries, ts):
    if not ts: return (name, len(entries), 0, 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    mcl = cur = 0
    for t in ts:
        if t["pnl"] <= 0: cur += 1
        else: cur = 0
        mcl = max(mcl, cur)
    return (name, len(entries), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts), mcl, len(entries)/500)

print("=" * 60)
print("  B Strategy Backtest: DC14 + BB6")
print("=" * 60)

print("\n[1/3] Loading stocks...")
t0 = _time.time()
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
print(f"  {len(stocks)} stocks ({_time.time()-t0:.0f}s)")

print("\n[2/3] Computing B signals...")
t0 = _time.time()
entries_dc14 = []; entries_bb6 = []; entries_both = []

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try:
        dc14 = signal_dc14(df)
        bb6 = signal_bb6(df)
    except: continue
    for i in range(250, len(df)-1):
        sc = s['c'][i]; sv = s['v'][i]
        if sc < 10: continue  # Basic price filter
        ni = i+1
        if ni >= len(s['d']): continue
        no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01: continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: continue
        if dc14[i]:
            entries_dc14.append({'code': code, 'idx': ni, 'ep': no})
        if bb6[i]:
            entries_bb6.append({'code': code, 'idx': ni, 'ep': no})
        if dc14[i] and bb6[i]:
            entries_both.append({'code': code, 'idx': ni, 'ep': no})

print(f"  DC14: {len(entries_dc14)}, BB6: {len(entries_bb6)}, Both: {len(entries_both)}")
print(f"  ({_time.time()-t0:.0f}s)")

print("\n[3/3] Exit simulation...")
results = []
for name, ents in [("B:DC14(起爆点)", entries_dc14), ("B:BB6(强庄/连阳)", entries_bb6), ("B:DC14+BB6共振", entries_both)]:
    t = simulate_exit(ents)
    results.append(calc(name, ents, t))

print(f"\n{'='*60}")
print(f"  B Strategy Results (vs A Strategy)")
print(f"{'='*60}")
print(f"  {'Scheme':<22} {'Ent':>5} {'Trd':>6} {'WR':>6} {'PF':>6} {'PnL':>8} {'MCL':>4} {'Daily':>6}")
print(f"  {'-'*22} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*4} {'-'*6}")
for r in results:
    print(f"  {r[0]:<22} {r[1]:>5} {r[2]:>6} {r[3]:>5.1%} {r[6]:>6.2f} {r[7]:>+8.1f} {r[8]:>4} {r[9]:>5.1f}")

print(f"\n  A:双共振(F1) baseline:        PF=9.7 WR=52% Daily=5.5 (ref)")
print("  Done!")
