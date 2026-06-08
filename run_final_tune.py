"""最终优化 — 入场确认 + 诊断报告"""
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

# ═══════════════════ 通用出场 ═══════════════════
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
                trades.append({"pnl": (pnl-0.0013)*(s_pct/100), "days": dc, "reason": r,
                               "ep": ep, "exit": p, "code": code, "pp": pp, "is_zt": is_zt})
                remain -= s_pct; peak = p
                if remain <= 0: break
    return trades

def stats(name, entries, trades):
    if not trades: return (name, len(entries), 0, 0, 0, 0, 0, 0, 0, 0, {})
    w = [t for t in trades if t["pnl"] > 0]; l_ = [t for t in trades if t["pnl"] <= 0]
    wr = len(w)/len(trades); aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else 0
    total = sum(t["pnl"] for t in trades)
    avg_d = np.mean([t["days"] for t in trades])
    rc = defaultdict(int)
    for t in trades: rc[t["reason"]] += 1
    # Max consecutive losses
    mcl = cur = 0
    for t in trades:
        cur = cur+1 if t["pnl"] <= 0 else 0; mcl = max(mcl, cur)
    daily = len(entries) / 1500
    return (name, len(entries), len(trades), wr, aw, al, pf, total, avg_d, mcl, daily, rc)

# ═══════════════════ 收集信号 ═══════════════════
print("Computing signals & filtering...")
FILTER = {"min_price": 15, "min_turnover": 1e8, "min_vol_ratio": 2.0}

# Base entries (no open-check)
e_base = []
# +open confirm: T+1 open > signal close
e_confirm = []
# +open confirm + gap size matters: open > signal close * 1.01
e_gap1pct = []
# Diagnostic: track what happened to filtered signals
diag = {"total_signals": 0, "price_filtered": 0, "turnover_filtered": 0,
        "vol_filtered": 0, "limit_up": 0, "yiziban": 0,
        "open_below_signal": 0, "open_above_5pct": 0}

for code in codes:
    s = stocks[code]
    df = pd.DataFrame({'open': s['o'], 'high': s['h'], 'low': s['l'],
                       'close': s['c'], 'volume': s['v']})
    try: ff = f1(df)
    except: continue
    for i in range(250, len(df)-1):
        if not ff[i]: continue
        diag["total_signals"] += 1
        sc = s['c'][i]; sv = s['v'][i]
        if sc < FILTER["min_price"]: diag["price_filtered"] += 1; continue
        if sv*sc < FILTER["min_turnover"]: diag["turnover_filtered"] += 1; continue
        avg20 = np.mean(s['v'][max(0,i-20):i+1])
        if sv < avg20 * FILTER["min_vol_ratio"]: diag["vol_filtered"] += 1; continue

        ni = i+1; no = s['o'][ni]; lu = round(sc*1.10, 2)
        if no >= lu-0.01: diag["limit_up"] += 1; continue
        if s['o'][ni] == s['h'][ni] == s['c'][ni]: diag["yiziban"] += 1; continue

        e_base.append({'code': code, 'idx': ni, 'ep': no})

        # Open confirm: open > signal close
        if no > sc:
            e_confirm.append({'code': code, 'idx': ni, 'ep': no})
        else:
            diag["open_below_signal"] += 1

        # Gap > 1% confirm
        if no > sc * 1.01 and no < sc * 1.05:
            e_gap1pct.append({'code': code, 'idx': ni, 'ep': no})
        elif no >= sc * 1.05:
            diag["open_above_5pct"] += 1

# ═══════════════════ 跑三个方案 ═══════════════════
print(f"\n  Filtering: {diag['total_signals']} signals -> "
      f"price={diag['price_filtered']} turnover={diag['turnover_filtered']} "
      f"vol={diag['vol_filtered']} limit_up={diag['limit_up']}")
print(f"  Open confirm: {diag['open_below_signal']} below signal, {diag['open_above_5pct']} above 5%\n")

r1 = stats("A:开盘无脑买", e_base, simulate_exit(e_base))
r2 = stats("B:开盘>信号价才买", e_confirm, simulate_exit(e_confirm))
r3 = stats("C:开盘>信号价1-5%", e_gap1pct, simulate_exit(e_gap1pct))

print(f"  {'方案':<22} {'入场':>6} {'交易':>6} {'胜率':>6} {'均盈':>7} {'均亏':>7} {'PF':>6} {'持仓':>5} {'连亏':>4} {'日选':>5} {'总盈亏':>10}")
print(f"  {'-'*22} {'-'*6} {'-'*6} {'-'*6} {'-'*7} {'-'*7} {'-'*6} {'-'*5} {'-'*4} {'-'*5} {'-'*10}")
for r in [r1, r2, r3]:
    print(f"  {r[0]:<22} {r[1]:>6} {r[2]:>6} {r[3]:>5.1%} {r[4]:>+6.2f} {r[5]:>+6.2f} {r[6]:>6.2f} {r[7]:>5.1f} {r[9]:>4} {r[10]:>5.1f} {r[7]:>+10.2f}")

# ═══════════════════ 诊断报告 ═══════════════════
print(f"\n{'='*65}")
print(f"  DIAGNOSTIC REPORT")
print(f"{'='*65}")

best = max([r1, r2, r3], key=lambda x: x[6])
print(f"\n  Best: {best[0]} PF={best[6]:.2f} WR={best[3]:.1%}")

# Analyze -3% triggers for best config
best_entries = {"A": e_base, "B": e_confirm, "C": e_gap1pct}[best[0][0]]
best_trades = simulate_exit(best_entries)
minus3_trades = [t for t in best_trades if "3%减半" in t["reason"]]
if minus3_trades:
    avg_day_of_minus3 = np.mean([t["days"] for t in minus3_trades])
    avg_gap_of_minus3 = np.mean([(t["ep"] - best_entries[0].get("ep", 0)) for t in minus3_trades if t["code"] == best_entries[0].get("code", "")])

print(f"\n  -3%减半诊断 ({len(minus3_trades)}笔):")
print(f"    平均触发天数: {avg_day_of_minus3:.1f}天")
# Check if -3% happens mostly on day 1
day1_minus3 = sum(1 for t in minus3_trades if t["days"] <= 1)
print(f"    第1天触发: {day1_minus3}笔 ({day1_minus3/len(minus3_trades)*100:.0f}%)")

# Win rate by hold days
print(f"\n  胜率与持仓天数关系:")
for d in [1, 2, 3, 4, 5]:
    day_trades = [t for t in best_trades if t["days"] == d]
    if day_trades:
        d_wr = sum(1 for t in day_trades if t["pnl"] > 0) / len(day_trades)
        print(f"    第{d}天退出: WR={d_wr:.0%} ({len(day_trades)}笔)")

# Recommendation
print(f"\n{'='*65}")
print(f"  OPTIMIZATION RECOMMENDATIONS")
print(f"{'='*65}")
if r2[6] > r1[6]:
    print(f"  [OK] 开盘>信号价确认有效: PF {r1[6]:.2f} -> {r2[6]:.2f}")
else:
    print(f"  [--] 开盘>信号价无效, 保持原方案")
if r3[6] > r2[6]:
    print(f"  [OK] 开盘涨幅1-5%最优")

print(f"\n  最终建议: 使用{best[0]}方案")
print("  Done!")
