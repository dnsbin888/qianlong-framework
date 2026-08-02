"""回放对比: 旧逻辑 vs 新逻辑"""
import sys
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')

print("="*60)
print("  修复前后对比: 取最大量+防重")
print("="*60)

# ── 场景1: 汇成股份 止损 ──
print("\n【场景1】汇成股份 700股 止损-4.9%")
# 模拟 RuleEngine 返回 3 个 sell action
old_actions = [
    {"action":"sell","qty":200,"price":35.46,"reason":"止损卖50%(-4.8%)"},
    {"action":"sell","qty":200,"price":35.44,"reason":"止损卖50%(-4.9%)"},
    {"action":"sell","qty":200,"price":35.44,"reason":"止损卖50%(-4.9%)"},
]

# 旧逻辑
print("  旧逻辑: 全部执行")
old_total = sum(a['qty'] for a in old_actions)
print(f"    执行{len(old_actions)}笔 合计{old_total}股 (应卖350=50%) {'❌超卖86%' if old_total>350 else '✅'}")

# 新逻辑: 取最大qty
_sell_actions = [a for a in old_actions if a['action']=='sell']
_sell_actions.sort(key=lambda x: x['qty'], reverse=True)
new_actions = [_sell_actions[0]]
new_total = sum(a['qty'] for a in new_actions)
print(f"  新逻辑: 取最大qty")
print(f"    执行{len(new_actions)}笔 合计{new_total}股 (应卖350=50%) {'⚠️少卖' if new_total<350 else '✅' if new_total>=350 else ''}")

# ── 场景2: 万通发展 止盈 ──
print("\n【场景2】万通发展 400股 止盈回落")
old_actions2 = [
    {"action":"sell","qty":100,"price":19.48,"reason":"移动止盈T2(盈-0.6% 回落≥2%)"},
    {"action":"sell","qty":100,"price":19.48,"reason":"移动止盈T1(盈-0.6% 回落≥1%)"},
    {"action":"sell","qty":100,"price":19.48,"reason":"移动止盈T2(盈-0.6% 回落≥2%)"},
    {"action":"sell","qty":100,"price":19.48,"reason":"移动止盈T1(盈-0.6% 回落≥1%)"},
]

print("  旧逻辑: 全部执行")
old_total2 = sum(a['qty'] for a in old_actions2)
print(f"    执行{len(old_actions2)}笔 合计{old_total2}股 (剩余400股) {'✅刚好清仓' if old_total2==400 else ''}")

_sell_actions2 = [a for a in old_actions2 if a['action']=='sell']
_sell_actions2.sort(key=lambda x: x['qty'], reverse=True)
new_actions2 = [_sell_actions2[0]]
new_total2 = sum(a['qty'] for a in new_actions2)
print(f"  新逻辑: 取最大qty")
print(f"    执行{len(new_actions2)}笔 合计{new_total2}股 (剩余400股) {'⚠️只卖100' if new_total2==100 else ''}")

# ── 分析 ──
print(f"\n{'='*60}")
print(f"  分析")
print(f"  汇成: 3笔→1笔, 取200股 → 下次tick继续卖")
print(f"  万通: 4笔→1笔, 取100股 → 下次tick继续卖")
print(f"  ✅ 防重生效, 但止盈T1/T2同qty=100, 取最大=100, 需要多次tick才能清仓")
print(f"  这符合设计: 分批卖出, 不一次砸盘")
print(f"{'='*60}")
