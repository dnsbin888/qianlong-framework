"""潜龙 v3.1 全系统通测 (2026-07-02)"""
import sys, json, os, numpy as np
sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")

print("=" * 55)
print("潜龙 v3.1 全系统诊断")
print("=" * 55)

issues = []

# ── 1. 因子健康 ──
print("\n[1/6] 因子健康...")
try:
    reg = json.load(open(r"D:\quant_framework\factor_registry.json", encoding="utf-8"))
    active = [f for f in reg["factors"] if f.get("status") == "active"]
    healthy = sum(1 for f in active if f.get("weight_multiplier", 1.0) > 0)
    retired = [f for f in reg["factors"] if f.get("status") == "retired"]
    print(f"  Active: {len(active)}, Healthy(weight>0): {healthy}, Retired: {len(retired)}")
    low_ic = [f["name"] for f in active if abs(f.get("ic_5d", 0) or 0) < 0.01]
    if low_ic: issues.append(f"低IC因子: {low_ic}")
    for f in active:
        if f.get("_trading_paused"):
            issues.append(f"因子暂停交易: {f['name']}")
except Exception as e:
    issues.append(f"因子健康检查失败: {e}")

# ── 2. 策略状态 ──
print("\n[2/6] 策略状态...")
try:
    sp = r"D:\quant_framework\user_customizations\user_strategies.json"
    strats = json.load(open(sp, encoding="utf-8"))["strategies"]
    sim_strats = [s for s in strats if s.get("status") in ("sim", "sim_running")]
    real_strats = [s for s in strats if s.get("status") == "real"]
    print(f"  总策略: {len(strats)}, 模拟: {len(sim_strats)}, 实盘: {len(real_strats)}")
    for s in sim_strats:
        if not s.get("backtest"):
            print(f"  ⚠ {s['name']}: 未回测就部署模拟")
    for s in strats:
        if s.get("status") == "sim_running" and len(sim_strats) > 1:
            issues.append(f"多策略并行: {len(sim_strats)} 个sim策略共享资金池")
except Exception as e:
    issues.append(f"策略检查失败: {e}")

# ── 3. 交易合规 ──
print("\n[3/6] 交易合规...")
try:
    paper = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
    trades = paper.get("trade_log", [])
    today_trades = [t for t in trades if t.get("date") == "2026-07-02"]
    yesterday = [t for t in trades if t.get("date") == "2026-07-01"]
    print(f"  昨日: {len(yesterday)}笔, 今日: {len(today_trades)}笔")

    daily_count = paper.get("daily_trade_count", 0)
    if daily_count > 5:
        issues.append(f"日交易超限: {daily_count}>5")

    sells = [t for t in yesterday if t.get("side") == "sell"]
    buys = [t for t in yesterday if t.get("side") == "buy"]
    dupes = 0
    seen = set()
    for t in sells:
        key = (t.get("symbol"), t.get("price"), t.get("qty"), t.get("time"))
        if key in seen: dupes += 1
        seen.add(key)
    if dupes > 0:
        issues.append(f"重复交易: {dupes}笔")

    pnl = sum(t.get("pnl", 0) or 0 for t in sells)
    wins = sum(1 for t in sells if (t.get("pnl", 0) or 0) > 0)
    wr = round(wins / len(sells) * 100, 1) if sells else 0
    print(f"  胜率: {wr}%, 净利: ¥{pnl:,.0f}")
except Exception as e:
    issues.append(f"交易合规检查失败: {e}")

# ── 4. 权益数据 ──
print("\n[4/6] 权益数据...")
try:
    eq = json.load(open(r"D:\quant_framework\equity_log.json", encoding="utf-8"))
    eq_log = eq.get("log", [])
    print(f"  日志点: {len(eq_log)}")
    if len(eq_log) >= 2:
        last = eq_log[-1]
        prev = eq_log[-2]
        chg = last[1] - prev[1]
        print(f"  最新: {last[0]} ¥{last[1]:,.0f}, 变化: ¥{chg:+,.0f}")
    if len(eq_log) < 2:
        issues.append("权益日志不足2点, 日盈亏无法计算")
except Exception as e:
    issues.append(f"权益检查失败: {e}")

# ── 5. API连通性 ──
print("\n[5/6] API连通性...")
apis = [
    ("因子IC表", "/api/factor/ic-table"),
    ("IC分析", "/api/factor/ic-analysis/defensive_v2"),
    ("AI决策", "/api/ai/decision"),
]
import urllib.request
for name, path in apis:
    try:
        u = f"http://127.0.0.1:5002{path}"
        r = urllib.request.urlopen(u, timeout=10)
        code = json.loads(r.read()).get("code", 0)
        status = "✅" if code == 200 else f"⚠ code={code}"
        print(f"  {status} {name}")
    except Exception as e:
        print(f"  ❌ {name}: {str(e)[:50]}")
        issues.append(f"API不可用: {name}")

# ── 6. 数据安全 ──
print("\n[6/6] 数据安全...")
locked = 0
for f in ["paper_account.json", "trade_log.csv", "live_trader_config.json"]:
    p = os.path.join(r"D:\quant_framework", f)
    if os.path.exists(p):
        if os.stat(p).st_file_attributes & 1:
            locked += 1
print(f"  锁定文件: {locked}/3")
if locked < 3:
    issues.append("关键文件未全部锁定")

# ── 总结 ──
print(f"\n{'='*55}")
if issues:
    print(f"发现 {len(issues)} 个问题:")
    for i, iss in enumerate(issues, 1):
        print(f"  {i}. {iss}")
else:
    print("✅ 全系统通过, 无问题")
print(f"{'='*55}")
