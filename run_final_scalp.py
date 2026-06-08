"""最终版超短策略 — 优化出场规则 + 擒龙决双共振"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

# ═══════════════════ 辅助函数 ═══════════════════
def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()

def signal_f1(df):
    """公式1: 擒龙决 AND 涨停先锋"""
    c, v = df["close"], df["volume"]
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    ema20 = _ema(c, 20)
    dev = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper = _ref(ema20 + 2*dev, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    qlj = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = qlj & (_count(qlj, 7) == 1)
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    ztxf = (c > profit100) & (_count(c > profit100, 7) == 1)
    return (qlj & ztxf).astype(int).values

# ═══════════════════ 加载 ═══════════════════
print("=" * 65)
print("  最终版超短策略 — 优化出场规则")
print("=" * 65)
print(f"  入场: 擒龙决 AND 涨停先锋 (双共振)")
print(f"  出场规则:")
print(f"    冲高+7%回落1.5% → 全卖")
print(f"    封板 → 不卖! 次日冲高回落再卖")
print(f"    次日低开-3% → 卖50%, 余量等冲高/ATR追踪")
print(f"    硬止损-5%(余量) | 时间止损8天")

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"\n  股票池: {len(data)} 只\n")

# ═══════════════════ 回测 ═══════════════════
trades = []
total_trades_with_half_exit = 0  # Count half-exit events

for si, (sym, sd) in enumerate(data.items()):
    if si % 500 == 0: print(f"  {si}/{len(data)}...")

    df = pd.DataFrame({
        "open": sd["open"][-500:], "high": sd["high"][-500:],
        "low": sd["low"][-500:], "close": sd["close"][-500:],
        "volume": sd["volume"][-500:],
    })
    if len(df) < 300: continue

    try: sig_arr = signal_f1(df)
    except Exception: continue

    # Position state
    pos = None  # {entry_p, entry_i, peak, shares_full, shares_remain, half_sold, limit_up_held}

    for i in range(250, len(df)):
        p = df["close"].iloc[i]; o = df["open"].iloc[i]
        h = df["high"].iloc[i]; l = df["low"].iloc[i]
        v = df["volume"].iloc[i]
        if p <= 3.0: continue
        prev_c = df["close"].iloc[i-1] if i >= 1 else p
        limit_up_p = round(prev_c*1.10, 2) if prev_c > 0 else 999999

        if pos is None:
            # Entry: signal + not limit-up
            if sig_arr[i] and p < limit_up_p - 0.01 and o < h:  # not一字板
                pos = dict(entry_p=p, entry_i=i, peak=p, shares=100, remain=100,
                          half_sold=False, limit_held=False)
        else:
            # Update peak
            if h > pos["peak"]: pos["peak"] = h

            days = i - pos["entry_i"]
            pnl = (p - pos["entry_p"]) / pos["entry_p"]
            peak_pnl = (pos["peak"] - pos["entry_p"]) / pos["entry_p"]

            # ── 封板处理: 如果涨停了, 不卖! 持有到次日 ──
            if p >= limit_up_p - 0.01:
                pos["limit_held"] = True
                continue  # 今天不卖, 等明天

            # ── 次日低开-3%: 卖一半 ──
            if not pos["half_sold"] and days <= 2 and pnl <= -0.03:
                # Sell 50%
                net = (pnl - 0.0003 - 0.001) * 0.5
                trades.append(dict(sym=sym, entry=pos["entry_p"], exit=p,
                                   pnl=net, days=days, reason="低开减半"))
                pos["half_sold"] = True
                pos["remain"] = 50
                total_trades_with_half_exit += 1
                # Reset peak for remaining half
                pos["peak"] = p
                continue  # 余量继续持有

            # ── 出场判断 (对剩余仓位) ──
            pnl_now = (p - pos["entry_p"]) / pos["entry_p"]
            peak_now = (pos["peak"] - pos["entry_p"]) / pos["entry_p"]
            exit_reason = None

            # 昨日封板, 今天冲高回落 → 卖
            if pos.get("limit_held") and peak_now >= 0.03 and (p - pos["peak"]) / pos["entry_p"] <= -0.015:
                exit_reason = "封板次日回落"
            # +7%回落1.5% → 卖
            elif peak_now >= 0.07 and (p - pos["peak"]) / pos["entry_p"] <= -0.015:
                exit_reason = f"冲高回落 峰{peak_now:.0%}"
            # 盈利回落
            elif pnl_now >= 0.05 and (pos["peak"] - p) / pos["entry_p"] >= 0.015:
                exit_reason = f"盈利回落 峰{peak_now:.0%}"
            # 硬止损-5% (已减半的更宽)
            elif pnl_now <= (-0.05 if pos["half_sold"] else -0.03):
                exit_reason = f"硬止损{pnl_now:.0%}"
            # 时间止损 (已减半的给更长时间)
            elif days >= (8 if pos["half_sold"] else 5) and pnl_now < 0.01:
                exit_reason = f"时间{days}天"

            if exit_reason:
                sell_pct = pos["remain"] / 100.0
                net = (pnl_now - 0.0003 - 0.001) * sell_pct
                trades.append(dict(sym=sym, entry=pos["entry_p"], exit=p,
                                   pnl=net, days=days, reason=exit_reason,
                                   half_sold=pos["half_sold"]))
                pos = None  # Clear position

# ═══════════════════ 结果 ═══════════════════
if not trades:
    print("No trades!"); import sys; sys.exit(1)

w = [t for t in trades if t["pnl"] > 0]
l_ = [t for t in trades if t["pnl"] <= 0]
wr = len(w)/len(trades)
aw = np.mean([t["pnl"] for t in w]) if w else 0
al = np.mean([t["pnl"] for t in l_]) if l_ else 0
pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
avg_days = np.mean([t["days"] for t in trades])

# By reason
reasons = defaultdict(lambda: [0, 0.0])
for t in trades:
    r = t["reason"].split("峰")[0].split("硬")[0].split("时间")[0].split("低开")[0].split("封板")[0].strip()[:6]
    reasons[r][0] += 1; reasons[r][1] += t["pnl"]

# Half-sold recovery analysis
half_trades = [t for t in trades if t.get("half_sold")]
half_w = [t for t in half_trades if t["pnl"] > 0]

print(f"\n{'='*65}")
print(f"  FINAL RESULTS ({len(data)} stocks)")
print(f"{'='*65}")
print(f"  Total Trades:       {len(trades):>8}")
print(f"  Half-Exit Events:   {len(half_trades):>8}")
print(f"  Win Rate:           {wr:>8.1%}  ({len(w)}W/{len(l_)}L)")
print(f"  Avg Win:            {aw:>8.2%}")
print(f"  Avg Loss:           {al:>8.2%}")
print(f"  Profit Factor:      {pf:>8.2f}")
print(f"  Avg Hold Days:      {avg_days:>8.1f}")
print(f"  Half-Sold Recovery: {len(half_w):>8}/{len(half_trades)} ({len(half_w)/max(len(half_trades),1)*100:.0f}%)")
print()

print(f"  Exit Reason Breakdown:")
for r, (c, pnl) in sorted(reasons.items(), key=lambda x: -x[1][0]):
    bar = "█" * max(1, int(c / max(len(trades)/30, 1)))
    print(f"  {r:<12}: {c:>5}笔 {bar} P&L={pnl:>+8.2f}")

# P&L buckets
print(f"\n  P&L Distribution:")
pnls = [t["pnl"] for t in trades]
bins = [-0.10, -0.05, -0.03, -0.01, 0, 0.01, 0.03, 0.05, 0.07, 0.10, 0.20, 1.0]
for i in range(len(bins)-1):
    count = sum(1 for p in pnls if bins[i] <= p < bins[i+1])
    if count > 0:
        bar = "█" * max(1, count * 40 // max(len(trades)//6, 1))
        print(f"  {bins[i]:>+6.1%}~{bins[i+1]:>+6.1%}: {count:>5} {bar}")

print(f"\n  Done!")
