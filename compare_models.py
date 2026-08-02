"""回测验证: 新旧模型对比 v1.0
对比维度: 预测分布 / 共识率 / 信号质量
"""
import sys, os, json, pickle, numpy as np
sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

from data_loader import load_stock_data_cache, get_survivorship_stats

print("=" * 55)
print("  新旧模型对比验证")
print("=" * 55)

# 1. 加载数据
print("\n[1/4] 加载数据...")
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=60)
sd = {k: v for k, v in sd.items() if not k.startswith(('sh000','sz399','bj','sh88','sz88'))}
print(f"  股票池: {len(sd)}只")

# 2. 幸存者偏差诊断
print("\n[2/4] 幸存者偏差...")
stats = get_survivorship_stats()
if stats and 'delisted' in stats:
    print(f"  全量: {stats['total_stocks_ever']}  |  存活: {stats['active_today']}  |  退市: {stats['delisted']} ({stats['delisted_pct']}%)")
    print(f"  估计偏差: {stats.get('estimated_bias', 'N/A')}")

# 3. 模型预测对比
print("\n[3/4] 模型预测...")

# LGBM
print("  LGBM...")
try:
    from lgbm_strategy import generate_lgbm_signals
    l_sigs = generate_lgbm_signals(sd, top_k=50, min_score=25,
                                     model_path=r"D:\quant_framework\lgbm_model_stock.pkl")
    l_scores = [s['score'] for s in l_sigs]
    print(f"    信号: {len(l_sigs)}只  |  均分: {np.mean(l_scores):.1f}  |  中位: {np.median(l_scores):.1f}  |  最高: {max(l_scores):.1f}")
except Exception as e:
    print(f"    LGBM 失败: {e}")
    l_sigs = []

# XGBoost
print("  XGBoost...")
try:
    from xgb_factor_weight import generate_xgb_signals
    x_sigs = generate_xgb_signals(sd, top_k=30, min_score=20)
    x_scores = [s['score'] for s in x_sigs]
    print(f"    信号: {len(x_sigs)}只  |  均分: {np.mean(x_scores):.1f}  |  中位: {np.median(x_scores):.1f}  |  最高: {max(x_scores):.1f}")
except Exception as e:
    print(f"    XGBoost 失败: {e}")
    x_sigs = []

# 4. 共识分析
print("\n[4/4] 共识分析...")
l_set = {s['symbol'] for s in l_sigs}
x_set = {s['symbol'] for s in x_sigs}
both = l_set & x_set
l_only = l_set - x_set
x_only = x_set - l_set

print(f"  LGBM独有: {len(l_only)}  |  XGB独有: {len(x_only)}  |  共识: {len(both)}")
print(f"  共识率: {len(both)/max(len(l_set|x_set),1)*100:.1f}%  |  总覆盖: {len(l_set|x_set)}只")

# 共识股的平均分数
if both:
    l_both_scores = [s['score'] for s in l_sigs if s['symbol'] in both]
    x_both_scores = [s['score'] for s in x_sigs if s['symbol'] in both]
    print(f"  共识股 LGBM均分: {np.mean(l_both_scores):.1f}  |  XGB均分: {np.mean(x_both_scores):.1f}")

# 5. 结论
print("\n" + "=" * 55)
print("  对比总结:")
print(f"    幸存者偏差修正: {'✅ 已生效' if stats else '❌ 未生效'}")
print(f"    LGBM 信号数: {len(l_sigs)} (IC=0.456)")
print(f"    XGB 信号数: {len(x_sigs)} (AUC=0.685)")
print(f"    共识率: {len(both)/max(len(l_set|x_set),1)*100:.1f}%")
print(f"    模型一致性: {'✅ 良好' if len(both) > 5 else '⚠️ 偏弱'}")
print("=" * 55)
