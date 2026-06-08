"""三公式同台 PK — 超短风格统一出场规则"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")
import time as _time

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 2000

CONFIG = dict(hard_stop=-0.03, time_stop=5, trail_peak=0.07, trail_drop=-0.015,
              comm=0.0003, tax=0.001, filter_lu=True, position_pct=0.20)

# ═══════════════════ 辅助函数 ═══════════════════
def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()
def _exist(c, n): return c.rolling(n, min_periods=1).max().fillna(0).astype(bool)
def _every(c, n): return c.rolling(n, min_periods=1).min().fillna(0).astype(bool)
def _barslast(c):
    r = pd.Series(np.nan, index=c.index); last = -1
    for i, idx in enumerate(c.index):
        if c.iloc[i]: last = i
        r.iloc[i] = i - last if last >= 0 else np.nan
    return r

# ═══════════════════ 三公式信号定义 ═══════════════════

def signal_formula1(df):
    """公式1: 擒龙决 AND 涨停先锋 (双信号共振·打板+分歧低吸)"""
    c, v = df["close"], df["volume"]
    # 擒龙决
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    ema20 = _ema(c, 20)
    dev = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper = _ref(ema20 + 2*dev, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    qlj = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    qlj = qlj & (_count(qlj, 7) == 1)
    # 涨停先锋
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    ztxf = (c > profit100) & (_count(c > profit100, 7) == 1)
    return (qlj & ztxf).astype(int).values

def signal_formula2(df):
    """公式2: 突破起飞 (牛线突破+MACD多头+涨停)"""
    c, h, l = df["close"], df["high"], df["low"]
    # 牛线
    t1 = (2.15*c + l + h) / 4
    t2 = (3.48*c + h + l) / 4
    w = np.abs(t2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan)
    w = w.clip(0, 1)
    dma = t1.copy()
    for i in range(1, len(dma)): dma.iloc[i] = w.iloc[i]*t1.iloc[i] + (1-w.iloc[i])*dma.iloc[i-1]
    bull = _ema(dma, 200) * 1.118
    # MACD
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    # XG
    cross_bull = (_ref(c, 1) <= _ref(bull, 1)) & (c > bull)
    macd_ok = dif > dea
    surge = c / _ref(c, 1) > 1.09
    return (cross_bull & macd_ok & surge).astype(int).values

def signal_formula3(df):
    """公式3: 终极选股 (XG AND B1 — 牛线突破+底部反转)"""
    c, h, l = df["close"], df["high"], df["low"]
    # 牛线
    t1 = (2.15*c + l + h) / 4
    t2 = (3.48*c + h + l) / 4
    w = np.abs(t2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan)
    w = w.clip(0, 1)
    dma = t1.copy()
    for i in range(1, len(dma)): dma.iloc[i] = w.iloc[i]*t1.iloc[i] + (1-w.iloc[i])*dma.iloc[i-1]
    bull = _ema(dma, 200) * 1.118
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    # XG
    xg = (_ref(c, 1) <= _ref(bull, 1)) & (c > bull) & (dif > dea) & (c / _ref(c, 1) > 1.09)
    # ATF-based AA line
    tr_arr = pd.concat([h-l, (h-_ref(c,1)).abs(), (l-_ref(c,1)).abs()], axis=1).max(axis=1)
    atr = tr_arr.ewm(span=14, adjust=False).mean()
    aa = _hhv(h, 20) - 2*atr
    # BB: close crosses above 55-day high
    bb = (_ref(c, 1) <= _ref(_hhv(h, 55), 2)) & (c > _ref(_hhv(h, 55), 1))
    # T: close crosses below min(MA13, AA)
    ma13 = c.rolling(13).mean()
    min_line = pd.concat([ma13, aa], axis=1).min(axis=1)
    t_sig = (_ref(c, 1) >= _ref(min_line, 1)) & (c < min_line)
    # B1
    bbb = _barslast(bb); r = _barslast(t_sig)
    b1 = (bbb == 0) & (_ref(r, 1) < _ref(bbb, 1))
    return (xg & b1).astype(int).values

# ═══════════════════ 加载 ═══════════════════
print("=" * 70)
print("  三公式同台 PK — 统一超短出场规则")
print("=" * 70)
print(f"  F1: 擒龙决 AND 涨停先锋 (双共振)")
print(f"  F2: 突破起飞 (牛线+MACD+涨停)")
print(f"  F3: 终极选股 (XG AND B1)")
print(f"  出场: +7%回落1.5%卖 | -3%止损 | 5天时限")
print()

with open(CACHE, "rb") as f: data = pickle.load(f)
import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  股票池: {len(data)} 只")

# ═══════════════════ 逐公式回测 ═══════════════════
all_results = {}

for formula_name, signal_func in [
    ("F1:擒龙决+涨停先锋", signal_formula1),
    ("F2:突破起飞", signal_formula2),
    ("F3:终极选股", signal_formula3),
]:
    print(f"\n  [{formula_name}]", end=" ", flush=True)
    trades = []
    t0 = _time.time()

    for si, (sym, sd) in enumerate(data.items()):
        if si % 600 == 0: print(".", end="", flush=True)

        df = pd.DataFrame({
            "open": sd["open"][-500:], "high": sd["high"][-500:],
            "low": sd["low"][-500:], "close": sd["close"][-500:],
            "volume": sd["volume"][-500:],
        })
        if len(df) < 300: continue

        try:
            sig_arr = signal_func(df)
        except Exception:
            continue

        in_pos = False; entry_p = 0; entry_i = 0; peak = 0

        for i in range(250, len(df)):
            p = df["close"].iloc[i]; hi = df["high"].iloc[i]
            if p <= 3.0: continue

            if CONFIG["filter_lu"] and i >= 1:
                pc = df["close"].iloc[i-1]
                if pc > 0 and p >= round(pc*1.10,2)-0.01:
                    if in_pos:
                        pnl = (p - entry_p)/entry_p - CONFIG["comm"] - CONFIG["tax"]
                        trades.append(dict(pnl=pnl, days=i-entry_i, reason="涨停卖"))
                        in_pos = False
                    continue

            if not in_pos:
                if sig_arr[i]:
                    entry_p = p; entry_i = i; peak = p; in_pos = True
            else:
                if hi > peak: peak = hi
                days = i - entry_i
                pnl = (p - entry_p)/entry_p
                peak_pnl = (peak - entry_p)/entry_p

                reason = None
                if pnl <= CONFIG["hard_stop"]:
                    reason = "硬止损"
                elif days >= CONFIG["time_stop"] and pnl < 0.01:
                    reason = "时间止损"
                elif peak_pnl >= CONFIG["trail_peak"] and (p - peak)/entry_p <= CONFIG["trail_drop"]:
                    reason = "冲高回落"
                elif pnl >= 0.05 and (peak - p)/entry_p >= CONFIG["trail_drop"]:
                    reason = "盈利回落"

                if reason:
                    net = pnl - CONFIG["comm"] - CONFIG["tax"]
                    trades.append(dict(pnl=net, days=days, reason=reason))
                    in_pos = False

    # Stats
    if not trades:
        print(" 0 trades")
        all_results[formula_name] = {"trades": 0, "wr": 0, "aw": 0, "al": 0, "pf": 0, "avg_days": 0}
        continue

    w = [t for t in trades if t["pnl"] > 0]
    l_ = [t for t in trades if t["pnl"] <= 0]
    wr = len(w)/len(trades)
    aw = np.mean([t["pnl"] for t in w]) if w else 0
    al = np.mean([t["pnl"] for t in l_]) if l_ else 0
    pf = abs(sum(t["pnl"] for t in w)/sum(t["pnl"] for t in l_)) if l_ else float("inf")
    avg_days = np.mean([t["days"] for t in trades])

    all_results[formula_name] = dict(trades=len(trades), wr=wr, aw=aw, al=al, pf=pf, avg_days=avg_days,
                                     win_n=len(w), loss_n=len(l_))
    print(f" {len(trades)}trades {_time.time()-t0:.0f}s")

# ═══════════════════ 对比输出 ═══════════════════
print(f"\n{'='*70}")
print(f"  FINAL SHOWDOWN")
print(f"{'='*70}")
print(f"  {'公式':<28} {'交易':>6} {'胜率':>7} {'均盈':>7} {'均亏':>7} {'PF':>6} {'持天':>5}")
print(f"  {'-'*28} {'-'*6} {'-'*7} {'-'*7} {'-'*7} {'-'*6} {'-'*5}")

for name, r in sorted(all_results.items(), key=lambda x: -x[1]["pf"]):
    print(f"  {name:<28} {r['trades']:>6} {r['wr']:>6.1%} {r['aw']:>6.2%} {r['al']:>6.2%} {r['pf']:>6.2f} {r['avg_days']:>5.1f}")

# Best combination: if we combine all signals
best_name = max(all_results, key=lambda k: all_results[k]["pf"])
best = all_results[best_name]
print(f"\n  🏆 Winner: {best_name}")
print(f"     PF={best['pf']:.2f} WR={best['wr']:.1%} "
      f"AvgWin={best['aw']:.2%} AvgLoss={best['al']:.2%}")
print(f"     如果每只20%仓位, 1,000笔交易, 胜率{best['wr']:.0%}:")
print(f"     预期年化: 约 {(best['wr']*best['aw']+(1-best['wr'])*best['al'])*250/best['avg_days']*0.20:.0%}")
print("\n  Done!")
