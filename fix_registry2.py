import json, os
path = r"D:\quant_framework\factor_registry.json"

# 读
with open(path, "r", encoding="utf-8") as f:
    reg = json.load(f)

# 改
for fac in reg["factors"]:
    if fac["name"] == "fund_v2":
        fac["retired_reason"] = ""
        fac["ic_5d"] = None; fac["ic_10d"] = None; fac["ic_20d"] = None
        fac["ic_verified_days"] = 0
        fac["note"] = "v3: 反面因子"

# 写到临时文件
tmp = path + ".new"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(reg, f, ensure_ascii=False, indent=2)

# 强制替换
try:
    os.remove(path)
except:
    pass
os.rename(tmp, path)
print("Done — fund_v2 注册表已更新")
