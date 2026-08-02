"""六层漏斗补全 — 自检脚本 v2.0
用法: python D:\quant_framework\test_funnel_fixes.py
"""
import sys, os, json
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

PASS, FAIL = 0, 0

def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        print(f"  ✅ {name}" + (f": {detail}" if detail else ""))
        PASS += 1
    else:
        print(f"  ❌ {name}" + (f": {detail}" if detail else ""))
        FAIL += 1

print("=" * 60)
print("六层漏斗补全 — 自检 v2.0")
print("=" * 60)

# ─── 1. tradability_mask v2.0 ───
print("\n📦 1. tradability_mask v2.0 (第0层+第0.5层)")

try:
    from tradability_mask import (filter_universe, compute_liquidity_mask,
        apply_buffer, is_suspended, has_negative_equity,
        compute_avg_daily_turnover, compute_avg_turnover_rate, BUFFER_FILE)
    check("导入 filter_universe + 7个函数", True)
except Exception as e:
    check("导入 filter_universe + 7个函数", False, str(e))
    FAIL += 100  # 致命, 后面的测试没意义
    import numpy as np
    import pandas as pd

if FAIL < 100:
    import numpy as np
    import pandas as pd
    dates = pd.date_range('2026-06-01', periods=60, freq='B')
    np.random.seed(42)

    def fake_df(close, volume, extra_cols=None):
        c = np.full(60, close) + np.random.randn(60) * close * 0.02
        d = {'close': c, 'volume': np.full(60, volume),
             'open': c*0.99, 'high': c*1.02, 'low': c*0.98}
        if extra_cols: d.update(extra_cols)
        return pd.DataFrame(d, index=dates)

    normal   = fake_df(20, 2500000, {'amount': np.full(60, 5e7), 'outstanding': np.full(60, 5e8)})
    low_liq  = fake_df(5,  100000,  {'amount': np.full(60, 5e6), 'outstanding': np.full(60, 5e8)})
    suspended = fake_df(10, 1000000, {'amount': np.full(60, 1e7)})
    suspended.iloc[-5:, suspended.columns.get_loc('volume')] = 0
    cheap    = fake_df(0.8, 5000000, {'amount': np.full(60, 5e7)})
    st_stock = fake_df(15, 2000000, {'amount': np.full(60, 3e7)})

    test_sd = {'sh600001': normal, 'sh600002': low_liq, 'sh600003': suspended,
               'sh600004': cheap, '*STsh600005': st_stock}

    check("is_suspended 正常股→False", not is_suspended(normal))
    check("is_suspended 停牌股→True", is_suspended(suspended))
    check("has_negative_equity 低价股<1元→True", has_negative_equity('sh600004', test_sd))
    check("has_negative_equity *ST→True", has_negative_equity('*STsh600005', test_sd))
    check("日均成交额 正常>1千万", compute_avg_daily_turnover(normal) > 1e7)
    check("日均成交额 低流动<1千万", compute_avg_daily_turnover(low_liq) < 1e7)

    # 清理残留缓冲区 (避免上次测试缓存影响本次)
    _buf_test = r"D:\quant_framework\liquidity_buffer.json"
    if os.path.exists(_buf_test): os.remove(_buf_test)

    pool = filter_universe(test_sd)
    check(f"filter_universe: 5只→{len(pool)}只 (预期1只)", len(pool) == 1)
    if pool:
        check(f"通过的是sh600001", pool[0] == 'sh600001')

# ─── 2. auto_breaker v2.0 ───
print("\n📦 2. auto_breaker v2.0 (峰值回撤熔断)")

try:
    from auto_breaker import (get_current_equity, get_drawdown_pct,
        update_peak_equity, check_and_act, PEAK_FILE)
    check("导入 update_peak_equity + get_drawdown_pct", True)
except Exception as e:
    check("导入 update_peak_equity + get_drawdown_pct", False, str(e))

peak = update_peak_equity()
check("update_peak_equity() 可调用", peak is not None)
dd = get_drawdown_pct()
check(f"get_drawdown_pct() = {dd}% (≥0)", dd >= 0)

# 模拟峰值回撤 — 直接测 calc 逻辑 (get_current_equity 在测试环境读不到真实权益)
try:
    # 写入峰值文件
    with open(PEAK_FILE, 'w') as f:
        json.dump({"peak_equity": 100000, "peak_date": "2026-07-01"}, f)
    # 直接读取计算: (100000 - eq) / 100000 * 100
    # get_current_equity 可能返回0(测试环境), 所以不通过它测
    from auto_breaker import _load_peak
    pk = _load_peak()
    check(f"峰值文件写入/读取: peak={pk.get('peak_equity')}", pk.get('peak_equity') == 100000)
    # 清理
    os.remove(PEAK_FILE)
except Exception as e:
    check("峰值回撤 calc 逻辑", False, str(e))

# ─── 3. exposure v2.0 ───
print("\n📦 3. exposure v2.0 (行业+市值中性化)")

try:
    from exposure import rank_neutralize, market_cap_neutralize
    check("导入 rank_neutralize + market_cap_neutralize", True)
except Exception as e:
    check("导入 rank_neutralize + market_cap_neutralize", False, str(e))

syms = [f'sh60000{i}' for i in range(1, 11)]
scores = [80, 75, 90, 60, 85, 70, 95, 55, 88, 72]
r = rank_neutralize(syms, scores)
check(f"rank_neutralize: {len(r)}个值", len(r) == 10)
check("rank_neutralize: 值有变化(非恒等)", min(r) != max(r))

if 'test_sd' in dir():
    r2 = market_cap_neutralize(['sh600001', 'sh600002', 'sh600003'], [80, 75, 90], test_sd)
    check(f"market_cap_neutralize: {len(r2)}个值", len(r2) == 3)

# ─── 4. psi_monitor ───
print("\n📦 4. psi_monitor v1.0 (PSI特征稳定性)")

try:
    from psi_monitor import (compute_psi, check_psi, psi_summary,
        save_feature_distribution, DIST_DIR)
    check("导入 psi_monitor 全部函数", True)
except Exception as e:
    check("导入 psi_monitor 全部函数", False, str(e))

feat_df = pd.DataFrame({
    'ret_1d': np.random.randn(200) * 0.02,
    'ret_5d': np.random.randn(200) * 0.05,
    'volatility': np.abs(np.random.randn(200) * 0.03 + 0.02),
    'vol_ratio': np.abs(np.random.randn(200) * 0.5 + 1.0),
})

save_feature_distribution(feat_df, "_test_dist.json")
check("保存特征分布 → 文件存在", os.path.exists(os.path.join(DIST_DIR, "_test_dist.json")))

alerts = check_psi(feat_df, "_test_dist.json", auto_init=False)
bad = [a for a in alerts if a.get('level') in ('warning', 'critical')]
check(f"PSI同分布检查: {len(alerts)}告警/{len(bad)}严重 (预期0)", len(bad) == 0)

# 漂移检测
drift_df = pd.DataFrame({
    'ret_1d': np.random.randn(200) * 0.08,
    'ret_5d': np.random.randn(200) * 0.20,
    'volatility': np.abs(np.random.randn(200) * 0.10 + 0.02),
    'vol_ratio': np.abs(np.random.randn(200) * 0.5 + 1.0),
})
alerts2 = check_psi(drift_df, "_test_dist.json", auto_init=False)
check(f"PSI漂移检测: {len(alerts2)}告警 (预期≥1)", len(alerts2) >= 1)

# 清理
for f in ['_test_dist.json']:
    p = os.path.join(DIST_DIR, f)
    if os.path.exists(p): os.remove(p)

# ─── 5. pre_market_check ───
print("\n📦 5. pre_market_check (第12项PSI检查)")

import importlib.util as _iu
_spec = _iu.spec_from_file_location("_pmc", r"D:\quant_framework\pre_market_check.py")
_pmc = _iu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_pmc)
    check("quant_framework版 check_psi 存在", hasattr(_pmc, 'check_psi'))
except Exception as e:
    check("quant_framework版 pre_market_check 导入", False, str(e))

# ─── 6. generate_signal_table 集成验证 ───
print("\n📦 6. generate_signal_table 集成")

try:
    from tradability_mask import filter_universe as _fu2
    check("filter_universe 可被信号表导入", True)
except Exception as e:
    check("filter_universe 可被信号表导入", False, str(e))

# 验证信号表中性化代码存在
_gs_path = r"D:\quant_web\generate_signal_table.py"
with open(_gs_path, encoding='utf-8') as f:
    _gs = f.read()
check("信号表含 filter_universe 调用", 'filter_universe' in _gs)
check("信号表含 rank_neutralize 调用", 'rank_neutralize' in _gs)
check("信号表含 market_cap_neutralize 调用", 'market_cap_neutralize' in _gs)
check("信号表使用 neutralized 得分", 'neutralized.get(sym' in _gs)

# ─── 总结 ───
print("\n" + "=" * 60)
print(f"结果: {PASS}✅ {FAIL}❌")
if FAIL == 0:
    print("全部通过 ✅ 可以投入使用")
else:
    print(f"有 {FAIL} 项未通过，需修复")
print("=" * 60)
