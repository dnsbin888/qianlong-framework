"""Core type definitions for the quant framework."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, TypeAlias

Symbol: TypeAlias = str
"""Stock/futures symbol code, e.g. '600000', '000001', 'IF2406'."""

StrategyId: TypeAlias = str
"""Unique identifier for a strategy instance."""

OrderId: TypeAlias = str
"""Unique identifier for an order."""

TradeId: TypeAlias = str
"""Unique identifier for a trade/fill."""

SignalId: TypeAlias = str
"""Unique identifier for a signal."""

JsonDict: TypeAlias = dict[str, Any]
"""Generic JSON-compatible dictionary."""


class TimeFrame:
    """Standardized timeframe constants for K-line periods."""

    TICK: str = "tick"
    M1: str = "1m"
    M5: str = "5m"
    M15: str = "15m"
    M30: str = "30m"
    H1: str = "60m"
    H4: str = "4h"
    D1: str = "1d"
    W1: str = "1w"
    M1_MONTH: str = "1M"

    MINUTES_MAP: dict[str, int] = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "60m": 60,
        "4h": 240,
        "1d": 1440,
        "1w": 10080,
        "1M": 43200,
    }

    @classmethod
    def to_minutes(cls, period: str) -> int:
        """Convert a period string to minutes."""
        return cls.MINUTES_MAP.get(period, 1440)
