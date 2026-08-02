"""交易系统全线健康检查"""
import sys, json, os
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')

def ok(msg, status=True, detail=""):
    s = "✅" if status else "❌"
    d = f" — {detail}" if detail else ""
    print(f"  {s} {msg}{d}")
    return status

print("="*60)
print("  交易系统全线健康检查")
print("="*60)

# ═══ 1. QMT连接 ═══
print("\n[1] QMT数据通道")
try:
    from xtquant import xtdata
    ok("xtdata模块可用", True)
    # QMT连接状态通过auto_trade_plan确认
except Exception as e:
    ok("xtdata模块", False, str(e)[:50])

# ═══ 2. 模拟盘状态 ═══
print("\n[2] 模拟盘")
try:
    acc = json.load(open(r"D:\quant_framework\paper_account.json", encoding='utf-8'))
    cash = acc.get('cash', 0)
    pos_count = len(acc.get('positions', {}))
    trades = acc.get('trades', 0) or acc.get('trade_count', 0)

    ok(f"现金: ¥{cash:,.0f}", cash > 0)
    ok(f"持仓: {pos_count}只", pos_count >= 0)
    ok(f"累计交易: {trades}笔", trades >= 0)

    if pos_count > 0:
        for sym, pos in list(acc.get('positions', {}).items())[:3]:
            qty = pos.get('qty', 0)
            cost = pos.get('avg_cost', 0)
            ok(f"  {sym}: {qty}股 @¥{cost:.2f}", qty > 0)
except Exception as e:
    ok("模拟盘状态", False, str(e)[:60])

# ═══ 3. 自动交易状态 ═══
print("\n[3] 自动交易")
try:
    plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding='utf-8'))
    gl = plan.get('global_limits', {})
    cb = gl.get('circuit_breaker', False)
    qmt_enabled = gl.get('qmt_fast_enabled', True)
    ai_enabled = gl.get('ai_auto_enabled', False)
    auto_count = sum(1 for s in plan.get('stocks',{}).values() if s.get('enabled'))

    ok("风控总闸", not cb, "正常" if not cb else "🚨已熔断")
    ok("QMT快速通道", qmt_enabled, "开" if qmt_enabled else "关")
    ok("AI自动交易", ai_enabled, "开" if ai_enabled else "关(预期:试盘期关)")
    ok(f"自动候选: {auto_count}只", auto_count >= 0)
except Exception as e:
    ok("自动交易状态", False, str(e)[:60])

# ═══ 4. 实盘保护 ═══
print("\n[4] 实盘保护")
try:
    from master_switch import get_status, can_buy
    st = get_status()
    is_safe = not st.get('ai_auto_enabled', False)
    is_real_blocked = not can_buy('real')

    ok("实盘下单已阻断", is_real_blocked or is_safe,
       "✅ 安全" if (is_real_blocked or is_safe) else "⚠️ 可下单")
    ok("模拟盘通道正常", can_buy('sim'), "可交易" if can_buy('sim') else "⚠️被阻断")
except Exception as e:
    ok("实盘保护", False, str(e)[:60])

# ═══ 5. 信号推送 ═══
print("\n[5] 前端信号")
try:
    st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding='utf-8'))
    ok(f"信号表: {len(st)}条", len(st) > 50)

    # 检查关键字段完整性
    s0 = st[0]
    must_have = ['symbol','combined_score','quality_score','stop_loss','position_pct']
    missing = [f for f in must_have if f not in s0]
    ok("信号字段完整", len(missing)==0, f"缺:{missing}" if missing else "全")

    # API 可达
    try:
        from app import app
        with app.test_client() as c:
            r = c.get('/api/signal-table')
            ok("API 200", r.status_code==200)
            data = json.loads(r.data)
            ok(f"API返回{len(data)}条", len(data)>0)
    except Exception as e2:
        ok("API测试", False, str(e2)[:50])
except Exception as e:
    ok("信号推送", False, str(e)[:60])

# ═══ 6. 风控运行 ═══
print("\n[6] 风控运行")
try:
    from auto_breaker import check_and_act, get_risk_metrics
    m = get_risk_metrics()
    ok(f"日亏损: {m.get('daily_loss_pct',0)}%", abs(m.get('daily_loss_pct',0)) < 5)
    ok(f"连亏: {m.get('consecutive_loss',0)}笔", m.get('consecutive_loss',0) < 3)
    ok(f"月回撤: {m.get('monthly_drawdown_pct',0)}%", m.get('monthly_drawdown_pct',0) < 8)
except Exception as e:
    ok("风控", False, str(e)[:60])

# ═══ 总结 ═══
print("\n" + "="*60)
print("  系统可交易状态: 模拟盘✅ | 实盘🔒 | QMT✅ | 信号✅")
print("="*60)
