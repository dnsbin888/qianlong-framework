"""改前 vs 改后 实际对比"""
import sys, json, numpy as np
sys.path.insert(0, r"D:\quant_web")

st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
print(f"信号总数: {len(st)}\n")

# === 改前逻辑: 三模型平均+共识门控 ===
print("=== 改前: 打分融合+共识门控 ===")
auto_old = 0
for r in st:
    n = r.get('n_models', 0)
    cs = r.get('combined_score', 0)
    if n >= 2 and cs >= 65:
        auto_old += 1
print(f"  n≥2 + combined≥65 → 自动: {auto_old}只")
print(f"  n≥2占比: {sum(1 for r in st if r.get('n_models',0)>=2)}/{len(st)}")

# === 改后逻辑: LGBM单排序 ===
print("\n=== 改后: LGBM直接排序 (无共识) ===")
l_ranked = sorted([r for r in st if r.get('lgbm_score','')!=''],
                  key=lambda x: -(x.get('lgbm_score',0) or 0))
top30 = l_ranked[:30]
auto_new = sum(1 for r in top30 if (r.get('lgbm_score',0) or 0) >= 90)
print(f"  LGBM Top30: {len(top30)}只")
print(f"  LGBM均值: {np.mean([r.get('lgbm_score',0) or 0 for r in top30]):.1f}")
print(f"  auto_enabled: {sum(1 for r in st if r.get('auto_enabled'))}只")

# === 对比 ===
old_set = {r['symbol'] for r in st if r.get('n_models',0)>=2}
new_set = {r['symbol'] for r in top30}
print(f"\n=== 新旧Top对比 ===")
print(f"  改前通过(n≥2):        {len(old_set)}只")
print(f"  改后Top30(LGBM):      {len(new_set)}只")
print(f"  重叠:                 {len(old_set & new_set)}只")
print(f"  改前独有(被avg拉高的): {len(old_set - new_set)}只")
print(f"  改后独有(LGBM强但avg低): {len(new_set - old_set)}只")

# === 真实对比: 信号质量 ===
print(f"\n=== 信号质量对比 ===")
print(f"  改前自动候选: {auto_old}只 (被共识门控卡死)")
print(f"  改后自动候选: {sum(1 for r in st if r.get('auto_enabled'))}只 (排序+策略判)")
print(f"  改善:  {sum(1 for r in st if r.get('auto_enabled')) - auto_old:+d}只")

# === 行业分布 ===
sectors = {}
for r in top30:
    ind = r.get('industry','')
    sectors[ind] = sectors.get(ind, 0) + 1
print(f"\n=== LGBM Top30 行业分布 ===")
for ind, n in sorted(sectors.items(), key=lambda x: -x[1])[:8]:
    print(f"  {ind}: {n}只")
