"""诊断: 为什么全市场PF低于样本回测?"""
import struct, os, numpy as np, pandas as pd, random
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

stocks = {}
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        s = ld(m, f)
        if s and len(s['d']) >= 300: stocks[s['code']] = s

codes = list(stocks.keys())

def run_set(name, code_set, period_start, period_end):
    entries = []
    for code in code_set:
        s = stocks[code]
        df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                           'close': s['c'], 'volume': s['v']})
        try: ff = f1(df)
        except: continue
        for i in range(250, len(df)-1):
            if not ff[i]: continue
            d = s['d'][i]
            if d < period_start or d > period_end: continue
            sc = s['c'][i]; sv = s['v'][i]
            if sc < 15 or sv*sc < 1e8: continue
            if sv < np.mean(s['v'][max(0,i-20):i+1])*2.0: continue
            ni = i+1
            if ni >= len(s['d']): continue
            no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01: continue
            if s['o'][ni] == s['h'][ni] == s['c'][ni]: continue
            entries.append({'code': code, 'idx': ni, 'ep': no})
    return entries

def sim(entries):
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
    if not ts: return (0,0,0,0,0,0,0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    mcl = cur = 0
    for t in ts:
        if t["pnl"] <= 0: cur += 1
        else: cur = 0
        mcl = max(mcl, cur)
    return (len(entries), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts), mcl)

random.seed(42)
codes_2k = random.sample(codes, 2000)
codes_8k = codes

print("Diagnostic: Why does full-market PF < sample PF?\n")

# Test 1: Same 2000-sample, 2-year period
print("Test 1: 2000-sample, 2-year period")
e1 = run_set("2k-2y", codes_2k, 20240601, 20260601)
r1 = sim(e1)
print(f"  Entries={r1[0]} Trades={r1[1]} WR={r1[2]:.1%} PF={r1[5]:.2f} PnL={r1[6]:+.2f} MaxLoss={r1[7]}")

# Test 2: 2000-sample, 6-year period (original benchmark)
print("\nTest 2: 2000-sample, 6-year period")
e2 = run_set("2k-6y", codes_2k, 20200101, 20260601)
r2 = sim(e2)
print(f"  Entries={r2[0]} Trades={r2[1]} WR={r2[2]:.1%} PF={r2[5]:.2f} PnL={r2[6]:+.2f} MaxLoss={r2[7]}")

# Test 3: Full market, 6-year period
print("\nTest 3: Full-market, 6-year period")
e3 = run_set("8k-6y", codes_8k, 20200101, 20260601)
r3 = sim(e3)
print(f"  Entries={r3[0]} Trades={r3[1]} WR={r3[2]:.1%} PF={r3[5]:.2f} PnL={r3[6]:+.2f} MaxLoss={r3[7]}")

# Test 4: Full market, 2-year period
print("\nTest 4: Full-market, 2-year period")
e4 = run_set("8k-2y", codes_8k, 20240601, 20260601)
r4 = sim(e4)
print(f"  Entries={r4[0]} Trades={r4[1]} WR={r4[2]:.1%} PF={r4[5]:.2f} PnL={r4[6]:+.2f} MaxLoss={r4[7]}")

print(f"\n{'='*60}")
print(f"  DIAGNOSIS")
print(f"{'='*60}")
print(f"  2000-sample, 6y: PF={r2[5]:.2f} WR={r2[2]:.1%}  ← 我们的优化基准")
print(f"  2000-sample, 2y: PF={r1[5]:.2f} WR={r1[2]:.1%}  ← 同期对比")
print(f"  8296-full, 6y:   PF={r3[5]:.2f} WR={r3[2]:.1%}")
print(f"  8296-full, 2y:   PF={r4[5]:.2f} WR={r4[2]:.1%}  ← 最终报告")

print(f"\n  原因分析:")
print(f"  1. 2年vs6年: 2000-sample PF下降{r2[5]-r1[5]:.2f} → 2024-2026市场更难做")
print(f"  2. 全量vs样本: 6年PF下降{r2[5]-r3[5]:.2f} → 全市场噪音更大")
print(f"  3. 叠加效果: {r2[5]:.2f}→{r4[5]:.2f} 两个因素叠加")

print(f"\n  优化方向:")
if r2[5] > r1[5] + 1:
    print(f"  [P0] 加市场状态过滤: 只在指数C>MA20时交易")
if r3[5] < r2[5] - 0.5:
    print(f"  [P0] 收紧质量过滤: 价>20 或 成交>2亿, 减少垃圾股噪音")
print(f"  [P1] 加DMI趋势过滤: ADX>20 且 PDI>MDI")
print("  Done!")
