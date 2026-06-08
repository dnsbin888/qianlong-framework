"""信号导出器 — 适配打板键盘工具 + 同花顺下单

生成三种格式:
  1. 打板工具候选池 (.txt) — 代码列表，导入打板工具监控池
  2. 同花顺自动下单 (.json) — 被 auto_trade_bridge.py 读取执行
  3. 人工操作清单 (.md)  — 打印出来贴在屏幕旁，对着敲键盘

用法:
  python export_for_daban.py              # 导出今日信号
  python export_for_daban.py --auto       # 同时生成自动下单指令
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")
import pickle, json, os, numpy as np
from datetime import datetime
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
NAMES_FILE = r"d:\quant_framework\stock_names.json"

# ═══════════════════ 加载 ═══════════════════
with open(CACHE, "rb") as f: data = pickle.load(f)
names = {}
if os.path.exists(NAMES_FILE):
    with open(NAMES_FILE, "r", encoding="utf-8") as f:
        names = json.load(f)

latest_date = max(d for sd in data.values() for d in sd["dates"] if len(str(d))==8 and d<=20260601)
date_str = f"{str(latest_date)[:4]}-{str(latest_date)[4:6]}-{str(latest_date)[6:8]}"

# ═══════════════════ 扫描 ═══════════════════
MIN_PRICE, MIN_TURNOVER, TB_MIN, MIN_SCORE = 5, 5e7, 0.5, 60

signals = []
for sym, sd in data.items():
    if latest_date not in sd["dates"]: continue
    i = sd["dates"].index(latest_date)
    if i < 250: continue
    p = sd["close"][i]
    if p < MIN_PRICE: continue
    vs = sd["volume"][max(0,i-20):i+1]
    avg_v = float(np.mean(vs)) if len(vs) > 0 else 0
    if avg_v * p < MIN_TURNOVER: continue

    fv = sd["factors"]
    def g(n): arr = fv.get(n,[]); return float(arr[i]) if arr is not None and i<len(arr) and not np.isnan(float(arr[i])) else 0
    tb = g("trend_bottom")
    if tb < TB_MIN: continue
    add = g("add_position"); bp = g("bull_position")
    score = tb*60 + add*40 + bp*20
    if score < MIN_SCORE: continue

    atr_v = 0.01
    if i >= 14:
        h=np.array(sd["high"][i-14:i+1]); l=np.array(sd["low"][i-14:i+1])
        c_=np.array([sd["close"][i-15]]+sd["close"][i-14:i])
        tr=np.maximum(h-l,np.maximum(np.abs(h-c_),np.abs(l-c_)))
        atr_v=float(np.mean(tr))

    signals.append(dict(sym=sym, name=names.get(sym,""), price=p, score=score, tb=tb,
                        atr=atr_v, stop=round(p*.92,2), tp1=round(p*1.05,2),
                        buy_zone=round(p-atr_v*2,2), chg=round((p-sd["close"][i-1])/sd["close"][i-1]*100,2)))

signals.sort(key=lambda x: x["score"], reverse=True)

# ═══════════════════ 1. 打板工具候选池 (.txt) ═══════════════════
pool_file = r"d:\quant_framework\daban_pool.txt"
with open(pool_file, "w", encoding="utf-8") as f:
    f.write(f"# 量化信号候选池 {date_str}\n")
    f.write(f"# 总数: {len(signals)} 只\n\n")
    for s in signals[:50]:  # Top50 导入打板工具
        f.write(f"{s['sym']}\n")

print(f"[1] 打板工具候选池: {pool_file} ({min(50,len(signals))}只)")

# ═══════════════════ 2. 自动下单指令 (.json) ═══════════════════
auto_orders = []
for s in signals[:10]:  # Top10 生成自动下单
    auto_orders.append({
        "symbol": s["sym"],
        "action": "buy",
        "price": "zxjg",  # 最新价
        "volume": max(int(200000 / s["price"] / 100) * 100, 100),
        "reason": f"TB={s['tb']:.2f} Score={s['score']:.0f}",
        "stop_loss": s["stop"],
        "take_profit": s["tp1"],
    })

order_file = r"d:\quant_framework\live_orders.json"
with open(order_file, "w", encoding="utf-8") as f:
    json.dump({"orders": auto_orders, "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)

print(f"[2] 自动下单指令: {order_file} ({len(auto_orders)}条)")

# ═══════════════════ 3. 人工操作清单 (.md) ═══════════════════
md_file = r"d:\quant_framework\trade_plan.md"
with open(md_file, "w", encoding="utf-8") as f:
    f.write(f"# 交易计划 {date_str}\n\n")
    f.write(f"**候选池: {len(signals)} 只** | 过滤: 价>{MIN_PRICE} 成交>{MIN_TURNOVER/1e7:.0f}千万 TB>{TB_MIN}\n\n")
    f.write("## 🎯 优先操作 (Top 5)\n\n")
    f.write("| 代码 | 名称 | 现价 | 评分 | 买入区 | 止损 | 止盈1 |\n")
    f.write("|------|------|------|------|--------|------|-------|\n")
    for s in signals[:5]:
        f.write(f"| {s['sym']} | {s['name'][:8]} | {s['price']:.2f} | {s['score']:.0f} | <{s['buy_zone']:.2f} | {s['stop']:.2f} | {s['tp1']:.2f} |\n")
    f.write(f"\n## 📋 备选池 (Top 6-30)\n\n")
    f.write("| 代码 | 名称 | 现价 | 评分 | 买入区 |\n")
    f.write("|------|------|------|------|--------|\n")
    for s in signals[5:30]:
        f.write(f"| {s['sym']} | {s['name'][:8]} | {s['price']:.2f} | {s['score']:.0f} | <{s['buy_zone']:.2f} |\n")

print(f"[3] 人工交易清单: {md_file}")
print(f"\n  ✅ 导出完成!")

# ═══════════════════ 4. 打印摘要 ═══════════════════
print(f"\n{'='*60}")
print(f"  Top 10 打板信号")
print(f"{'='*60}")
print(f"  {'代码':<8} {'名称':<10} {'现价':>7} {'评分':>6} {'买入区':>8} {'止损':>8} {'止盈':>8}")
for s in signals[:10]:
    print(f"  {s['sym']:<8} {s['name'][:10]:<10} {s['price']:>7.2f} {s['score']:>6.0f} {s['buy_zone']:>8.2f} {s['stop']:>8.2f} {s['tp1']:>8.2f}")
