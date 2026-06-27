"""Westock因子获取 — 资金面+筹码面 (蓝图 v3.0 S1-6)

修复 fund_score/chip_score 在IC报告中为null的问题。
数据来源: Westock API → 本地缓存 → 默认值
"""

import json, os, logging, time

logger = logging.getLogger(__name__)

CACHE_FILE = r"D:\quant_framework\westock_factor_cache.json"
REQUEST_DELAY = 0.5  # API限流间隔


def fetch_westock_factors(stock_codes: list[str], date: str = None, force: bool = False) -> dict:
    """批量获取Westock因子。

    Args:
        stock_codes: 股票代码列表 (如 ['sh600519', 'sz000001'])
        date: 日期 (YYYY-MM-DD)
        force: 强制刷新 (忽略缓存)

    Returns:
        {code: {fund_score, chip_score, rating_score}}
    """
    results = {}
    cache = _load_cache() if not force else {}

    for idx, code in enumerate(stock_codes[:200]):  # 单次最多200只
        clean = code.replace("sh", "").replace("sz", "").replace("bj", "")
        cache_key = f"{clean}_{date}" if date else clean

        # 命中缓存
        if cache_key in cache and not force:
            results[clean] = cache[cache_key]
            continue

        # 调用数据源
        data = _fetch_single(clean)
        if data:
            results[clean] = data
            cache[cache_key] = data

        if idx > 0 and idx % 20 == 0:
            time.sleep(REQUEST_DELAY * 2)

    _save_cache(cache)
    return results


def _fetch_single(code: str) -> dict:
    """获取单只股票Westock因子。

    优先级: Westock API → 本地stock_data → 默认值
    """
    # 1. 尝试Westock API (如果可用)
    try:
        import subprocess
        cmd = f"westock-data factor {code} --limit 1 2>nul"
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            raw = json.loads(r.stdout)
            if isinstance(raw, dict):
                return {
                    "fund_score": _safe_float(raw.get("fund_score", raw.get("asfund_score", 50)), 50),
                    "chip_score": _safe_float(raw.get("chip_score", raw.get("chips_score", 50)), 50),
                    "rating_score": _safe_float(raw.get("rating_score", raw.get("total_score", 50)), 50),
                }
    except Exception:
        pass

    # 2. 降级: 从本地stock_data读取 (P0-2: 统一入口)
    try:
        import sys
        sys.path.insert(0, r"D:\quant_web")
        from data_loader import load_stock_data_from_cache
        data = load_stock_data_from_cache()
        if data and code in data:
            s = data[code]
            return {
                "fund_score": _safe_float(s.get("power_score", 50), 50),
                "chip_score": _safe_float(50),
                "rating_score": _safe_float(50),
            }
    except Exception:
        pass

    # 3. 默认值 (无数据源)
    return {"fund_score": 50, "chip_score": 50, "rating_score": 50}


def _load_cache() -> dict:
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(CACHE_FILE) or ".", exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logger.warning(f"Westock缓存写入失败: {e}")


def _safe_float(val, default=50.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
