"""开盘前全链路自检 v1.0 — 一条命令确认全系统就绪
用法: python morning_checklist.py
建议: 09:20 执行
"""
import sys, os, json, time, urllib.request

sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

PASS, FAIL, WARN = 0, 0, 0
BASE = "http://127.0.0.1:5002"

def check(name, ok, detail=""):
    global PASS, FAIL, WARN
    if ok is True: print(f"  ✅ {name}: {detail}"); PASS += 1
    elif ok is False: print(f"  ❌ {name}: {detail}"); FAIL += 1
    else: print(f"  ⚠️ {name}: {detail}"); WARN += 1

print("=" * 50)
print(f"潜龙开盘前全链路自检 — {time.strftime('%H:%M:%S')}")
print("=" * 50)

# 1. Flask + API
print("\n1. 系统服务")
try:
    r = urllib.request.urlopen(f"{BASE}/api/ping", timeout=5)
    check("Flask服务", r.status == 200)
except: check("Flask服务", False, "未响应")

# 2. QMT连接
try:
    sys.path.insert(0, r"D:\quant_web")
    from qmt_data_bridge import is_qmt_available
    check("QMT连接", is_qmt_available(), "xtquant可用" if is_qmt_available() else "不可用")
except: check("QMT连接", False, "模块缺失")

# 3. 数据
print("\n2. 数据文件")
for name, path in [
    ("信号表", r"D:\quant_web\data\signal_table.json"),
    ("交易计划", r"D:\quant_web\data\auto_trade_plan.json"),
    ("行情数据", r"D:\quant_web\stock_data.parquet"),
]:
    ok = os.path.exists(path)
    size = os.path.getsize(path) if ok else 0
    check(name, ok and size > 100, f"{size/1024:.0f}KB" if ok else "缺失")

# 4. 信号表时效
print("\n3. 信号时效")
try:
    sig = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
    gen_time = sig[0].get("_generated_at", "未知") if sig else "未知"
    today = time.strftime("%m-%d")
    fresh = today in str(gen_time)
    check("信号表新鲜度", fresh, f"生成时间: {gen_time}" if fresh else f"过期: {gen_time} (今天{today})")
except: check("信号表新鲜度", False, "无法读取")

# 5. Plan 标的数
print("\n4. 计划文件")
try:
    plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
    stocks = plan.get("stocks", {})
    limits = plan.get("global_limits", {})
    check("计划标的数", len(stocks) > 10, f"{len(stocks)}只")
    check("总闸", not limits.get("circuit_breaker", True), "正常" if not limits.get("circuit_breaker") else "熔断中!")
except: check("计划文件", False, "无法读取")

# 6. 纸引擎
print("\n5. 模拟盘")
try:
    from paper_engine import paper
    check("纸引擎", paper.cash > 0, f"现金{paper.cash:.0f} 持仓{len(paper.positions)}只")
except: check("纸引擎", False, "不可用")

# 7. 市场状态
print("\n6. 市场状态")
try:
    r = urllib.request.urlopen(f"{BASE}/api/market-regime", timeout=10)
    d = json.loads(r.read())
    check("市场状态API", d.get("code") == 200, f"{d.get('label')} scale={d.get('position_scale')}")
except: check("市场状态API", False, "超时")

# 总结
print("\n" + "=" * 50)
print(f"结果: {PASS}✅ {FAIL}❌ {WARN}⚠️")
if FAIL == 0:
    print("全链路就绪，可以开盘交易 ✅")
else:
    print(f"{FAIL}项失败，先修复再交易 ❌")
print("=" * 50)
