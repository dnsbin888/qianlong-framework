"""同花顺 (THS) Data Provider Adapter.

Wraps the legacy ths_hq_api behind the standard DataProvider interface.
Maps raw dict/DataFrame responses to Pydantic Quote/Bar models.

Compatible with the existing 同花顺 script environment.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider


class THSDataProvider(DataProvider):
    """同花顺行情数据适配器。

    Usage:
        provider = THSDataProvider()
        provider.connect()
        provider.subscribe_quote(["600000", "000001"])
        while True:
            changed = provider.wait_update()
            for sym in changed:
                quote = provider.get_quote([sym])[sym]
                print(f"{sym}: {quote.price}")
    """

    def __init__(self) -> None:
        self._api: Any = None  # ths_hq_api instance
        self._quote_cache: dict[Symbol, Quote] = {}
        self._subscriptions: set[Symbol] = set()
        self._connected = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Initialize the THS API connection."""
        try:
            from ths_api import hq  # type: ignore[import-untyped]
            self._api = hq.ths_hq_api()
            self._connected = True
        except ImportError:
            raise ImportError(
                "ths_api not available. THSDataProvider requires the 同花顺 Python environment."
            )

    def disconnect(self) -> None:
        """Release resources."""
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._api is not None

    @property
    def name(self) -> str:
        return "ths"

    # ------------------------------------------------------------------
    # Quote interface
    # ------------------------------------------------------------------

    def subscribe_quote(self, symbols: list[Symbol]) -> None:
        if not self._api:
            self.connect()
        # THS expects comma-separated string
        self._api.reg_quote(symbols)
        for s in symbols:
            self._subscriptions.add(s)

    def unsubscribe_quote(self, symbols: list[Symbol]) -> None:
        if self._api:
            self._api.unreg_quote(symbols)
        for s in symbols:
            self._subscriptions.discard(s)

    def get_quote(self, symbols: list[Symbol]) -> dict[Symbol, Quote]:
        """Pull latest quotes from cache.

        Note: In THS, quote data is updated by wait_update().
        Call wait_update() first to refresh the cache.
        """
        if not self._api:
            return {}
        raw = self._api.get_quote(symbols)
        return self._map_quotes(raw)

    # ------------------------------------------------------------------
    # K-line interface
    # ------------------------------------------------------------------

    def get_kline(
        self, symbols: list[Symbol], period: str, count: int
    ) -> dict[Symbol, list[Bar]]:
        if not self._api:
            return {}

        # Convert period: '1m' -> 1, '5m' -> 5, '60m' -> 60, '1d' -> '1440'
        period_map: dict[str, str | int] = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "60m": 60, "1d": 24 * 60, "1w": "week", "1M": "month",
        }
        ths_period = period_map.get(period, 24 * 60)

        result: dict[Symbol, list[Bar]] = {}
        for sym in symbols:
            kline = self._api.get_kline(sym, ths_period, count)
            bars = self._map_bars(sym, kline, period)
            result[sym] = bars
        return result

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """Block until next data update.

        The THS API's wait_update() returns a list of strings (symbol codes)
        that have changed. We update our internal Quote cache from the raw
        data, then return the list.

        Returns:
            List of symbols that had new data.
        """
        if not self._api:
            return []

        raw_changed: list[str] = self._api.wait_update()
        changed = [Symbol(s) for s in raw_changed]

        # Refresh internal cache from raw quote data
        self._refresh_cache(changed)
        return changed

    # ------------------------------------------------------------------
    # Mapping helpers
    # ------------------------------------------------------------------

    def _refresh_cache(self, symbols: list[Symbol]) -> None:
        """Update internal Quote cache from raw API data."""
        if not self._api:
            return
        try:
            raw = self._api.get_quote(symbols)
            quotes = self._map_quotes(raw)
            self._quote_cache.update(quotes)
        except Exception:
            pass  # Some symbols may not be subscribed

    def _map_quotes(self, raw: dict[str, dict[str, Any]]) -> dict[Symbol, Quote]:
        """Convert raw THS quote dict to Pydantic Quote models."""
        result: dict[Symbol, Quote] = {}
        now = datetime.now()
        for sym, data in raw.items():
            try:
                quote = Quote(
                    symbol=sym,
                    timestamp=now,
                    open=float(data.get("open", 0)),
                    high=float(data.get("high", 0)),
                    low=float(data.get("low", 0)),
                    price=float(data.get("price", 0)),
                    pre_close=float(data.get("pre_close", 0)),
                    volume=float(data.get("volume", 0)),
                    amount=float(data.get("amount", 0)),
                    bid_prices=[
                        float(data.get(f"b{i}_p", 0)) for i in range(1, 6)
                    ],
                    bid_volumes=[
                        int(data.get(f"b{i}_v", 0)) for i in range(1, 6)
                    ],
                    ask_prices=[
                        float(data.get(f"a{i}_p", 0)) for i in range(1, 6)
                    ],
                    ask_volumes=[
                        int(data.get(f"a{i}_v", 0)) for i in range(1, 6)
                    ],
                    limit_up=float(data.get("zt_p", 0)),
                    limit_down=float(data.get("dt_p", 0)),
                    change_pct=float(data.get("zf", 0)),
                )
                result[sym] = quote
            except (KeyError, ValueError, TypeError):
                continue
        return result

    @staticmethod
    def _map_bars(symbol: str, kline: Any, period: str) -> list[Bar]:
        """Convert THS kline DataFrame to list of Bar models.

        THS kline is typically a DataFrame with columns:
            datetime, open, high, low, close, volume, amount
        Or a list of dicts with keys: datetime, open, high, low, close, volume
        """
        import pandas as pd

        if kline is None:
            return []

        # DataFrame path
        if isinstance(kline, pd.DataFrame):
            bars: list[Bar] = []
            for _, row in kline.iterrows():
                try:
                    bar = Bar(
                        symbol=symbol,
                        datetime=row.get("datetime", row.name) if hasattr(row, "get") else row.name.to_pydatetime(),
                        open=float(row.get("open", 0)),
                        high=float(row.get("high", 0)),
                        low=float(row.get("low", 0)),
                        close=float(row.get("close", 0)),
                        volume=float(row.get("volume", 0)),
                        amount=float(row.get("amount", 0)),
                        period=period,
                    )
                    bars.append(bar)
                except (TypeError, ValueError):
                    continue
            return bars

        # Dict path (THS kline[code] returns list of dicts)
        if isinstance(kline, dict):
            raw_bars = kline.get(symbol, [])
            if isinstance(raw_bars, list):
                return THSDataProvider._map_dict_bars(symbol, raw_bars, period)

        # List path
        if isinstance(kline, list):
            return THSDataProvider._map_dict_bars(symbol, kline, period)

        return []

    @staticmethod
    def _map_dict_bars(symbol: str, raw_bars: list[dict[str, Any]], period: str) -> list[Bar]:
        """Map list of dict bars to Bar models."""
        bars: list[Bar] = []
        for b in raw_bars:
            try:
                dt = b.get("datetime", b.get("date", ""))
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                bar = Bar(
                    symbol=symbol,
                    datetime=dt,
                    open=float(b.get("open", 0)),
                    high=float(b.get("high", 0)),
                    low=float(b.get("low", 0)),
                    close=float(b.get("close", 0)),
                    volume=float(b.get("volume", 0)),
                    amount=float(b.get("amount", 0)),
                    period=period,
                )
                bars.append(bar)
            except (TypeError, ValueError, KeyError):
                continue
        return bars
