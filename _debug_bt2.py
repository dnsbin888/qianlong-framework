import sys; sys.path.insert(0, r'D:\quant_web'); sys.path.insert(0, r'D:\quant_framework')
from data_loader import load_stock_data_from_cache
from factor_registry import get_all_compute_fns
import json

# Check 1: factor functions
fn = get_all_compute_fns()
print("Factor functions:", list(fn.keys()))

# Check 2: strategy config
sp = r"D:\quant_framework\user_customizations\user_strategies.json"
s = json.load(open(sp))
s9 = next((x for x in s["strategies"] if "V1" in x["name"]), None)
print("Strategy:", s9["name"] if s9 else "NOT FOUND")
print("Factors:", [(f["name"], f["weight"]) for f in s9["factors"]] if s9 else "N/A")

# Check 3: missing fns
for fc in s9["factors"]:
    if fc["name"] not in fn:
        print(f"  MISSING: {fc['name']}")

# Check 4: test one stock
stock_data = load_stock_data_from_cache()
sym = list(stock_data.keys())[100]  # random stock
df = stock_data[sym]
print(f"Test stock: {sym}, rows: {len(df)}")
for fc in s9["factors"][:3]:
    f = fn.get(fc["name"])
    if f:
        val = f(df)
        print(f"  {fc['name']}: {val}")
    else:
        print(f"  {fc['name']}: FUNCTION NOT FOUND")
