"""强制更新 factor_registry.json — 等 Flask 释放锁"""
import json, time, os

path = r"D:\quant_framework\factor_registry.json"

for attempt in range(10):
    try:
        with open(path, "r", encoding="utf-8") as f:
            reg = json.load(f)
        for f in reg["factors"]:
            if f["name"] == "fund_v2":
                f["retired_reason"] = ""
                f["ic_5d"] = None
                f["ic_10d"] = None
                f["ic_20d"] = None
                f["ic_verified_days"] = 0
                f["note"] = "v3重写: 价量背离+涨跌量比+极端收益, 反面因子(高分=差)"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        print(f"[{attempt+1}] ✅ fund_v2 注册表已更新")
        break
    except PermissionError:
        print(f"[{attempt+1}] 文件锁, 1秒后重试...")
        time.sleep(1)
else:
    print("❌ 10次重试失败, 请手动停Flask后重跑")
