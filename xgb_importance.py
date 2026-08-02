import json, sys
sys.path.insert(0, r"D:\quant_framework")
from xgboost import XGBRegressor

model = XGBRegressor()
model.load_model(r"D:\quant_framework\xgb_model.json")

# 获取特征重要性
imp = model.feature_importances_
factors = ["trend_score","defensive_v2","qmt_composite","chase_v2",
           "chip_v2","momentum_score","bull_line","fund_v2"]

result = []
for i, (fname, val) in enumerate(zip(factors, imp)):
    result.append({"factor": fname, "importance": round(float(val), 4)})
result.sort(key=lambda x: -x["importance"])

print("XGBoost 8因子重要性:")
for r in result:
    bar = "█" * int(r["importance"] * 50)
    print(f"  {r['factor']:20s} {r['importance']:.4f} {bar}")

# Save
json.dump(result, open(r"D:\quant_web\data\xgb_importance.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("\nSaved to xgb_importance.json")
