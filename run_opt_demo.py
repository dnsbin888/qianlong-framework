"""快速验证优化引擎"""
import sys
sys.path.insert(0, "src")
import pandas as pd
from scripts.optimizer import grid_search, run_backtest

df = pd.read_csv("data/market/600000/1d.csv", index_col=0, parse_dates=True)
print(f"Data: {len(df)} rows")

results = grid_search(df, "macd_cross")
results.sort(key=lambda r: r.sharpe, reverse=True)

print("\nTop 10 MACD Parameters (by Sharpe):")
print(f"{'Rank':<6} {'fast slow signal':<30} {'Sharpe':>8} {'Return%':>10} {'MaxDD%':>8} {'Trades':>7}")
print("-" * 75)
for i, r in enumerate(results[:10]):
    p = f"fast={r.params['fast']} slow={r.params['slow']} signal={r.params['signal']}"
    print(f"{i+1:<6} {p:<30} {r.sharpe:>8.3f} {r.total_return:>+9.2f}% {r.max_drawdown:>7.2f}% {r.total_trades:>7}")

default = run_backtest(df, "macd_cross", {"fast": 12, "slow": 26, "signal": 9})
best = results[0]
print(f"\nDefault (12,26,9):  Sharpe={default.sharpe:.3f}  Return={default.total_return:+.2f}%  MaxDD={default.max_drawdown:.2f}%")
print(f"Best   {best.params}: Sharpe={best.sharpe:.3f}  Return={best.total_return:+.2f}%  MaxDD={best.max_drawdown:.2f}%")
print(f"Sharpe improvement: {best.sharpe - default.sharpe:+.3f}")
