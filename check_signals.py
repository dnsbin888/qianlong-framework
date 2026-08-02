"""验证信号数据 vs 前端表格对齐"""
import json, os

st = json.load(open(r"D:\quant_web\data\signal_table.json", encoding="utf-8"))
print(f"信号总数: {len(st)}\n")

# 通道来源统计
ml_n = sum(1 for r in st if r.get('decision','') in ('✅ 共振','📊 LGBM','📊 XGB','📊 Ridge','⚠️ 分歧'))
rev_n = sum(1 for r in st if r.get('consensus','') == '反转')
dab_n = sum(1 for r in st if r.get('consensus','') == '打板')
other_n = len(st) - ml_n - rev_n - dab_n
print(f"通道: ML={ml_n} 反转={rev_n} 打板={dab_n} 其他={other_n}")

# 比对前端表格列
print("\n--- 前端列 vs 数据对应 ---")
checks = [
    ("L列(lgbm_score)", all(r.get('lgbm_score','') != '' for r in st[:10] if r.get('lgbm_score','') != '')),
    ("X列(xgb_score)", any(r.get('xgb_score','') != '' for r in st)),
    ("R列(ridge_score)", any(r.get('ridge_score','') != '' for r in st)),
    ("止盈(take_profit)", any(r.get('take_profit','') != '' for r in st)),
    ("止损(stop_loss)", any(r.get('stop_loss','') != '' for r in st)),
    ("决策(decision)", all(r.get('decision','') != '' for r in st[:5])),
]
for label, ok in checks:
    print(f"  {'✅' if ok else '❌'} {label}")

# 样例3条
print("\n--- 样例 (前3条) ---")
for r in st[:3]:
    print(f"  {r['symbol']:12s} 综合={r.get('combined_score',0):.0f}  "
          f"L={r.get('lgbm_score','-')} X={r.get('xgb_score','-')} R={r.get('ridge_score','-')}  "
          f"{r.get('decision','?')}  "
          f"止损={r.get('stop_loss','-')} 止盈={r.get('take_profit','-')}")
