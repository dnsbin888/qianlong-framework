"""Quick test: does _factor_low return varied values?"""
import sys, numpy as np
sys.path.insert(0, r"D:\quant_web")
from data_loader import load_stock_data_from_cache

sd = load_stock_data_from_cache()
sd = {k:v for k,v in sd.items() if not k.startswith(('sh000','sz399','bj'))}

from full_market_ic import _factor_low

vals = []
for sym, df in list(sd.items())[:200]:
    v = _factor_low(df)
    if v is not None:
        vals.append(v)

print(f"Valid: {len(vals)}/200")
if len(vals) >= 5:
    print(f"Range: {min(vals):.1f} - {max(vals):.1f}")
    print(f"Mean: {np.mean(vals):.1f}, Std: {np.std(vals):.1f}")
    print(f"Unique values: {len(set(vals))}")
    if len(set(vals)) <= 1:
        print("!!! ALL VALUES IDENTICAL - No IC possible")
else:
    print("!!! TOO FEW VALID - Need to relax filters")
