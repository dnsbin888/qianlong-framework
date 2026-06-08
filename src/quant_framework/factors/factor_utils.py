"""因子缓存读取辅助 — 支持按年度切片的因子安全读取。

P0-因子-01: 回测时按当前日期取对应年份的因子值，确保不会看到未来数据。

Usage:
    from quant_framework.factors.factor_utils import get_factor_for_date, get_factor_array_for_year

    # 方式1: 取单个日期的单个因子值
    tb = get_factor_for_date(cache_entry, date_int=20220315, factor_name="trend_bottom")

    # 方式2: 取某年份对应的完整因子数组（已与dates对齐，截断后填NaN）
    arr = get_factor_array_for_year(cache_entry, year=2022, factor_name="trend_bottom")

    # 方式3: 检查缓存是否包含分年因子
    if has_year_sliced_factors(cache_entry):
        ...
"""

from __future__ import annotations

import numpy as np

# 缓存中 factors_by_year 缺失时的哨兵年份
_FALLBACK_YEAR = 0


def has_year_sliced_factors(cache_entry: dict) -> bool:
    """判断缓存条目是否包含分年因子数据。"""
    return bool(cache_entry.get("factors_by_year"))


def available_years(cache_entry: dict) -> list:
    """返回缓存条目中可用的因子年份列表（升序）。"""
    fby = cache_entry.get("factors_by_year")
    if not fby:
        return []
    return sorted(fby.keys())


def get_factor_for_date(
    cache_entry: dict,
    date_int: int,
    factor_name: str,
    default: float = 0.0,
) -> float:
    """按日期安全地获取因子值 — 根据日期的年份选择对应的因子版本。

    Args:
        cache_entry: 缓存中单只股票的数据 dict
        date_int: 日期 (int, YYYYMMDD 格式，如 20220315)
        factor_name: 因子名称 ("trend_bottom", "add_position", "bull_position")
        default: 因子值缺失时的默认值

    Returns:
        因子值 (float)

    实现原理:
        对于日期 2022-03-15，查找 factors_by_year[2022][factor_name]，
        该因子是在数据截断至 2021 年底时计算的，确保不包含 2022 年及之后的数据。
        如果当前日期位置为 NaN（超出截断范围），向前找最近的非 NaN 值。
    """
    # 先找日期在 dates 数组中的位置
    dates = cache_entry.get("dates")
    if not dates:
        return default

    try:
        idx = dates.index(date_int)
    except ValueError:
        # 日期不在数据中，找最近的日期
        idx = _find_nearest_index(dates, date_int)
        if idx is None:
            return default

    # 确定年份
    year = int(str(date_int)[:4])

    # 尝试从分年因子中获取
    fby = cache_entry.get("factors_by_year")
    if fby and year in fby:
        arr = fby[year].get(factor_name)
        if arr is not None and idx < len(arr):
            val = arr[idx]
            if not _is_missing(val):
                return float(val)
            # 当前位置为 NaN，向前查找最近的非 NaN 值
            val = _find_last_valid(arr, idx)
            if val is not None:
                return float(val)

    # 回退到旧格式的 factors dict
    legacy = cache_entry.get("factors", {})
    arr = legacy.get(factor_name)
    if arr is not None and len(arr) > 0 and idx < len(arr):
        val = arr[idx]
        if not _is_missing(val):
            return float(val)
        val = _find_last_valid(arr, idx)
        if val is not None:
            return float(val)

    return default


def get_factor_array_for_year(
    cache_entry: dict,
    year: int,
    factor_name: str,
) -> np.ndarray | None:
    """获取某年份对应的完整因子数组。

    返回的数组已与 cache_entry["dates"] 对齐（长度一致），
    超出截断日期的位置为 NaN。

    Args:
        cache_entry: 缓存中单只股票的数据 dict
        year: 目标年份 (如 2022)
        factor_name: 因子名称

    Returns:
        np.ndarray 或 None
    """
    fby = cache_entry.get("factors_by_year")
    if fby and year in fby:
        arr = fby[year].get(factor_name)
        if arr is not None and len(arr) > 0:
            return arr

    # 回退: 找最近可用年份
    if fby:
        available = sorted(fby.keys())
        # 找 <= year 的最大年份
        best = None
        for y in available:
            if y <= year:
                best = y
            else:
                break
        if best is not None:
            arr = fby[best].get(factor_name)
            if arr is not None and len(arr) > 0:
                return arr

    # 最终回退到旧格式
    legacy = cache_entry.get("factors", {})
    arr = legacy.get(factor_name)
    if arr is not None and len(arr) > 0:
        return np.asarray(arr)

    return None


# ======================================================================
# 内部辅助
# ======================================================================

def _is_missing(val) -> bool:
    """检查值是否缺失。"""
    if val is None:
        return True
    try:
        return bool(np.isnan(val)) or bool(np.isinf(val))
    except (TypeError, ValueError):
        return False


def _find_last_valid(arr: np.ndarray, idx: int) -> float | None:
    """从 idx 位置向前查找最近的有效值。"""
    for i in range(idx, -1, -1):
        val = arr[i]
        if not _is_missing(val):
            return float(val)
    return None


def _find_nearest_index(dates: list, date_int: int) -> int | None:
    """在日期列表中找最接近 date_int 的索引（不超过 date_int）。"""
    best_idx = None
    for i, d in enumerate(dates):
        if d <= date_int:
            best_idx = i
        else:
            break
    return best_idx
