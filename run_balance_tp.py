"""平衡止盈: 减少回撤 vs 抓住涨停 — 多方案实测对比"""
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

def sim(mode):
    """
    mode A: tight - +5%回1%半 → +7%回1%清
    mode B: threshold - +5%回1.5%半 → +7%回1.5%清 (old)
    mode C: smart1 - +5%回1%半 → if pp>=9% wait for 涨停 or 回3% else +7%回1%清
    mode D: smart2 - +5%回1%半 → 涨停就不卖, 不涨停才+7%回1%清
    mode E: smart3 - +5%回1.5%半 → 涨停就不卖, 不涨停才+7%回2%清
    """
    ts = []; zt_held = 0; zt_next_pnl = 0.0
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0
        h7 = lh = False; near_zt = False

        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep

            # Limit-up detection
            prev_c = s['c'][j-1] if j > idx else p
            limit_up_price = round(prev_c*1.10, 2) if prev_c > 0 else 999
            is_zt = (p >= limit_up_price - 0.01)
            near_zt_now = (pp >= 0.09)  # Within 1% of limit-up

            if is_zt:
                lh = True
                continue  # ZT: skip any sell today

            # Yesterday was ZT, today normal
            if lh and not is_zt:
                lh = False
                # ZT次日: if pullback 2%, exit remaining
                if pp >= 0.02 and (p-peak)/ep <= -0.02:
                    ts.append({"pnl": (pnl-0.0013)*(remain/100), "days": dc, "r": "ZT次日回落"})
                    zt_held += 1; zt_next_pnl += pnl
                    break

            r, sp = None, 0

            # --- TP logic depends on mode ---
            if mode == 'A':
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
                elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.010: r, sp = "TP2", remain

            elif mode == 'B':
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.015: r, sp = "TP1", 50; h7 = True
                elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.015: r, sp = "TP2", remain

            elif mode == 'C':
                # Smart: approaching ZT → wider stop
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
                elif h7:
                    if near_zt_now:
                        # Near ZT: let it run, only sell if drop 3% from peak
                        if (p-peak)/ep <= -0.03: r, sp = "TP2(近ZT)", remain
                    else:
                        if pp >= 0.07 and (p-peak)/ep <= -0.010: r, sp = "TP2", remain

            elif mode == 'D':
                # Simple: ZT hold, no ZT use tight
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
                elif h7:
                    if lh:  # Was ZT, still holding
                        if (p-peak)/ep <= -0.03: r, sp = "TP2(ZT后)", remain
                    else:
                        if pp >= 0.07 and (p-peak)/ep <= -0.010: r, sp = "TP2", remain

            elif mode == 'E':
                # Current baseline: 1.5%/1.5% + ZT hold
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.015: r, sp = "TP1", 50; h7 = True
                elif h7:
                    if lh:
                        if (p-peak)/ep <= -0.03: r, sp = "TP2(ZT后)", remain
                    else:
                        if pp >= 0.07 and (p-peak)/ep <= -0.020: r, sp = "TP2", remain

            elif mode == 'F':
                # +5%回1%半 → +7%回1%清, but ZT overrides all
                if not h7 and pp >= 0.05 and (p-peak)/ep <= -0.010: r, sp = "TP1", 50; h7 = True
                elif h7 and pp >= 0.07 and (p-peak)/ep <= -0.010:
                    if lh: continue  # Was ZT, skip TP2
                    else: r, sp = "TP2", remain

            # --- Common stops ---
            if r is None:
                if pnl <= -0.055: r, sp = "SL", remain
                elif dc >= 5 and pnl < 0.01: r, sp = "Time", remain
                elif not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "Drop", remain

            if r and sp > 0:
                s2 = min(sp, remain)
                ts.append({"pnl": (pnl-0.0013)*(s2/100), "days": dc, "r": r})
                remain -= s2; peak = p
                if remain <= 0: break

    if not ts: return (0, 0, 0, 0, 0, 0, 0, 0)
    w = [t for t in ts if t["pnl"] > 0]; l_ = [t for t in ts if t["pnl"] <= 0]
    wr = len(w)/len(ts); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    total = sum(t["pnl"] for t in ts)
    zt_trades = sum(1 for t in ts if "ZT" in t["r"])
    return (len(ts), wr, aw, al, pf, total, zt_trades, np.mean([t["days"] for t in ts]))

MODES = [
    ("A:紧(+5%回1%/+7%回1%)",     'A'),
    ("B:宽(+5%回1.5%/+7%回1.5%)", 'B'),
    ("C:智能(+9%近ZT放宽到3%)",    'C'),
    ("D:ZT持有+紧(1%)",            'D'),
    ("E:ZT持有+宽(1.5%/2%)",       'E'),
    ("F:ZT持有覆盖TP2",            'F'),
]

print(f"Entries: {len(entries)}\n")
print(f"  {'Scheme':<30} {'Trd':>5} {'WR':>6} {'AW':>6} {'AL':>6} {'PF':>6} {'PnL':>10} {'ZT笔':>5} {'Day':>5}")
print(f"  {'-'*30} {'-'*5} {'-'*6} {'-'*6} {'-'*6} {'-'*6} {'-'*10} {'-'*5} {'-'*5}")
results = []
for name, mode in MODES:
    nt, wr, aw, al, pf, pnl, zt, avg_d = sim(mode)
    results.append((name, mode, nt, wr, aw, al, pf, pnl, zt, avg_d))
    print(f"  {name:<30} {nt:>5} {wr:>5.1%} {aw:>+5.2f} {al:>+5.2f} {pf:>6.2f} {pnl:>+10.2f} {zt:>5} {avg_d:>5.1f}")

results.sort(key=lambda x: (-x[6], -x[3]))
print(f"\n  {'='*65}")
print(f"  RANKING by PF:")
for rank, r in enumerate(results):
    print(f"  {rank+1}. {r[0]:<30} PF={r[6]:.2f} WR={r[3]:.1%} PnL={r[7]:+.2f} ZT={r[8]}")

print(f"\n  Done!")
