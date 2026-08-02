import json, os
path = r"D:\quant_framework\factor_registry.json"
# 清理残留tmp
for tmp in [path+".tmp", path+".tmp.*"]:
    try:
        for f in [f for f in os.listdir(os.path.dirname(path)) if f.startswith("factor_registry.json.tmp")]:
            os.remove(os.path.join(os.path.dirname(path), f))
            print(f"Deleted: {f}")
    except: pass

with open(path, "r", encoding="utf-8") as f:
    reg = json.load(f)
for fac in reg["factors"]:
    if fac["name"] == "fund_v2":
        fac["retired_reason"] = ""
        fac["ic_5d"] = None
        fac["ic_10d"] = None
        fac["ic_20d"] = None
        fac["ic_verified_days"] = 0
        fac["note"] = "v3: 价量背离+涨跌量比+极端收益, 反面因子"
with open(path, "w", encoding="utf-8") as f:
    json.dump(reg, f, ensure_ascii=False, indent=2)
print("Done")
