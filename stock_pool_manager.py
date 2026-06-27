"""固定股票池管理 (蓝图 v3.0 E1-4)

解决进化算法随机抽样偏差，确保结果可复现。
默认: 核心池(沪深300) + 扩展池(中证500) = ~800只
"""

import json, os, logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

POOL_FILE = r"D:\quant_framework\stock_pool.json"
CACHE_DAYS = 90

POOL_SIZES = {
    "core": 300,                # 沪深300
    "extended": 500,            # 中证500
    "core_plus_extended": 800,  # 核心+扩展 (推荐)
    "full": 5000,               # 全市场 (不推荐进化使用)
}


def get_stock_pool(pool_type: str = "core_plus_extended", force_update: bool = False) -> list[str]:
    """获取固定股票池。

    Args:
        pool_type: 'core' | 'extended' | 'core_plus_extended' | 'full'
        force_update: 强制刷新

    Returns:
        股票代码列表
    """
    # 检查缓存
    if not force_update and os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            update_date = datetime.strptime(cache.get("update_date", "2000-01-01"), "%Y-%m-%d")
            if (datetime.now() - update_date).days < CACHE_DAYS:
                if cache.get("pool_type") == pool_type:
                    return cache.get("stocks", [])
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    # 生成股票池
    stocks = _generate_pool(pool_type)

    # 保存
    cache = {
        "update_date": datetime.now().strftime("%Y-%m-%d"),
        "pool_type": pool_type,
        "count": len(stocks),
        "stocks": stocks,
    }
    try:
        with open(POOL_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"股票池已更新: {pool_type} {len(stocks)}只")
    except IOError as e:
        logger.warning(f"股票池缓存写入失败: {e}")

    return stocks


def _generate_pool(pool_type: str) -> list[str]:
    """生成股票池。"""
    if pool_type == "full":
        return _load_from_stock_data(max_count=5000)

    if pool_type == "core":
        stocks = _load_index_components("hs300", 300)
        if stocks:
            return stocks
        return _load_from_stock_data(max_count=300)

    if pool_type == "extended":
        stocks = _load_index_components("zz500", 500)
        if stocks:
            return stocks
        return _load_from_stock_data(max_count=500)

    # core_plus_extended
    core = _load_index_components("hs300", 300)
    ext = _load_index_components("zz500", 500)
    combined = list(dict.fromkeys(core + ext))  # 去重保持顺序
    if combined:
        return combined[:800]
    return _load_from_stock_data(max_count=800)


def _load_index_components(index: str, expected: int) -> list[str]:
    """从Westock获取指数成分股。"""
    try:
        import subprocess
        cmd = f"westock-data index-components {index} 2>nul"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=15)
        if r.returncode == 0 and r.stdout.strip():
            data = json.loads(r.stdout)
            if isinstance(data, list) and len(data) > 10:
                return [s for s in data if isinstance(s, str)][:expected]
    except Exception:
        pass
    return []


def _load_from_stock_data(max_count: int = 800) -> list[str]:
    """从本地stock_data降级加载 (P0-2: parquet优先)。"""
    stocks = []
    import sys
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_from_cache
    data = load_stock_data_from_cache()
    if not data:
        # 兜底: 旧路径
        for path in [r"D:\quant_web\stock_data.parquet", r"D:\quant_web\stock_data.pkl.gz", r"D:\quant_web\stock_data.pkl"]:
            if not os.path.exists(path): continue
            try:
                if path.endswith('.parquet'):
                    from data_loader import load_stock_data_cache
                    data = load_stock_data_cache(path) or {}
                elif path.endswith(".gz"):
                    import gzip, pickle
                    data = pickle.load(gzip.open(path, "rb"))
                else:
                    import pickle
                    data = pickle.load(open(path, "rb"))
                break
            except: continue
    if data:
                data = pickle.load(open(path, "rb"))
            if isinstance(data, dict):
                stocks = list(data.keys())[:max_count]
                break
        except Exception:
            continue
    return stocks


def get_pool_info() -> dict:
    """获取当前股票池信息。"""
    if os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"update_date": "never", "pool_type": "none", "count": 0, "stocks": []}
