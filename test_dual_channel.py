"""QMT 双通道全链路测试 — 审核通道 + 快速通道前置条件
=========================================================
用法: C:\Python311\python.exe D:\quant_framework\test_dual_channel.py
"""
import sys, os, json, time, urllib.request, urllib.error

FLASK = "http://127.0.0.1:5002"
PASS = "✅"; FAIL = "❌"; WARN = "⚠️"

def test(label, ok, detail=""):
    print(f"  {PASS if ok else FAIL} {label}" + (f" — {detail}" if detail else ""))

print("=" * 60)
print("  QMT 双通道全链路测试")
print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ═══════════════════════════════════════════════════════
# 一、数据文件检查
# ═══════════════════════════════════════════════════════
print("\n[1] 数据文件")

files = {
    "auto_trade_plan.json": r"D:\quant_web\data\auto_trade_plan.json",
    "qmt_trade_config.json": r"D:\quant_web\data\qmt_trade_config.json",
    "ml_score_cache.json": r"D:\quant_web\data\ml_score_cache.json",
}
for name, path in files.items():
    ok = os.path.exists(path)
    mtime = ""
    if ok:
        mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(path)))
    test(name, ok, f"更新 {mtime}" if ok else "文件缺失")

# ═══════════════════════════════════════════════════════
# 二、auto_trade_plan 内容检查
# ═══════════════════════════════════════════════════════
print("\n[2] 快速通道 — auto_trade_plan 审批状态")
try:
    with open(files["auto_trade_plan.json"], "r") as f:
        plan = json.load(f)
    stocks = plan.get("stocks", {})
    limits = plan.get("global_limits", {})
    enabled = [s for s, c in stocks.items() if c.get("enabled")]
    test("已批准股票", len(enabled) > 0, f"{len(enabled)}只: {', '.join(enabled[:5])}")
    test("熔断状态", not limits.get("circuit_breaker"), "正常" if not limits.get("circuit_breaker") else "熔断中!")
    test("日交易上限", limits.get("max_daily_trades", 0) > 0, f"{limits.get('max_daily_trades')}笔")
    test("日亏损上限", limits.get("max_daily_loss_pct", 0) > 0, f"{limits.get('max_daily_loss_pct')}%")
except Exception as e:
    test("plan读取", False, str(e))

# ═══════════════════════════════════════════════════════
# 三、ML评分检查
# ═══════════════════════════════════════════════════════
print("\n[3] 快速通道 — ML评分")
try:
    with open(files["qmt_trade_config.json"], "r") as f:
        config = json.load(f)
    for sym in enabled[:3]:
        entry = config.get(sym, {})
        lgbm = entry.get("lgbm", 0)
        xgb = entry.get("xgb", 0)
        cb = entry.get("cb", 0)
        ok = (lgbm >= 60 or xgb >= 60)
        test(f"{sym} ML评分", ok, f"LGBM={lgbm} XGB={xgb} CB={cb}")
except Exception as e:
    test("config读取", False, str(e))

# ═══════════════════════════════════════════════════════
# 四、Flask /api/qmt/signal 审核通道
# ═══════════════════════════════════════════════════════
print("\n[4] 审核通道 — Flask /api/qmt/signal")
try:
    # 模拟 QMT 策略发送一个信号
    data = json.dumps({
        "symbol": "sh600030",
        "signal_type": "盘中突破",
        "price": 28.45
    }).encode()
    req = urllib.request.Request(f"{FLASK}/api/qmt/signal", data=data,
        headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode())
    test("Flask连通", True, f"HTTP {resp.getcode()}")
    test("ML审核结果", result.get("approved", False),
         f"LGBM={result.get('lgbm',0)} XGB={result.get('xgb',0)}"
         f" → {'✅通过' if result.get('approved') else '❌拒绝: '+result.get('reason','')}")
    if result.get("approved"):
        test("仓位计算", result.get("position_pct", 0) > 0,
             f"{result['position_pct']}% {result.get('shares',0)}股")
        test("止损计算", result.get("stop_loss", 0) > 0,
             f"¥{result.get('stop_loss',0)}")
except urllib.error.HTTPError as e:
    test("Flask连通", False, f"HTTP {e.code}")
except urllib.error.URLError as e:
    test("Flask连通", False, f"连接失败 — Flask可能未启动: {e.reason}")
except Exception as e:
    test("审核通道", False, str(e))

# ═══════════════════════════════════════════════════════
# 五、行情桥接
# ═══════════════════════════════════════════════════════
print("\n[5] QMT行情桥接")
qmt_cache = r"D:\quant_framework\quote_cache.json"
ok = os.path.exists(qmt_cache)
if ok:
    age = time.time() - os.path.getmtime(qmt_cache)
    test("缓存文件", True, f"{age:.0f}秒前更新")
    test("新鲜度", age < 5, "实时" if age < 5 else f"过期 {age:.0f}秒")
    try:
        with open(qmt_cache, "r") as f:
            raw = json.load(f)
        count = raw.get("count", 0)
        test("股票数量", count > 100, f"{count}只")
    except:
        test("缓存读取", False)
else:
    test("缓存文件", False, "quote_cache.json 不存在 — QMT桥接未运行")

# ═══════════════════════════════════════════════════════
# 六、Flask 信号中心 (前端展示)
# ═══════════════════════════════════════════════════════
print("\n[6] 前端信号中心")
try:
    resp = urllib.request.urlopen(f"{FLASK}/api/signal-center", timeout=10)
    data = json.loads(resp.read().decode())
    count = data.get("count", len(data.get("signals", [])))
    test("信号中心", count > 0, f"{count}条信号")
except Exception as e:
    test("信号中心", False, str(e)[:60])

# ═══════════════════════════════════════════════════════
# 总结
# ═══════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  测试完成")
print("=" * 60)
print("""
下一步 — 需在 QMT 策略编辑器中验证:
  1. 打开 QMT → 策略编辑器
  2. 找到 qmt_full_strategy.py 并运行
  3. 查看 QMT 日志是否出现 passorder 或 审核 POST
""")
