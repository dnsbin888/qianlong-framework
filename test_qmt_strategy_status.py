"""QMT 策略状态诊断 — 从 Flask 端反推 QMT 策略是否在运行
=================================================================
用法: C:\Python311\python.exe D:\quant_framework\test_qmt_strategy_status.py
"""
import os, json, time, urllib.request, glob as _glob

PASS = "✅"; FAIL = "❌"; WARN = "⚠️"

print("=" * 60)
print("  QMT 策略运行状态诊断")
print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# 1. Flask /api/qmt/signal 是否收到过信号
print("\n[1] 审核通道 — 是否收到过 QMT 信号?")
log_paths = [
    r"D:\quant_web\app_run.log",
    r"D:\quant_web\stdout.txt",
    r"D:\quant_web\stderr.txt",
]
found = False
for lp in log_paths:
    if not os.path.exists(lp): continue
    try:
        with open(lp, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "qmt" in line.lower() or "QMT" in line or "passorder" in line:
                    if not found:
                        print(f"  {PASS} 找到 QMT 相关日志 ({os.path.basename(lp)}):")
                        found = True
                    print(f"    {line.strip()[:120]}")
    except:
        pass
if not found:
    print(f"  {FAIL} 所有日志文件均无 QMT 记录")
    print(f"         → QMT 策略从未向 Flask 发送过信号")

# 2. 检查 auto_trade_plan 是否被 QMT 读取过
print("\n[2] 快速通道 — auto_trade_plan 是否被 QMT 消费?")
plan_path = r"D:\quant_web\data\auto_trade_plan.json"
if os.path.exists(plan_path):
    plan_atime = os.path.getatime(plan_path)  # 最后访问时间 (QMT读取会更新)
    plan_mtime = os.path.getmtime(plan_path)  # 最后修改时间 (潜龙写入)
    plan_ctime = os.path.getctime(plan_path)  # 创建时间
    print(f"  文件修改: {time.strftime('%H:%M:%S', time.localtime(plan_mtime))} (潜龙写入)")
    print(f"  文件访问: {time.strftime('%H:%M:%S', time.localtime(plan_atime))} (QMT读取)")
    if plan_atime > plan_mtime:
        print(f"  {PASS} QMT 在潜龙写入后读取过此文件")
    else:
        print(f"  {WARN} 访问时间不晚于修改时间 — QMT 可能未读取此文件")
        print(f"        Windows 可能禁用了访问时间跟踪")

# 3. QMT 策略文件本身
print("\n[3] QMT 策略文件")
qmt_strategy_paths = [
    r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py",
    r"D:\quant_framework\qmt_strategies\qmt_engine.py",
    r"D:\quant_framework\qmt_strategies\qmt_quick_trade.py",
]
for p in qmt_strategy_paths:
    ok = os.path.exists(p)
    print(f"  {PASS if ok else FAIL} {os.path.basename(p)}")

# 4. 模拟 QMT 策略的信号发送 (端到端)
print("\n[4] 模拟 QMT 策略发送信号 (端到端)")
try:
    data = json.dumps({
        "symbol": "sh600030",
        "signal_type": "盘中突破",
        "price": 28.45
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:5002/api/qmt/signal", data=data,
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode())
    print(f"  {PASS} 审核通道响应: HTTP 200")
    print(f"  ML: LGBM={result.get('lgbm')} XGB={result.get('xgb')}")
    print(f"  审核: {'✅通过' if result.get('approved') else '❌拒绝: '+result.get('reason','')}")
except Exception as e:
    print(f"  {FAIL} {e}")

# 5. 模拟 QMT passorder 的前置条件检查
print("\n[5] 快速通道 — passorder 前置条件")
with open(plan_path, "r") as f:
    plan = json.load(f)
stocks = plan.get("stocks", {})
limits = plan.get("global_limits", {})

print(f"  熔断: {'❌已触发!!' if limits.get('circuit_breaker') else '✅正常'}")
print(f"  批准股票: {len([s for s,c in stocks.items() if c.get('enabled')])}只")

# 模拟 qmt_engine._fast_execute 的条件检查
for sym in ["sh600030", "sh600396", "sh600418"]:
    stock = stocks.get(sym, {})
    checks = []
    checks.append(("enabled", stock.get("enabled", False)))
    checks.append(("非熔断", not limits.get("circuit_breaker")))
    # ML check
    cfg_path = r"D:\quant_web\data\qmt_trade_config.json"
    ml = {}
    if os.path.exists(cfg_path):
        with open(cfg_path) as f: ml = json.load(f).get(sym, {})
    best = max(ml.get("lgbm",0), ml.get("xgb",0), ml.get("cb",0))
    min_ml = stock.get("min_ml_score", 60)
    checks.append((f"ML {best:.0f}>={min_ml}", best >= min_ml))

    all_ok = all(c[1] for c in checks)
    status = PASS if all_ok else FAIL
    detail = " | ".join(f"{'✅' if c[1] else '❌'}{c[0]}" for c in checks)
    print(f"  {status} {sym}: {detail}")

print("\n" + "=" * 60)
print("  诊断完成")
print("=" * 60)
print("""
如果 [1] 和 [4] 都是 ❌:
  → QMT 策略编辑器里没有运行 qmt_full_strategy.py
  → 或者 QMT 没有连接网络

如果 [4] ✅ 但 [1] ❌:
  → Flask 能响应，但 QMT 策略从未发送过信号
  → QMT 策略没启动或没产生交易信号

需要在 QMT 桌面端确认:
  1. 策略编辑器 → 看是否加载了 qmt_full_strategy.py
  2. 点"启动策略"按钮
  3. 看下方日志是否显示 [QMT-Engine] 或 passorder
""")
