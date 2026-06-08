"""Simulated/Local Data Provider for testing and backtest data feeding.

Uses CSVDataStore as the backend. Provides a controllable data source
that can replay historical data bar-by-bar for backtesting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider
from quant_framework.data.store import CSVDataStore, DataStore


class SimulatedDataProvider(DataProvider):
    """Simulated data provider backed by local data store.

    Useful for:
    - Backtesting (replay historical bars in order)
    - Paper trading with historical data
    - Unit testing strategies

    The bar replay cursor is advanced by calling wait_update(),
    which emits the next bar batch as "updates".
    """

    def __init__(
        self,
        store: DataStore | None = None,
        symbols: list[Symbol] | None = None,
        period: str = "1d",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> None:
        self._store: DataStore = store or CSVDataStore()
        self._symbols: list[Symbol] = symbols or []
        self._period: str = period
        self._start: datetime = start or datetime(2020, 1, 1)
        self._end: datetime = end or datetime.now()

        # Replay state
        self._cursor: int = 0
        self._timeline: list[datetime] = []  # All unique bar datetimes across symbols
        self._bars_by_time: dict[datetime, dict[Symbol, Bar]] = {}
        self._quote_cache: dict[Symbol, Quote] = {}
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Load historical data and build the replay timeline."""
        self._load_data()
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._cursor = 0

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "simulated"

    # ------------------------------------------------------------------
    # Quote interface
    # ------------------------------------------------------------------

    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        for s in symbols:
            if s not in self._symbols:
                self._symbols.append(s)

    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        for s in symbols:
            if s in self._symbols:
                self._symbols.remove(s)

    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        return {s: q for s, q in self._quote_cache.items() if s in symbols}

    # ------------------------------------------------------------------
    # K-line interface
    # ------------------------------------------------------------------

    def get_kline(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        result: dict[Symbol, list[Bar]] = {}
        end = datetime.now()
        start = datetime(2000, 1, 1)

        for sym in symbols:
            bars = self._store.load_bars(sym, period, start, end)
            result[sym] = bars[-count:] if len(bars) > count else bars
        return result

    # ------------------------------------------------------------------
    # Polling — replay bars sequentially
    # ------------------------------------------------------------------

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """Advance the replay cursor and return symbols with new data.

        Each call advances the timeline by one step (one bar batch).
        Returns empty list when replay is complete.
        """
        if not self._timeline or self._cursor >= len(self._timeline):
            return []

        current_time = self._timeline[self._cursor]
        bars_at_time = self._bars_by_time.get(current_time, {})

        # Convert bars to quotes for the current time
        changed: list[Symbol] = []
        for sym, bar in bars_at_time.items():
            quote = Quote(
                symbol=sym,
                timestamp=bar.dt,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                price=bar.close,
                pre_close=self._quote_cache.get(sym, Quote(symbol=sym)).price,
                volume=bar.volume,
                amount=bar.amount,
                change_pct=0.0,
            )
            # Calculate change_pct
            prev = self._quote_cache.get(sym)
            if prev and prev.price > 0:
                quote.change_pct = (quote.price - prev.price) / prev.price

            self._quote_cache[sym] = quote
            changed.append(sym)

        self._cursor += 1
        return changed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def total_steps(self) -> int:
        """Total number of replay steps."""
        return len(self._timeline)

    @property
    def current_step(self) -> int:
        """Current replay step."""
        return self._cursor

    @property
    def progress_pct(self) -> float:
        """Replay progress as percentage (0-100)."""
        if not self._timeline:
            return 100.0
        return min(100.0, self._cursor / len(self._timeline) * 100)

    def _load_data(self) -> None:
        """Load all bars into memory and build the unified timeline."""
        all_times: set[datetime] = set()
        self._bars_by_time.clear()

        for sym in self._symbols:
            bars = self._store.load_bars(sym, self._period, self._start, self._end)
            for bar in bars:
                all_times.add(bar.dt)
                self._bars_by_time.setdefault(bar.dt, {})[sym] = bar

        self._timeline = sorted(all_times)
        self._cursor = 0
