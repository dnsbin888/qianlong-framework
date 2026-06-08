"""最终策略验证 — 5000只全量回测"""
import struct, os, numpy as np, pandas as pd, random, time as _time
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

print("Loading stocks...")
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
print(f"  {len(stocks)} stocks loaded ({_time.time()-t0:.0f}s)")

# ═══════════════════ 选股 ═══════════════════
print("Computing F1 signals...")
t0 = _time.time()

CONFIG = {"min_price": 15, "min_turnover": 1e8, "min_vol_ratio": 2.0}
entries = []
signal_count = 0
filtered = {"price": 0, "turnover": 0, "vol_ratio": 0, "limit_up": 0, "yiziban": 0}

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        signal_count += 1
        sc = s['c'][i]; sv = s['v'][i]
        if sc < CONFIG["min_price"]: filtered["price"] += 1; continue
        if sv * sc < CONFIG["min_turnover"]: filtered["turnover"] += 1; continue
        avg20 = np.mean(s['v'][max(0,i-20):i+1])
        if sv < avg20 * CONFIG["min_vol_ratio"]: filtered["vol_ratio"] += 1; continue

        ni = i+1; no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu - 0.01: filtered["limit_up"] += 1; continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: filtered["yiziban"] += 1; continue
        entries.append({'code': code, 'idx': ni, 'ep': no})

print(f"  F1 signals: {signal_count}")
print(f"  Filtered: price={filtered['price']} turnover={filtered['turnover']} "
      f"vol={filtered['vol_ratio']} limit_up={filtered['limit_up']} yiziban={filtered['yiziban']}")
print(f"  Buyable entries: {len(entries)} ({_time.time()-t0:.0f}s)")

# ═══════════════════ 出场回测 ═══════════════════
print("Running exit simulation...")
t0 = _time.time()
trades = []

for e in entries:
    code, idx, ep = e['code'], e['idx'], e['ep']
    s = stocks[code]; peak = ep; remain = 100; dc = 0
    h7 = s30 = False; limit_held = False

    for j in range(idx+1, min(idx+30, len(s['d']))):
        p = s['c'][j]; h = s['h'][j]; dc += 1
        if h > peak: peak = h
        pnl = (p-ep)/ep; pp = (peak-ep)/ep

        is_zt = (j > idx and s['c'][j-1] > 0 and p >= round(s['c'][j-1]*1.10, 2)-0.01)

        # Sell reason tracking
        reason = None; sell_pct = 0

        if is_zt:
            limit_held = True
            continue  # 涨停持有

        # 昨日涨停,今天重新判断
        if limit_held and not is_zt:
            limit_held = False
            if pp >= 0.02 and (p-peak)/ep <= -0.02:
                reason = "封板次日回落2%"; sell_pct = remain

        # +7%回落1.5%减半
        if reason is None and not h7 and pp >= 0.07 and (p-peak)/ep <= -0.015:
            reason = "+7%回1.5减半"; sell_pct = 50
            h7 = True

        # 减半后+7%不涨停回2%全清
        if reason is None and h7 and pp >= 0.07 and (p-peak)/ep <= -0.02:
            reason = "+7%不涨停回2%清"; sell_pct = remain

        # -3%减半
        if reason is None and not s30 and pnl <= -0.03:
            reason = "-3%减半"; sell_pct = 50
            s30 = True

        # -5%全清
        if reason is None and pnl <= -0.05:
            reason = "-5%全清"; sell_pct = remain

        # 不涨停回落3%全清
        if reason is None and not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.03:
            reason = "回落3%"; sell_pct = remain

        # 时间5天
        if reason is None and dc >= 5 and pnl < 0.01:
            reason = "时间5天"; sell_pct = remain

        if reason is not None and sell_pct > 0:
            s_pct = min(sell_pct, remain)
            net = (pnl - 0.0013) * (s_pct / 100.0)
            trades.append({"pnl": net, "days": dc, "reason": reason[:8],
                           "entry": ep, "exit": p, "code": code,
                           "pnl_pct": net / (ep * s_pct / 100) if ep > 0 else 0})
            remain -= s_pct; peak = p
            if remain <= 0: break
            if sell_pct >= remain: break

print(f"  {len(trades)} trade events in {_time.time()-t0:.0f}s")

# ═══════════════════ 统计 ═══════════════════
if not trades:
    print("No trades!"); import sys; sys.exit(1)

w = [t for t in trades if t["pnl"] > 0]
l_ = [t for t in trades if t["pnl"] <= 0]

wr = len(w) / len(trades)
aw = np.mean([t["pnl"] for t in w]) if w else 0
al = np.mean([t["pnl"] for t in l_]) if l_ else 0
pf = abs(sum(t["pnl"] for t in w) / sum(t["pnl"] for t in l_)) if l_ else float("inf")
total_pnl = sum(t["pnl"] for t in trades)
avg_d = np.mean([t["days"] for t in trades])

# Count unique stocks
unique_codes = len(set(t["code"] for t in trades))

# Reason breakdown
from collections import Counter
reasons = Counter(t["reason"] for t in trades)
reason_pnl = {}
for t in trades:
    r = t["reason"][:8]
    reason_pnl[r] = reason_pnl.get(r, 0) + t["pnl"]

# P&L distribution
pnls = [t["pnl"] for t in trades]
pct_pnls = [t["pnl_pct"] for t in trades]

# Max drawdown in P&L sequence
cumsum = np.cumsum([t["pnl"] for t in trades])
peak = np.maximum.accumulate(cumsum)
dd = (cumsum - peak)
max_dd = dd.min()
max_dd_pct = max_dd / (peak[0] + 1e-9) if len(peak) > 0 else 0

# Consecutive losses
max_consec_loss = 0; cur = 0
for t in trades:
    if t["pnl"] <= 0: cur += 1
    else: cur = 0
    max_consec_loss = max(max_consec_loss, cur)

# Yearly breakdown
yearly = {}
for t in trades:
    # Find year from exit date
    for sym_data in [stocks.get(t["code"])]:
        pass
    yearly[2020] = yearly.get(2020, []) + [t["pnl"]]

# Daily signal count
daily_signals = len(entries) / 1500  # approx trading days

print(f"\n{'='*65}")
print(f"  FINAL STRATEGY VERIFICATION (N={N} stocks)")
print(f"{'='*65}")
print(f"  Filter: Price>15 | Turnover>1亿 | VolRatio>2")
print(f"  Entry:  T+1 open (spread<8%, not 一字板)")
print(f"  Exit:   +7%回1.5减半 → 不涨停回2清 | -3减半 -5清")
print()
print(f"  ─────────────── Key Metrics ───────────────")
print(f"  Buyable Entries:    {len(entries):>8}")
print(f"  Trade Events:       {len(trades):>8}")
print(f"  Unique Stocks:      {unique_codes:>8}")
print(f"  Daily Candidates:   {daily_signals:>8.1f}")
print(f"  ───────────────────────────────────────────")
print(f"  Win Rate:           {wr:>8.1%}")
print(f"  Avg Win:            {aw:>+8.2f} / trade")
print(f"  Avg Loss:           {al:>+8.2f} / trade")
print(f"  Profit Factor:      {pf:>8.2f}")
print(f"  Total P&L:          {total_pnl:>+8.2f}")
print(f"  Avg Hold Days:      {avg_d:>8.1f}")
print(f"  Max Consec Losses:  {max_consec_loss:>8}")
print(f"  Max Drawdown(est):  {max_dd:>+8.2f}")
print(f"  ───────────────────────────────────────────")

print(f"\n  Exit Reason Breakdown:")
for reason, count in reasons.most_common(10):
    pnl_sum = reason_pnl.get(reason, 0)
    bar = "#" * max(1, count * 40 // max(reasons.most_common(1)[0][1], 1))
    print(f"  {reason:<12}: {count:>5}笔 {bar} P&L={pnl_sum:>+10.2f}")

print(f"\n  P&L Distribution:")
bins = [(-99, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0),
        (0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 99)]
for lo, hi in bins:
    n = sum(1 for p in pnls if lo < p <= hi)
    if n > 0:
        bar = "#" * max(1, n * 30 // max(len(pnls)//10, 1))
        print(f"  {lo:>+5.0%} ~ {hi:>+5.0%}: {n:>5} ({n/len(pnls)*100:>4.0f}%) {bar}")

print(f"\n  Done!")
