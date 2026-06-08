"""Technical indicators — a facade over pandas-ta with caching.

Provides convenient methods for common indicators used in strategies.
All methods return pandas DataFrames/Series for direct use in conditions.

Indicator calculations are cached per (symbol, period, params) key
to avoid redundant computation across strategies sharing a data feed.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from typing import Any

import pandas as pd

from quant_framework.core.types import Symbol


class IndicatorCache:
    """LRU cache for computed indicators.

    Evicts the least-recently-used entries when capacity is exceeded.
    Default maxsize is 10,000 entries — suitable for multi-symbol production use.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self.maxsize = maxsize
        self._cache: OrderedDict[str, pd.DataFrame | pd.Series] = OrderedDict()

    @staticmethod
    def _make_key(symbol: str, indicator: str, **params: Any) -> str:
        """Create a stable cache key from indicator name and parameters."""
        raw = json.dumps({"symbol": symbol, "indicator": indicator, **params}, sort_keys=True)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, symbol: str, indicator: str, **params: Any) -> pd.DataFrame | pd.Series | None:
        """Get cached indicator result, or None.

        Moves the entry to the end (most-recently-used) on hit.
        """
        key = self._make_key(symbol, indicator, **params)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(
        self, symbol: str, indicator: str, result: pd.DataFrame | pd.Series, **params: Any
    ) -> None:
        """Store indicator result in cache.

        Evicts the least-recently-used entry if at capacity.
        """
        key = self._make_key(symbol, indicator, **params)
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = result
        while len(self._cache) > self.maxsize:
            self._cache.popitem(last=False)  # Pop LRU (first item)

    def clear(self) -> None:
        """Clear all cached indicators."""
        self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)

    @property
    def size(self) -> int:
        return len(self._cache)


# Global indicator cache shared across strategies (optional — each strategy
# can also have its own cache via BaseStrategy._indicator_cache)
_global_indicator_cache = IndicatorCache()


def compute_sma(kline_df: pd.DataFrame, period: int) -> pd.Series:
    """Simple Moving Average."""
    return kline_df["close"].rolling(window=period).mean()


def compute_ema(kline_df: pd.DataFrame, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return kline_df["close"].ewm(span=period, adjust=False).mean()


def compute_macd(
    kline_df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD indicator.

    Returns DataFrame with columns: dif, dea, hist
    """
    ema_fast = compute_ema(kline_df, fast)
    ema_slow = compute_ema(kline_df, slow)
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2  # Chinese convention: histogram = 2*(DIF-DEA)

    return pd.DataFrame({"dif": dif, "dea": dea, "hist": hist}, index=kline_df.index)


def compute_rsi(kline_df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = kline_df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_bollinger(
    kline_df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands.

    Returns DataFrame with columns: middle, upper, lower, bandwidth, percent_b
    """
    middle = compute_sma(kline_df, period)
    std = kline_df["close"].rolling(window=period).std()
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle
    percent_b = (kline_df["close"] - lower) / (upper - lower)

    return pd.DataFrame(
        {
            "middle": middle,
            "upper": upper,
            "lower": lower,
            "bandwidth": bandwidth,
            "percent_b": percent_b,
        },
        index=kline_df.index,
    )


def compute_atr(kline_df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = kline_df["high"], kline_df["low"], kline_df["close"]
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def compute_kdj(
    kline_df: pd.DataFrame,
    n: int = 9,
    m1: int = 3,
    m2: int = 3,
) -> pd.DataFrame:
    """KDJ indicator.

    Returns DataFrame with columns: k, d, j
    """
    low_min = kline_df["low"].rolling(window=n).min()
    high_max = kline_df["high"].rolling(window=n).max()
    rsv = ((kline_df["close"] - low_min) / (high_max - low_min + 1e-9)) * 100

    k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
    d = k.ewm(alpha=1 / m2, adjust=False).mean()
    j = 3 * k - 2 * d

    return pd.DataFrame({"k": k, "d": d, "j": j}, index=kline_df.index)


def compute_volume_ma(kline_df: pd.DataFrame, period: int = 5) -> pd.Series:
    """Volume moving average."""
    return kline_df["volume"].rolling(window=period).mean()


def compute_vwap(kline_df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (cumulative)."""
    cumulative_pv = (kline_df["close"] * kline_df["volume"]).cumsum()
    cumulative_vol = kline_df["volume"].cumsum()
    return cumulative_pv / cumulative_vol.replace(0, float("nan"))


# Aliases for compatibility with pandas-ta naming
sma = compute_sma
ema = compute_ema
macd = compute_macd
rsi = compute_rsi
boll = compute_bollinger
atr = compute_atr
kdj = compute_kdj
