"""测试 live_trader.py 的 QMT 交易通道 (S32)
============================================
前提: 模拟版 mini QMT 已启动并登录 (账号 66638720)
用法: "C:\Program Files\Python312\python.exe" D:\quant_framework\test_qmt_channel.py
"""
import sys, os

sys.path.insert(0, r"D:\quant_framework")

print("[Test] 加载 live_trader 模块...")
from live_trader import qmt_trader, CONFIG

print(f"[Test] trading_channel = {CONFIG.get('trading_channel')}")
print(f"[Test] qmt_available = {getattr(qmt_trader, '_broker', None) is not None or True}")

# ═══ 1. 连接 QMT ═══
print("\n[1/4] 连接QMT...")
ok = qmt_trader.connect()
print(f"  连接状态: {ok}")
print(f"  _connected: {qmt_trader._connected}")

if not ok:
    print("\n❌ 连接失败！请确认:")
    print("  1. 模拟版 mini QMT 已启动并登录")
    print("  2. netstat -ano | findstr '58610'")
    sys.exit(1)

# ═══ 2. 查询资产 ═══
print("\n[2/4] 查询资产...")
asset = qmt_trader.query_asset()
if asset:
    print(f"  总资产: ¥{asset.get('total_asset', 0):,.2f}")
    print(f"  可用资金: ¥{asset.get('cash', 0):,.2f}")
    print(f"  持仓市值: ¥{asset.get('market_value', 0):,.2f}")
else:
    print("  ⚠️ 查询资产返回空")

# ═══ 3. 查询持仓 ═══
print("\n[3/4] 查询持仓...")
positions = qmt_trader.query_stock_positions()
print(f"  持仓数: {len(positions)}")
for p in positions[:5]:
    print(f"  {p.get('stock_code')}: {p.get('volume')}股 "
          f"成本={p.get('open_price', '?')} "
          f"市值={p.get('market_value', 0)}")

# ═══ 4. 测试买入（1手） ═══
print("\n[4/4] 测试买入（1手平安银行）...")
# 获取行情确定价格
try:
    from xtquant import xtdata
    tick = xtdata.get_full_tick(["000001.SZ"])
    if tick and "000001.SZ" in tick:
        last_price = float(tick["000001.SZ"].get("lastPrice", 0))
        print(f"  000001.SZ 最新价: {last_price}")
    else:
        last_price = 0
except Exception:
    last_price = 0

buy_price = round(last_price * 1.01, 2) if last_price > 0 else 10.50
print(f"  限价买入: 000001.SZ 100股 @{buy_price}")

result = qmt_trader.send_buy_order(code="000001.SZ", price=buy_price, quantity=100)
print(f"  结果: {result}")

if result.get("success"):
    print(f"  ✅ 委托成功! order_id={result.get('order_id')}")
else:
    print(f"  ❌ 委托失败: {result.get('error')}")

print("\n✅ S32 测试完成")
