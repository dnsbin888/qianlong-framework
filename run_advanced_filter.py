"""高级过滤: 大盘情绪 + 竞价抢筹 + 流动性"""
import struct, os, numpy as np, pandas as pd, random, time as _time
from collections import defaultdict
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 5000

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

def simulate_exit(entries):
    trades = []
    for e in entries:
        code, idx, ep = e['code'], e['idx'], e['ep']
        s = stocks[code]; peak = ep; remain = 100; dc = 0; h7 = s3 = lh = False
        for j in range(idx+1, min(idx+30, len(s['d']))):
            p = s['c'][j]; h = s['h'][j]; dc += 1
            if h > peak: peak = h
            pnl = (p-ep)/ep; pp = (peak-ep)/ep
            is_zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)
            if is_zt: lh = True; continue
            r, sp = None, 0
            if lh and not is_zt:
                lh = False
                if pp >= 0.02 and (p-peak)/ep <= -0.02: r, sp = "封板次日回落", remain
            if r is None and not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015: r, sp = "+7%回1.5减半", 50; h7 = True
            if r is None and h7 and pp >= 0.07 and (p-peak)/ep <= -0.02: r, sp = "+7%不涨停回2清", remain
            if r is None and not s3 and pnl <= -0.03: r, sp = "-3%减半", 50; s3 = True
            if r is None and pnl <= -0.05: r, sp = "-5%全清", remain
            if r is None and not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.03: r, sp = "回落3%", remain
            if r is None and dc >= 5 and pnl < 0.01: r, sp = "时间5天", remain
            if r:
                s_pct = min(sp, remain)
                trades.append({"pnl": (pnl-0.0013)*(s_pct/100), "days": dc, "reason": r})
                remain -= s_pct; peak = p
                if remain <= 0: break
    return trades

def calc(name, entries, trades):
    if not trades: return (name, len(entries), 0, 0, 0, 0, 0, 0, 0)
    w = [t for t in trades if t["pnl"] > 0]; l_ = [t for t in trades if t["pnl"] <= 0]
    wr = len(w)/len(trades); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    return (name, len(entries), len(trades), wr, aw, al, pf, sum(t["pnl"] for t in trades),
            np.mean([t["days"] for t in trades]))

print("Loading..."); t0 = _time.time()
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

# Load index for market sentiment
idx_data = {}
for sym in ["999999", "1A0001"]:
    d = stocks.get(sym)  # Try from our loaded stocks
    if d and len(d['d']) > 500:
        idx_data = d
        break
if not idx_data:
    # Try direct read
    for sym in ["999999", "1A0001"]:
        path = os.path.join(ROOT, "sh", "lday", f"sh{sym}.day")
        if os.path.exists(path):
            s = ld("sh", f"sh{sym}.day")
            if s: idx_data = s; break

# Build index trend map
idx_trend = {}
if idx_data:
    idx_c = np.array(idx_data['c'])
    idx_ma20 = pd.Series(idx_c).rolling(20).mean().values
    for i, d in enumerate(idx_data['d']):
        if i >= 20:
            idx_trend[d] = (idx_c[i] > idx_ma20[i])

FILTER = {"min_price": 15, "min_turnover": 1e8, "min_vol_ratio": 2.0}

# ═══════════════════ 构建入场池 ═══════════════════
e_base = []      # 基准
e_market = []    # +大盘情绪
e_auction = []   # +竞价抢筹
e_all = []       # 全部过滤

filtered_stats = {"total": 0, "base": 0, "market_skip": 0, "auction_skip": 0}

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        filtered_stats["total"] += 1
        sc = s['c'][i]; sv = s['v'][i]
        if sc < FILTER["min_price"] or sv*sc < FILTER["min_turnover"]: continue
        avg20 = np.mean(s['v'][max(0,i-20):i+1])
        if sv < avg20 * FILTER["min_vol_ratio"]: continue

        ni = i+1; no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01 or (s['o'][ni] == s['h'][ni] == s['c'][ni]): continue

        filtered_stats["base"] += 1
        e_base.append({'code': code, 'idx': ni, 'ep': no})

        # 大盘情绪: index C > MA20
        sig_date = s['d'][i]
        market_ok = idx_trend.get(sig_date, True)  # Default to True if no index data
        if market_ok:
            e_market.append({'code': code, 'idx': ni, 'ep': no})
        else:
            filtered_stats["market_skip"] += 1

        # 竞价抢筹: T+1开盘量 > 5日均量*1.5 且 开盘价>信号价
        avg_v5 = np.mean(s['v'][max(0,ni-5):ni])
        auction_vol = s['v'][ni]
        auction_ok = (auction_vol > avg_v5 * 1.5) and (no > sc)
        if auction_ok:
            e_auction.append({'code': code, 'idx': ni, 'ep': no})
        else:
            filtered_stats["auction_skip"] += 1

        # All filters
        if market_ok and auction_ok:
            e_all.append({'code': code, 'idx': ni, 'ep': no})

# ═══════════════════ 跑对比 ═══════════════════
results = []
for name, entries in [("A:基准", e_base), ("B:+大盘情绪(C>MA20)", e_market),
                       ("C:+竞价抢筹", e_auction), ("D:全部(大盘+抢筹)", e_all)]:
    t = simulate_exit(entries)
    results.append(calc(name, entries, t))

print(f"\n  Signals: {filtered_stats['total']} -> Base:{filtered_stats['base']} "
      f"MarketSkip:{filtered_stats['market_skip']} AuctionSkip:{filtered_stats['auction_skip']}")
print(f"\n  {'方案':<25} {'入场':>6} {'交易':>6} {'胜率':>6} {'均盈':>7} {'均亏':>7} {'PF':>6} {'持仓':>5} {'总盈亏':>10}")
print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*6} {'-'*5} {'-'*10}")
for r in results:
    if r[2] > 0:
        print(f"  {r[0]:<25} {r[1]:>6} {r[2]:>6} {r[3]:>5.1%} {r[4]:>+6.2f} {r[5]:>+6.2f} {r[6]:>6.2f} {r[8]:>5.1f} {r[7]:>+10.2f}")

results.sort(key=lambda x: -x[3])  # Sort by WR
best_wr = results[0]
results.sort(key=lambda x: -x[6])  # Sort by PF
best_pf = results[0]

print(f"\n  最高胜率: {best_wr[0]} WR={best_wr[3]:.1%} PF={best_wr[6]:.2f}")
print(f"  最高PF:   {best_pf[0]} WR={best_pf[3]:.1%} PF={best_pf[6]:.2f}")
print("  Done!")
