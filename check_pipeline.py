"""上下游数据通达验证"""
import json, os

# 1. 信号表
st_path = r"D:\quant_web\data\signal_table.json"
st = json.load(open(st_path, encoding="utf-8")) if os.path.exists(st_path) else []
print(f"[1] signal_table.json: {len(st)} rows {'✅' if len(st)>0 else '❌'}")

# 2. 自动交易计划
ap_path = r"D:\quant_web\data\auto_trade_plan.json"
ap = json.load(open(ap_path, encoding="utf-8")) if os.path.exists(ap_path) else {}
stocks = ap.get("stocks", {})
auto_n = sum(1 for v in stocks.values() if v.get("enabled"))
print(f"[2] auto_trade_plan.json: {len(stocks)} stocks, {auto_n} auto-enabled {'✅' if auto_n>0 else '⚠️ 无自动'}")

# 3. QMT配置
qc_path = r"D:\quant_web\data\qmt_trade_config.json"
qc = json.load(open(qc_path, encoding="utf-8")) if os.path.exists(qc_path) else {}
print(f"[3] qmt_trade_config.json: regime={qc.get('market_regime','?')}, sentiment={qc.get('sentiment_stage','?')} {'✅' if qc else '❌'}")

# 4. 退市股
import pandas as pd
dp_path = r"D:\quant_framework\delisted_stocks.parquet"
dp = pd.read_parquet(dp_path) if os.path.exists(dp_path) else None
if dp is not None:
    print(f"[4] delisted_stocks.parquet: {dp.symbol.nunique()} stocks, {len(dp)} rows ✅")

# 5. 状态
ss_path = r"D:\quant_web\stock_status.json"
ss = json.load(open(ss_path, encoding="utf-8")) if os.path.exists(ss_path) else {}
print(f"[5] stock_status.json: {len(ss)} stocks {'✅' if len(ss)>0 else '❌'}")

# 6. 模型文件
models = {
    "LGBM": r"D:\quant_framework\lgbm_model_stock.pkl",
    "XGBoost": r"D:\quant_framework\xgb_model.json",
    "Ridge": r"D:\quant_framework\ridge_model.pkl",
}
print("\n[6] 模型文件:")
for name, path in models.items():
    ok = os.path.exists(path)
    size = os.path.getsize(path) / 1024 if ok else 0
    print(f"  {name:10s}: {'✅' if ok else '❌'} {size:.0f}KB")

# 7. 流水线检查
print("\n[7] 数据流检查:")
checks = [
    ("signal_table → 有数据", len(st) > 0),
    ("auto_trade_plan → 有自动", auto_n > 0),
    ("qmt_trade_config → 已生成", bool(qc)),
    ("delisted_stocks → 已合并", dp is not None),
    ("退市股数 > 100", dp is not None and dp.symbol.nunique() > 100),
    ("三模型文件 → 全在", all(os.path.exists(p) for p in models.values())),
]
all_ok = True
for label, ok in checks:
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok: all_ok = False

print(f"\n{'✅ 全链路通达' if all_ok else '❌ 有断点'}")

# 8. Top 5 信号样例
if st:
    print("\n--- Top 5 信号 ---")
    for r in sorted(st, key=lambda x: -x.get('combined_score', 0))[:5]:
        print(f"  {r['symbol']:12s} {r.get('name','?'):8s} "
              f"combined={r.get('combined_score',0):.0f} "
              f"n={r.get('n_models',0)} auto={r.get('auto_enabled',False)}")
