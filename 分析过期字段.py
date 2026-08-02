"""分析过期字段对系统的实际影响"""
import json, os

# 1. 信号表实际decision值
st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
decs = {}
for r in st:
    d = r.get('decision', '?')
    decs[d] = decs.get(d, 0) + 1
print("=== 信号表 decision 字段分布 ===")
for k, v in sorted(decs.items(), key=lambda x: -x[1]):
    print(f"  {k}: {v}条")

# 2. 信号表有多少条有"共振"decision
res = [r for r in st if '共振' in str(r.get('decision',''))]
print(f"\n含'共振'decision: {len(res)}条 (旧共识概念, 新数据仍用这个词)")

# 3. TDX信号字段是否还在数据里
tdx_fields = ['signal_resonance', 'signal_final', 'signal_xg', 'signal_b1', 'signal_qlj', 'signal_ztxf']
has_tdx = False
for r in st:
    for f in tdx_fields:
        if r.get(f):
            has_tdx = True
            break
print(f"\nTDX信号字段(signal_resonance等): {'✅ 数据里仍有' if has_tdx else '❌ 已无数据'}")

# 4. quality_score是否还在
qs = [r for r in st if r.get('quality_score', 0) > 0]
print(f"\nquality_score字段: {len(qs)}条有值 (前端已不显示, 后端仍在计算)")

# 5. CB相关字段
cb = [r for r in st if r.get('cb_score', '') != '']
print(f"cb_score字段: {len(cb)}条有值 (CB已禁用)")

# 6. signal_card.js里sig.resonance筛选条件
print(f"\n=== 前端筛选影响 ===")
print(f"  '✅ 共振'按钮 → 筛选decision含'共振' → {len(res)}条匹配 (仍有效但不是旧语义)")
print(f"  '共振(≥2)'TDX筛选 → 查signal_resonance字段 → {'仍有数据' if has_tdx else '无效筛选'}")
print(f"  quality_score排序 → signal_card.js仍在用 → 应改成combined_score")
print(f"  CB字段 → cb_score=0 → 实际不影响显示")
