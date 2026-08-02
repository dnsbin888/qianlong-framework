"""信号系统压力测试"""
import json, sys, numpy as np
sys.path.insert(0, r"D:\quant_web")

st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
print(f"信号总数: {len(st)}\n")

# 1. 模型覆盖
print("=" * 40)
print("1. 模型覆盖")
l_n = sum(1 for r in st if r.get('lgbm_score','') != '')
x_n = sum(1 for r in st if r.get('xgb_score','') != '')
r_n = sum(1 for r in st if r.get('ridge_score','') != '')
print(f"  LGBM: {l_n} | XGB: {x_n} | Ridge: {r_n}")
print(f"  并集(L∪X): {len(set(r['symbol'] for r in st if r.get('lgbm_score','') or r.get('xgb_score','')))}")

# 2. 分数分布
print("\n" + "=" * 40)
print("2. 分数分布")
l_scores = [r.get('lgbm_score',0) or 0 for r in st if r.get('lgbm_score','') != '']
x_scores = [r.get('xgb_score',0) or 0 for r in st if r.get('xgb_score','') != '']
if l_scores:
    print(f"  LGBM: min={min(l_scores):.0f} max={max(l_scores):.0f} mean={np.mean(l_scores):.1f} std={np.std(l_scores):.1f}")
if x_scores:
    print(f"  XGB:  min={min(x_scores):.0f} max={max(x_scores):.0f} mean={np.mean(x_scores):.1f} std={np.std(x_scores):.1f}")

# 3. 综合分来源
print("\n" + "=" * 40)
print("3. 综合分来源 (max=L/X?)")
l_dom = sum(1 for r in st if (r.get('lgbm_score',0) or 0) >= (r.get('xgb_score',0) or 0) and r.get('lgbm_score',''))
x_dom = sum(1 for r in st if (r.get('xgb_score',0) or 0) > (r.get('lgbm_score',0) or 0) and r.get('xgb_score',''))
print(f"  LGBM主导: {l_dom} | XGB主导: {x_dom}")

# 4. 自动交易分布
print("\n" + "=" * 40)
print("4. 自动交易来源")
auto = [r for r in st if r.get('auto_enabled')]
print(f"  自动: {len(auto)}只")
l_auto = sum(1 for r in auto if r.get('lgbm_score',''))
x_auto = sum(1 for r in auto if r.get('xgb_score',''))
print(f"  有LGBM: {l_auto} | 有XGB: {x_auto}")

# 5. 极端值检查
print("\n" + "=" * 40)
print("5. 极端值检查")
bad = [r for r in st if r.get('combined_score',0) > 100 or r.get('combined_score',0) < 0]
print(f"  combined异常: {len(bad)}只")
pos_bad = [r for r in st if r.get('position_pct',0) > 20 or r.get('position_pct',0) < 0]
print(f"  仓位异常: {len(pos_bad)}只")
dup = len(st) - len(set(r['symbol'] for r in st))
print(f"  重复symbol: {dup}只")

# 6. 数据完整性
print("\n" + "=" * 40)
print("6. 字段完整性")
fields = ['symbol','name','industry','close','combined_score','position_pct','stop_loss','auto_enabled','consensus']
for f in fields:
    missing = sum(1 for r in st if r.get(f) is None)
    print(f"  {f}: {'✅' if missing==0 else '❌ ' + str(missing) + '条缺失'}")

# 7. LGBM / XGB 归因
print("\n" + "=" * 40)
print("7. 双模型贡献可视化 (Top10)")
for i, r in enumerate(sorted(st, key=lambda x: -(x.get('combined_score',0)))[:10]):
    l = r.get('lgbm_score',0) or 0
    x = r.get('xgb_score',0) or 0
    c = r.get('combined_score',0)
    src = 'L' if l >= x else 'X'
    print(f"  {i+1:2d}. {r['symbol']:12s} combined={c:.0f} ({src}) L={l:.0f} X={x:.0f}")

print("\n✅ 压力测试完成")
