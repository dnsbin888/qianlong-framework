"""打板信号回测 — 封板次日溢价比"""
import sys; sys.path.insert(0, r"D:\quant_framework"); sys.path.insert(0, r"D:\quant_web")
from data_loader import load_stock_data_cache
from qmt_strategies.daban_signals import DABAN_SIGNALS
import numpy as np

print("=" * 55)
print("  打板信号回测 — 次日溢价比")
print("=" * 55)

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=250)
print(f"  加载: {len(sd)}只 × 250天")

results = {}
for signal_name, check_fn in DABAN_SIGNALS.items():
    wins, losses, total = 0, 0, 0
    for sym, df in list(sd.items())[:3000]:  # 取样3000只
        if df is None or len(df) < 25:
            continue
        # 逐日扫描: 从第21天到倒数第2天 (留一天看次日)
        c = df["close"].values
        for i in range(20, len(c) - 2):
            sub = df.iloc[:i+1]
            try:
                if check_fn(sub, sym):
                    # 买入当天涨停价, 看次日溢价
                    next_open = float(df.iloc[i+1]["open"]) if i+1 < len(df) else c[i]
                    next_close = float(df.iloc[i+1]["close"]) if i+1 < len(df) else c[i]
                    # 次日溢价: 以次日开盘为买入价, 收盘为卖出价
                    ret = (next_close / next_open - 1) * 100
                    total += 1
                    if ret > 0:
                        wins += 1
                    else:
                        losses += 1
            except Exception:
                pass

    if total > 0:
        wr = wins / total * 100
        avg_win = sum(1 for _ in range(wins)) / max(wins, 1)  # placeholder
        results[signal_name] = {
            "total": total, "wins": wins, "losses": losses,
            "win_rate": round(wr, 1),
        }

print(f"\n  {'信号':16s} {'触发':>6s} {'胜':>5s} {'负':>5s} {'胜率':>6s}")
print("-" * 55)
for name, r in sorted(results.items(), key=lambda x: -x[1]["total"]):
    print(f"  {name:16s} {r['total']:>6d} {r['wins']:>5d} {r['losses']:>5d} {r['win_rate']:>5.1f}%")
print(f"\n  注: 次日溢价=次日收盘/次日开盘-1")
print(f"  样本: 3000只 × 250天, 仅统计有信号的日期")
