"""快速回测: 预计算信号 → 一次性喂引擎 (比实时适配器快5倍)"""
import sys, numpy as np, pandas as pd, json
from collections import defaultdict
sys.path.insert(0,r"D:\quant_web"); sys.path.insert(0,r"D:\quant_framework")

print("📂 加载数据...")
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=0)
trading_days = sorted(set(d for df in sd.values() for d in df.index))
start_dt = pd.Timestamp("2024-07-01")
days = [d for d in trading_days if d >= start_dt]
print(f"  {len(days)}个交易日")

# 每20天跑一次ML评分 (非每天, 大幅加速)
from signals.ml.daily import generate
step = 20
signal_store = {}
for i in range(0, len(days), step):
    date = days[i]
    print(f"  [{i+1}/{len(days)}] {date.date()}")
    day_data = {}
    for sym, df in sd.items():
        sliced = df[df.index <= date]
        if len(sliced) >= 60:
            day_data[sym] = sliced
    signals = generate(day_data)
    for s in signals:
        sym = s['symbol']
        signal_store.setdefault(sym, {})[date] = s['score']

# 转 Series
for sym in signal_store:
    signal_store[sym] = pd.Series(signal_store[sym])

print(f"  信号覆盖: {len(signal_store)}只")

# 跑回测
from backtest_engine import BacktestEngine
engine = BacktestEngine(sd, [], {})
result = engine.run(
    strategy="tdx_resonance", signal_field="",
    signal_store=signal_store,
    start="2024-07-01", end="2026-07-15",
    max_positions=3, position_pct=0.2, min_power=0,
    entry_buffer=0.01,
)
m = result['metrics']
print(f"\n📊 Sharpe={m.get('sharpe'):.2f} 胜率={m.get('win_rate',0)*100:.0f}% 盈亏比={m.get('profit_factor'):.2f} 笔数={m.get('n_trades')}")
