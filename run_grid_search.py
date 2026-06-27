"""网格搜索 — 对止损×止盈组合跑真实回测，生成热力图数据。

用法: python run_grid_search.py --stocks 200
输出: grid_search_result.json
"""
import sys, os, json, time, random, itertools
import numpy as np, pandas as pd

sys.path.insert(0, r"d:\quant_framework\src")
sys.path.insert(0, r"d:\quant_framework")

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from backtest_engine import BacktestEngine

# 轻量网格
STOP_LOSSES = [-0.03, -0.05, -0.07, -0.10]
TAKE_PROFITS = [0.05, 0.08, 0.10, 0.15]
STOCKS = 200
START = "2023-01-01"
END = "2025-06-30"

print("=" * 60)
print("  网格搜索 — 止损×止盈热力图")
print(f"  {len(STOP_LOSSES)}×{len(TAKE_PROFITS)}={len(STOP_LOSSES)*len(TAKE_PROFITS)} 组 × {STOCKS}只股票")
print("=" * 60)

# 1. 加载数据
print("\n[1/3] 加载数据...")
provider = THSDayDataProvider()
provider.connect()
all_syms = provider.scan_symbols()
random.seed(42)
valid = [s for s in all_syms if len(provider._read_day_file(s)) >= 500]
if len(valid) > STOCKS:
    valid = random.sample(valid, STOCKS)
print(f"  Pool: {len(valid)} stocks")

stock_data = {}
for sym in valid:
    data = provider._read_day_file(sym)
    if not data: continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
        dt = _date_to_datetime(date_int)
        if dt and o > 0 and c > 0:
            records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    if len(records) < 200: continue
    prefix = "sh" if sym[0] == "6" else "sz"
    stock_data[prefix + sym] = pd.DataFrame(records).set_index("date")
print(f"  Loaded: {len(stock_data)} stocks")

# 2. 预计算信号
print("\n[2/3] 预计算信号...")
try:
    from quant_framework.factors.tdx_signals2 import factor_final_pick
    signal_store = {}
    for i, (key, df) in enumerate(stock_data.items()):
        if i % 100 == 0: print(f"  {i}/{len(stock_data)}...")
        try:
            sig = factor_final_pick(df)
            if isinstance(sig, pd.Series): signal_store[key] = sig
        except Exception: continue
    print(f"  Signals: {len(signal_store)}")
except ImportError:
    signal_store = None

# 3. 网格搜索
print(f"\n[3/3] 网格搜索...")
engine = BacktestEngine(stock_data=stock_data, factor_cache=None, name_map={})
heatmap_z = []
results = []

combos = list(itertools.product(STOP_LOSSES, TAKE_PROFITS))
for i, (sl, tp) in enumerate(combos):
    t0 = time.time()
    print(f"  [{i+1}/{len(combos)}] SL={sl:.2f} TP={tp:.2f} ...", end=" ", flush=True)
    try:
        result = engine.run(
            strategy="tdx_resonance",
            signal_store=signal_store,
            formula_symbols=list(signal_store.keys()) if signal_store else list(stock_data.keys())[:100],
            start=START, end=END,
            max_positions=3, position_pct=0.30,
            stop_loss=sl, take_profit=tp, hold_days=3,
            min_power=40, initial_capital=1_000_000,
        )
        m = result.get("metrics", {})
        sharpe = m.get("sharpe", 0)
        total_ret = m.get("total_return", 0)
        n_trades = m.get("n_trades", 0)
        print(f"Sharpe={sharpe:.2f} Ret={total_ret:.2%} Trades={n_trades}")
        results.append({"stop_loss": sl, "take_profit": tp, "sharpe": sharpe,
                        "total_return": total_ret, "n_trades": n_trades})
    except Exception as e:
        print(f"FAIL: {e}")
        results.append({"stop_loss": sl, "take_profit": tp, "sharpe": 0,
                        "total_return": 0, "n_trades": 0})

# 构建热力图矩阵
heatmap_z = []
for sl in STOP_LOSSES:
    row = []
    for tp in TAKE_PROFITS:
        match = [r for r in results if abs(r["stop_loss"]-sl)<0.001 and abs(r["take_profit"]-tp)<0.001]
        row.append(round(match[0]["sharpe"], 2) if match else 0)
    heatmap_z.append(row)

output = {
    "heatmap": {
        "x": [f"止盈{int(tp*100)}%" for tp in TAKE_PROFITS],
        "y": [f"止损{int(abs(sl)*100)}%" for sl in STOP_LOSSES],
        "z": heatmap_z,
    },
    "results": results,
    "params": {"stop_losses": STOP_LOSSES, "take_profits": TAKE_PROFITS,
               "stocks": len(stock_data), "period": f"{START}~{END}"},
}

out_path = r"d:\quant_framework\grid_search_result.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
print(f"\n保存到: {out_path}")
print("Done!")
