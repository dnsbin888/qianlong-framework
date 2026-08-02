"""弱转强回测: 不同参数组合对比"""
import sys, os, json, numpy as np, pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

from data_loader import load_stock_data_cache
from ruler_trade import measure, compare_table

print("=" * 60)
print("  弱转强 A1 策略优化回测")
print("=" * 60)

# 加载数据
print("\n[1] 加载数据...")
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=0)
sd = {k: v for k, v in sd.items() if not k.startswith(('sh000','sz399','bj','sh88','sz88','sh51','sz15'))}
print(f"  股票池: {len(sd)}只")

# 生成弱转强信号
print("[2] 生成弱转强日线信号...")
from reversal_strategy import generate_weak_to_strong

dates = set()
for df in sd.values():
    for d in df.index[-500:]:  # 最近500天
        dates.add(str(d)[:10])
dates = sorted(dates)[-750:]  # 最近3年 (~250天/年)
print(f"  交易日: {len(dates)}天")

# 为每个日期生成当日信号
signal_store = {}  # {symbol: pd.Series(date→signal)}
print("[3] 逐日生成信号 (慢, 约1-2分钟)...")

for i, date_str in enumerate(dates):
    # 取当天之前的数据构建 snapshot
    snapshot = {}
    for sym, df in sd.items():
        try:
            mask = df.index <= pd.Timestamp(date_str)
            sliced = df[mask]
            if len(sliced) >= 21:
                snapshot[sym] = sliced
        except:
            pass

    if len(snapshot) < 100:
        continue

    signals = generate_weak_to_strong(snapshot)
    for s in signals:
        sym = s['symbol']
        if sym not in signal_store:
            signal_store[sym] = pd.Series(dtype=float, index=pd.DatetimeIndex([]))
        signal_store[sym].loc[pd.Timestamp(date_str)] = s['score']

    if (i+1) % 100 == 0:
        n_signals = sum(len(v) for v in signal_store.values())
        print(f"  {i+1}/{len(dates)}... 累计{n_signals}条信号")

n_signals = sum(len(v) for v in signal_store.values())
print(f"  总信号: {n_signals}条")

if n_signals < 20:
    print("❌ 信号不足, 无法回测")
    sys.exit(1)

# 加载名称
name_map = {}
names_path = r"D:\quant_web\stock_names_full.csv"
if os.path.exists(names_path):
    with open(names_path, "r", encoding="utf-8") as f:
        for line in f:
            p = line.strip().split(",")
            if len(p) >= 2:
                name_map[p[0]] = p[1]

print(f"\n[4] 运行回测对比...")
kwargs = dict(
    stock_data=sd, name_map=name_map, signal_store=signal_store,
    start="2023-07-19", end="2026-07-19",
    max_positions=3, position_pct=0.3,
    initial_capital=1_000_000,
    formula_symbols=list(signal_store.keys()),
)

# 对比组
configs = [
    ("基线 hold=2 无ATR",  2, None, 2.0),
    ("hold=2 + ATRx2",     2, None, 2.0),
    ("hold=2 + ATRx1.5",   2, None, 1.5),
    ("hold=2 + ATRx2.5",   2, None, 2.5),
    ("hold=3 + ATRx2",     3, None, 2.0),
    ("hold=5 + ATRx2",     5, None, 2.0),
    ("hold=2 + ATRx2 + TP5%", 2, 0.05, 2.0),
]

reports = []
for label, hold, tp, atr in configs:
    print(f"  {label}...")
    r = measure(
        strategy="custom", signal_field="score",
        hold_days=hold, take_profit=tp, atr_multiplier=atr,
        **kwargs,
    )
    r["strategy"] = label
    reports.append(r)

print(f"\n" + compare_table(reports))

# 最优
best = max(reports, key=lambda r: r["sharpe"])
print(f"\n✅ 最优: {best['strategy']}")
print(f"   胜率={best['win_rate_pct']:.1f}% 盈亏比={best['profit_factor']:.2f} Sharpe={best['sharpe']:.2f} 回撤={best['max_drawdown_pct']:.1f}%")
