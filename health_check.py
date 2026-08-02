"""潜龙全系统健康检测 v1.0"""
import sys, os, json, time
sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")

OK, WARN, ERR, SKIP = "✅", "⚠️", "❌", "⬜"
results = []

def check(name, ok, detail=""):
    icon = OK if ok else ERR
    print(f"  {icon} {name}: {detail}")
    results.append((name, ok, detail))

print("=" * 50)
print("  潜龙全系统健康检测")
print("=" * 50)

# 1. 文件完整性
print("\n📁 文件完整性")
for f, desc in [
    (r"D:\quant_web\stock_data.parquet", "日线数据"),
    (r"D:\quant_framework\paper_account.json", "模拟盘"),
    (r"D:\quant_framework\trade_config_master.json", "参数配置"),
    (r"D:\quant_framework\strategy_registry.json", "策略注册表"),
    (r"D:\quant_web\data\signal_table.json", "信号表"),
    (r"D:\quant_web\data\auto_trade_plan.json", "QMT配置"),
    (r"D:\quant_framework\live_trader_config.json", "实盘配置"),
    (r"D:\quant_framework\dingtalk_alerts.py", "钉钉告警"),
]:
    ok = os.path.exists(f)
    size = os.path.getsize(f) if ok else 0
    check(desc, ok, f"{size/1024:.0f}KB" if ok else "缺失")

# 2. 数据质量
print("\n📊 数据质量")
try:
    import pandas as pd
    df = pd.read_parquet(r"D:\quant_web\stock_data.parquet")
    d_min, d_max = df['date'].min(), df['date'].max()
    n_stocks = df['symbol'].nunique()
    n_rows = len(df)
    check("日期范围", n_rows > 1_000_000, f"{d_min} ~ {d_max}")
    check("股票数", n_stocks >= 5000, f"{n_stocks}只")
    check("总行数", n_rows >= 5_000_000, f"{n_rows/1e6:.1f}M行")
    check("数据新鲜度", str(d_max) >= "2026-07-10", f"最新{d_max}")
except Exception as e:
    check("数据加载", False, str(e))

# 3. 模拟盘
print("\n💰 模拟盘")
try:
    pp = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
    cash = pp.get("cash", 0)
    n_pos = len(pp.get("positions", {}))
    n_trades = len(pp.get("trade_log", []))
    check("现金", cash > 1000, f"{cash:,.0f}")
    check("持仓", n_pos >= 0, f"{n_pos}只")
    check("交易记录", n_trades > 0, f"{n_trades}笔")
except Exception as e:
    check("模拟盘", False, str(e))

# 4. 策略状态
print("\n🎯 策略状态")
try:
    reg = json.load(open(r"D:\quant_framework\strategy_registry.json", encoding="utf-8"))
    for s in reg.get("strategies", []):
        v = s.get("validation", {})
        lc = s.get("lifecycle", "?")
        nt = v.get("n_trades", 0)
        sh = v.get("sharpe", 0)
        check(s['name'], lc in ('live','backtested'), f"{lc} {nt}笔 Sharpe={sh:.1f}")
except Exception as e:
    check("策略注册表", False, str(e))

# 5. 关键模块导入
print("\n🔧 模块导入")
for mod, desc in [
    ("dingtalk_alerts", "钉钉告警"),
    ("reversal_strategy", "反转策略"),
    ("daban_quality", "打板策略"),
    ("signals.ml.daily", "ML策略"),
    ("backtest_engine", "回测引擎"),
    ("ruler_trade", "尺子"),
    ("strategy_metrics", "分层指标"),
]:
    try:
        __import__(mod)
        check(desc, True)
    except Exception as e:
        check(desc, False, str(e)[:60])

# 6. QMT连接
print("\n📡 QMT")
try:
    from xtquant import xtdata
    tick = xtdata.get_full_tick(['000001.SH'])
    check("xtdata", tick is not None and len(tick) > 0)
except Exception as e:
    check("xtdata", False, str(e)[:60])

# 7. Flask API
print("\n🌐 Flask API")
import requests, time as _t
api_tests = [
    ("/api/market-regime", "市场状态"),
    ("/api/signal-table", "信号表"),
    ("/api/strategy-performance", "策略表现"),
]
for path, desc in api_tests:
    try:
        r = requests.get(f"http://localhost:5002{path}", timeout=5, proxies={"http":None,"https":None})
        check(desc, r.status_code == 200, f"HTTP {r.status_code}")
    except Exception as e:
        check(desc, False, str(e)[:60])

# 汇总
print(f"\n{'='*50}")
n_ok = sum(1 for _, ok, _ in results if ok)
n_total = len(results)
print(f"  总计: {n_ok}/{n_total} 通过")
if n_ok == n_total:
    print("  🎉 系统健康")
else:
    failed = [(n, d) for n, ok, d in results if not ok]
    for n, d in failed:
        print(f"  ❌ {n}: {d}")
print(f"{'='*50}")
