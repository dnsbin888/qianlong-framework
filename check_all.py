"""全系统连通性检查"""
import json, os, urllib.request, sys

BASE = "http://127.0.0.1:5002"
ok, fail = 0, 0

def check(label, condition):
    global ok, fail
    if condition: print(f"  ✅ {label}"); ok += 1
    else: print(f"  ❌ {label}"); fail += 1

print("=== 数据层 ===")
check("stock_data.parquet", os.path.exists(r"D:\quant_web\stock_data.parquet"))
check("signal_table.json", os.path.exists(r"D:\quant_web\data\signal_table.json"))
check("auto_trade_plan.json", os.path.exists(r"D:\quant_web\data\auto_trade_plan.json"))
check("qmt_trade_config.json", os.path.exists(r"D:\quant_web\data\qmt_trade_config.json"))
check("delisted_stocks.parquet", os.path.exists(r"D:\quant_framework\delisted_stocks.parquet"))
check("stock_status.json", os.path.exists(r"D:\quant_web\stock_status.json"))
check("LGBM模型", os.path.exists(r"D:\quant_framework\lgbm_model_stock.pkl"))
check("XGB模型", os.path.exists(r"D:\quant_framework\xgb_model.json"))
check("Ridge模型", os.path.exists(r"D:\quant_framework\ridge_model.pkl"))

print("\n=== API层 ===")
for url, name in [
    ("/api/signal-table", "信号表API"),
    ("/api/paper-account", "纸交易API"),
    ("/api/market-regime", "市场状态API"),
    ("/api/signal-center", "信号中心API"),
]:
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=5)
        data = json.load(r)
        check(name, True)
    except Exception as e:
        check(name + f" ({e})", False)

print("\n=== 前端页 ===")
for url, name in [
    ("/ml-signals", "ML选股器"),
    ("/terminal", "终端页"),
    ("/", "首页"),
]:
    try:
        r = urllib.request.urlopen(f"{BASE}{url}", timeout=5)
        check(f"{name} (HTTP {r.status})", r.status == 200)
    except Exception as e:
        check(name + f" ({e})", False)

print("\n=== 信号质量 ===")
try:
    st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
    check(f"信号总数: {len(st)}", len(st) >= 50)
    auto_n = sum(1 for r in st if r.get("auto_enabled"))
    check(f"自动交易: {auto_n}只", auto_n > 0)
    l_n = sum(1 for r in st if r.get('lgbm_score','') != '')
    check(f"LGBM覆盖: {l_n}只", l_n > 0)
    cons = set(r.get('consensus','?') for r in st)
    check(f"来源: {sorted(cons)}", len(cons) >= 4)
except Exception as e:
    check(f"信号检查 ({e})", False)

print(f"\n{'='*30}")
print(f"通过: {ok} | 失败: {fail}")
print(f"{'✅ 全系统正常' if fail == 0 else '❌ 有断点'}")
