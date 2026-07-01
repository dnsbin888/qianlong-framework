"""Diagnose: sample some factors on a single date"""
import sys, pickle, gzip, numpy as np, pandas as pd
sys.path.insert(0, r"D:\quant_web")
from data_loader import load_stock_data_from_cache

sd = load_stock_data_from_cache()
# Filter A-shares
sd = {k: v for k, v in sd.items() if not k.startswith(('sh000','sz399','bj'))}
print(f"A-shares: {len(sd)}")

from full_market_ic import (
    _factor_trend_old, _factor_def, _factor_chase, _factor_chip,
    _factor_bull_old, _factor_mom_old, _factor_power_old, _factor_low
)

factors = {
    "trend": _factor_trend_old,
    "defense": _factor_def,
    "chase": _factor_chase,
    "chip": _factor_chip,
    "bull": _factor_bull_old,
    "momentum": _factor_mom_old,
    "low_v3": _factor_low,
}

# Pick a recent date
date_str = "2026-06-17"
valid = 0
total = 0
for sym, df in list(sd.items())[:200]:
    total += 1
    if date_str not in [str(ts)[:10] for ts in df.index]:
        continue
    idx = df.index.get_loc(pd.Timestamp(date_str)) if pd.Timestamp(date_str) in df.index else None
    if idx is None or idx < 60:
        continue
    past = df.iloc[max(0, idx - 60): idx + 1]
    for name, fn in factors.items():
        val = fn(past)
        if val is not None:
            valid += 1
            break
    if valid > 0 and valid % 20 == 0:
        print(f"  valid: {valid}/{total}")

print(f"\n{date_str}: {valid} valid / {total} checked")
print("If valid is near 0, factors are too strict")
print("\nCheck low_absorb_v3 specifically:")
for sym in list(sd.keys())[:10]:
    df = sd[sym]
    if date_str in [str(ts)[:10] for ts in df.index]:
        idx = df.index.get_loc(pd.Timestamp(date_str))
        past = df.iloc[max(0, idx-60): idx+1]
        v = _factor_low(past)
        print(f"  {sym}: low_v3={v}")
