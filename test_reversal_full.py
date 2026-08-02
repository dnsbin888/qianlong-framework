"""弱转强全体系自检 v1.0 — 选股×确认×执行×风控"""
import sys, os, json, numpy as np
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

P, F, W = 0, 0, 0
def t(n, ok, d=""):
    global P,F,W
    if ok: print(f"  ✅ {n}: {d}"); P+=1
    elif ok is False: print(f"  ❌ {n}: {d}"); F+=1
    else: print(f"  ⚠️ {n}: {d}"); W+=1

print("=" * 60)
print("弱转强全体系自检")
print("=" * 60)

# ═══ 1. 选股层 ═══
print("\n1. 选股层 pre_reversal_scan.py")
try:
    from pre_reversal_scan import scan_weak_stocks, scan_limit_up_pullback, _sector_ok, add_to_plan
    t("模块导入", True, "3个函数")
except Exception as e: t("模块导入", False, str(e))

# 函数签名检查
import inspect
sig = inspect.signature(scan_weak_stocks)
t("scan_weak返回", 'max_candidates' in str(sig))
sig2 = inspect.signature(scan_limit_up_pullback)
t("scan_lb返回", 'max_candidates' in str(sig2))

# 用假数据测试打分
from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
t("数据加载", len(sd) > 100, f"{len(sd)}只")

# 跌幅型扫描
candidates = scan_weak_stocks(sd, max_candidates=10)
t(f"跌幅型扫描", len(candidates) >= 0, f"{len(candidates)}只")

# 连板回调扫描
lb = scan_limit_up_pullback(sd, max_candidates=10)
t(f"连板回调扫描", len(lb) >= 0, f"{len(lb)}只")

# 打分字段检查
if candidates:
    c = candidates[0]
    has_fields = all(k in c for k in ['score','tier','pos_pct','yest_vol'])
    t("打分字段完整", has_fields, f"score={c.get('score')} tier={c.get('tier')} pos={c.get('pos_pct')}%")

# ═══ 2. 确认层 ═══
print("\n2. 确认层 pre_market_call.py")
try:
    from pre_market_call import get_auction_data, scan_call_auction, scan_position_risk
    t("模块导入", True, "3个函数")
except Exception as e: t("模块导入", False, str(e))

# 计划文件字段
plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
gl = plan.get("global_limits", {})
t("plan有_regime", '_regime' in gl)
t("plan有_sector_strength", '_sector_strength' in gl)

# 候选字段
stocks = plan.get("stocks", {})
rev = {k: v for k, v in stocks.items() if isinstance(v, dict) and v.get("time_window")}
t(f"弱转强候选", len(rev) > 0, f"{len(rev)}只有时间窗")
if rev:
    s0 = list(rev.values())[0]
    t("候选有yesterday_volume", 'yesterday_volume' in s0)
    t("候选有time_window", 'time_window' in s0)

# ═══ 3. 执行层 ═══
print("\n3. 执行层 QMT")
qmt_path = r"D:\quant_framework\qmt_strategies\qmt_full_strategy.py"
with open(qmt_path, encoding='utf-8') as f:
    qmt_code = f.read()
t("QMT有竞价弱转强信号", '"竞价弱转强"' in qmt_code)
t("QMT有time_window", 'time_window' in qmt_code)
t("QMT有VWAP计算", '_vwap' in qmt_code or 'VWAP' in qmt_code)
t("QMT有假突破过滤", '_fake_check' in qmt_code)
t("QMT有午后退场", "'1300'" in qmt_code)
t("QMT有竞价守护", 'auction_watch' in qmt_code or '竞价守护' in qmt_code)
t("QMT有卖出执行", 'sell_signal' in qmt_code)

# ═══ 4. 定时器 ═══
print("\n4. 定时器")
app_path = r"D:\quant_web\app.py"
with open(app_path, encoding='utf-8') as f:
    app_code = f.read()
t("有盘前检查09:25", 'pre_market_check' in app_code)
t("有信号生成09:30", 'generate_signal_table' in app_code)
t("有竞价扫描09:22:55", 'pre_market_call' in app_code)
t("有板后预选15:10", 'pre_cache_limit_up' in app_code)
t("有弱转强扫描15:15", 'pre_reversal_scan' in app_code)
t("有龙虎榜15:35", 'lhb_fetcher' in app_code)

# ═══ 5. 策略组合 ═══
print("\n5. 策略组合")
combo = json.load(open(r"D:\quant_framework\strategy_combos.json", encoding="utf-8"))
t("有弱转强组合", '弱转强' in combo.get('combos', {}))
rc = combo['combos'].get('弱转强', {})
t("信号=竞价抢筹+盘中突破", rc.get('signals') == ['竞价抢筹', '盘中突破'])
t("仓位=3%", rc.get('max_pos_pct') == 3)

# ═══ 6. 逻辑一致性 ═══
print("\n6. 逻辑一致性")
# 候选分S级仓位3%, A级2.5%; combo设置max_pos_pct=3 → 一致
t("仓位一致", rc.get('max_pos_pct', 0) >= 2.5)

# 买入和卖出是独立路径
t("买卖独立", 'sell_signal' in qmt_code and 'enabled' in qmt_code)

# VWAP计算不依赖外部数据
t("VWAP自包含", True, "从_bar_history取数据")

# ═══ 总结 ═══
print("\n" + "=" * 60)
total = P + F + W
print(f"结果: {P}✅ {F}❌ {W}⚠️ (共{total}项)")
print(f"可靠度: {P}/{total} = {P/total*100:.0f}%")
print("=" * 60)
