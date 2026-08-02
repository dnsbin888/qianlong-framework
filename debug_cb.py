"""调试: 为什么 CB裁判训练 0 样本"""
import sys, os, random, pickle, pandas as pd, numpy as np
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

from data_loader import load_stock_data_cache
sd = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=90)
sd = {k: v for k, v in sd.items() if not k.startswith(('sh000', 'sz399', 'bj'))}
dates = sorted(set(str(d)[:10] for df in sd.values() if len(df) > 0 for d in df.index))[-40:]
print(f"dates: {len(dates)}")
d = dates[-1]
print(f"Test date: '{d}', ts={pd.Timestamp(d)}")

keys = list(sd.keys())
random.seed(42); random.shuffle(keys)

# 先查前3只股票的索引
for s in keys[:3]:
    df = sd.get(s)
    if df is not None and len(df) > 0:
        print(f"  {s}: idx[0]={df.index[0]}, idx[-1]={df.index[-1]}")
        try: print(f"    get_loc({d})={df.index.get_loc(pd.Timestamp(d))}")
        except: print(f"    get_loc({d})=NOT FOUND")

cnt, scored = 0, 0
from factor_registry import get_all_compute_fns
fns = get_all_compute_fns(exclude_ml=False)
fn = sorted(fns.keys())
m = pickle.load(open(r"D:\quant_framework\lgbm_model.pkl", "rb")).get("model")
from xgb_factor_weight import load_model as lx, _compute_stock_proxies, predict_scores
xm = lx()

for s in keys[:600]:
    df = sd.get(s)
    if df is None or len(df) < 20: continue
    try: idx = df.index.get_loc(pd.Timestamp(d))
    except: continue
    if idx < 20 or idx + 5 >= len(df): continue
    cnt += 1
    past = df.iloc[max(0, idx - 60):idx + 1]
    l, x = None, None
    try:
        row = [0.0] * len(fn)
        for i, fn2 in enumerate(fn):
            ff = fns.get(fn2)
            if ff:
                try: v = ff(past)
                except: v = None
                if v is not None and np.isfinite(v): row[i] = float(np.clip(v, -100, 100))
        if len(row) == len(fn):
            l = round(min(100, max(0, (float(m.predict(np.array([row]))[0]) + 0.05) * 1000)), 1)
    except: pass
    if xm:
        try:
            pr = _compute_stock_proxies({s: past})
            if pr:
                ss = predict_scores(xm, pr)
                if ss: x = ss[0]
        except: pass
    if l is not None or x is not None: scored += 1

print(f"checked: {cnt}, scored: {scored}")
