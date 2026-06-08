"""History data store interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar


class DataStore(ABC):
    """Abstract interface for historical market data persistence.

    Implementations can use SQLite, Parquet, CSV, TimescaleDB, etc.
    """

    @abstractmethod
    def save_bars(self, bars: list[Bar]) -> None:
        """Persist a batch of bars.

        Args:
            bars: List of Bar models to save.
        """

    @abstractmethod
    def load_bars(
        self,
        symbol: Symbol,
        period: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Load historical bars for a symbol within a date range.

        Args:
            symbol: Security code.
            period: Bar timeframe.
            start: Start datetime (inclusive).
            end: End datetime (inclusive).

        Returns:
            List of Bar objects sorted by datetime ascending.
        """

    @abstractmethod
    def load_bars_dataframe(
        self,
        symbol: Symbol,
        period: str,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        """Load historical bars as a DataFrame.

        Returns:
            DataFrame with datetime index and OHLCV columns.
        """

    @abstractmethod
    def get_symbols(self) -> list[Symbol]:
        """Return all available symbols in the store."""

    @abstractmethod
    def get_date_range(self, symbol: Symbol, period: str) -> tuple[datetime, datetime] | None:
        """Return the (earliest, latest) datetime range for a symbol.

        Returns None if no data exists.
        """

    @abstractmethod
    def delete_bars(self, symbol: Symbol, period: str = "") -> None:
        """Delete bars for a symbol. If period is empty, delete all periods."""


class CSVDataStore(DataStore):
    """CSV-based data store for lightweight local storage.

    Directory layout:
        data_dir/{symbol}/{period}.csv
    Each CSV has columns: datetime,open,high,low,close,volume,amount
    """

    def __init__(self, data_dir: str = "./data/market") -> None:
        import os
        self._data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    def _path(self, symbol: Symbol, period: str) -> str:
        import os
        return os.path.join(self._data_dir, symbol, f"{period}.csv")

    def save_bars(self, bars: list[Bar]) -> None:
        import os
        import pandas as pd

        # Group bars by symbol and period
        groups: dict[tuple[str, str], list[Bar]] = {}
        for bar in bars:
            key = (bar.symbol, bar.period)
            groups.setdefault(key, []).append(bar)

        for (symbol, period), group_bars in groups.items():
            path = self._path(symbol, period)
            os.makedirs(os.path.dirname(path), exist_ok=True)

            records = [
                {
                    "datetime": b.dt.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "amount": b.amount,
                }
                for b in group_bars
            ]
            new_df = pd.DataFrame(records)

            # Append or create
            if os.path.exists(path):
                existing = pd.read_csv(path)
                combined = pd.concat([existing, new_df], ignore_index=True)
                combined.drop_duplicates(subset=["datetime"], keep="last", inplace=True)
                combined.sort_values("datetime", inplace=True)
                combined.to_csv(path, index=False)
            else:
                new_df.to_csv(path, index=False)

    def load_bars(
        self, symbol: Symbol, period: str, start: datetime, end: datetime
    ) -> list[Bar]:
        import os
        import pandas as pd

        path = self._path(symbol, period)
        if not os.path.exists(path):
            return []

        df = pd.read_csv(path)
        df["datetime"] = pd.to_datetime(df["datetime"])
        mask = (df["datetime"] >= start) & (df["datetime"] <= end)
        df = df[mask].sort_values("datetime")

        return [
            Bar(
                symbol=symbol,
                datetime=row["datetime"].to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=float(row["amount"]),
                period=period,
            )
            for _, row in df.iterrows()
        ]

    def load_bars_dataframe(
        self, symbol: Symbol, period: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        import os
        import pandas as pd

        path = self._path(symbol, period)
        if not os.path.exists(path):
            return pd.DataFrame()

        df = pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")
        mask = (df.index >= start) & (df.index <= end)
        return df[mask].sort_index()

    def get_symbols(self) -> list[Symbol]:
        import os
        symbols: list[str] = []
        if not os.path.exists(self._data_dir):
            return symbols
        for entry in os.listdir(self._data_dir):
            entry_path = os.path.join(self._data_dir, entry)
            if os.path.isdir(entry_path):
                symbols.append(entry)
        return symbols

    def get_date_range(self, symbol: Symbol, period: str) -> tuple[datetime, datetime] | None:
        bars = self.load_bars(symbol, period, datetime(2000, 1, 1), datetime(2099, 12, 31))
        if not bars:
            return None
        return (bars[0].dt, bars[-1].dt)

    def delete_bars(self, symbol: Symbol, period: str = "") -> None:
        import os
        import shutil

        if period:
            path = self._path(symbol, period)
            if os.path.exists(path):
                os.remove(path)
        else:
            dir_path = os.path.join(self._data_dir, symbol)
            if os.path.exists(dir_path):
                shutil.rmtree(dir_path)
