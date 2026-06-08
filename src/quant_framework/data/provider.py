"""Abstract data provider interface for market data.

All market data sources must implement this interface. Supports two modes:
1. Pull mode: actively call get_quote()/get_kline()
2. Subscribe mode: subscribe_quote() + callback/event-driven notification
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote


class DataProvider(ABC):
    """Abstract base class for market data providers.

    Each concrete implementation wraps a specific data source:
    - THSDataProvider: 同花顺 ths_hq_api
    - TushareDataProvider: Tushare Pro API
    - AKShareDataProvider: AKShare free data
    - XTQuantDataProvider: QMT/MiniQMT
    - CSVDataProvider: local CSV/Parquet files

    Supports two usage patterns:
    1. Pull (主动拉取): call get_quote() / get_kline() directly
    2. Subscribe (订阅推送): call subscribe_quote() then wait for events
    """

    # ------------------------------------------------------------------
    # Quote (real-time snapshot) interface
    # ------------------------------------------------------------------

    @abstractmethod
    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        """Subscribe to real-time quote updates for given symbols.

        After subscribing, quotes will be updated internally and available
        via get_quote() or via event callbacks.

        Args:
            symbols: List of security codes to subscribe.
        """

    @abstractmethod
    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        """Unsubscribe from real-time quote updates.

        Args:
            symbols: List of security codes to unsubscribe.
        """

    @abstractmethod
    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """Pull latest quote snapshots for the given symbols.

        Args:
            symbols: List of security codes.

        Returns:
            Dict mapping symbol to its Quote model.
        """

    # ------------------------------------------------------------------
    # K-line (historical & streaming bars) interface
    # ------------------------------------------------------------------

    @abstractmethod
    def get_kline(
        self,
        symbols: list[Symbol],
        period: str,
        count: int,
    ) -> dict[Symbol, list[Bar]]:
        """Pull historical K-line bars.

        Args:
            symbols: List of security codes.
            period: Bar timeframe ('1m','5m','15m','30m','60m','1d','1w','1M').
            count: Number of recent bars to fetch.

        Returns:
            Dict mapping symbol to list of Bar objects (most recent last).
        """

    def get_kline_dataframe(
        self,
        symbols: list[Symbol],
        period: str,
        count: int,
    ) -> pd.DataFrame:
        """Convenience: pull K-lines as a pandas DataFrame.

        Args:
            symbols: List of security codes.
            period: Bar timeframe.
            count: Number of recent bars.

        Returns:
            Multi-index DataFrame (symbol, datetime) with OHLCV columns.
        """
        result = self.get_kline(symbols, period, count)
        records = []
        for symbol, bars in result.items():
            for bar in bars:
                records.append({
                    "symbol": symbol,
                    "datetime": bar.dt,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "amount": bar.amount,
                })
        if not records:
            return pd.DataFrame(
                columns=["symbol", "datetime", "open", "high", "low", "close", "volume", "amount"]
            )
        df = pd.DataFrame(records)
        df.set_index(["symbol", "datetime"], inplace=True)
        return df

    # ------------------------------------------------------------------
    # Polling / wait interface (for polling engine)
    # ------------------------------------------------------------------

    @abstractmethod
    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """Block until the next data update arrives.

        Returns the list of symbols whose data changed.
        Used by PollingEngine for the legacy while-wait_update pattern.

        Args:
            timeout: Maximum seconds to wait. None = block indefinitely.

        Returns:
            List of symbols that have new data.
        """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the data source."""

    @abstractmethod
    def disconnect(self) -> None:
        """Release connection and resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the provider is connected and ready."""

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable provider name (e.g. 'ths', 'tushare')."""

    @property
    def subscribed_symbols(self) -> set[Symbol]:
        """Set of currently subscribed symbols."""
        return set()
