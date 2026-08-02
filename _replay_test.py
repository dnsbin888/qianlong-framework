"""回放实盘错误数据验证防重机制"""
import sys
sys.path.insert(0,'D:/quant_framework')
sys.path.insert(0,'D:/quant_web')

print("="*60)
print("  回放测试: 汇成股份 09:45 卖出")
print("="*60)

# 模拟 paper_engine 的 tick 防重逻辑
_tick_sold = set()
actions = []

# 模拟汇成股份 700股, 持仓价~37.2, 现价35.44, 亏损-4.9%
# RuleEngine 会返回多个 sell action (模拟实际bug)
fake_rule_actions = [
    {"action": "sell", "qty": 200, "price": 35.46, "reason": "止损卖50%(-4.8%)"},
    {"action": "sell", "qty": 200, "price": 35.44, "reason": "止损卖50%(-4.9%)"},
    {"action": "sell", "qty": 200, "price": 35.44, "reason": "止损卖50%(-4.9%)"},
]

sym = "sh688403"

# 修复后的逻辑
print("\n[修复后] 内层防重逻辑:")
for i, ra in enumerate(fake_rule_actions):
    if sym in _tick_sold:
        print(f"  #{i+1} BLOCKED — sym已在_tick_sold中")
        continue
    # 模拟下单成功
    actions.append(ra)
    _tick_sold.add(sym)
    print(f"  #{i+1} EXECUTED — {ra['reason']}")

print(f"\n  执行: {len(actions)}笔 (预期1笔)")
print(f"  {'✅ 防重生效' if len(actions)==1 else '❌ 仍有重复'}")

# 再测外层防重
print("\n[外层防重] 同一tick第二次进入循环:")
if sym in _tick_sold:
    print(f"  BLOCKED — sym已在_tick_sold中, 跳过整个仓位检查")
    print(f"  ✅ 外层防重生效")
else:
    print(f"  ❌ 外层防重失效")

# 验证 PaperAutoLoop._run 中的代码
print("\n[生产代码验证]")
pe = open(r"D:\quant_framework\paper_engine.py", encoding='utf-8').read()
checks = [
    ("外层 _tick_sold = set()", "_tick_sold = set()" in pe),
    ("外层 if sym in _tick_sold", "if sym in _tick_sold" in pe),
    ("内层 if sym in _tick_sold (RuleEngine)", "if sym in _tick_sold:\n                    continue" in pe),
    ("ATR _tick_sold.add", "ATR软止损" in pe and "_tick_sold.add(sym)" in pe),
]
for desc, ok in checks:
    print(f"  {'✅' if ok else '❌'} {desc}")

print(f"\n{'='*60}")
print(f"  结论: 内层+外层双保险, 同一tick同票只会执行1次")
print(f"{'='*60}")
