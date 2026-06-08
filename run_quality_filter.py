"""二次严选过滤对比 — 提高胜率"""
import sys; sys.path.insert(0, r"d:\quant_framework\src")
import struct, os, numpy as np, pandas as pd, random
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 1500

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

def run(name, min_turnover=0, min_price=0, min_vol_ratio=0):
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
            if min_turnover > 0 and sv*sc < min_turnover: continue
            if min_price > 0 and sc < min_price: continue
            if min_vol_ratio > 0:
                avg20 = np.mean(s['v'][max(0,i-20):i+1])
                if sv < avg20*min_vol_ratio: continue
            ni = i+1; no = s['o'][ni]; lu = round(sc*1.10, 2)
            if no >= lu-0.01: continue
            if s['o'][ni] == s['h'][ni] == s['c'][ni]: continue
            entries.append({'code': code, 'idx': ni, 'ep': no})

    ts = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = s30 = False
        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if zt: continue
            if not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015:
                ts.append(dict(pnl=(pnl-0.0013)*0.5)); h7 = True; remain -= 50; peak = p
                if remain <= 0: break; continue
            if h7 and pp >= 0.07 and (p-peak)/ep <= -0.02:
                ts.append(dict(pnl=(pnl-0.0013)*(remain/100))); break
            if not s30 and pnl <= -0.03:
                ts.append(dict(pnl=(pnl-0.0013)*0.5)); s30 = True; remain -= 50; peak = p
                if remain <= 0: break; continue
            if pnl <= -0.05: ts.append(dict(pnl=(pnl-0.0013)*(remain/100))); break
            if not zt and pp >= 0.02 and (p-peak)/ep <= -0.03:
                ts.append(dict(pnl=(pnl-0.0013)*(remain/100))); break
            if dc >= 5 and pnl < 0.01: ts.append(dict(pnl=(pnl-0.0013)*(remain/100))); break
    if not ts: return (name, len(entries), 0, 0, 0, 0, 0)
    w = [t for t in ts if t['pnl'] > 0]; l_ = [t for t in ts if t['pnl'] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t['pnl'] for t in w]) if w else 0
    al = np.mean([t['pnl'] for t in l_]) if l_ else 0
    pf = abs(sum(t['pnl'] for t in w)/sum(t['pnl'] for t in l_)) if l_ else 0
    return (name, len(entries), len(ts), wr, aw, al, pf)

TESTS = [
    ("baseline(no filter)", 0, 0, 0),
    ("Turnover>50M", 5e7, 0, 0),
    ("Turnover>100M", 1e8, 0, 0),
    ("Price>10", 0, 10, 0),
    ("Price>15+Turnover>100M", 1e8, 15, 0),
    ("VolRatio>1.5x", 0, 0, 1.5),
    ("T>100M+P>10+VR>1.5", 1e8, 10, 1.5),
    ("T>100M+P>15+VR>2.0", 1e8, 15, 2.0),
]

print(f"{'Filter':<28} {'Entries':>7} {'Trades':>7} {'WR':>6} {'AW':>6} {'AL':>6} {'PF':>6}")
print("-" * 72)
for name, mt, mp, mvr in TESTS:
    r = run(name, mt, mp, mvr)
    if r[2] > 0:
        print(f"{r[0]:<28} {r[1]:>7} {r[2]:>7} {r[3]:>5.1%} {r[4]:>5.2%} {r[5]:>5.2%} {r[6]:>6.2f}")

print("\nDone!")
