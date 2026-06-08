"""水下买入法 vs 突破买入法 对比"""
import struct, os, numpy as np, pandas as pd, random
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

def sim(entries):
    ts = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = s3 = lh = False
        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if zt: lh = True; continue
            r, sp = None, 0
            if lh and not zt: lh = False
            if not lh and r is None and pp >= 0.02 and (p-peak)/ep <= -0.02: r, sp = "封板回落", remain
            if not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015: r, sp = "+7%回1.5减半", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.02: r, sp = "+7%不涨停回2清", remain
            elif not s3 and pnl <= -0.03: r, sp = "-3%减半", 50; s3 = True
            elif pnl <= -0.05: r, sp = "-5%全清", remain
            elif not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "回落3%", remain
            elif dc >= 5 and pnl < 0.01: r, sp = "时间5天", remain
            if r and sp > 0:
                s2 = min(sp, remain); ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break
    return ts

def stats(name, ents, ts):
    if not ts: return (name, len(ents), 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return (name, len(ents), len(ts), wr, aw, al, pf, sum(t["pnl"] for t in ts))

print("Loading...")
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

FILTER = {"min_price": 15, "min_turnover": 1e8, "min_vol_ratio": 2.0}

e_breakout = []   # 突破买入: T+1 open (original)
e_dip = []        # 水下买入: T+1 low < open, vol shrinking, buy at close
e_dip_deep = []   # 深水买入: T+1 gap down >2%, vol < signal day vol

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        sc = s['c'][i]; sv = s['v'][i]
        if sc < FILTER["min_price"]: continue
        if sv*sc < FILTER["min_turnover"]: continue
        avg20 = np.mean(s['v'][max(0,i-20):i+1])
        if sv < avg20 * FILTER["min_vol_ratio"]: continue

        n1 = i+1; no = s['o'][n1]; nc = s['c'][n1]; nl = s['l'][n1]
        nv = s['v'][n1]; lu = round(sc*1.10, 2)

        # Scheme 1: 突破买入 (original) - T+1 open
        if no < lu-0.01 and not(no == s['h'][n1] == nc):
            e_breakout.append({'code': code, 'idx': n1, 'ep': no})

        # Scheme 2: 水下买入 - T+1 low < open AND volume < signal volume AND close > open/2
        # 缩量下刹: 价格打下去但量在缩, 说明卖压在枯竭
        dip_condition = (nl < no * 0.98) and (nv < sv * 0.9) and (nc > nl)
        if dip_condition and nc < lu-0.01:
            e_dip.append({'code': code, 'idx': n1, 'ep': nc})  # buy at close (尾盘确认后)

        # Scheme 3: 深水买入 - gap down >2% + volume < signal*0.7
        gap_down = no < sc * 0.98
        vol_shrink = nv < sv * 0.7
        if gap_down and vol_shrink and nc > nl and nc < lu-0.01:
            e_dip_deep.append({'code': code, 'idx': n1, 'ep': nc})

results = []
for name, ents in [("突破买入(T+1开盘)", e_breakout),
                    ("水下买入(缩量下刹)", e_dip),
                    ("深水买入(低开2%+缩量)", e_dip_deep)]:
    t = sim(ents); results.append(stats(name, ents, t))

print(f"\nEntries: 突破={len(e_breakout)} 水下={len(e_dip)} 深水={len(e_dip_deep)}\n")
print(f"  {'Scheme':<22} {'Ent':>5} {'Trd':>6} {'WR':>6} {'AW':>7} {'AL':>7} {'PF':>6} {'PnL':>10}")
print(f"  {'-'*22} {'-'*5} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*6} {'-'*10}")
for r in results:
    if r[2] > 0:
        print(f"  {r[0]:<22} {r[1]:>5} {r[2]:>6} {r[3]:>5.1%} {r[4]:>+6.2f} {r[5]:>+6.2f} {r[6]:>6.2f} {r[7]:>+10.2f}")

print("\n  Done!")
