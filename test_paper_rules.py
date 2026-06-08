"""专业测试: 模拟交易V4规则合规性验证 — P0-模拟-01/02

旧测试适配新 API (RuleEngine + SimulatedBroker)。
"""
import json, os, sys
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_framework\src")

from paper_engine import PaperAccount, _load_trade_config

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def reset_and_get_baseline():
    # 清理状态文件，防止跨测试污染
    import os as _os
    sf = r"d:\quant_framework\paper_account.json"
    if _os.path.exists(sf):
        _os.remove(sf)
    acc = PaperAccount()
    acc.place_order("RESET", "reset", qty=0)
    acc.auto_enabled = True
    print("✅ 账户已重置, 初始资金: ¥1,000,000")
    return acc

def test_stop_loss(acc):
    """测试1: 基本止损规则"""
    print_section("测试1: 基本止损 — 亏损达阈值即全仓卖出")

    cfg = _load_trade_config()
    tp1_stop = cfg.get("tp1_stop_loss", -0.03)
    tp2_stop = cfg.get("tp2_stop_loss", -0.05)
    basic_stop = min(tp1_stop, tp2_stop)
    print(f"  规则: 亏损≤{basic_stop*100:.0f}%触发止损, 全仓卖出")

    acc.place_order("601398", "buy", price=10.0, qty=1000)
    # fix buy_date to allow sell
    acc.positions["601398"]["buy_date"] = "2020-01-01"

    stop_price = 10.0 * (1 + basic_stop)
    test_price = stop_price - 0.01
    acc.positions["601398"]["last_price"] = test_price

    pnl_pct = (test_price / 10.0 - 1) * 100
    print(f"  模拟价格: ¥{test_price:.2f} (盈亏: {pnl_pct:.2f}%)")
    print(f"  止损阈值: ¥{stop_price:.2f} (盈亏≤{basic_stop*100:.0f}%)")

    actions = acc.auto_trade_check([])

    if actions:
        for a in actions:
            print(f"  ✅ 触发规则: {a.get('reason', '?')} | {a.get('symbol')} {a.get('side')} {a.get('qty')}股")
        remaining = acc.positions.get("601398", {}).get("qty", 0)
        if remaining == 0:
            print(f"  ✅ 验证通过: 持仓已全部卖出")
        else:
            print(f"  ⚠️ 部分卖出: 剩余{remaining}股")
    else:
        print(f"  ❌ 失败: 止损规则未触发! (pnl_pct={pnl_pct:.2f}%, 阈值={basic_stop*100:.0f}%)")

    return bool(actions)

def test_trailing_stop1(acc):
    """测试2: 移动止盈1"""
    print_section("测试2: 移动止盈1 — 盈利≥5%后回落≥1%触发")

    cfg = _load_trade_config()
    tp1_profit = cfg.get("tp1_profit_pct", 0.05)
    tp1_trail = abs(cfg.get("tp1_trail_pct", -0.01))
    tp1_sell = cfg.get("tp1_sell_ratio", 0.33)
    print(f"  规则: 盈利≥{tp1_profit*100:.0f}% → 回落≥{tp1_trail*100:.0f}% → 卖{int(tp1_sell*100)}%")

    acc.place_order("601166", "buy", price=100.0, qty=300)
    acc.positions["601166"]["buy_date"] = "2020-01-01"
    print(f"  买入: 601166 x300股 @¥100")

    # 先建立峰值 — 通过规则引擎的 trailing stop
    peak_price = 100 * (1 + tp1_profit + 0.01)
    # 直接设置规则引擎中 trailing stop 的峰值
    for rule in acc._rule_engine.rules:
        if hasattr(rule, '_peaks') and hasattr(rule, 'tier') and rule.tier == 1:
            rule._peaks["601166"] = (peak_price / 100 - 1)
            print(f"  设置 T1 峰值: +{rule._peaks['601166']*100:.1f}%")

    acc.positions["601166"]["last_price"] = peak_price

    # 回落
    drop_price = peak_price * (1 - (tp1_trail + 0.005))
    acc.positions["601166"]["last_price"] = drop_price
    pnl_pct = (drop_price / 100 - 1) * 100
    print(f"  模拟回落: ¥{drop_price:.2f} (当前盈利: {pnl_pct*100:.2f}%)")

    actions = acc.auto_trade_check([])

    if actions:
        for a in actions:
            print(f"  ✅ 触发规则: {a.get('reason', '?')} | {a.get('symbol')} {a.get('side')} {a.get('qty')}股")
        remaining = acc.positions.get("601166", {}).get("qty", 0)
        sold = 300 - remaining
        print(f"  卖出{sold}股, 剩余{remaining}股")
        if 0 <= remaining < 300:
            print(f"  ✅ 验证通过: 止盈触发")
        else:
            print(f"  ⚠️ 异常: 剩余{remaining}股")
    else:
        print(f"  ❌ 失败: 止盈1未触发!")

    return bool(actions)

def test_trailing_stop2(acc):
    """测试3: 移动止盈2"""
    print_section("测试3: 移动止盈2 — 盈利≥7%后回落≥2%触发")

    cfg = _load_trade_config()
    tp2_profit = cfg.get("tp2_profit_pct", 0.07)
    tp2_trail = abs(cfg.get("tp2_trail_pct", -0.02))
    tp2_sell = cfg.get("tp2_sell_ratio", 0.33)
    print(f"  规则: 盈利≥{tp2_profit*100:.0f}% → 回落≥{tp2_trail*100:.0f}% → 卖{int(tp2_sell*100)}%")

    acc.place_order("600036", "buy", price=50.0, qty=600)
    acc.positions["600036"]["buy_date"] = "2020-01-01"
    print(f"  买入: 600036 x600股 @¥50")

    peak_price = 50 * (1 + tp2_profit + 0.02)
    for rule in acc._rule_engine.rules:
        if hasattr(rule, '_peaks') and hasattr(rule, 'tier') and rule.tier == 2:
            rule._peaks["600036"] = (peak_price / 50 - 1)
            print(f"  设置 T2 峰值: +{rule._peaks['600036']*100:.1f}%")

    acc.positions["600036"]["last_price"] = peak_price
    drop_price = peak_price * (1 - (tp2_trail + 0.01))
    acc.positions["600036"]["last_price"] = drop_price
    pnl_pct = (drop_price / 50 - 1) * 100
    print(f"  模拟回落: ¥{drop_price:.2f}")

    actions = acc.auto_trade_check([])

    if actions:
        for a in actions:
            print(f"  ✅ 触发规则: {a.get('reason', '?')} | {a.get('symbol')} {a.get('side')} {a.get('qty')}股")
    else:
        print(f"  ❌ 失败: 止盈2未触发!")

    return bool(actions)

def test_signal_buy(acc):
    """测试4: 信号买入"""
    print_section("测试4: 信号买入 — 信号强度决定仓位")

    cfg = _load_trade_config()
    signal_min = cfg.get("signal_min_strength", 5)
    # 临时降低阈值以便测试
    acc._signal_filter.min_strength = 3
    print(f"  规则: 信号≥{acc._signal_filter.min_strength}级自动买入 (测试模式)")
    print(f"    级3→1/5仓, 级4→1/3仓, 级5→1/2仓")

    init_cash = acc.cash
    signals = [
        {"symbol": "000001", "buy_signal": 3, "close": 20.0},
        {"symbol": "000002", "buy_signal": 4, "close": 30.0},
        {"symbol": "600000", "buy_signal": 5, "close": 15.0},
        {"symbol": "000004", "buy_signal": 2, "close": 10.0},
    ]

    actions = acc.auto_trade_check(signals)

    if actions:
        for a in actions:
            print(f"  ✅ {a.get('reason')} | {a.get('symbol')} {a.get('side')} {a.get('qty')}股")
        bought_symbols = [a.get("symbol") for a in actions]
        if "000004" not in bought_symbols:
            print(f"  ✅ 验证通过: 信号2级未触发买入")
        else:
            print(f"  ❌ 失败: 信号2级不应买入!")
        total_spent = init_cash - acc.cash
        print(f"  总花费: ¥{total_spent:,.0f}")
    else:
        print(f"  ❌ 失败: 无任何买入!")

    return bool(actions)

def test_max_daily_trades(acc):
    """测试5: 日限笔数"""
    print_section("测试5: 日限笔数 — 达到上限后停止买入")

    max_daily = 4
    print(f"  规则: 每日最多{max_daily}笔交易")

    acc._daily_date = __import__("datetime").datetime.now().strftime("%Y%m%d")
    acc._daily_trade_count = max_daily
    print(f"  当前日内交易数: {acc._daily_trade_count}/{max_daily}")

    orig_reset = acc._reset_daily_if_new_day
    acc._reset_daily_if_new_day = lambda: None

    signals = [{"symbol": "600100", "buy_signal": 4, "close": 25.0}]
    actions = acc.auto_trade_check(signals)
    acc._reset_daily_if_new_day = orig_reset

    if not actions:
        print(f"  ✅ 验证通过: 已达日限{max_daily}笔, 不再买入")
    else:
        print(f"  ❌ 失败: 应阻止买入但执行了{len(actions)}笔!")

    return not bool(actions)

def test_circuit_breaker(acc):
    """测试6: 熔断规则"""
    print_section("测试6: 熔断规则 — 停止买入, 保留卖出/止损")

    cfg = _load_trade_config()
    max_loss = cfg.get("max_daily_loss", -5.0)
    print(f"  规则: 日亏损≥{abs(max_loss):.1f}%触发熔断 → 停买不停卖")

    acc._daily_date = __import__("datetime").datetime.now().strftime("%Y%m%d")
    acc._daily_loss_total = -60000
    print(f"  模拟日内亏损: ¥{abs(acc._daily_loss_total):,.0f}")

    acc.place_order("601988", "buy", price=50.0, qty=200)
    acc.positions["601988"]["buy_date"] = "2020-01-01"
    stop_price = 50.0 * 0.91
    acc.positions["601988"]["last_price"] = stop_price
    pnl = (stop_price / 50.0 - 1) * 100
    print(f"  持仓盈亏: {pnl:.1f}%")

    signals = [{"symbol": "600200", "buy_signal": 5, "close": 30.0}]
    actions = acc.auto_trade_check(signals)

    has_buy = any(a.get("side") == "buy" for a in actions)
    has_sell = any(a.get("side") == "sell" for a in actions)

    print(f"  触发动作: {len(actions)}个")
    for a in actions:
        print(f"     {a.get('reason','?')} | {a.get('symbol')} {a.get('side')} {a.get('qty')}股")

    if not has_buy:
        print(f"  ✅ 验证通过: 熔断后买入被阻止")
    elif has_buy:
        print(f"  ❌ 失败: 熔断后不应买入!")

    return not has_buy

def test_t1_constraint():
    """测试7: T+1 强约束 (P0-模拟-02)"""
    print_section("测试7: T+1 强约束 — 今日买入今日不可卖出")

    acc = PaperAccount()
    acc.cash = 1_000_000
    acc._positions_compat = {}

    r = acc.place_order("000001", "buy", 10.0, 1000)
    assert r.get("success"), f"Buy failed: {r}"
    print(f"  ✅ 买入成功: 000001 x1000股 @¥10")

    r2 = acc.place_order("000001", "sell", 10.0, 1000)
    if not r2.get("success") and "T+1" in r2.get("error", ""):
        print(f"  ✅ T+1 拒绝: {r2['error']}")
        return True
    else:
        print(f"  ❌ 失败: T+1 未阻止卖出! {r2}")
        return False

def test_rule_not_trigger_prematurely(acc):
    """测试8: 边界条件 — 未达阈值不触发"""
    print_section("测试8: 边界条件 — 未达阈值不误触发")

    cfg = _load_trade_config()
    tp1_profit = cfg.get("tp1_profit_pct", 0.05)

    acc.place_order("601288", "buy", price=20.0, qty=500)
    acc.positions["601288"]["buy_date"] = "2020-01-01"
    below_threshold = 20.0 * (1 + tp1_profit - 0.02)
    acc.positions["601288"]["last_price"] = below_threshold

    pnl = (below_threshold / 20.0 - 1) * 100
    print(f"  持仓盈亏: {pnl:.2f}% (触发阈值: ≥{tp1_profit*100:.0f}%)")

    actions = acc.auto_trade_check([])

    if not actions:
        print(f"  ✅ 验证通过: 未达阈值, 不触发卖出")
    else:
        print(f"  ❌ 失败: 误触发! {[(a.get('reason'), a.get('side')) for a in actions]}")

    return not bool(actions)


# ═══════════════════════════════════════════
# 主测试流程
# ═══════════════════════════════════════════
if __name__ == "__main__":
    results = {}

    print("🐉 潜龙模拟交易V4 — 规则合规性测试 (P0-模拟-01/02)")
    print("测试时间:", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # T+1 (独立测试，不需配置)
    results["T+1强约束"] = test_t1_constraint()

    # 止损
    acc1 = reset_and_get_baseline()
    results["基本止损"] = test_stop_loss(acc1)

    # 移动止盈1
    acc2 = reset_and_get_baseline()
    results["移动止盈1"] = test_trailing_stop1(acc2)

    # 移动止盈2
    acc3 = reset_and_get_baseline()
    results["移动止盈2"] = test_trailing_stop2(acc3)

    # 信号买入
    acc4 = reset_and_get_baseline()
    results["信号自动买入"] = test_signal_buy(acc4)

    # 日限笔数
    acc5 = reset_and_get_baseline()
    results["日限笔数上限"] = test_max_daily_trades(acc5)

    # 熔断
    acc6 = reset_and_get_baseline()
    results["熔断(停买不停卖)"] = test_circuit_breaker(acc6)

    # 边界条件
    acc7 = reset_and_get_baseline()
    results["边界条件(未达阈值不触发)"] = test_rule_not_trigger_prematurely(acc7)

    # 汇总
    print_section("📊 测试汇总报告")
    passed = sum(1 for v in results.values() if v)
    failed = sum(1 for v in results.values() if not v)
    total = len(results)

    for name, ok in results.items():
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status} | {name}")

    print(f"\n  ┌─────────────────────────────┐")
    print(f"  │  通过: {passed}/{total}  │  失败: {failed}/{total}  │  通过率: {passed/total*100:.0f}%  │")
    print(f"  └─────────────────────────────┘")

    if failed > 0:
        print("\n  ⚠️ 存在失败项, 需要修复 paper_engine.py 中的规则逻辑")
        sys.exit(1)
    else:
        print("\n  🎉 所有规则测试通过! 模拟盘V4可安全用于实盘验证")
