"""V5 A股规则测试: T+1 + 防重 — 独立实例"""
import sys, os
os.environ['STREAMLIT_WATCH'] = 'false'
sys.path.insert(0, r'D:\quant_framework')
sys.path.insert(0, r'D:\quant_framework\src')
from paper_engine import PaperAccount
from datetime import datetime

# 创建独立测试引擎
p = PaperAccount()
p._meta = {}
p._trades = []
p._broker._positions = {}
p._broker._cash = 1_000_000
p._daily_trade_count = 0
p._daily_buy_count = 0
p._consecutive_losses = 0

today = datetime.now().strftime("%Y-%m-%d")

print("=" * 50)
print("  V5 A股规则测试")
print("=" * 50)

# ── T+1: 买入后立刻卖出应拒绝 ──
print("\n--- T+1 ---")
r = p.place_order('sh600406', 'buy', 22.0, 100)
print(f"  BUY: {r.get('success')} {r.get('error','')}")
r = p.place_order('sh600406', 'sell', 22.0, 100)
print(f"  SELL: {r.get('success')} {r.get('error','')}")
t1 = not r.get("success") and ("T+1" in str(r.get("error","")) or "无源" in str(r.get("error","")))

# ── E303: 同票今日已买→再买应拦截 ──
print("\n--- E303防重 ---")
r = p.place_order('sh600089', 'buy', 19.0, 100)
print(f"  首次: {r.get('success')}")
# 模拟当天已买记录
p._trades.append({"symbol":"sh600089","side":"buy","price":19.0,"qty":100,
    "date":today,"time":"09:30:00","type":"auto"})
r = p.place_order('sh600089', 'buy', 19.0, 100)
print(f"  二次: {r.get('success')} {r.get('error','')}")
t2 = not r.get("success")

# ── 非A股过滤 ──
print("\n--- 过滤 ---")
r = p.place_order('sh510050', 'buy', 2.5, 100)
print(f"  ETF: {r.get('success')} {r.get('error','')}")
t3 = not r.get("success")

print("\n" + "=" * 50)
print(f"  T+1:  {'PASS' if t1 else 'FAIL'}")
print(f"  防重: {'PASS' if t2 else 'FAIL'}")
print(f"  过滤: {'PASS' if t3 else 'FAIL'}")
print("=" * 50)
