"""Tushare Data Provider Adapter.

Optional dependency: pip install tushare rich

Requires a Tushare Pro token (free registration at https://tushare.pro).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.data.provider import DataProvider


class TushareDataProvider(DataProvider):
    """Tushare market data provider.

    Supports A-share daily K-line data and basic quote info.
    Requires 'tushare' package and a valid token.

    Usage:
        provider = TushareDataProvider(token="your_token")
        provider.connect()
        bars = provider.get_kline(["000001.SZ"], "1d", 100)
    """

    def __init__(self, token: str = "") -> None:
        self._token: str = token
        self._api: Any = None
        self._quote_cache: dict[Symbol, Quote] = {}
        self._subscriptions: set[Symbol] = set()
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            import tushare as ts  # type: ignore[import-untyped]
            ts.set_token(self._token)
            self._api = ts.pro_api()
            self._connected = True
        except ImportError:
            raise ImportError(
                "tushare not installed. Run: pip install tushare"
            )

    def disconnect(self) -> None:
        self._api = None
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "tushare"

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

        # Tushare daily is the most reliable; other periods need different APIs
        if period in ("1d", "1w", "1M"):
            freq_map = {"1d": "D", "1w": "W", "1M": "M"}
            for sym_clean, bars in self._fetch_daily(symbols, count, freq_map.get(period, "D")).items():
                result[sym_clean] = bars
        else:
            # For intraday, Tushare needs different approach — return empty for now
            pass

        return result

    def _fetch_daily(
        self, symbols: list[Symbol], count: int, freq: str
    ) -> dict[Symbol, list[Bar]]:
        """Fetch daily/weekly/monthly K-lines from Tushare."""
        result: dict[Symbol, list[Bar]] = {}
        if not self._api:
            return result

        for raw_sym in symbols:
            # Normalize symbol: '600000' -> '600000.SH', '000001' -> '000001.SZ'
            ts_code = self._normalize_symbol(raw_sym)
            try:
                df: pd.DataFrame = self._api.daily(
                    ts_code=ts_code,
                    limit=count,
                )
                if df is None or df.empty:
                    continue

                # Tushare returns rows newest-first; reverse to chronological
                df = df.sort_values("trade_date").iloc[-count:]

                bars: list[Bar] = []
                for _, row in df.iterrows():
                    dt = datetime.strptime(str(row["trade_date"]), "%Y%m%d")
                    bar = Bar(
                        symbol=raw_sym,
                        datetime=dt,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["vol"]),
                        amount=float(row.get("amount", 0)),
                        period=freq,
                    )
                    bars.append(bar)
                result[raw_sym] = bars
            except Exception:
                continue

        return result

    # ------------------------------------------------------------------
    # Polling (not supported for Tushare — it's a REST API)
    # ------------------------------------------------------------------

    def wait_update(self, timeout: float | None = None) -> list[Symbol]:
        """Tushare is REST-based, polling is not supported."""
        return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """Convert plain code to Tushare ts_code format.

        '600000' -> '600000.SH'
        '000001' -> '000001.SZ'
        '300033' -> '300033.SZ'
        """
        if "." in symbol:
            return symbol  # Already normalized

        code_int = int(symbol) if symbol.isdigit() else 0
        # Shanghai: 600xxx, 601xxx, 603xxx, 688xxx
        if code_int >= 600000 or code_int >= 688000:
            return f"{symbol}.SH"
        # Shenzhen: 000xxx, 001xxx, 002xxx, 300xxx
        return f"{symbol}.SZ"
