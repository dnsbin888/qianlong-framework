"""分层止损网格搜索 — 1-3层, -3%~-6.2%"""
import struct, os, numpy as np, pandas as pd, random, itertools
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

# Build entries once
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

def simulate(stop_rules):
    """stop_rules: [(threshold, sell_pct), ...] 按阈值从小到大排列"""
    ts = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0
        h7 = False; lh = False; sl_done = [False] * len(stop_rules)

        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if zt: lh = True; continue

            r, sp = None, 0

            # TP
            if lh and not zt: lh = False
            if not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015: r, sp = "TP1", 50; h7 = True
            elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.02: r, sp = "TP2", remain

            # SL tiers (check from worst to best)
            if r is None:
                for i, (threshold, sell_pct) in enumerate(stop_rules):
                    if not sl_done[i] and pnl <= threshold:
                        r, sp = f"SL{i+1}", sell_pct
                        sl_done[i] = True
                        if i < len(stop_rules) - 1:
                            pass  # Continue with remaining position
                        else:
                            sp = remain  # Last tier: sell all remaining
                        break

            # Generic exits
            if r is None and not zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "回落", remain
            if r is None and dc >= 5 and pnl < 0.01: r, sp = "时间", remain

            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc})
                remain -= s2; peak = p
                if remain <= 0: break

    if not ts: return None
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return wr, aw, al, pf, sum(t["pnl"] for t in ts), np.mean([t["days"] for t in ts]), len(ts)

# ═══════════════════ Stop combinations ═══════════════════
COMBOS = [
    # (name, [(threshold, sell_pct), ...])
    ("基准:-3%半→-5%全",     [(-0.03, 50), (-0.05, 100)]),
    ("-4%半→-5%全",        [(-0.04, 50), (-0.05, 100)]),
    ("-3%半→-6%全",        [(-0.03, 50), (-0.06, 100)]),
    ("-4%半→-6%全",        [(-0.04, 50), (-0.06, 100)]),
    ("-4%半→-6.2%全",      [(-0.04, 50), (-0.062, 100)]),
    ("-3%1/3→-5%1/3→-7%全",[(-0.03, 33), (-0.05, 33), (-0.07, 100)]),
    ("-3%半→-5%半→-6%全",  [(-0.03, 50), (-0.05, 50), (-0.06, 100)]),
    ("-3.5%半→-5.5%全",    [(-0.035, 50), (-0.055, 100)]),
    ("-4%半→-5%半→-6.2%全",[(-0.04, 50), (-0.05, 50), (-0.062, 100)]),
    ("-3%1/3→-4%1/3→-5%全",[(-0.03, 33), (-0.04, 33), (-0.05, 100)]),
]

print(f"Entries: {len(entries)}\n")
results = []
for name, rules in COMBOS:
    r = simulate(rules)
    if r:
        wr, aw, al, pf, pnl, avg_d, nt = r
        results.append((name, rules, wr, aw, al, pf, pnl, avg_d, nt))
        print(f"  {name:<28} T={nt:>5} WR={wr:>5.1%} AW={aw:>+5.2f} AL={al:>+5.2f} PF={pf:>6.2f} PnL={pnl:>+10.2f} D={avg_d:>4.1f}")

results.sort(key=lambda x: (-x[5], -x[2]))

print(f"\n{'='*75}")
print(f"  FINAL RANKING (by PF)")
print(f"{'='*75}")
print(f"  {'#':<3} {'方案':<28} {'交易':>5} {'胜率':>6} {'均盈':>7} {'均亏':>7} {'PF':>6} {'总盈亏':>10} {'持仓':>5}")
print(f"  {'-'*3} {'-'*28} {'-'*5} {'-'*6} {'-'*7} {'-'*7} {'-'*6} {'-'*10} {'-'*5}")
for rank, r in enumerate(results):
    rules_str = "→".join(f"{abs(t)*100:.0f}%{'半' if s<100 else '全'}" for t, s in r[1])
    print(f"  {rank+1:<3} {r[0]:<28} {r[8]:>5} {r[2]:>5.1%} {r[3]:>+6.2f} {r[4]:>+6.2f} {r[5]:>6.2f} {r[6]:>+10.2f} {r[7]:>5.1f}")

print("\n  Done!")
