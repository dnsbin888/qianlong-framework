"""测试通达信选股公式回测"""
import sys
sys.path.insert(0, "src")
sys.path.insert(0, "scripts")

import pandas as pd
import numpy as np
from optimizer import grid_search, run_backtest

for sym in ["600000", "600036", "000858", "002415"]:
    df = pd.read_csv(f"data/market/{sym}/1d.csv", index_col=0, parse_dates=True)
    cols = ",".join(df.columns.tolist())
    print(f"\n{sym} ({len(df)} rows, cols={cols})")

    for strat in ["bull_line_breakout", "dragon_tiger", "macd_cross"]:
        try:
            results = grid_search(df, strat)
            results.sort(key=lambda r: r.sharpe, reverse=True)
            best = results[0]
            p = ", ".join(f"{k}={v}" for k, v in best.params.items())
            print(f"  {strat:<22s}: Sharpe={best.sharpe:+.3f}  Return={best.total_return:+.1f}%  "
                  f"MaxDD={best.max_drawdown:.1f}%  Trades={best.total_trades}  [{p}]")
        except Exception as e:
            print(f"  {strat:<22s}: ERROR — {e}")
