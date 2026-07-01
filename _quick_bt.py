"""Quick debug: score distribution"""
import sys, numpy as np
sys.path.insert(0, r"D:\quant_web"); sys.path.insert(0, r"D:\quant_framework")
from data_loader import load_stock_data_from_cache
from factor_registry import get_all_compute_fns
import json

sd = load_stock_data_from_cache()
compute = get_all_compute_fns()
print("Factors loaded:", list(compute.keys()))
all_s = json.load(open(r"D:\quant_framework\user_customizations\user_strategies.json"))["strategies"]
strat = [s for s in all_s if s["name"].startswith("九")][0]
factors = strat["factors"]
print(f"Factors in strat: {[(f['name'],f['weight']) for f in factors]}")

dates = sorted(set(str(ts)[:10] for ts in list(sd.values())[100].index))[-20:]
date = dates[-3]
print(f"Testing date: {date}")

scores = []
for sym, df in list(sd.items())[:300]:
    try:
        if date not in [str(ts)[:10] for ts in df.index]: continue
        idx = df.index.get_loc(pd.Timestamp(date))
        if idx < 20: continue
        past = df.iloc[max(0,idx-60):idx+1]
        if len(past) < 20: continue
        ts=0.0; tw=0.0; vc=0
        for fc in factors:
            fn = compute.get(fc["name"])
            if not fn: continue
            try:
                val = fn(past)
            except Exception:
                val = None
            if val is None: continue
            tw += fc["weight"]; ts += val * fc["weight"]; vc += 1
        if vc >= 2 and tw > 0:
            scores.append(ts/tw)
    except Exception:
        pass

print(f"Valid stocks: {len(scores)}")
if scores:
    s = np.array(scores)
    print(f"Range: {s.min():.1f} - {s.max():.1f}")
    print(f"Mean: {s.mean():.1f}, Std: {s.std():.1f}")
    for t in [70, 60, 50, 40]:
        print(f"  >={t}: {sum(1 for x in s if x>=t)}")
