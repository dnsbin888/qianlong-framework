"""DataManager — 统一数据访问层 (蓝图 v5.0 P3-02)

对标: vnpy OmsEngine / LEAN SecurityManager
包装现有数据源为统一接口，自动路由到最优数据源。

数据源优先级:
  1. QMT xtdata / 实时行情 (realtime_quotes._quote_cache)
  2. Westock (westock_factors)
  3. TDX .day 本地文件
  4. 价格缓存 (price_cache.json)

用法:
    from quant_framework.data.data_manager import DataManager
    dm = DataManager()
    q = dm.get_quote("600000")  # → {symbol, price, change_pct, volume, time}
    h = dm.get_history("600000", 60)  # → DataFrame
    f = dm.get_factor("600000", "trend_score")  # → float
    s = dm.status()  # → {source: {alive, ts, lag}}
"""
from __future__ import annotations

import os
import json
import logging
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("quant_framework.data_manager")


class DataManager:
    """统一数据访问。自动路由到最优可用数据源。"""

    def __init__(self):
        self._cache: dict = {}
        self._realtime_available = False
        self._realtime_lag: int = 999
        self._westock_available = False
        self._tdx_available = False
        self._last_status_check: float = 0
        self._check_sources()

    # ── 行情 ──

    def get_quote(self, symbol: str) -> dict | None:
        """获取实时行情。自动路由到最优数据源。

        Returns:
            {symbol, name, price, change_pct, volume, amount, high, low, time}
            或 None (所有数据源不可用)
        """
        code = self._clean(symbol)

        # 1. 实时行情 (QMT / 新浪 / 腾讯)
        q = self._from_realtime(code)
        if q:
            return q

        # 2. Westock
        q = self._from_westock(symbol)
        if q:
            return q

        # 3. 价格缓存
        q = self._from_price_cache(code)
        if q:
            return q

        return None

    def get_batch_quotes(self, symbols: list[str]) -> dict[str, dict]:
        """批量获取行情。"""
        result = {}
        for sym in symbols:
            q = self.get_quote(sym)
            if q:
                result[sym] = q
        return result

    # ── 历史数据 ──

    def get_history(self, symbol: str, days: int = 60):
        """获取历史K线 (从 stock_data)。

        Returns:
            DataFrame 或 None
        """
        try:
            import sys
            import pandas as pd
            sys.path.insert(0, r"D:\quant_web")
            from data_loader import load_stock_data_from_cache
            data = load_stock_data_from_cache()
            if data:
                df = data.get(symbol)
                if df is not None and len(df) >= days:
                    return df.tail(days)
        except Exception as e:
            logger.error(f"[DataManager] get_history({symbol}) failed: {e}")
        return None

    # ── 因子 ──

    def get_factor(self, symbol: str, factor_name: str) -> float | None:
        """获取指定因子值 (从 factor_registry / factor_cache)。"""
        try:
            from factor_registry import get_factor as _gf
            f = _gf(factor_name)
            if f and f.get("compute"):
                fn = None
                compute_path = f.get("compute", "")
                if compute_path:
                    import importlib
                    parts = compute_path.rsplit(".", 1)
                    if len(parts) == 2:
                        mod = importlib.import_module(parts[0])
                        fn = getattr(mod, parts[1], None)
                if fn and callable(fn):
                    # 需要 DataFrame 输入 — 从 history 获取
                    df = self.get_history(symbol, 120)
                    if df is not None:
                        result = fn(df)
                        if hasattr(result, 'iloc'):
                            return float(result.iloc[-1])
                        return float(result) if result else None
        except Exception as e:
            logger.error(f"[DataManager] get_factor({symbol}, {factor_name}) failed: {e}")
        return None

    # ── 健康检查 ──

    def status(self) -> dict:
        """各数据源状态。"""
        self._check_sources()
        now = time.time()
        return {
            "checked_at": datetime.now().strftime("%H:%M:%S"),
            "sources": {
                "realtime": {
                    "alive": self._realtime_available,
                    "lag": self._realtime_lag,
                },
                "westock": {
                    "alive": self._westock_available,
                },
                "tdx_local": {
                    "alive": self._tdx_available,
                },
                "price_cache": {
                    "alive": self._price_cache_exists(),
                },
            },
        }

    # ── 内部 ──

    def _check_sources(self):
        """检查各数据源可用性。"""
        now = time.time()
        if now - self._last_status_check < 30:  # 30秒内不重复检查
            return
        self._last_status_check = now

        # 实时行情
        try:
            from realtime_quotes import _quote_cache
            if _quote_cache and _quote_cache.get("data"):
                self._realtime_available = len(_quote_cache["data"]) > 100
                _ts = _quote_cache.get("ts", 0) or 0
                if _ts > 1000000000:  # 有效时间戳（2001年之后）
                    _lag = int(now - _ts)
                    self._realtime_lag = _lag if 0 <= _lag < 86400 else 999
                else:
                    self._realtime_lag = 999  # 时间戳无效
            else:
                self._realtime_available = False
        except Exception:
            self._realtime_available = False

        # Westock
        try:
            from westock_factors import get_quote
            self._westock_available = True
        except Exception:
            self._westock_available = False

        # TDX
        self._tdx_available = os.path.exists(r"D:\new_tdx\vipdoc")

    def _from_realtime(self, code: str) -> dict | None:
        """从实时行情获取。"""
        try:
            from realtime_quotes import _quote_cache
            if not _quote_cache or not _quote_cache.get("data"):
                return None
            q = _quote_cache["data"].get(code)
            if q:
                return {
                    "symbol": code,
                    "name": q.get("name", ""),
                    "price": float(q.get("close", q.get("price", 0)) or 0),
                    "change_pct": float(q.get("change_pct", 0) or 0),
                    "volume": float(q.get("volume", 0) or 0),
                    "amount": float(q.get("amount", 0) or 0),
                    "high": float(q.get("high", 0) or 0),
                    "low": float(q.get("low", 0) or 0),
                    "time": _quote_cache.get("ts", ""),
                    "source": "realtime",
                }
        except Exception:
            pass
        return None

    def _from_westock(self, symbol: str) -> dict | None:
        """从 Westock 获取。"""
        try:
            from westock_factors import get_quote
            q = get_quote(symbol)
            if q:
                price = 0
                for k in ("current_price", "close", "price"):
                    try:
                        price = float(q.get(k, 0) or 0)
                        if price > 0:
                            break
                    except (ValueError, TypeError):
                        continue
                if price > 0:
                    return {
                        "symbol": symbol,
                        "price": price,
                        "change_pct": float(q.get("change_pct", 0) or 0),
                        "source": "westock",
                    }
        except Exception:
            pass
        return None

    def _from_price_cache(self, code: str) -> dict | None:
        """从价格缓存获取。"""
        try:
            pf = r"d:\quant_framework\price_cache.json"
            if os.path.exists(pf):
                with open(pf, "r") as f:
                    pc = json.load(f)
                for k in (code, "sh" + code, "sz" + code):
                    if k in pc:
                        return {
                            "symbol": code,
                            "price": float(pc[k]),
                            "change_pct": 0,
                            "source": "cache",
                            "cached": True,
                        }
        except Exception:
            pass
        return None

    def _price_cache_exists(self) -> bool:
        return os.path.exists(r"d:\quant_framework\price_cache.json")

    @staticmethod
    def _clean(symbol: str) -> str:
        return symbol.replace("sh", "").replace("sz", "").replace("bj", "").replace("SH", "").replace("SZ", "")
