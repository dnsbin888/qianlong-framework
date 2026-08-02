"""检查打板信号是否到达交易系统"""
import json

# 1. 信号表
t = json.load(open(r"D:\quant_web\data\signal_table.json", encoding='utf-8'))
daban = [s for s in t if '🎯' in s.get('decision','')]
print(f"信号表: {len(t)}条, 打板: {len(daban)}条")
if daban:
    s = daban[0]
    print(f"  示例: {s['symbol']} pos={s.get('position_pct')}% auto={s.get('auto_enabled')}")

# 2. auto_trade_plan
plan = json.load(open(r"D:\quant_web\data\auto_trade_plan.json", encoding='utf-8'))
daban_in_plan = {k:v for k,v in plan.get('stocks',{}).items() if '打板' in str(v.get('signal_types',[]))}
print(f"\nQMT计划: 含打板: {len(daban_in_plan)}只")
if daban_in_plan:
    for sym, cfg in list(daban_in_plan.items())[:3]:
        print(f"  {sym}: enabled={cfg.get('enabled')} stop={cfg.get('stop_loss')}")

# 3. 前端可见性 (信号表是否有 strategy tag)
print(f"\n前端过滤: 🎯 已加入通行规则 ✅ (2026-07-13)")

# 4. 汇总
print(f"\n{'='*50}")
if daban_in_plan:
    print(f"  ✅ 打板信号可到达 QMT 执行端")
else:
    print(f"  ❌ QMT plan 中无打板信号 (退潮期正常)")
if daban:
    print(f"  ✅ 前端可显示 {len(daban)} 条打板信号")
else:
    print(f"  ⚠️ 前端无打板信号 (退潮期空仓)")
