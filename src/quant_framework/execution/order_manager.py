"""Order Manager — order state machine and lifecycle tracking.

Tracks all orders and their state transitions:
    CREATED -> PENDING_SUBMIT -> SUBMITTED -> [PARTIALLY_FILLED] -> FILLED
                                         |-> CANCELLED
                                         |-> REJECTED
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime

from quant_framework.core.constants import OrderStatus
from quant_framework.core.types import Symbol
from quant_framework.execution.order import Order, OrderRequest, Trade

logger = logging.getLogger("quant_framework.order_manager")


class OrderManager:
    """Central order tracking and state management.

    Maintains an in-memory registry of all orders. In production,
    this should be backed by a persistent store (via TradeRecorder).
    """

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._trades: dict[str, list[Trade]] = defaultdict(list)
        self._orders_by_symbol: dict[Symbol, set[str]] = defaultdict(set)
        self._orders_by_strategy: dict[str, set[str]] = defaultdict(set)

    # ---- Order CRUD ----

    def create_order(self, request: OrderRequest, order_id: str = "") -> Order:
        """Create an Order from an OrderRequest."""
        from uuid import uuid4

        order = Order(
            order_id=order_id or f"ord_{uuid4().hex[:12]}",
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            direction=request.direction,
            order_type=request.order_type,
            price=request.price,
            requested_volume=request.volume or 0,
            status=OrderStatus.CREATED,
            time_in_force=request.time_in_force,
            metadata=request.metadata,
        )
        self._orders[order.order_id] = order
        self._orders_by_symbol[order.symbol].add(order.order_id)
        self._orders_by_strategy[order.strategy_id].add(order.order_id)
        logger.debug("Order created: %s", order.order_id)
        return order

    # ---- State transitions ----

    def mark_pending(self, order_id: str) -> Order:
        """Mark order as pending submission."""
        order = self._orders[order_id]
        order.status = OrderStatus.PENDING_SUBMIT
        order.updated_time = datetime.now()
        return order

    def mark_submitted(self, order_id: str) -> Order:
        """Mark order as submitted to broker."""
        order = self._orders[order_id]
        order.status = OrderStatus.SUBMITTED
        order.updated_time = datetime.now()
        logger.info("Order submitted: %s %s %s @%s",
                     order.symbol, order.direction.value, order.requested_volume, order.price or "MKT")
        return order

    def mark_filled(self, order_id: str, fill_price: float = 0.0) -> Order:
        """Mark order as fully filled."""
        order = self._orders[order_id]
        order.filled_volume = order.requested_volume
        order.status = OrderStatus.FILLED
        order.avg_fill_price = fill_price or order.avg_fill_price
        order.filled_time = datetime.now()
        order.updated_time = datetime.now()
        logger.info("Order filled: %s %s %s @%.3f",
                     order.symbol, order.direction.value, order.filled_volume, order.avg_fill_price)
        return order

    def fill_partial(
        self, order_id: str, fill_volume: int, fill_price: float, commission: float = 0.0
    ) -> tuple[Order, Trade]:
        """Record a partial fill.

        Returns the updated Order and a new Trade record.
        """
        from uuid import uuid4

        order = self._orders[order_id]

        # Update average fill price
        total_cost = order.avg_fill_price * order.filled_volume + fill_price * fill_volume
        order.filled_volume += fill_volume
        order.avg_fill_price = total_cost / order.filled_volume if order.filled_volume > 0 else 0.0
        order.commission += commission

        if order.filled_volume >= order.requested_volume:
            order.status = OrderStatus.FILLED
            order.filled_time = datetime.now()
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        order.updated_time = datetime.now()

        trade = Trade(
            trade_id=f"trd_{uuid4().hex[:12]}",
            order_id=order_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            direction=order.direction,
            price=fill_price,
            volume=fill_volume,
            commission=commission,
        )
        self._trades[order_id].append(trade)

        logger.info("Partial fill: %s %s %d/%d @%.3f",
                     order.symbol, order.direction.value,
                     order.filled_volume, order.requested_volume, fill_price)
        return order, trade

    def mark_cancelled(self, order_id: str) -> Order:
        """Mark order as cancelled."""
        order = self._orders[order_id]
        order.cancelled_volume = order.remaining_volume
        order.status = OrderStatus.CANCELLED
        order.updated_time = datetime.now()
        logger.info("Order cancelled: %s", order_id)
        return order

    def mark_rejected(self, order_id: str, reason: str) -> Order:
        """Mark order as rejected."""
        order = self._orders[order_id]
        order.status = OrderStatus.REJECTED
        order.reject_reason = reason
        order.updated_time = datetime.now()
        logger.warning("Order rejected: %s — %s", order_id, reason)
        return order

    def mark_expired(self, order_id: str) -> Order:
        """Mark order as expired."""
        order = self._orders[order_id]
        order.status = OrderStatus.EXPIRED
        order.updated_time = datetime.now()
        return order

    # ---- Query ----

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(self, symbol: str | None = None, status: str | None = None) -> list[Order]:
        """Query orders with optional filters."""
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        if status:
            orders = [o for o in orders if o.status.value == status]
        return orders

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Get all orders that are still active."""
        orders = [o for o in self._orders.values() if o.is_active]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_orders_by_strategy(self, strategy_id: str) -> list[Order]:
        """Get all orders for a strategy."""
        order_ids = self._orders_by_strategy.get(strategy_id, set())
        return [self._orders[oid] for oid in order_ids if oid in self._orders]

    def get_trades(self, order_id: str | None = None) -> list[Trade]:
        """Get trades, optionally filtered by order."""
        if order_id:
            return self._trades.get(order_id, [])
        all_trades: list[Trade] = []
        for trades in self._trades.values():
            all_trades.extend(trades)
        return sorted(all_trades, key=lambda t: t.timestamp)

    @property
    def order_count(self) -> int:
        return len(self._orders)
