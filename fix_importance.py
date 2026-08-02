import pickle, json
path = r"D:\quant_framework\lgbm_model_stock.pkl"
with open(path, "rb") as f:
    d = pickle.load(f)
model = d.get("model")
factors = d.get("factors", [])
imp_raw = list(model.feature_importances_) if hasattr(model, 'feature_importances_') else []
imp = [{"factor": factors[i] if i < len(factors) else f"f{i}",
        "importance": round(float(imp_raw[i]), 4)}
       for i in range(len(imp_raw))]
imp.sort(key=lambda x: -x["importance"])
print(f"Factors: {len(factors)}, Importance: {len(imp)}")
for x in imp[:8]:
    print(f"  {x['factor']:25s} {x['importance']:.4f}")
# Save
with open(r"D:\quant_web\data\lgbm_importance.json", "w", encoding="utf-8") as f:
    json.dump(imp, f, ensure_ascii=False, indent=2)
print("Saved to lgbm_importance.json")
