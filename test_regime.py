import sys; sys.path.insert(0,r'D:\quant_framework'); sys.path.insert(0,r'D:\quant_web')
from data_loader import load_stock_data_cache
from market_regime import detect_regime

sd = load_stock_data_cache(r'D:\quant_web\stock_data.parquet', keep_days=30)
keys = list(sd.keys())[:200]
valid = 0
for sym in keys:
    df = sd.get(sym)
    if df is not None and len(df) >= 20 and 'close' in df.columns:
        valid += 1
print(f'First 200 stocks: {valid} valid')
print(f'First 5 keys: {keys[:5]}')

r = detect_regime(sd)
print(f'Regime: {r["regime"]} scale={r["position_scale"]} conf={r["confidence"]}')
