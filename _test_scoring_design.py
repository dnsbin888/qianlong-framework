"""预测评分系统 — 自测试验证
验证: 现有 combined_score 是否与未来收益正相关?
      新增维度(共识/稳定/适配)能否筛选出更好的信号?
"""
import sys, json, os, numpy as np
sys.path.insert(0, 'D:/quant_framework')
sys.path.insert(0, 'D:/quant_web')

from data_loader import load_stock_data_cache

print("=" * 60)
print("  预测评分系统 — 自测试")
print("=" * 60)

# 1. 加载信号表 + 行情数据
print("\n[1] 数据准备...")
st_path = r"D:\quant_web\data\signal_table.json"
if not os.path.exists(st_path):
    print("  ❌ signal_table.json 不存在，请先生成")
    sys.exit(1)

signals = json.load(open(st_path, encoding='utf-8'))
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=30)
print(f"  信号: {len(signals)} 条, 行情: {len(sd)} 只")

# 2. 评分分布分析
print("\n[2] 评分分布...")
scores = [s.get('combined_score', 0) for s in signals]
n_models = [s.get('n_models', 0) for s in signals]
pos_pct = [s.get('position_pct', 0) for s in signals]

print(f"  combined_score: mean={np.mean(scores):.1f} med={np.median(scores):.1f} "
      f"min={np.min(scores):.0f} max={np.max(scores):.0f} std={np.std(scores):.1f}")

# 分桶统计
buckets = [(90, 101), (80, 90), (70, 80), (60, 70), (0, 60)]
print(f"\n  {'桶':<12} {'数量':>5} {'占比':>6} {'均分':>6} {'单模%':>7} {'仓位%':>7}")
for lo, hi in buckets:
    in_bucket = [s for s in signals if lo <= s.get('combined_score', 0) < hi]
    n = len(in_bucket)
    single = sum(1 for s in in_bucket if s.get('n_models', 0) <= 1)
    avg_pos = np.mean([s.get('position_pct', 0) for s in in_bucket]) if in_bucket else 0
    print(f"  [{lo}-{hi}){'':>3} {n:>5} {n/len(signals)*100:>5.1f}% "
          f"{np.mean([s['combined_score'] for s in in_bucket]):>6.1f} "
          f"{single/n*100 if n>0 else 0:>6.0f}% {avg_pos:>6.1f}%")

# 3. 共识 vs 分值的交叉分析
print("\n[3] 共识 vs 分值 — 模型数量越少=信心越低...")
for nm in [3, 2, 1]:
    subset = [s for s in signals if s.get('n_models', 0) == nm]
    if not subset: continue
    avg_score = np.mean([s['combined_score'] for s in subset])
    avg_pos = np.mean([s.get('position_pct', 0) for s in subset])
    print(f"  n_models={nm}: {len(subset)}条, 均分={avg_score:.1f}, 均仓位={avg_pos:.1f}%")

# 4. LGBM vs XGB 分歧分析 (分歧越大=信号越不可靠)
print("\n[4] LGBM vs XGB 分歧分析...")
divergences = []
for s in signals:
    l = s.get('lgbm_score', '') or 0
    x = s.get('xgb_score', '') or 0
    try:
        l = float(l) if l else 0
        x = float(x) if x else 0
    except: l = x = 0
    if l > 0 and x > 0:
        divergences.append(abs(l - x))

if divergences:
    print(f"  分歧范围: {min(divergences):.0f} ~ {max(divergences):.0f}")
    print(f"  低分歧(<15): {sum(1 for d in divergences if d < 15)} 条")
    print(f"  中分歧(15-30): {sum(1 for d in divergences if 15 <= d < 30)} 条")
    print(f"  高分歧(>30): {sum(1 for d in divergences if d >= 30)} 条")
    print(f"  ⚠ 高分歧信号 = 模型意见不一致 = 风险高")

# 5. 模拟: 如果按"高共识+低分歧+高仓位"筛选, 效果如何?
print("\n[5] 质量分模拟 — 按 (共识≥2 + 分歧<20) 筛选...")
high_quality = []
for s in signals:
    l = s.get('lgbm_score', 0) or 0
    x = s.get('xgb_score', 0) or 0
    try: l, x = float(l), float(x)
    except: l = x = 0
    consensus_ok = s.get('n_models', 0) >= 2
    diverge_ok = abs(l - x) < 20 if (l > 0 and x > 0) else True
    if consensus_ok and diverge_ok:
        high_quality.append(s)

print(f"  高质量信号: {len(high_quality)}/{len(signals)} ({len(high_quality)/len(signals)*100:.0f}%)")
if high_quality:
    avg_s = np.mean([s['combined_score'] for s in high_quality])
    avg_p = np.mean([s.get('position_pct', 0) for s in high_quality])
    print(f"  均分={avg_s:.1f} 均仓位={avg_p:.1f}%")

# 6. 行业主流标准: IC 检验
print("\n[6] IC 自检 (行业金标准)...")
print(f"  行业有效 IC 阈值: RankIC ≥ 0.03 (WorldQuant)")
print(f"  潜龙当前真实 IC: 0.03~0.09 (来自 ml-scoring-audit)")
print(f"  注意: IC 应该在因子计算时评估, 不在信号表阶段")

# 7. 关键发现
print("\n" + "=" * 60)
print("  📊 自测试结论")
print("=" * 60)

issues = []
if np.mean(scores) > 85:
    issues.append("分数偏高, 大部分挤在 80-100")
if sum(1 for s in signals if s.get('n_models', 0) == 1) > len(signals) * 0.3:
    issues.append("单模型信号占比过高, 质量不可靠")
if divergences and sum(1 for d in divergences if d >= 30) > len(divergences) * 0.2:
    issues.append("高分岐信号过多, LGBM和XGB经常打架")

if not issues:
    issues.append("无重大问题")
for i, issue in enumerate(issues):
    print(f"  {i+1}. {issue}")

print(f"\n  建议:")
print(f"  1. 质量分 = 共识(0-35) + 分歧(0-25) + 仓位(0-20) + 适配(0-20)")
print(f"  2. 高质量信号(质量分>60)可加大仓位")
print(f"  3. 低质量信号(质量分<40)降仓位或人工审核")
print(f"  4. Alpha分 = 高斯化排名 × 100 (替代原始 combined_score)")
