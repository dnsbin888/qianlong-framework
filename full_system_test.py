"""全链路端到端测试 v1.0 — 数据→信号→仓位→风控→执行→展示"""
import sys, os, json, time, numpy as np

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

PASS, FAIL, WARN = 0, 0, 0

def check(ok, name="", detail=""):
    global PASS, FAIL
    if ok: PASS += 1; print(f"  ✅ {name}" + (f" ({detail})" if detail else ""))
    else: FAIL += 1; print(f"  ❌ {name}" + (f" — {detail}" if detail else ""))

def warn(name, detail=""):
    global WARN; WARN += 1; print(f"  ⚠️ {name} — {detail}")

print("=" * 65)
print("  潜龙全链路端到端测试")
print("=" * 65)

# ═══════════════════════════════════════
# 1. 数据层
# ═══════════════════════════════════════
print("\n[1] 数据层")
t0 = time.time()
try:
    from data_loader import load_stock_data_cache
    sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
    elapsed = time.time() - t0
    check(len(sd) > 5000, f"Parquet加载: {len(sd)}只, {elapsed:.1f}s")
    check("sh000001" in sd, "上证指数存在")
    check("sh600519" in sd, "个股数据存在")
    sample = sd.get("sh600519")
    check(sample is not None and all(c in sample.columns for c in ["open","high","low","close","volume"]),
          "OHLCV字段完整")
except Exception as e:
    check(False, f"数据层异常: {e}")

# ═══════════════════════════════════════
# 2. 因子层
# ═══════════════════════════════════════
print("\n[2] 因子层")
try:
    reg = json.load(open(r"D:\quant_framework\factor_registry.json", encoding="utf-8"))
    factors = reg.get("factors", [])
    active = [f for f in factors if f.get("status") == "active"]
    retired = [f for f in factors if f.get("status") != "active"]
    check(len(factors) > 20, f"注册表: {len(factors)}因子")
    check(len(active) >= 5, f"活跃因子: {len(active)}个")
    # 检查IC值不为空
    has_ic = sum(1 for f in active if f.get("ic_5d"))
    check(has_ic >= 3, f"IC有效: {has_ic}个")
    # 检查无负IC的活跃因子
    bad_ic = [f['name'] for f in active if f.get("ic_5d", 0) < -0.02 and f.get("direction")=="long"]
    check(len(bad_ic) == 0, f"无异常负IC因子" if not bad_ic else f"负IC因子: {bad_ic}")
except Exception as e:
    check(False, f"因子层异常: {e}")

# ═══════════════════════════════════════
# 3. ML模型层
# ═══════════════════════════════════════
print("\n[3] ML模型层")
# LGBM
try:
    from lgbm_strategy import generate_lgbm_signals, is_model_ready
    check(is_model_ready(), "LGBM模型就绪")
    sigs_l = generate_lgbm_signals(sd, top_k=5, min_score=30)
    check(len(sigs_l) >= 3, f"LGBM信号: {len(sigs_l)}只")
except Exception as e:
    check(False, f"LGBM异常: {e}")

# XGBoost
try:
    from xgb_factor_weight import generate_xgb_signals, is_ready
    check(is_ready(), "XGBoost模型就绪")
    sigs_x = generate_xgb_signals(sd, top_k=5, min_score=30)
    check(len(sigs_x) >= 3, f"XGBoost信号: {len(sigs_x)}只")
except Exception as e:
    check(False, f"XGBoost异常: {e}")

# CatBoost
try:
    cb_path = r"D:\quant_framework\catboost_model.cbm"
    check(os.path.exists(cb_path), "CatBoost模型存在")
except Exception as e:
    check(False, f"CatBoost异常: {e}")

# ═══════════════════════════════════════
# 4. 信号层
# ═══════════════════════════════════════
print("\n[4] 信号层")
try:
    sig_table = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
    check(isinstance(sig_table, list) and len(sig_table) > 10,
          f"信号表: {len(sig_table)}条")
    row = sig_table[0]
    required = ["symbol", "combined_score", "position_pct", "stop_loss", "take_profit"]
    missing = [k for k in required if k not in row]
    check(not missing, f"字段完整" if not missing else f"缺字段: {missing}")
    # 仓位一致性
    for r in sig_table[:10]:
        pos = r.get("position_pct", 0)
        if pos > 0:
            check(2 <= pos <= 20, f"{r['symbol']}仓位{pos}%在[2,20]")
except Exception as e:
    check(False, f"信号层异常: {e}")

# ═══════════════════════════════════════
# 5. QMT执行计划
# ═══════════════════════════════════════
print("\n[5] QMT执行计划")
try:
    plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
    stocks = plan.get("stocks", {})
    enabled_list = [s for s, v in stocks.items() if v.get("enabled")]
    limits = plan.get("global_limits", {})
    check(len(stocks) > 10, f"计划: {len(stocks)}只, 启用{len(enabled_list)}只")
    check(not limits.get("circuit_breaker"), "熔断关闭")
    # QMT配置文件
    qmt_cfg = json.load(open(r"D:\quant_web\data\qmt_trade_config.json", encoding="utf-8"))
    check(len(qmt_cfg) > 10, f"QMT配置: {len(qmt_cfg)}只")
    # 交叉验证
    for sym in enabled_list[:3]:
        check(sym in qmt_cfg, f"{sym}在QMT配置中")
except Exception as e:
    check(False, f"执行计划异常: {e}")

# ═══════════════════════════════════════
# 6. 仓位计算一致性
# ═══════════════════════════════════════
print("\n[6] 仓位计算一致性")
try:
    from market_regime import detect_regime
    regime = detect_regime(sd)
    check(regime["regime"] != "unknown", f"市场状态: {regime['regime']} {regime['confidence']:.0%}")
    check(0.3 <= regime["position_scale"] <= 1.0, f"仓位系数: {regime['position_scale']}")
    # 验证信号表中的仓位 = 信号等级 × 市场系数
    from generate_signal_table import main as _dummy
    lv_map = {5: 12, 4: 8, 3: 5, 2: 2, 1: 0}
    for r in sig_table[:5]:
        lv = int(r.get("signal", "Lv3").replace("Lv", "")[0]) if "Lv" in str(r.get("signal","")) else 3
        expected = round(lv_map.get(lv, 5) * regime["position_scale"], 0)
        actual = r.get("position_pct", 0)
        if abs(expected - actual) <= 2:
            pass  # within tolerance
        else:
            warn(f"{r['symbol']}仓位{actual}%≠期望{expected}% (Lv{lv}×{regime['position_scale']})")
except Exception as e:
    check(False, f"仓位计算异常: {e}")

# ═══════════════════════════════════════
# 7. 实盘状态持久化
# ═══════════════════════════════════════
print("\n[7] 实盘状态持久化")
try:
    from live_trader import auto_engine
    # STATE_FILE已定义
    check(hasattr(auto_engine, "STATE_FILE"), f"STATE_FILE: {getattr(auto_engine, 'STATE_FILE', 'MISSING')}")
    # 写入测试
    auto_engine.daily_trade_count = 99
    auto_engine._save_state()
    path = auto_engine.STATE_FILE
    check(os.path.exists(path), f"状态文件已写入")
    if os.path.exists(path):
        data = json.load(open(path, encoding="utf-8"))
        check(data.get("daily_trade_count") == 99, f"读写一致: {data.get('daily_trade_count')}")
    auto_engine.daily_trade_count = 0  # 恢复
except Exception as e:
    check(False, f"持久化异常: {e}")

# ═══════════════════════════════════════
# 8. 模拟盘状态
# ═══════════════════════════════════════
print("\n[8] 模拟盘状态")
try:
    pa = json.load(open(r"D:\quant_framework\paper_account.json", encoding="utf-8"))
    check("cash" in pa and "positions" in pa, "状态文件完整")
    check(pa.get("cash", 0) > 0, f"资金: ¥{pa.get('cash',0):,.0f}")
    # .bak存在
    bak = r"D:\quant_framework\paper_account.json.bak"
    check(os.path.exists(bak), ".bak备份存在")
except Exception as e:
    check(False, f"模拟盘异常: {e}")

# ═══════════════════════════════════════
# 9. 风控一致性
# ═══════════════════════════════════════
print("\n[9] 风控一致性")
try:
    master = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
    tp = master.get("take_profit", {})
    sl = master.get("stop_loss", {})
    check(tp.get("tp3",{}).get("sell_ratio") == 1.0, f"TP3全清: {tp['tp3']['sell_ratio']}")
    check(sl.get("hard") == -0.055, f"硬止损: {sl['hard']}")
    check(sl.get("soft") == -0.03, f"软止损: {sl['soft']}")
    # TP各级stop_loss已定义
    for t in ["tp1","tp2","tp3"]:
        check(tp.get(t,{}).get("stop_loss") is not None, f"{t}.stop_loss已定义")
except Exception as e:
    check(False, f"风控异常: {e}")

# ═══════════════════════════════════════
# 10. 前后端数据一致性
# ═══════════════════════════════════════
print("\n[10] 前后端数据一致性")
try:
    # API测试
    import requests
    base = "http://localhost:5002"
    # 市场状态API
    r = requests.get(f"{base}/api/market-regime", timeout=5)
    if r.status_code == 200:
        d = r.json()
        check(d.get("code") == 200, f"market-regime API: {d.get('regime')} scale={d.get('position_scale')}")
    else:
        warn(f"market-regime API: {r.status_code} (需重启Flask)")
    # 信号API
    r2 = requests.get(f"{base}/api/qmt-signals", timeout=5)
    if r2.status_code == 200:
        sigs = r2.json()
        check(isinstance(sigs, list), f"qmt-signals: {len(sigs)}条")
    else:
        warn(f"qmt-signals API: {r2.status_code}")

    # 实盘状态API
    r3 = requests.get(f"{base}/api/live-trade/status", timeout=5)
    check(r3.status_code == 200, f"live-trade/status: {r3.status_code}")
    if r3.status_code == 200:
        lv = r3.json()
        check("positions" in lv, "含持仓数据")
        check("orders" in lv, "含委托数据")
        check("fills" in lv, "含成交数据")

    # 板块热力API
    r4 = requests.get(f"{base}/api/screener/sector-heat", timeout=5)
    check(r4.status_code == 200, f"sector-heat: {r4.status_code}")
    if r4.status_code == 200:
        heat = r4.json()
        check("top" in heat, f"热门板块: {len(heat.get('top',{}))}个")
except Exception as e:
    check(False, f"API测试异常: {e}")

# ═══════════════════════════════════════
# 总结
# ═══════════════════════════════════════
print(f"\n{'='*65}")
total = PASS + FAIL + WARN
print(f"  结果: ✅{PASS} ❌{FAIL} ⚠️{WARN}  (共{total}项)")
pct = PASS / total * 100 if total else 0
print(f"  通过率: {pct:.0f}%")
if FAIL == 0 and WARN <= 2:
    print(f"  状态: 🟢 系统就绪，可以交易")
elif FAIL == 0:
    print(f"  状态: 🟡 有警告，建议检查")
else:
    print(f"  状态: 🔴 有错误，修后再交易")
print(f"{'='*65}")
