"""Order, Trade, Position, and Account models.

These Pydantic models represent the execution state:
- OrderRequest: Strategy's intent (before risk/position sizing)
- Order: A live order tracked by the broker
- Trade: A single fill/trade record
- Position: Current holding for a symbol
- AccountInfo: Broker account summary
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from quant_framework.core.constants import OrderDirection, OrderStatus, OrderType, TimeInForce
from quant_framework.core.types import JsonDict


def _new_id(prefix: str = "") -> str:
    """Generate a unique ID."""
    return f"{prefix}_{uuid4().hex[:12]}"


class OrderRequest(BaseModel):
    """Order request from a strategy (before risk/position sizing).

    Represents the strategy's trading intent. Flows through:
    Strategy -> RiskEngine -> PositionSizer -> Broker -> Order
    """

    strategy_id: str
    symbol: str
    direction: OrderDirection
    price: float | None = None             # None = market order
    volume: int | None = None              # Exact shares
    amount: float | None = None            # Exact cash amount
    position_pct: float | None = None      # Fraction of account/position
    order_type: OrderType = OrderType.LIMIT
    time_in_force: TimeInForce = TimeInForce.DAY
    reason: str = ""
    metadata: JsonDict = Field(default_factory=dict)


class Order(BaseModel):
    """A live order tracked by the broker.

    Orders go through a state machine:
    CREATED -> PENDING_SUBMIT -> SUBMITTED -> [PARTIALLY_FILLED] -> FILLED
                                      |-> CANCELLED
                                      |-> REJECTED
    """

    order_id: str = Field(default_factory=lambda: _new_id("ord"))
    strategy_id: str = ""
    symbol: str = ""
    direction: OrderDirection = OrderDirection.BUY
    order_type: OrderType = OrderType.LIMIT
    price: float | None = None             # Limit price (None = market)
    requested_volume: int = 0              # Original volume
    filled_volume: int = 0                 # Cumulative filled
    cancelled_volume: int = 0              # Cancelled volume
    avg_fill_price: float = 0.0            # Volume-weighted average fill price
    status: OrderStatus = OrderStatus.CREATED
    time_in_force: TimeInForce = TimeInForce.DAY
    commission: float = 0.0
    created_time: datetime = Field(default_factory=datetime.now)
    updated_time: datetime = Field(default_factory=datetime.now)
    filled_time: datetime | None = None
    reject_reason: str = ""
    metadata: JsonDict = Field(default_factory=dict)

    @property
    def remaining_volume(self) -> int:
        """Volume not yet filled or cancelled."""
        return self.requested_volume - self.filled_volume - self.cancelled_volume

    @property
    def is_active(self) -> bool:
        """Whether the order is still active in the market."""
        return self.status in (
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.PENDING_SUBMIT,
        )

    @property
    def is_done(self) -> bool:
        """Whether the order has reached a terminal state."""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def fill_pct(self) -> float:
        """Percentage filled (0.0-1.0)."""
        if self.requested_volume == 0:
            return 1.0 if self.status == OrderStatus.FILLED else 0.0
        return self.filled_volume / self.requested_volume

    @property
    def value(self) -> float:
        """Total order value (requested volume * price)."""
        price = self.price or self.avg_fill_price
        return self.requested_volume * price


class Trade(BaseModel):
    """A single fill/trade record."""

    trade_id: str = Field(default_factory=lambda: _new_id("trd"))
    order_id: str = ""
    strategy_id: str = ""
    symbol: str = ""
    direction: OrderDirection = OrderDirection.BUY
    price: float = 0.0
    volume: int = 0
    commission: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.now)
    metadata: JsonDict = Field(default_factory=dict)

    @property
    def value(self) -> float:
        """Trade value = price * volume."""
        return self.price * self.volume


class Position(BaseModel):
    """Current holding for a single symbol."""

    symbol: str
    volume: int = 0                        # Total holding (long positive, short negative)
    available: int = 0                     # Available to sell
    frozen: int = 0                        # Frozen (pending orders)
    avg_cost: float = 0.0                  # Average cost price
    current_price: float = 0.0             # Last market price
    market_value: float = 0.0              # volume * current_price
    unrealized_pnl: float = 0.0            # Floating P&L
    realized_pnl: float = 0.0              # Cumulative realized P&L
    commission: float = 0.0                # Cumulative commission
    updated_time: datetime = Field(default_factory=datetime.now)

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage of cost."""
        cost = self.avg_cost * self.volume
        return self.unrealized_pnl / cost if cost > 0 else 0.0

    @property
    def total_pnl(self) -> float:
        """Total P&L = realized + unrealized."""
        return self.realized_pnl + self.unrealized_pnl


class AccountInfo(BaseModel):
    """Broker account summary."""

    account_id: str = ""
    total_equity: float = 0.0              # Total account value
    cash: float = 0.0                      # Available cash
    frozen_cash: float = 0.0               # Frozen cash (pending orders)
    market_value: float = 0.0              # Total market value of holdings
    total_pnl: float = 0.0                 # Cumulative realized P&L
    daily_pnl: float = 0.0                 # Today's P&L
    commission: float = 0.0                # Cumulative commission
    margin_ratio: float = 0.0              # Margin used / total equity
    risk_ratio: float = 0.0                # Risk ratio
    updated_time: datetime = Field(default_factory=datetime.now)
