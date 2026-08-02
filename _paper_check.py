"""模拟盘今日全貌"""
import json, sys
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')

print("="*60)
print("  模拟盘今日审计")
print("="*60)

acc = json.load(open(r"D:\quant_framework\paper_account.json", encoding='utf-8'))

# 基本
print(f"\n[账户]")
print(f"  现金: ¥{acc.get('cash',0):,.2f}")
print(f"  持仓: {len(acc.get('positions',{}))}只")
trades = acc.get('trades',[]) or []
today = '2026-07-13'
today_trades = [t for t in trades if str(t.get('date',''))[:10] == today]
print(f"  今日交易: {len(today_trades)}笔")

# 持仓
print(f"\n[当前持仓]")
for sym, pos in acc.get('positions',{}).items():
    cost = pos.get('avg_cost',0)
    qty = pos.get('qty',0)
    mkt = pos.get('market_value', pos.get('last_price',0)*qty)
    pnl = mkt - cost*qty
    print(f"  {sym}: {qty}股 成本¥{cost:.2f} 市值¥{mkt:.0f} 浮盈¥{pnl:+.0f}")

# 今日交易明细
print(f"\n[今日交易]")
for t in today_trades:
    side = t.get('side','?')
    sym = t.get('symbol','?')
    qty = t.get('qty',0)
    price = t.get('price',0)
    pnl = t.get('pnl', t.get('net_profit',0))
    reason = t.get('reason','手动')
    print(f"  {side} {sym} {qty}股 @¥{price:.2f} PnL=¥{pnl:+.0f} {reason[:40]}")

# 汇总
total_pnl = sum(t.get('pnl', t.get('net_profit',0)) or 0 for t in today_trades)
print(f"\n[汇总]")
print(f"  今日净盈亏: ¥{total_pnl:+,.0f}")
print(f"  重复卖出: {sum(1 for t in today_trades if t.get('reason','').count('T1')+t.get('reason','').count('T2')>1)}笔" if total_pnl else "  —")
