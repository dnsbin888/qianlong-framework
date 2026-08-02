import json, os
p = r"D:\quant_web\data\xgb_importance.json"
if os.path.exists(p):
    d = json.load(open(p, encoding="utf-8"))
    print(f"OK: {len(d)} items")
    for x in d[:3]: print(f"  {x['factor']}: {x['importance']}")
else:
    print("FILE MISSING — 重跑 xgb_importance.py")
