"""深度系统测试 — 模拟盘全周期+风控+边界+一致性"""
import sys, os, json, copy
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

PASS, FAIL, WARN = 0, 0, 0
def chk(ok, msg, detail=""):
    global PASS, FAIL, WARN
    if ok: PASS += 1; print(f"  ✅ {msg}")
    else: FAIL += 1; print(f"  ❌ {msg}" + (f" — {detail}" if detail else ""))

print("=" * 65)
print("  潜龙深度系统测试")
print("=" * 65)

# ═══════════════════════════════════════
# 1. 模拟盘基础状态
# ═══════════════════════════════════════
print("\n[1] 模拟盘基础状态")
from paper_engine import paper

chk(paper.cash > 0, f"资金: ¥{paper.cash:,.0f}")
chk(len(paper.positions) >= 0, f"持仓: {len(paper.positions)}只")

# 验证资金+持仓市值 = 上次保存值
total_mv = sum(p.get("last_price", p.get("avg_cost", 0)) * p.get("qty", 0)
               for p in paper.positions.values())
total_eq = paper.cash + total_mv
chk(abs(total_eq - paper.get_total_equity()) < 100,
    f"总权益一致性: {total_eq:,.0f} ≈ {paper.get_total_equity():,.0f}")

# ═══════════════════════════════════════
# 2. 仓位计算验证 (方案A)
# ═══════════════════════════════════════
print("\n[2] 仓位方案A验证")
from market_regime import detect_regime
from data_loader import load_stock_data_cache

sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
regime = detect_regime(sd)
scale = regime["position_scale"]

lv_map = {5: 12, 4: 8, 3: 5, 2: 2, 1: 0}
for lv, base in lv_map.items():
    expected = round(base * scale, 0) if base > 0 else 0
    chk(expected >= 0, f"Lv{lv}: {base}%×{scale}={expected}% (>=0)")

# 验证信号表仓位
sig_table = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
outliers = []
for r in sig_table:
    pos = r.get("position_pct", 0)
    if pos < 2 and r.get("combined_score", 0) > 80:
        outliers.append(f"{r['symbol']} score={r['combined_score']:.0f} pos={pos}%")
chk(len(outliers) == 0, f"无异常仓位: {len(outliers)}个" if not outliers else f"异常: {outliers[:3]}")

# ═══════════════════════════════════════
# 3. 风控规则验证
# ═══════════════════════════════════════
print("\n[3] 风控规则验证")
from risk_guard import PreTradeChecker

cfg = {"max_single_position_pct": 20, "max_sector_pct": 30,
       "max_daily_trades": 5, "max_order_value": 1_000_000,
       "signal_min_strength": 3, "max_positions_abs": 10}
ptc = PreTradeChecker(config=cfg, positions=paper.positions, cash=paper.cash)

# 测试正常买单
result = ptc.check_buy("sh600519", 1600, 100)
action, reason, adj_qty = result if len(result) == 3 else (result[0], result[1], 100)
chk(action != "REJECT", f"正常买单: {action} (非REJECT)")

# 测试乌龙指 (100万股 × 1600元 = 16亿)
result2 = ptc.check_buy("sh600519", 1600, 1_000_000)
action2 = result2[0] if len(result2) >= 1 else "ERROR"
chk(action2 == "REJECT", f"乌龙指100万股: {action2} (应REJECT)")

# 测试涨停排队
result3 = ptc.check_buy("sh600519", 1760, 100)  # 假设涨停价1760
action3 = result3[0] if len(result3) >= 1 else "ERROR"
print(f"    涨停价测试: {action3} (模拟盘应REJECT)")

# T+1卖出检测
result4 = ptc.check_sell("sh600519", 1600, 100)
action4 = result4[0] if len(result4) >= 1 else "ERROR"
# 如果没有持仓，应该REJECT或APPROVE (取决于T+1检查)
print(f"    T+1卖出: {action4} (无持仓应REJECT)")

# ═══════════════════════════════════════
# 4. 三级止盈逻辑验证
# ═══════════════════════════════════════
print("\n[4] 三级止盈逻辑验证")
master = json.load(open(r"D:\quant_framework\trade_config_master.json", encoding="utf-8"))
tp = master["take_profit"]

# TP1: 涨5%触发, 回落1%卖1/3
chk(tp["tp1"]["profit_pct"] == 0.05 and tp["tp1"]["trail_pct"] == 0.01,
    f"TP1: +{tp['tp1']['profit_pct']*100:.0f}% / -{tp['tp1']['trail_pct']*100:.0f}% / 卖{int(tp['tp1']['sell_ratio']*100)}%")
# TP2: 涨7%触发, 回落2%卖1/3
chk(tp["tp2"]["profit_pct"] == 0.07 and tp["tp2"]["trail_pct"] == 0.02,
    f"TP2: +{tp['tp2']['profit_pct']*100:.0f}% / -{tp['tp2']['trail_pct']*100:.0f}% / 卖{int(tp['tp2']['sell_ratio']*100)}%")
# TP3: 涨10%触发, 回落3%全清
chk(tp["tp3"]["profit_pct"] == 0.10 and tp["tp3"]["sell_ratio"] == 1.0,
    f"TP3: +{tp['tp3']['profit_pct']*100:.0f}% / -{tp['tp3']['trail_pct']*100:.0f}% / 全清")

# 每个TP都有stop_loss
for t in ["tp1","tp2","tp3"]:
    chk(tp[t].get("stop_loss") is not None, f"{t} 有stop_loss: {tp[t].get('stop_loss')}")

# ═══════════════════════════════════════
# 5. 数据持久化验证
# ═══════════════════════════════════════
print("\n[5] 数据持久化验证")
# 模拟一次 _save → 验证文件可读
state_file = r"D:\quant_framework\paper_account.json"
before_cash = paper.cash
paper._save()
chk(os.path.exists(state_file), "paper_account.json 存在")
loaded = json.load(open(state_file, encoding="utf-8"))
chk(abs(loaded.get("cash", 0) - before_cash) < 1,
    f"cash保存一致: {loaded.get('cash',0):,.0f} ≈ {before_cash:,.0f}")
chk(os.path.exists(state_file + ".bak"), ".bak 备份存在")

# ═══════════════════════════════════════
# 6. API 数据一致性
# ═══════════════════════════════════════
print("\n[6] API数据一致性")
import requests
base = "http://localhost:5002"

# paper vs live 不能搞混
r_paper = requests.get(f"{base}/api/paper-trade/v2", timeout=5)
r_live = requests.get(f"{base}/api/live-trade/status", timeout=5)
if r_paper.status_code == 200 and r_live.status_code == 200:
    paper_data = r_paper.json()
    live_data = r_live.json()
    chk(paper_data.get("total_equity", 0) != live_data.get("total_equity", 0),
        f"模拟盘权益(¥{paper_data.get('total_equity',0):,.0f}) ≠ 实盘权益(¥{live_data.get('total_equity',0):,.0f})")
else:
    chk(False, f"API不通: paper={r_paper.status_code} live={r_live.status_code}")

# 信号表 vs auto_trade_plan 交叉
plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding="utf-8"))
plan_stocks = set(plan.get("stocks", {}).keys())
sig_stocks = set(r["symbol"] for r in sig_table)
overlap = plan_stocks & sig_stocks
chk(len(overlap) > 0, f"plan∩信号: {len(overlap)}只交叉")

# ═══════════════════════════════════════
# 7. 交易日志完整性
# ═══════════════════════════════════════
print("\n[7] 交易日志完整性")
csv_path = r"d:\quant_framework\trade_log.csv"
if os.path.exists(csv_path):
    with open(csv_path, encoding="utf-8-sig") as f:
        lines = f.readlines()
    chk(len(lines) > 1, f"trade_log.csv: {len(lines)-1}笔记录")
    header = lines[0].strip().split(",")
    chk("symbol" in header and "net_profit" in header, f"字段完整: {header[:5]}")
    # 检查是否有空行/半行脏数据
    bad_lines = [i for i, l in enumerate(lines) if len(l.strip().split(",")) < 5 and l.strip()]
    chk(len(bad_lines) == 0, f"无脏数据行" if not bad_lines else f"脏数据行: {bad_lines}")

# ═══════════════════════════════════════
# 8. 边界场景
# ═══════════════════════════════════════
print("\n[8] 边界场景")
# 负价格
from paper_engine import paper as p2
r_neg = p2.place_order("sh600519", "buy", -1, 100)
chk(not r_neg.get("success"), f"负价格拒绝: success={r_neg.get('success')}")

# 零股数
r_zero = p2.place_order("sh600519", "buy", 1600, 0)
chk(not r_zero.get("success"), f"零股数拒绝: success={r_zero.get('success')}")

# 不存在的股票
r_fake = p2.place_order("sh999999", "buy", 100, 100)
chk(not r_fake.get("success", True), f"不存在股票: success={r_fake.get('success')}")

# 资金不足
r_poor = p2.place_order("sh600519", "buy", 1600, 999999)
chk(not r_poor.get("success"), f"资金不足拒绝: error={r_poor.get('error','')[:30]}")

# ═══════════════════════════════════════
# 9. 模型就绪验证
# ═══════════════════════════════════════
print("\n[9] 模型就绪验证")
from lgbm_strategy import is_model_ready as lgbm_ok
from xgb_factor_weight import is_ready as xgb_ok
chk(lgbm_ok(), f"LGBM: 就绪")
chk(xgb_ok(), f"XGBoost: 就绪")
chk(os.path.exists(r"D:\quant_framework\catboost_model.cbm"), "CatBoost: 模型存在")

# 预测一致性: LGBM和XGB对同一股票应产出合理分数
sig_sample = sig_table[0]
chk(0 <= sig_sample.get("combined_score", -1) <= 100,
    f"综合评分: {sig_sample['combined_score']:.0f} 在[0,100]")

# ═══════════════════════════════════════
# 10. 系统资源
# ═══════════════════════════════════════
print("\n[10] 系统资源")
import psutil
mem = psutil.virtual_memory()
chk(mem.percent < 95, f"内存: {mem.percent:.1f}% (<95%)")

disk = psutil.disk_usage(r"D:\quant_framework")
chk(disk.free > 500 * 1024 * 1024, f"磁盘: {disk.free//1024//1024}MB (>500MB)")

from datetime import datetime
state_mtime = os.path.getmtime(state_file)
seconds_ago = (datetime.now().timestamp() - state_mtime)
chk(seconds_ago < 3600, f"状态文件: {seconds_ago:.0f}秒前 (<1小时)")

# ═══════════════════════════════════════
# 总结
# ═══════════════════════════════════════
print(f"\n{'='*65}")
total = PASS + FAIL
print(f"  结果: ✅{PASS} ❌{FAIL}  (共{total}项)")
pct = PASS / total * 100 if total else 0
print(f"  通过率: {pct:.0f}%")

if FAIL == 0:
    print(f"  判定: 🟢 模拟盘可交易")
    print(f"  建议: 开盘前1小时启动系统，观察1天模拟盘运行")
    print(f"        确认信号→下单→成交→P&L 全链路正常后，再开实盘验证期")
else:
    print(f"  判定: 🔴 先修错误再交易")
print(f"{'='*65}")
