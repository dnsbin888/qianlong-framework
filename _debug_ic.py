"""Diagnose: why are all IC values null?"""
import pandas as pd
df = pd.read_parquet(r"D:\quant_web\stock_data.parquet")
print("Columns:", df.columns.tolist())
print("Rows:", len(df))
print("Sample:\n", df.head(2))

# Test factor computation on one stock
from full_market_ic import _factor_trend_old, _factor_low, load_data
data = load_data()
sym = list(data.keys())[0]
stock_df = data[sym]
print(f"\nTest stock: {sym}, rows: {len(stock_df)}")
print("Columns:", stock_df.columns.tolist())

v = _factor_trend_old(stock_df)
print(f"trend_score: {v}")

v2 = _factor_low(stock_df)
print(f"low_absorb_v3: {v2}")
