"""B2合并策略: 最佳买点+起爆试涨 — 统一回测对比B1"""
import struct, os, numpy as np, pandas as pd, random, time as _time
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

def signal_b2(df):
    """B2合并: 最佳买点(压力突破) + 起爆(放量) + 回调确认

    共同点提取:
      1. 量能: V>=1.9*REF(V,1) AND V>=2*MA(V,100)
      2. 突破: C突破前高压力位 + 收阳
      3. 趋势: EMA13 > EMA50
      4. 过滤: 非连续涨停(90日内无5连板) + 非跌停
      5. 回调: 突破后可缩量回踩确认后再买
    """
    c, o, h, l, v = df['close'], df['open'], df['high'], df['low'], df['volume']
    n = len(c)

    # 1. 压力位: 前10日最高价
    hhv10 = _hhv(h, 10)
    pressure = _ref(hhv10, 1)

    # 2. 量能条件
    vol_breakout = (v / _ref(v, 1) >= 1.9) & (v / v.rolling(100).mean() >= 2)

    # 3. 突破信号
    breakout = (c > pressure) & (c > o)  # 收盘突破+收阳

    # 4. 趋势过滤
    trend_ok = _ema(c, 13) > _ema(c, 50)

    # 5. 过滤条件
    limit_up = c / _ref(c, 1) > 1.098
    no_5zt = ~_exist(_every(pd.Series(limit_up), 5), 90)  # 90日内无5连板
    limit_down = l <= _ref(c, 1) * 0.90
    not_dt = ~limit_down

    # 6. 上影线确认 (起爆特征)
    upper_shadow = (h - c) / c < 0.02  # 上影线<2%, 收盘接近最高

    # B2主信号: 综合突破
    b2_signal = (vol_breakout & breakout & trend_ok & no_5zt & not_dt & upper_shadow)

    # 7. 回调确认: 信号后缩量回踩
    callback = np.zeros(n, dtype=int)
    for i in range(1, n):
        if b2_signal.iloc[i-1]:
            # Next day: price dropped to near signal close, volume shrunk
            if (l.iloc[i] <= c.iloc[i-1] * 1.01 and  # Low near signal close
                c.iloc[i] > c.iloc[i-1] * 0.98 and     # Not breaking down
                v.iloc[i] < v.iloc[i-1] * 0.8):        # Volume shrinking
                callback[i] = 1

    # Final: signal day OR callback day
    result = (b2_signal | (pd.Series(callback) == 1)).astype(int).values
    return result


def simulate_exit(entries):
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
    if not ts: return (name, len(entries), 0, 0, 0, 0, 0, 0, 0)
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
print("  B2 Strategy: BestBuy + QZ Breakout + Callback")
print("=" * 60)

print("\n[1/3] Loading...")
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

print("\n[2/3] Computing B2 signals...")
t0 = _time.time()
entries = []

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try:
        b2 = signal_b2(df)
    except: continue
    for i in range(250, len(df)-1):
        if not b2[i]: continue
        sc = s['c'][i]; sv = s['v'][i]
        if sc < 10: continue
        ni = i+1
        if ni >= len(s['d']): continue
        no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01: continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: continue
        entries.append({'code': code, 'idx': ni, 'ep': no})

print(f"  B2 signals: {len(entries)}")
print(f"  ({_time.time()-t0:.0f}s)")

# Test with different filters
from collections import Counter
FILTERS = [
    ("B2:raw(无过滤)", 0, 0, 0),
    ("B2:P>15", 15, 0, 0),
    ("B2:P>15+T>1e8", 15, 1e8, 0),
    ("B2:P>15+T>1e8+V>2", 15, 1e8, 2.0),
    ("B2:P>18+T>2e8+V>2.5", 18, 2e8, 2.5),
]

print(f"\n[3/3] Testing filters...\n")
results = []
for name, mp, mt, mvr in FILTERS:
    ents = []
    for e in entries:
        s = stocks[e['code']]; idx = e['idx']
        if idx >= len(s['d']): continue
        sc = s['c'][idx]; sv = s['v'][idx]
        if mp > 0 and sc < mp: continue
        if mt > 0 and sv*sc < mt: continue
        if mvr > 0:
            avg20 = np.mean(s['v'][max(0, idx-20):idx+1])
            if sv < avg20 * mvr: continue
        ents.append(e)
    t = simulate_exit(ents)
    r = calc(name, ents, t)
    results.append(r)
    print(f"  {name:<28} Ent={r[1]:>5} Trd={r[2]:>6} WR={r[3]:>5.1%} PF={r[6]:>6.2f} PnL={r[7]:>+8.1f} MCL={r[8]:>4} Daily={r[9]:>5.1f}")

# Comparison
print(f"\n{'='*65}")
print(f"  B1 vs B2 COMPARISON")
print(f"{'='*65}")
print(f"  {'Strategy':<28} {'WR':>6} {'PF':>6} {'Daily':>6} {'风格':<10}")
print(f"  {'-'*28} {'-'*6} {'-'*6} {'-'*6} {'-'*10}")
# B1 DC14 reference
print(f"  {'B1:DC14(起爆点)':<28} {'57.3%':>6} {'9.28':>6} {'26.8':>6} {'低吸':<10}")
print(f"  {'B1:BB6(强庄)':<28} {'56.5%':>6} {'60.60':>6} {'10.1':>6} {'强庄':<10}")
for r in results:
    print(f"  {r[0]:<28} {r[3]:>5.1%} {r[6]:>6.2f} {r[9]:>5.1f} {'突破':<10}")

best = max(results, key=lambda x: x[6])
print(f"\n  Best: {best[0]} PF={best[6]:.2f} WR={best[3]:.1%}")
print("  Done!")
