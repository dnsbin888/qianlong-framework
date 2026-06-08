"""AKShare Data Provider Adapter.

AKShare is a free, open-source Python financial data interface.
Install: pip install akshare

Provides A-share daily K-line, real-time quotes, and more.
No API token required — data is scraped from public sources.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider


class AKShareDataProvider(DataProvider):
    """AKShare market data provider.

    Free, no-registration-required data source for Chinese markets.
    Supports daily K-line data. Best for research and backtesting.

    Usage:
        provider = AKShareDataProvider()
        provider.connect()
        bars = provider.get_kline(["600000"], "1d", 100)
    """

    def __init__(self) -> None:
        self._api: Any = None
        self._quote_cache: dict[Symbol, Quote] = {}
        self._subscriptions: set[Symbol] = set()
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import akshare as ak  # type: ignore[import-untyped]
            self._api = ak
            self._connected = True
        except ImportError:
            raise ImportError(
                "akshare not installed. Run: pip install akshare"
            )

    def disconnect(self) -> None:
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "akshare"

    # ------------------------------------------------------------------
    # Quote
    # ------------------------------------------------------------------

    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        self._subscriptions.update(symbols)

    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        for s in symbols:
            self._subscriptions.discard(s)

    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        return {s: self._quote_cache.get(s, Quote(symbol=s)) for s in symbols}

    # ------------------------------------------------------------------
    # K-line
    # ------------------------------------------------------------------

    def get_kline(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        if not self._api:
            return {}

        result: dict[Symbol, list[Bar]] = {}

        for sym in symbols:
            try:
                if period in ("1d", "1w", "1M"):
                    period_map = {"1d": "daily", "1w": "weekly", "1M": "monthly"}
                    freq = period_map[period]
                    df = self._api.stock_zh_a_hist(
                        symbol=sym,
                        period=freq,
                        adjust="qfq",  # 前复权
                    )
                    if df is None or df.empty:
                        continue

                    df = df.tail(count)
                    bars: list[Bar] = []
                    for _, row in df.iterrows():
                        bar = Bar(
                            symbol=sym,
                            datetime=pd.to_datetime(row["日期"]).to_pydatetime(),
                            open=float(row["开盘"]),
                            high=float(row["最高"]),
                            low=float(row["最低"]),
                            close=float(row["收盘"]),
                            volume=float(row["成交量"]),
                            amount=float(row["成交额"]),
                            period=period,
                        )
                        bars.append(bar)
                    result[sym] = bars
            except Exception:
                continue

        return result

    # ------------------------------------------------------------------
    # Polling (not supported — AKShare is a REST API)
    # ------------------------------------------------------------------

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """AKShare is REST-based, polling is not supported."""
        return []


# Lazy import for pd.to_datetime at module level
import pandas as pd  # noqa: E402
