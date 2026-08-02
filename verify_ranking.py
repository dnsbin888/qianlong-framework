"""验证: LGBM排序 vs 当前combined平均排序 哪个更有效"""
import json, sys
sys.path.insert(0, r"D:\quant_web")

st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))

# 只取ML信号
ml = [r for r in st if r.get('lgbm_score','') != '']

# 排序A: LGBM单独排
lgbm_rank = sorted(ml, key=lambda x: -(x.get('lgbm_score', 0) or 0))
# 排序B: combined当前排
combined_rank = sorted(ml, key=lambda x: -(x.get('combined_score', 0) or 0))

top = min(20, len(ml))

print(f"ML信号: {len(ml)}只\n")

# LGBM Top20 vs Combined Top20
l_top20 = {r['symbol'] for r in lgbm_rank[:top]}
c_top20 = {r['symbol'] for r in combined_rank[:top]}
overlap = l_top20 & c_top20

print(f"=== Top{top} 重叠度 ===")
print(f"LGBM独有:     {len(l_top20 - c_top20)}只")
print(f"Combined独有:  {len(c_top20 - l_top20)}只")
print(f"重叠:         {len(overlap)}只 ({len(overlap)/top*100:.0f}%)")

# 分析Combined独有的——是被XGB拉进来的?
if c_top20 - l_top20:
    print(f"\nCombined独有 (被XGB拉进Top20):")
    for r in combined_rank[:top]:
        if r['symbol'] in c_top20 - l_top20:
            print(f"  {r['symbol']:12s} LGBM={r.get('lgbm_score','-'):>6} XGB={r.get('xgb_score','-'):>6} R={r.get('ridge_score','-'):>6} combined={r.get('combined_score'):.0f}")

# 分析LGBM强但被Combined挤出Top20的
if l_top20 - c_top20:
    print(f"\nLGBM强但被Combined挤出Top20:")
    for r in lgbm_rank[:top]:
        if r['symbol'] in l_top20 - c_top20:
            print(f"  {r['symbol']:12s} LGBM={r.get('lgbm_score','-'):>6} XGB={r.get('xgb_score','-'):>6} R={r.get('ridge_score','-'):>6} combined={r.get('combined_score'):.0f}")

# XGB分数分布
x_scores = [r.get('xgb_score', 0) for r in ml if r.get('xgb_score', '') != '']
l_scores = [r.get('lgbm_score', 0) for r in ml if r.get('lgbm_score', '') != '']
import numpy as np
print(f"\n=== 分数分布 ===")
print(f"LGBM:  范围 {min(l_scores):.0f}-{max(l_scores):.0f}  均值 {np.mean(l_scores):.1f}  标准差 {np.std(l_scores):.1f}")
if x_scores:
    print(f"XGB:   范围 {min(x_scores):.0f}-{max(x_scores):.0f}  均值 {np.mean(x_scores):.1f}  标准差 {np.std(x_scores):.1f}")
else:
    print(f"XGB:   无有效分数 (只有30只有XGB分)")

# 结论
print(f"\n=== 结论 ===")
if len(overlap)/top > 0.8:
    print(f"LGBM和Combined Top{top}重叠度 {len(overlap)/top*100:.0f}% → Combined≈LGBM, XGB贡献微弱")
    print(f"建议: 直接用LGBM排序, 省掉XGB的combined平均")
elif len(overlap)/top > 0.5:
    print(f"重叠度 {len(overlap)/top*100:.0f}% → Combined有独立价值但不大")
    print(f"建议: 以LGBM为主排序, XGB作参考列")
else:
    print(f"重叠度 {len(overlap)/top*100:.0f}% → LGBM和Combined差异大")
    print(f"建议: 继续用combined, 但需要回测验证哪种排序更准")
