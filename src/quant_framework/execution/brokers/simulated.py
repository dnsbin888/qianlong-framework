"""Simulated Broker — paper trading with virtual fills.

Uses FillSimulator to generate synthetic fills from market quotes.
Maintains virtual positions and account in memory.
"""

from __future__ import annotations

import logging
from datetime import datetime

from quant_framework.core.constants import OrderDirection
from quant_framework.core.types import Symbol
from quant_framework.execution.broker import AbstractBroker
from quant_framework.execution.fill_simulator import FillSimulator, FillSimulatorConfig
from quant_framework.execution.order import (
    AccountInfo,
    Order,
    OrderRequest,
    Position,
    Trade,
)

logger = logging.getLogger("quant_framework.broker.simulated")


class SimulatedBroker(AbstractBroker):
    """Paper trading broker with virtual fills.

    Maintains simulated positions and account. Uses FillSimulator to
    generate fills from market data. Great for testing strategies
    before going live.

    Usage:
        broker = SimulatedBroker(initial_cash=1000000.0)
        broker.connect()
        order = broker.submit_order(request)
        # Call broker.update_with_quote(quote) on each data update
    """

    def __init__(self, initial_cash: float = 1_000_000.0) -> None:
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._frozen_cash = 0.0
        self._positions: dict[Symbol, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._fill_sim = FillSimulator(FillSimulatorConfig())
        self._connected = False
        self._commission_total = 0.0
        self._realized_pnl = 0.0

    def connect(self) -> None:
        self._connected = True
        logger.info("Simulated broker started with cash=%.2f", self._initial_cash)

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "simulated"

    # ---- Order ----

    def submit_order(self, request: OrderRequest) -> Order:
        from uuid import uuid4

        order = Order(
            order_id=f"ord_{uuid4().hex[:12]}",
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            direction=request.direction,
            price=request.price,
            requested_volume=request.volume or 0,
        )

        # Check sufficient funds/position
        if order.direction == OrderDirection.BUY:
            estimated_cost = (order.price or 1.0) * order.requested_volume * 1.001
            if estimated_cost > self._cash:
                raise ValueError(
                    f"Insufficient funds: need {estimated_cost:.0f}, have {self._cash:.0f}"
                )
            self._frozen_cash += estimated_cost

        if order.direction == OrderDirection.SELL:
            pos = self._positions.get(order.symbol)
            if not pos or pos.available < order.requested_volume:
                raise ValueError(
                    f"Insufficient position: need {order.requested_volume}, "
                    f"have {pos.available if pos else 0}"
                )

        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order or not order.is_active:
            return False
        order.cancelled_volume = order.remaining_volume
        order.status = "cancelled"  # type: ignore[assignment]
        # Release frozen cash
        if order.direction == OrderDirection.BUY:
            self._frozen_cash -= (order.price or 1.0) * order.remaining_volume * 1.001
        return True

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        count = 0
        for order in self.get_open_orders(symbol):
            if self.cancel_order(order.order_id):
                count += 1
        return count

    # ---- Fill on new data ----

    def update_with_quote(
        self,
        symbol: str,
        price: float,
        high: float = 0.0,
        low: float = 0.0,
        limit_up: float = 0.0,
        limit_down: float = 0.0,
    ) -> list[Trade]:
        """Check for fills against the latest quote data.

        Should be called each time new market data arrives.
        Returns any trades that were filled.
        """
        fills: list[Trade] = []
        for order in self.get_open_orders(symbol):
            trade = self._fill_sim.simulate_fill(
                order, price, high, low, limit_up, limit_down
            )
            if trade:
                self._apply_fill(order, trade)
                fills.append(trade)
        return fills

    def _apply_fill(self, order: Order, trade: Trade) -> None:
        """Update account/position state after a fill."""
        if order.direction == OrderDirection.BUY:
            # Deduct cash
            cost = trade.price * trade.volume + trade.commission
            self._cash -= cost
            self._frozen_cash -= (order.price or trade.price) * trade.volume * 1.001

            # Update position
            if order.symbol not in self._positions:
                self._positions[order.symbol] = Position(symbol=order.symbol)
            pos = self._positions[order.symbol]
            total_cost_basis = pos.avg_cost * pos.volume + trade.price * trade.volume
            pos.volume += trade.volume
            pos.available += trade.volume
            pos.avg_cost = total_cost_basis / pos.volume if pos.volume > 0 else 0.0
        else:
            # Add cash
            proceeds = trade.price * trade.volume - trade.commission
            self._cash += proceeds

            # Update position
            pos = self._positions[order.symbol]
            pos.volume -= trade.volume
            pos.available -= trade.volume
            if pos.volume <= 0:
                pos.avg_cost = 0.0

        # Update order
        order.filled_volume = trade.volume
        order.avg_fill_price = trade.price
        order.commission = trade.commission

        self._commission_total += trade.commission
        self._trades.append(trade)

    # ---- Query ----

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(self, symbol: str | None = None, status: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        if status:
            orders = [o for o in orders if o.status.value == status]
        return orders

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = [o for o in self._orders.values() if o.is_active]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_positions(self) -> dict[Symbol, Position]:
        return self._positions

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def _create_position(self, symbol: str, volume: int, avg_cost: float,
                         current_price: float = 0.0) -> Position:
        """内部: 直接创建/更新持仓 (paper_engine_v2 restore用)"""
        pos = Position(
            symbol=symbol, volume=volume, available=volume,
            avg_cost=avg_cost,
            current_price=current_price or avg_cost,
            market_value=volume * (current_price or avg_cost),
            updated_time=datetime.now())
        self._positions[symbol] = pos
        return pos

    def get_account(self) -> AccountInfo:
        market_value = sum(
            p.volume * (p.current_price or p.avg_cost)
            for p in self._positions.values()
            if p.volume > 0
        )
        return AccountInfo(
            account_id="simulated",
            total_equity=self._cash + market_value,
            cash=self._cash,
            frozen_cash=self._frozen_cash,
            market_value=market_value,
            total_pnl=self._realized_pnl,
            commission=self._commission_total,
            updated_time=datetime.now(),
        )

    def get_trades(self, symbol: str | None = None, limit: int = 100) -> list[Trade]:
        trades = self._trades
        if symbol:
            trades = [t for t in trades if t.symbol == symbol]
        return trades[-limit:]
