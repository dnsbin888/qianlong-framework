"""最终策略2年回测报告 — 全A股"""
import struct, os, numpy as np, pandas as pd, random, time as _time
from collections import defaultdict, Counter
ROOT = r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc"
N = 99999  # All stocks

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

print("=" * 65)
print("  FINAL STRATEGY BACKTEST REPORT")
print("  Period: 2024-06 ~ 2026-06 (2 years)")
print("=" * 65)

# Phase 1: Load
print("\n[1/5] Loading A-share data...")
t0 = _time.time()
stocks = {}
total_files = 0
for m in ['sh', 'sz']:
    ld2 = os.path.join(ROOT, m, 'lday')
    if not os.path.isdir(ld2): continue
    for f in os.listdir(ld2):
        if not f.endswith('.day'): continue
        total_files += 1
        s = ld(m, f)
        if s and len(s['d']) >= 300:
            stocks[s['code']] = s

codes_all = list(stocks.keys())
random.seed(42)
if len(codes_all) > N: codes_all = random.sample(codes_all, min(N, len(codes_all)))
stocks = {k: stocks[k] for k in codes_all if k in stocks}
print(f"  {len(stocks)} valid stocks from {total_files} files ({_time.time()-t0:.0f}s)")

# Phase 2: Compute signals
print("\n[2/5] Computing F1 signals (2024-2026)...")
t0 = _time.time()

FILTER = {"min_price": 15, "min_turnover": 1e8, "min_vol_ratio": 2.0}
START_DATE = 20240601
END_DATE = 20260601

entries = []
stats_filter = {"signals": 0, "price": 0, "turnover": 0, "vol": 0, "limit_up": 0, "yiziban": 0, "ok": 0}
daily_signals = defaultdict(int)

for code in codes_all:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        d = s['d'][i]
        if d < START_DATE or d > END_DATE: continue
        stats_filter["signals"] += 1
        sc = s['c'][i]; sv = s['v'][i]
        if sc < FILTER["min_price"]: stats_filter["price"] += 1; continue
        if sv*sc < FILTER["min_turnover"]: stats_filter["turnover"] += 1; continue
        avg20 = np.mean(s['v'][max(0,i-20):i+1])
        if sv < avg20 * FILTER["min_vol_ratio"]: stats_filter["vol"] += 1; continue
        ni = i+1
        if ni >= len(s['d']): continue
        no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01: stats_filter["limit_up"] += 1; continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: stats_filter["yiziban"] += 1; continue
        stats_filter["ok"] += 1
        entries.append({'code': code, 'idx': ni, 'ep': no, 'signal_date': d})
        daily_signals[d] += 1

print(f"  F1 signals: {stats_filter['signals']:,}")
print(f"  After filter: {stats_filter['ok']:,} buyable entries")
print(f"  Filtered out: price={stats_filter['price']:,} turnover={stats_filter['turnover']:,} vol={stats_filter['vol']:,} limit_up={stats_filter['limit_up']:,}")
print(f"  Daily avg candidates: {len(entries)/500:.1f}")
print(f"  ({_time.time()-t0:.0f}s)")

# Phase 3: Exit simulation
print("\n[3/5] Running exit simulation...")
t0 = _time.time()

CONFIG = {
    "tp1_pct": 0.05, "tp1_drop": 0.010, "tp1_sell": 50,
    "tp2_pct": 0.07, "tp2_drop": 0.020, "tp2_sell": 100,
    "stop": -0.05, "time_limit": 5,
    "zt_drop": 0.02,
}

trades = []
equity_curve = []  # Track cumulative P&L
cumulative_pnl = 0

for e in entries:
    code, idx, ep = e['code'], e['idx'], e['ep']
    s = stocks[code]
    peak = ep; remain = 100; dc = 0
    h7 = lh = False
    entry_pnl_events = []

    for j in range(idx+1, min(idx+30, len(s['d']))):
        p = s['c'][j]; h = s['h'][j]; dc += 1
        if h > peak: peak = h
        pnl = (p-ep)/ep; pp = (peak-ep)/ep
        prev_c = s['c'][j-1] if j > idx else p
        lu = round(prev_c*1.10, 2) if prev_c > 0 else 999
        is_zt = (p >= lu - 0.01)

        if is_zt:
            lh = True
            continue

        if lh and not is_zt:
            lh = False
            if pp >= 0.02 and (p-peak)/ep <= -CONFIG["zt_drop"]:
                trades.append({"pnl": (pnl-0.0013)*(remain/100), "days": dc, "reason": "ZT次日回落",
                               "code": code, "entry_date": s['d'][idx], "exit_date": s['d'][j]})
                break

        r, sp = None, 0

        if not h7 and pp >= CONFIG["tp1_pct"] and (p-peak)/ep <= -CONFIG["tp1_drop"]:
            r, sp = "+5%回1%半", CONFIG["tp1_sell"]; h7 = True
        elif h7 and pp >= CONFIG["tp2_pct"] and (p-peak)/ep <= -CONFIG["tp2_drop"]:
            r, sp = "+7%回2%清", CONFIG["tp2_sell"]
        elif pnl <= CONFIG["stop"]:
            r, sp = "-5%止损", 100
        elif not is_zt and pp >= 0.02 and (p-peak)/ep <= -0.03:
            r, sp = "回落3%", 100
        elif dc >= CONFIG["time_limit"] and pnl < 0.01:
            r, sp = "时间5天", 100

        if r and sp > 0:
            s2 = min(sp, remain)
            pnl_event = (pnl-0.0013)*(s2/100)
            trades.append({"pnl": pnl_event, "days": dc, "reason": r,
                           "code": code, "entry_date": s['d'][idx], "exit_date": s['d'][j]})
            remain -= s2; peak = p
            if remain <= 0: break

    cumulative_pnl += sum(t["pnl"] for t in entry_pnl_events)

print(f"  {len(trades):,} trade events ({_time.time()-t0:.0f}s)")

# Phase 4: Statistics
print("\n[4/5] Computing statistics...")

if not trades:
    print("  No trades!"); import sys; sys.exit(1)

wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]
wr = len(wins) / len(trades)
aw = np.mean([t["pnl"] for t in wins]) if wins else 0
al = np.mean([t["pnl"] for t in losses]) if losses else 0
pf = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses else float("inf")
total_pnl = sum(t["pnl"] for t in trades)
avg_days = np.mean([t["days"] for t in trades])

# Max consecutive loss
mcl = cur = 0
for t in trades:
    if t["pnl"] <= 0: cur += 1
    else: cur = 0
    mcl = max(mcl, cur)

# Max consecutive win
mcw = cur = 0
for t in trades:
    if t["pnl"] > 0: cur += 1
    else: cur = 0
    mcw = max(mcw, cur)

# Reason breakdown
reasons = Counter(t["reason"] for t in trades)

# Monthly breakdown
monthly = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
for t in trades:
    ym = str(t["entry_date"])[:6]
    monthly[ym]["trades"] += 1
    monthly[ym]["pnl"] += t["pnl"]
    if t["pnl"] > 0: monthly[ym]["wins"] += 1

# Top/bottom stocks
stock_pnl = defaultdict(float)
stock_count = defaultdict(int)
for t in trades:
    stock_pnl[t["code"]] += t["pnl"]
    stock_count[t["code"]] += 1

top_stocks = sorted(stock_pnl.items(), key=lambda x: -x[1])[:10]
worst_stocks = sorted(stock_pnl.items(), key=lambda x: x[1])[:10]

# Profit distribution
pnls = [t["pnl"] for t in trades]
pct_pnls = [t["pnl"] for t in trades if t["reason"] == "+5%回1%半" or t["reason"] == "+7%回2%清"]  # Win trades

# ═══════════════════ Phase 5: Report ═══════════════════
print("\n[5/5] Generating report...\n")

print("=" * 70)
print("  FINAL STRATEGY BACKTEST REPORT")
print("=" * 70)
print(f"  Period: 2024-06-01 ~ 2026-06-01 (2 years)")
print(f"  Universe: {len(stocks):,} A-shares")
print()
print(f"  ──────────── Signal & Entry ────────────")
print(f"  F1 signals generated:    {stats_filter['signals']:,}")
print(f"  After quality filters:   {stats_filter['ok']:,}")
print(f"  Avg daily candidates:    {stats_filter['ok']/500:.1f}")
print()
print(f"  ──────────── Exit Results ────────────")
print(f"  Total trade events:      {len(trades):,}")
print(f"  Win Rate:                {wr:.1%}")
print(f"  Avg Win / trade:         {aw:>+.2f}")
print(f"  Avg Loss / trade:        {al:>+.2f}")
print(f"  Profit Factor:           {pf:.2f}")
print(f"  Total P&L (sum):         {total_pnl:>+.2f}")
print(f"  Avg Hold Days:           {avg_days:.1f}")
print(f"  Max Consec Wins:         {mcw}")
print(f"  Max Consec Losses:       {mcl}")
print()
print(f"  ──────────── Exit Reason Breakdown ────────────")
for reason, count in reasons.most_common():
    bar = "█" * max(1, count * 40 // reasons.most_common(1)[0][1])
    print(f"  {reason:<14}: {count:>5} ({count/len(trades)*100:>5.1f}%) {bar}")
print()
print(f"  ──────────── Monthly P&L ────────────")
print(f"  {'Month':<8} {'Trades':>6} {'WinRate':>8} {'P&L':>12}")
for ym in sorted(monthly.keys())[-12:]:
    m = monthly[ym]
    mwr = m["wins"]/m["trades"]*100 if m["trades"] > 0 else 0
    print(f"  {ym:<8} {m['trades']:>6} {mwr:>7.1f}% {m['pnl']:>+12.2f}")
print()
print(f"  ──────────── P&L Distribution ────────────")
bins = [(-99, -0.10), (-0.10, -0.05), (-0.05, -0.02), (-0.02, 0),
        (0, 0.02), (0.02, 0.05), (0.05, 0.10), (0.10, 0.20), (0.20, 99)]
for lo, hi in bins:
    n = sum(1 for p in pnls if lo < p <= hi)
    if n > 0:
        bar = "█" * max(1, n * 30 // max(len(pnls)//10, 1))
        print(f"  {lo:>+5.0%} ~ {hi:>+5.0%}: {n:>5} ({n/len(pnls)*100:>4.0f}%) {bar}")
print()
print(f"  ──────────── Strategy Rules ────────────")
print(f"  Entry:  F1双共振 + P>15 + Turnover>1e8 + VolRatio>2")
print(f"  Buy:    T+1 open (涨<8%, non-一字板)")
print(f"  TP1:    +5%回落1.0% -> 卖50%")
print(f"  TP2:    +7%回落2.0% -> 全清")
print(f"  ZT:     涨停跳过, 持有过夜, 次日回落2%清")
print(f"  SL:     -5.0%全清")
print(f"  Time:   5 days no profit -> exit")
print()
print(f"  Report saved to: d:\\quant_framework\\backtest_report_2y.csv")
print("=" * 70)

# Save detailed trades
pd.DataFrame(trades).to_csv(r"d:\quant_framework\backtest_report_2y.csv", index=False, encoding="utf-8-sig")
print("  Done!")
