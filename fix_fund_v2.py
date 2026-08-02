"""复活 fund_v2 + 验证"""
import json, sys
sys.path.insert(0, r"D:\quant_framework")

# 1. 更新注册表
try:
    with open(r"D:\quant_framework\factor_registry.json", "r", encoding="utf-8") as f:
        reg = json.load(f)
    for f in reg["factors"]:
        if f["name"] == "fund_v2":
            f["retired_reason"] = ""
            f["ic_5d"] = None
            f["ic_10d"] = None
            f["ic_20d"] = None
            f["ic_verified_days"] = 0
            f["note"] = "v3重写: 价量背离+涨跌量比+极端收益, 反面因子(高分=差)"
            print("[1] fund_v2 已复活")
            break
    with open(r"D:\quant_framework\factor_registry.json", "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
except PermissionError:
    print("[1] ⚠️ 文件被锁, 先停Flask再跑")

# 2. 验证新公式
from full_market_ic import _factor_fund
import pandas as pd, numpy as np
np.random.seed(42)
df = pd.DataFrame({
    'close': np.random.randn(60).cumsum() + 100,
    'volume': np.random.randn(60) * 1000 + 10000
})
result = _factor_fund(df)
print(f"[2] fund_v2 测试: {result:.1f} (0-100, 50=中性)")
print("Done")
