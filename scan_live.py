"""盘中实时信号扫描 — 同花顺实时行情 + 因子预筛 + 触发告警

原理:
  1. 盘前: 用昨日收盘数据预筛候选池 (2000→200只)
  2. 盘中: 订阅THS实时行情, 只盯候选池
  3. 触发: 价格回落到支撑位 → 告警
  4. 刷新: 每3秒更新一次报价

用法:
  python scan_live.py                    # 默认过滤
  python scan_live.py --min-score 80     # 只盯高分信号
"""

import sys, os, time, pickle
sys.path.insert(0, r"d:\quant_framework\src")
import numpy as np
import warnings; warnings.filterwarnings("ignore")

CACHE = r"d:\quant_framework\cache_ohlcv.pkl"
MIN_PRICE = 5
MIN_TURNOVER = 5e7
TB_MIN = 0.5
MIN_SCORE = 60

# ══════════════════════════════ 1. 盘前预筛 ══════════════════════════════
print("=" * 60)
print("  盘中实时信号扫描")
print("=" * 60)

print("\n[1] 加载昨日因子...")
with open(CACHE, "rb") as f:
    data = pickle.load(f)

# Find latest date (yesterday's close)
all_dates = set()
for sd in data.values():
    for d in sd["dates"]:
        if len(str(d)) == 8 and d <= 20260601:
            all_dates.add(d)
latest = max(all_dates)
date_str = f"{str(latest)[:4]}-{str(latest)[4:6]}-{str(latest)[6:8]}"
print(f"  数据日期: {date_str}")

# Pre-filter: find stocks with strong signals at yesterday's close
candidates = []
for sym, sd in data.items():
    if latest not in sd["dates"]: continue
    i = sd["dates"].index(latest)
    if i < 250: continue

    p = sd["close"][i]
    if p < MIN_PRICE: continue

    vs = sd["volume"][max(0,i-20):i+1]
    avg_v = float(np.mean(vs)) if len(vs) > 0 else 0
    if avg_v * p < MIN_TURNOVER: continue

    # P0-因子-01: 按日期取对应年份切片因子，杜绝未来函数
    from quant_framework.factors.factor_utils import get_factor_for_date
    tb = get_factor_for_date(sd, latest, "trend_bottom")
    if tb < TB_MIN: continue
    add = int(get_factor_for_date(sd, latest, "add_position"))
    bp = get_factor_for_date(sd, latest, "bull_position")
    score = tb*60 + add*40 + bp*20
    if score < MIN_SCORE: continue

    # ATR
    atr_v = 0.01
    if i >= 14:
        h_arr=np.array(sd["high"][i-14:i+1]); l_arr=np.array(sd["low"][i-14:i+1])
        c_arr=np.array([sd["close"][i-15]]+sd["close"][i-14:i])
        tr=np.maximum(h_arr-l_arr, np.maximum(np.abs(h_arr-c_arr), np.abs(l_arr-c_arr)))
        atr_v=float(np.mean(tr))

    candidates.append(dict(
        sym=sym, yesterday_close=p, score=score, tb=tb, atr=atr_v,
        stop=p*.92, tp1=p*1.05, tp2=p*1.10,
        support=p - atr_v*2,  # ATR支撑位(买入区域)
    ))

candidates.sort(key=lambda x: x["score"], reverse=True)
candidates = candidates[:200]  # 盯前200只
print(f"  预筛候选: {len(candidates)} 只 (全市场{len(data)}只)")

if not candidates:
    print("  无候选! 放宽参数重试")
    sys.exit(1)

# ══════════════════════════════ 2. 实时监控 ══════════════════════════════
print(f"\n[2] 启动实时监控...")
print(f"  订阅 {len(candidates)} 只股票行情...")

# Load stock names for display
import json
names = {}
if os.path.exists(r"d:\quant_framework\stock_names.json"):
    with open(r"d:\quant_framework\stock_names.json", "r", encoding="utf-8") as f:
        names = json.load(f)

# Initialize THS real-time API
try:
    from quant_framework.data.providers.ths import THSDataProvider
    provider = THSDataProvider()
    provider.connect()
    symbols = [c["sym"] for c in candidates]
    provider.subscribe_quote(symbols)
    print(f"  THS连接成功! 开始监控...")
except ImportError:
    print("  同花顺环境未就绪, 使用模拟模式演示")
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║  盘中扫描已就绪!                               ║")
    print("  ║                                               ║")
    print("  ║  实盘使用: 在同花顺Python环境中运行此脚本       ║")
    print("  ║  模拟演示: 以下为昨日收盘数据快照               ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print(f"  {'代码':<10} {'名称':<12} {'昨收':>8} {'评分':>6} {'TB':>6} {'建议买入区':>10} {'止损':>8}")
    print(f"  {'-'*10} {'-'*12} {'-'*8} {'-'*6} {'-'*6} {'-'*10} {'-'*8}")
    for c in candidates[:20]:
        name = names.get(c["sym"], "")[:10]
        print(f"  {c['sym']:<10} {name:<12} {c['yesterday_close']:>8.2f} {c['score']:>6.0f} "
              f"{c['tb']:>6.3f} {c['support']:>10.2f} {c['stop']:>8.2f}")
    print()
    print(f"  ...共{len(candidates)}只候选 ({', '.join(symbols[:5])}...)")
    sys.exit(0)

# Real-time monitoring loop
print(f"  {'代码':<10} {'名称':<10} {'现价':>7} {'涨跌':>7} {'昨收':>7} {'距支撑':>8} {'状态':<8}")
print(f"  {'-'*10} {'-'*10} {'-'*7} {'-'*7} {'-'*7} {'-'*8} {'-'*8}")

try:
    while True:
        changed = provider.wait_update(timeout=3.0)
        if not changed:
            continue

        quotes = provider.get_quote(symbols) if hasattr(provider, 'get_quote') else {}

        for sym in changed:
            c = next((x for x in candidates if x["sym"] == sym), None)
            if c is None: continue

            q = quotes.get(sym)
            if q is None: continue

            price = q.price if hasattr(q, 'price') else q.get("price", 0)
            yc = c["yesterday_close"]
            chg = (price - yc) / yc * 100 if yc > 0 else 0
            dist_to_support = (c["support"] - price) / c["support"] * 100

            # Status:  approaching support = buy zone
            if price <= c["support"]:
                status = "🔥买入区!"
            elif dist_to_support < 2:
                status = "⏳接近支撑"
            elif chg > 5:
                status = "📈已拉升"
            else:
                status = "⏳等待"

            name = names.get(sym, "")[:8]
            print(f"  {sym:<10} {name:<10} {price:>7.2f} {chg:>+6.2f}% {yc:>7.2f} {dist_to_support:>+7.1f}% {status:<8}")

            # Alert on buy signal
            if status == "🔥买入区!":
                print(f"  ╔════════════════════════════════════════╗")
                print(f"  ║ 🚨 买入信号! {sym} {name}              ║")
                print(f"  ║ 现价:{price:.2f} 止损:{c['stop']:.2f} 止盈:{c['tp1']:.2f}/{c['tp2']:.2f} ║")
                print(f"  ╚════════════════════════════════════════╝")

except KeyboardInterrupt:
    print("\n  监控结束。")

provider.disconnect()
