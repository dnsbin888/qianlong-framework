"""
超短线突破策略回测 — 匹配用户交易风格:
  入场: 擒龙决 / 涨停先锋 / 起爆点 (突破关键位)
  出场: 冲高+7%后回落1.5%→卖 | 硬止损-3% | 时间止损5天
  仓位: 固定20%资金
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, numpy as np, pandas as pd
from collections import defaultdict
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
N_SAMPLE = 3000  # 全量

# ═══════════════════ 策略配置 ═══════════════════
CONFIG = {
    "initial_cash": 1_000_000,
    "max_positions": 5,
    "position_pct": 0.20,       # 每只20%资金
    "hard_stop": -0.03,         # -3%硬止损(超短)
    "time_stop": 5,             # 5天不涨就出
    "trail_peak": 0.07,         # +7%启动跟踪
    "trail_drop": -0.015,       # 从最高点回落1.5%→卖
    "commission": 0.0003,
    "stamp_tax": 0.001,
    "filter_limit_up": True,
}

# ═══════════════════ 辅助函数 ═══════════════════
def _ref(s, n): return s.shift(n)
def _hhv(s, n): return s.rolling(n, min_periods=1).max()
def _llv(s, n): return s.rolling(n, min_periods=1).min()
def _ema(s, n): return s.ewm(span=n, adjust=False).mean()
def _count(c, n): return c.astype(int).rolling(n, min_periods=1).sum()
def _exist(c, n): return c.rolling(n, min_periods=1).max().fillna(0).astype(bool)
def _every(c, n): return c.rolling(n, min_periods=1).min().fillna(0).astype(bool)

def signal_qlj(df):
    """擒龙决: 放量突破压力线+布林上轨, 量比>1.8, 7日内首次"""
    c, v = df["close"], df["volume"]
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    ema20 = _ema(c, 20)
    dev = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper = _ref(ema20 + 2*dev, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    cond = (c > pressure) & (c > upper) & (vol_ratio > 1.8)
    return (cond & (_count(cond, 7) == 1)).astype(int).values

def signal_ztxf(df):
    """涨停先锋: 突破获利盘99%成本线, 7日首次"""
    c = df["close"]
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    cond = c > profit100
    return (cond & (_count(cond, 7) == 1)).astype(int).values

def signal_qbd(df):
    """起爆点: 涨停+放量+非连板"""
    c, o, v = df["close"], df["open"], df["volume"]
    limit_up = (c / _ref(c, 1)) > 1.097
    vol_break = v >= 2 * v.rolling(100).mean()
    no_5_in_90 = ~_exist(_every(limit_up, 5), 90)
    above_ema10 = c > _ema(c, 10)
    no_2_in_7 = ~_exist(_every(limit_up, 2), 7)
    had_vol = _exist(vol_break, 15)
    return (limit_up & had_vol & above_ema10 & no_5_in_90 & no_2_in_7).astype(int).values

# ═══════════════════ 加载数据 ═══════════════════
print("=" * 65)
print("  超短线双信号共振 — 擒龙决 AND 涨停先锋")
print("=" * 65)
print(f"  入场: 双信号共振(打板+分歧低吸) | 出场: +7%回落1.5%卖 | 止损-3% | 限时5天")
print()

with open(CACHE, "rb") as f:
    data = pickle.load(f)

import random; random.seed(42)
keys = random.sample(list(data.keys()), min(N_SAMPLE, len(data)))
data = {k: data[k] for k in keys}
print(f"  股票池: {len(data)} 只")

# ═══════════════════ 逐只回测 ═══════════════════
all_trades = []
for si, (sym, sd) in enumerate(data.items()):
    if si % 500 == 0:
        print(f"  {si}/{len(data)}...")

    # Build DataFrame
    dates = sd["dates"]
    df = pd.DataFrame({
        "open": sd["open"][-500:], "high": sd["high"][-500:],
        "low": sd["low"][-500:], "close": sd["close"][-500:],
        "volume": sd["volume"][-500:],
    })

    if len(df) < 300:
        continue

    # Compute signals
    try:
        sig1 = signal_qlj(df)
        sig2 = signal_ztxf(df)
        sig3 = signal_qbd(df)
    except Exception:
        continue

    # Combined: 双信号共振 — 擒龙决 AND 涨停先锋 同时触发
    signals = ((sig1 > 0) & (sig2 > 0)).astype(int)

    # Track trades for this stock
    in_position = False
    entry_price = 0
    entry_idx = 0
    peak_since_entry = 0

    for i in range(250, len(df)):
        price = df["close"].iloc[i]
        high_today = df["high"].iloc[i]
        if price <= 3.0:
            continue

        # Limit-up filter
        if CONFIG["filter_limit_up"] and i >= 1:
            prev_c = df["close"].iloc[i-1]
            if prev_c > 0 and price >= round(prev_c*1.10, 2) - 0.01:
                if in_position:  # 涨停了, 可以卖!
                    pnl = (price - entry_price) / entry_price
                    all_trades.append(dict(sym=sym, entry=entry_price, exit=price,
                                           pnl=pnl, days=i-entry_idx, reason="涨停卖出"))
                    in_position = False
                continue

        if not in_position:
            if signals[i]:
                entry_price = price
                entry_idx = i
                peak_since_entry = price
                in_position = True
        else:
            # Update peak
            if high_today > peak_since_entry:
                peak_since_entry = high_today

            days_held = i - entry_idx
            pnl = (price - entry_price) / entry_price
            peak_pnl = (peak_since_entry - entry_price) / entry_price

            exit_reason = None

            # 出场规则1: 硬止损-3%
            if pnl <= CONFIG["hard_stop"]:
                exit_reason = f"硬止损{pnl:.1%}"

            # 出场规则2: 时间止损5天
            elif days_held >= CONFIG["time_stop"] and pnl < 0.01:
                exit_reason = f"时间{days_held}天"

            # 出场规则3: +7%后回落1.5% → 卖! (用户核心规则)
            elif peak_pnl >= CONFIG["trail_peak"] and (price - peak_since_entry) / entry_price <= CONFIG["trail_drop"]:
                exit_reason = f"冲高回落 峰{peak_pnl:.1%}→{pnl:.1%}"

            # 出场规则4: 直接赚了5%以上且回落 (收紧版)
            elif pnl >= 0.05 and (peak_since_entry - price) / entry_price >= CONFIG["trail_drop"]:
                exit_reason = f"盈利回落 峰{peak_pnl:.1%}→{pnl:.1%}"

            if exit_reason and in_position:
                # 扣手续费
                net_pnl = pnl - CONFIG["commission"] - CONFIG["stamp_tax"]
                all_trades.append(dict(sym=sym, entry=entry_price, exit=price,
                                       pnl=net_pnl, days=days_held, reason=exit_reason,
                                       peak_pnl=peak_pnl))
                in_position = False

# ═══════════════════ 结果 ═══════════════════
if not all_trades:
    print("  No trades! Signals too sparse.")
    import sys; sys.exit(1)

trades = all_trades
wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]

wr = len(wins) / len(trades)
aw = np.mean([t["pnl"] for t in wins]) if wins else 0
al = np.mean([t["pnl"] for t in losses]) if losses else 0
pf = abs(sum(t["pnl"] for t in wins) / sum(t["pnl"] for t in losses)) if losses else float("inf")
avg_days = np.mean([t["days"] for t in trades])
total_pnl = sum(t["pnl"] for t in trades)

# By reason
reasons = defaultdict(lambda: [0, 0.0])
for t in trades:
    r = t["reason"].split("峰")[0].split("硬")[0].split("时间")[0].strip()[:4]
    reasons[r][0] += 1; reasons[r][1] += t["pnl"]

# Daily P&L distribution
pnls = [t["pnl"] for t in trades]

print(f"\n{'='*65}")
print(f"  BACKTEST RESULTS ({len(data)} stocks)")
print(f"{'='*65}")
print(f"  Total Trades:    {len(trades):>8}")
print(f"  Win Rate:        {wr:>8.1%}  ({len(wins)}W/{len(losses)}L)")
print(f"  Avg Win:         {aw:>8.2%}")
print(f"  Avg Loss:        {al:>8.2%}")
print(f"  Profit Factor:   {pf:>8.2f}")
print(f"  Total P&L:       {total_pnl:>+8.2f} (per-trade avg)")
print(f"  Avg Hold Days:   {avg_days:>8.1f}")
print()

# Max consecutive wins/losses
consec = 0; max_w = 0; max_l = 0; last_win = None
for t in trades:
    is_w = t["pnl"] > 0
    if is_w == last_win: consec += 1
    else: consec = 1
    if is_w: max_w = max(max_w, consec)
    else: max_l = max(max_l, consec)
    last_win = is_w
print(f"  Max Consec Wins:  {max_w}")
print(f"  Max Consec Losses:{max_l}")

# P&L distribution
print(f"\n  P&L Distribution:")
bins = [-0.10, -0.05, -0.03, -0.01, 0, 0.01, 0.03, 0.05, 0.07, 0.10, 0.20, 1.0]
for i in range(len(bins)-1):
    count = sum(1 for p in pnls if bins[i] <= p < bins[i+1])
    bar = "█" * max(1, count * 50 // max(len(trades)//5, 1))
    print(f"  {bins[i]:>+6.1%} ~ {bins[i+1]:>+6.1%}: {count:>5} {bar}")

# Exit reason breakdown
print(f"\n  Exit Reasons:")
for r, (c, pnl) in sorted(reasons.items(), key=lambda x: -x[1][0]):
    print(f"    {r:<15}: {c:>5} trades, P&L={pnl:>+8.2f}")

# Best/worst
sorted_t = sorted(trades, key=lambda t: t["pnl"])
print(f"\n  Worst 5: {[(t['sym'],f"{t['pnl']:.1%}") for t in sorted_t[:5]]}")
print(f"  Best 5:  {[(t['sym'],f"{t['pnl']:.1%}") for t in sorted_t[-5:]]}")
print(f"\n  Done!")
