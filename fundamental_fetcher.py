"""基本面数据获取 — 股息率/PE/行业 (蓝图 v3.0 S1-2)

数据来源优先级: Westock API → 本地缓存 → 空
"""

import json
import os
import logging

logger = logging.getLogger(__name__)

FUNDAMENTAL_CACHE_FILE = r"D:\quant_framework\fundamental_cache.json"

# 行业波动率分类 (高波动行业在防守策略中被排除)
HIGH_VOL_INDUSTRIES = ["光伏", "半导体", "新能源汽车", "AI", "军工", "芯片", "锂电池"]


def get_fundamental(stock_code: str) -> dict:
    """获取单只股票基本面数据。

    Returns:
        {dividend_yield, pe_ratio, pb_ratio, industry, market_cap, roe}
    """
    cache = _load_cache()
    return cache.get(stock_code, {})


def get_fundamental_batch(stock_codes: list[str]) -> dict[str, dict]:
    """批量获取基本面数据。"""
    cache = _load_cache()
    return {code: cache.get(code, {}) for code in stock_codes}


def _load_cache() -> dict:
    """加载本地缓存。"""
    if os.path.exists(FUNDAMENTAL_CACHE_FILE):
        try:
            with open(FUNDAMENTAL_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"基本面缓存读取失败: {e}")
    return {}


def update_cache(data: dict[str, dict]) -> None:
    """更新本地缓存 (合并模式)。"""
    cache = _load_cache()
    cache.update(data)
    os.makedirs(os.path.dirname(FUNDAMENTAL_CACHE_FILE), exist_ok=True)
    try:
        with open(FUNDAMENTAL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"基本面缓存已更新: {len(cache)} 只股票")
    except IOError as e:
        logger.error(f"基本面缓存写入失败: {e}")


def get_high_vol_industries() -> list[str]:
    """获取高波动行业列表 (防守策略排除)。"""
    return HIGH_VOL_INDUSTRIES
