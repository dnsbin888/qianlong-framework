"""潜龙全链路端到端测试 v1.0
================================
测试: 总闸→计划→ML→信号→审核通道→风控→仓位
"""
import json, os, time, urllib.request

FLASK = "http://127.0.0.1:5002"
PASS = "✅"; FAIL = "❌"; WARN = "⚠️"

def test(label, ok, detail=""):
    print(f"  {PASS if ok else FAIL} {label}" + (f" — {detail}" if detail else ""))

print("=" * 60)
print("  潜龙全链路端到端测试")
print(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# ═══════════════════════════════════════════════════════
# 1. 总闸状态
# ═══════════════════════════════════════════════════════
print("\n[1] 总闸状态")
plan_path = r"D:\quant_web\data\auto_trade_plan.json"
with open(plan_path, "r") as f:
    plan = json.load(f)
limits = plan.get("global_limits", {})
test("qmt_fast_enabled", limits.get("qmt_fast_enabled", False),
     "开启" if limits.get("qmt_fast_enabled") else "关闭")
test("circuit_breaker", not limits.get("circuit_breaker"),
     "熔断!" if limits.get("circuit_breaker") else "正常")
test("日交易上限", limits.get("max_daily_trades", 0) > 0,
     f"{limits.get('max_daily_trades')}笔")
test("日亏损上限", limits.get("max_daily_loss_pct", 0) > 0,
     f"{limits.get('max_daily_loss_pct')}%")

# ═══════════════════════════════════════════════════════
# 2. 审批池
# ═══════════════════════════════════════════════════════
print("\n[2] 审批池")
stocks = plan.get("stocks", {})
enabled = [s for s, c in stocks.items() if c.get("enabled") in (True, "True")]
test("已批准股票", len(enabled) > 0, f"{len(enabled)}只")
for sym in enabled[:5]:
    stock = stocks[sym]
    sigs = stock.get("signal_types", [])
    test(f"  {sym} 信号白名单", len(sigs) > 0, f"{len(sigs)}个: {', '.join(sigs[:3])}")

# ═══════════════════════════════════════════════════════
# 3. ML评分
# ═══════════════════════════════════════════════════════
print("\n[3] ML评分 (qmt_trade_config.json)")
cfg_path = r"D:\quant_web\data\qmt_trade_config.json"
with open(cfg_path, "r") as f:
    ml_cfg = json.load(f)
qualified = []
for sym in enabled[:10]:
    ml = ml_cfg.get(sym, {})
    lgbm = ml.get("lgbm", 0) or 0
    xgb = ml.get("xgb", 0) or 0
    cb = ml.get("cb", 0) or 0
    best = max(lgbm, xgb, cb)
    min_ml = stocks[sym].get("min_ml_score", 60)
    ok = best >= min_ml
    if ok: qualified.append(sym)
    test(f"  {sym} ML={best:.0f}>={min_ml}", ok,
         f"LGBM={lgbm:.0f} XGB={xgb:.0f} CB={cb:.0f}")
test("ML达标股票", len(qualified) > 0, f"{len(qualified)}只可通过快速通道")

# ═══════════════════════════════════════════════════════
# 4. 审核通道 — 模拟 QMT 信号
# ═══════════════════════════════════════════════════════
print("\n[4] 审核通道 — 模拟QMT发信号")
if enabled:
    test_sym = enabled[0]
    test_price = stocks[test_sym].get("close", 10)
    try:
        data = json.dumps({
            "symbol": test_sym,
            "signal_type": "盘中突破",
            "price": test_price
        }).encode()
        req = urllib.request.Request(f"{FLASK}/api/qmt/signal", data=data,
            headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
        test("Flask响应", resp.getcode() == 200, f"HTTP {resp.getcode()}")
        approved = result.get("approved", False)
        test(f"ML审核 {test_sym}", approved,
             f"LGBM={result.get('lgbm',0)} XGB={result.get('xgb',0)} → {'通过' if approved else '拒绝: '+result.get('reason','')}")
        if approved:
            test("仓位计算", result.get("position_pct", 0) > 0,
                 f"{result['position_pct']}% {result.get('shares',0)}股")
            test("止损价", result.get("stop_loss", 0) > 0,
                 f"¥{result.get('stop_loss')}")
            test("止盈价", result.get("take_profit", 0) > 0,
                 f"¥{result.get('take_profit')}")
    except Exception as e:
        test("审核通道", False, str(e)[:80])
else:
    test("审核通道", False, "无已批准股票")

# ═══════════════════════════════════════════════════════
# 5. 模拟盘引擎
# ═══════════════════════════════════════════════════════
print("\n[5] 模拟盘引擎")
try:
    resp = urllib.request.urlopen(f"{FLASK}/api/paper-trade/v2", timeout=10)
    data = json.loads(resp.read().decode())
    eq = data.get("total_equity", 0)
    cash = data.get("cash", 0)
    positions = data.get("positions", [])
    test("纸引擎响应", resp.getcode() == 200, f"HTTP {resp.getcode()}")
    test("总资产", eq > 0, f"¥{eq:,.0f}")
    test("现金", cash > 0, f"¥{cash:,.0f}")
    test("持仓", len(positions) >= 0, f"{len(positions)}只")
    pnl = data.get("total_pnl", 0)
    test("累计盈亏", True, f"¥{pnl:+,.0f}")
    auto = data.get("auto_enabled", False)
    test("自动交易", True, "开启" if auto else "关闭(手动下单)")
except Exception as e:
    test("模拟盘", False, str(e)[:80])

# ═══════════════════════════════════════════════════════
# 6. QMT行情桥接
# ═══════════════════════════════════════════════════════
print("\n[6] QMT行情桥接")
qmt_cache = r"D:\quant_framework\quote_cache.json"
if os.path.exists(qmt_cache):
    age = time.time() - os.path.getmtime(qmt_cache)
    with open(qmt_cache, "r") as f:
        raw = json.load(f)
    count = raw.get("count", 0)
    test("缓存文件", True, f"{count}只, {age:.0f}秒前")
    test("新鲜度", age < 5, "实时" if age < 5 else f"过期{age:.0f}秒 — QMT桥接可能停了")
    # 抽样一只股票
    data = raw.get("data", {})
    if data:
        first = list(data.keys())[0]
        tick = data[first]
        test("行情样本", tick.get("price", 0) > 0,
             f"{first} ¥{tick.get('price')} {tick.get('change_pct')}%")
else:
    test("缓存文件", False, "QMT桥接未运行")

# ═══════════════════════════════════════════════════════
# 7. Flask 信号中心
# ═══════════════════════════════════════════════════════
print("\n[7] 前端信号中心")
try:
    resp = urllib.request.urlopen(f"{FLASK}/api/signal-center", timeout=10)
    data = json.loads(resp.read().decode())
    sigs = data.get("signals", data)
    count = data.get("count", len(sigs) if isinstance(sigs, list) else 0)
    test("信号中心", count > 0, f"{count}条")
except Exception as e:
    test("信号中心", False, str(e)[:60])

# ═══════════════════════════════════════════════════════
# 8. 风控检查
# ═══════════════════════════════════════════════════════
print("\n[8] 风控采样")
try:
    resp = urllib.request.urlopen(f"{FLASK}/api/health", timeout=10)
    health = json.loads(resp.read().decode())
    checks = health.get("checks", {})
    # 行情
    rt = checks.get("realtime_quotes", {})
    test("实时行情", rt.get("ok", False), f"{rt.get('count',0)}只")
    # 因子
    fh = checks.get("factor_health", {})
    test("因子健康", fh.get("ok", False), fh.get("note", ""))
    # 数据源
    ds = checks.get("data_sources", {})
    test("数据源", ds.get("ok", False), f"{ds.get('alive',0)}/4存活")
except:
    test("健康检查", False)

print("\n" + "=" * 60)
print("  测试完成")
print("=" * 60)
