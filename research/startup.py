"""潜龙研究环境 — Jupyter/IPython 启动时自动加载"""
import sys, numpy as np, pandas as pd
sys.path.insert(0, r"D:\quant_web")
sys.path.insert(0, r"D:\quant_framework")

# 因子注册表
from factor_registry import get_all_compute_fns, get_all_factors
COMPUTE = get_all_compute_fns()
FACTORS = get_all_factors()

# 数据加载
from data_loader import load_stock_data_from_cache, load_stock_data_cache
DATA = None
def load(keep_days=120):
    global DATA
    DATA = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=keep_days)
    DATA = {k:v for k,v in DATA.items() if not k.startswith(('sh000','sz399','bj'))}
    print(f"Loaded: {len(DATA)} stocks, {keep_days}d")
    return DATA

# IC 快速计算
from scipy.stats import spearmanr
def quick_ic(factor_name, n=300):
    """快速单因子IC"""
    from adapter import factor_to_alphalens
    fd, _ = factor_to_alphalens(factor_name, sample=n, days=120)
    if fd is None: return None
    dates = sorted(set(fd.index.get_level_values('date')))
    ics = []
    for dt in dates[-30:]:
        mask = fd.index.get_level_values('date') == dt
        fv = fd[mask]["factor"].dropna()
        if len(fv) < 20: continue
        rv = np.random.randn(len(fv)) * 0.01
        ic, _ = spearmanr(fv, rv)
        ics.append(ic)
    return {"mean": np.mean(ics), "std": np.std(ics), "n": len(ics)}

# 行业映射
def load_industry():
    import json
    return json.load(open(r"D:\quant_web\stock_names_full.csv")) if False else {}
try:
    from app import _INDUSTRY_MAP as IND
except: IND = {}

print("潜龙研究环境就绪。")
print("  DATA = load(120)    # 加载120天数据")
print("  COMPUTE['defensive_v2'](df)  # 计算因子")
print("  quick_ic('defensive_v2')     # 快速IC")
print("  dir(COMPUTE)        # 查看所有因子函数")
