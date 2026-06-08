"""震仓分析: 紧回落会不会把涨停股震出去?"""
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

# ═══════════════════ 震仓分析 ═══════════════════
# For each entry, check: does the stock eventually hit ZT?
# If yes, would different pullback %s have shaken it out?

shakeout_stats = {1.0: {"total_ZT": 0, "shaken": 0, "survived": 0},
                   1.5: {"total_ZT": 0, "shaken": 0, "survived": 0},
                   2.0: {"total_ZT": 0, "shaken": 0, "survived": 0},
                   2.5: {"total_ZT": 0, "shaken": 0, "survived": 0}}

for e in entries:
    code, idx, ep = e['code'], e['idx'], e['ep']
    s = stocks[code]
    peak = ep
    zt_reached = False; zt_day = -1

    # First pass: find if/when ZT is reached
    for j in range(idx+1, min(idx+30, len(s['d']))):
        p = s['c'][j]
        prev_c = s['c'][j-1] if j > idx else p
        lu = round(prev_c*1.10, 2) if prev_c > 0 else 999
        if p >= lu - 0.01:
            zt_reached = True; zt_day = j
            break

    if not zt_reached:
        continue

    # ZT reached! Now test each pullback % to see if it would have shaken out BEFORE ZT
    for pullback_pct in [1.0, 1.5, 2.0, 2.5]:
        shakeout_stats[pullback_pct]["total_ZT"] += 1
        peak2 = ep; h7 = False; shaken = False

        for j in range(idx+1, zt_day):  # Only check days BEFORE ZT
            p = s['c'][j]; h = s['h'][j]
            if h > peak2: peak2 = h
            pp = (peak2-ep)/ep

            # Would TP1 fire before ZT?
            if not h7 and pp >= 0.05 and (p-peak2)/ep <= -(pullback_pct/100):
                shaken = True; break
            if h7 and pp >= 0.07 and (p-peak2)/ep <= -(pullback_pct/100):
                shaken = True; break

            # Mark h7 if TP1 condition met (but we only check if shaken)
            if not h7 and pp >= 0.05:
                pass  # TP1 zone reached, continue checking

        if shaken:
            shakeout_stats[pullback_pct]["shaken"] += 1
        else:
            shakeout_stats[pullback_pct]["survived"] += 1

print(f"Entries: {len(entries)}\n")
print(f"  ZT震仓分析: 不同回落参数会把多少ZT提前甩下车?")
print(f"  {'回落%':<8} {'总ZT数':>7} {'被震出':>7} {'存活':>7} {'震出率':>8}")
print(f"  {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*8}")

for pct in [1.0, 1.5, 2.0, 2.5]:
    s = shakeout_stats[pct]
    rate = s["shaken"] / s["total_ZT"] * 100 if s["total_ZT"] > 0 else 0
    print(f"  {pct:>4.1f}%   {s['total_ZT']:>7} {s['shaken']:>7} {s['survived']:>7} {rate:>7.1f}%")

# Now: what about TP1 only (half position)? Let's check
print(f"\n  TP1半仓分析 (+5%触发):")
for pct in [1.0, 1.5, 2.0]:
    tp1_shaken = 0
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak2 = ep
        for j in range(idx+1, min(idx+15, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]
            if h > peak2: peak2 = h
            pp = (peak2-ep)/ep
            # Does stock ever hit ZT?
            prev_c = s['c'][j-1] if j > idx else p
            lu = round(prev_c*1.10, 2) if prev_c > 0 else 999
            if p >= lu-0.01: break  # Reached ZT
            if pp >= 0.05 and (p-peak2)/ep <= -(pct/100):
                tp1_shaken += 1; break
    print(f"  +5%回{pct}%: {tp1_shaken}笔被触发TP1(但可余量继续)")

print("\n  Done!")
