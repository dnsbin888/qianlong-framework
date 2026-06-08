"""Event definitions for the internal event bus.

Events are immutable dataclass-based messages passed through the internal
event bus to decouple data providers, strategies, risk engine, execution,
and monitoring components.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_framework.core.constants import EventType


@dataclass(frozen=True, slots=True)
class Event:
    """Base event. All events share type and timestamp."""

    type: EventType
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class BarEvent(Event):
    """Triggered when a new K-line bar is completed.

    Will be imported lazily to avoid circular imports:
      from quant_framework.data.models import Bar
    """

    symbol: str = ""
    period: str = "1d"
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    close: float = 0.0
    volume: float = 0.0
    amount: float = 0.0


@dataclass(frozen=True, slots=True)
class QuoteEvent(Event):
    """Triggered on real-time quote update."""

    symbol: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    bid1_price: float = 0.0
    bid1_volume: int = 0
    ask1_price: float = 0.0
    ask1_volume: int = 0
    limit_up: float = 0.0
    limit_down: float = 0.0
    volume: float = 0.0
    amount: float = 0.0


@dataclass(frozen=True, slots=True)
class OrderEvent(Event):
    """Triggered on order status change."""

    order_id: str = ""
    strategy_id: str = ""
    symbol: str = ""
    direction: str = ""
    status: str = ""
    filled_volume: int = 0
    avg_fill_price: float = 0.0
    reason: str = ""


@dataclass(frozen=True, slots=True)
class TradeEvent(Event):
    """Triggered on trade/fill."""

    trade_id: str = ""
    order_id: str = ""
    strategy_id: str = ""
    symbol: str = ""
    direction: str = ""
    price: float = 0.0
    volume: int = 0
    commission: float = 0.0


@dataclass(frozen=True, slots=True)
class RiskBreachEvent(Event):
    """Triggered when a risk rule is breached."""

    strategy_id: str = ""
    rule_name: str = ""
    reason: str = ""
    order_id: str = ""


@dataclass(frozen=True, slots=True)
class TimerEvent(Event):
    """Periodic timer trigger."""

    timer_id: int = 0
    interval_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class SystemEvent(Event):
    """System-level event (start, stop, etc.)."""

    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
