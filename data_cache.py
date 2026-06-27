"""数据缓存 — 全市场 stock_data 加载一次，所有模块共享。

行业标准: Preload, don't reload.
P0-2: parquet优先 (30MB vs 284MB gzip)
"""

import sys, os
sys.path.insert(0, r"D:\quant_web")

_cache = None


def get_stock_data() -> dict:
    """获取全市场数据（只加载一次，后续走缓存）。"""
    global _cache
    if _cache is not None:
        return _cache
    from data_loader import load_stock_data_from_cache
    _cache = load_stock_data_from_cache()
    if _cache:
        return _cache
    # 兜底: 旧路径
    import gzip, pickle
    for p in [r"D:\quant_web\stock_data.parquet", r"D:\quant_web\stock_data.pkl.gz", r"D:\quant_web\stock_data.pkl"]:
        if os.path.exists(p):
            if p.endswith('.parquet'):
                from data_loader import load_stock_data_cache
                _cache = load_stock_data_cache(p)
            elif p.endswith(".gz"):
                with gzip.open(p, "rb") as f: _cache = pickle.load(f)
            else:
                with open(p, "rb") as f: _cache = pickle.load(f)
            return _cache or {}
    return {}
