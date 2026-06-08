"""策略对比——MACD vs 通达信选股公式"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd
import numpy as np
from optimizer import grid_search, run_backtest, STRATEGIES

# 测试股票
TEST_STOCKS = ["600000", "600036", "601318", "000858", "002415",
               "600519", "300750", "000001", "600887", "601166"]

print("=" * 80)
print("  策略对比回测 — MACD vs 通达信选股公式")
print("=" * 80)

# 汇总
all_results = []

for sym in TEST_STOCKS:
    try:
        df = pd.read_csv(f"data/market/{sym}/1d.csv", index_col=0, parse_dates=True)
    except Exception:
        continue

    if len(df) < 200:
        continue

    row = {"股票": sym, "数据": len(df)}
    best_strat = None
    best_sharpe = -999

    for strat_name in ["macd_cross", "bull_line_breakout", "dragon_tiger", "ma_condition", "grid_trading"]:
        try:
            results = grid_search(df, strat_name)
            results.sort(key=lambda r: r.sharpe, reverse=True)
            best = results[0]

            row[strat_name + "_收益"] = f"{best.total_return:+.1f}%"
            row[strat_name + "_夏普"] = f"{best.sharpe:.3f}"
            row[strat_name + "_回撤"] = f"{best.max_drawdown:.1f}%"
            row[strat_name + "_交易"] = best.total_trades

            if best.sharpe > best_sharpe:
                best_sharpe = best.sharpe
                best_strat = strat_name
        except Exception as e:
            row[strat_name + "_收益"] = "N/A"
            row[strat_name + "_夏普"] = "N/A"
            row[strat_name + "_回撤"] = "N/A"
            row[strat_name + "_交易"] = 0

    row["最优策略"] = best_strat
    all_results.append(row)

# 打印对比表
print(f"\n{'股票':<8s} {'MACD收益':>9s} {'MACD夏普':>8s}  {'牛线收益':>9s} {'牛线夏普':>8s}  {'双信号收益':>9s} {'双信号夏普':>8s}  {'最优':<12s}")
print("-" * 85)

for r in all_results:
    print(f"{r['股票']:<8s} {r['macd_cross_收益']:>9s} {r['macd_cross_夏普']:>8s}  "
          f"{r['bull_line_breakout_收益']:>9s} {r['bull_line_breakout_夏普']:>8s}  "
          f"{r['dragon_tiger_收益']:>9s} {r['dragon_tiger_夏普']:>8s}  "
          f"{r['最优策略']:<12s}")

# 统计哪种策略最优
print("\n--- 策略胜出统计 ---")
wins = {}
for r in all_results:
    s = r["最优策略"]
    wins[s] = wins.get(s, 0) + 1
for s, c in sorted(wins.items(), key=lambda x: -x[1]):
    print(f"  {s}: {c}/{len(all_results)} 只股票最优")

# 平均表现
print("\n--- 各策略平均表现 ---")
for strat_name in ["macd_cross", "bull_line_breakout", "dragon_tiger", "ma_condition", "grid_trading"]:
    returns = []
    sharpes = []
    maxdds = []
    trades = []
    for r in all_results:
        try:
            returns.append(float(r[f"{strat_name}_收益"].replace("%", "")))
            sharpes.append(float(r[f"{strat_name}_夏普"]))
            maxdds.append(float(r[f"{strat_name}_回撤"].replace("%", "")))
            trades.append(r[f"{strat_name}_交易"])
        except (ValueError, KeyError):
            continue
    if returns:
        print(f"  {strat_name:<22s}: 收益={np.mean(returns):+.1f}%  夏普={np.mean(sharpes):.3f}  "
              f"回撤={np.mean(maxdds):.1f}%  交易={np.mean(trades):.0f}次")
