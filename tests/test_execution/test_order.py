"""Tests for Order, Trade, Position, and OrderRequest models."""

import pytest

from quant_framework.core.constants import OrderDirection, OrderStatus, OrderType, TimeInForce
from quant_framework.execution.order import (
    AccountInfo,
    Order,
    OrderRequest,
    Position,
    Trade,
)


class TestOrderRequest:
    def test_default_creation(self):
        req = OrderRequest(strategy_id="test", symbol="600000", direction=OrderDirection.BUY)
        assert req.symbol == "600000"
        assert req.direction == OrderDirection.BUY
        assert req.price is None

    def test_with_volume(self):
        req = OrderRequest(
            strategy_id="test", symbol="600000",
            direction=OrderDirection.BUY, volume=1000, price=10.5,
        )
        assert req.volume == 1000
        assert req.price == 10.5

    def test_with_amount(self):
        req = OrderRequest(
            strategy_id="test", symbol="600000",
            direction=OrderDirection.BUY, amount=50000.0,
        )
        assert req.amount == 50000.0

    def test_model_validation(self):
        """OrderRequest should work as a Pydantic model."""
        req = OrderRequest(strategy_id="s1", symbol="000001", direction=OrderDirection.SELL)
        data = req.model_dump()
        assert data["symbol"] == "000001"


class TestOrder:
    def test_default_state(self):
        order = Order(symbol="600000", direction=OrderDirection.BUY)
        assert order.status == OrderStatus.CREATED
        assert order.requested_volume == 0
        assert order.filled_volume == 0

    def test_remaining_volume(self):
        order = Order(requested_volume=1000)
        assert order.remaining_volume == 1000

    def test_remaining_after_partial_fill(self):
        order = Order(requested_volume=1000, filled_volume=300)
        assert order.remaining_volume == 700

    def test_is_active(self):
        order = Order(status=OrderStatus.SUBMITTED)
        assert order.is_active is True

    def test_is_done_when_filled(self):
        order = Order(status=OrderStatus.FILLED)
        assert order.is_done is True

    def test_is_done_when_rejected(self):
        order = Order(status=OrderStatus.REJECTED)
        assert order.is_done is True

    def test_fill_pct_full(self):
        order = Order(status=OrderStatus.FILLED, requested_volume=1000, filled_volume=1000)
        assert order.fill_pct == 1.0

    def test_fill_pct_partial(self):
        order = Order(requested_volume=1000, filled_volume=300)
        assert order.fill_pct == 0.3

    def test_value(self):
        order = Order(price=10.0, requested_volume=1000)
        assert order.value == 10000.0

    def test_unique_order_ids(self):
        o1 = Order()
        o2 = Order()
        assert o1.order_id != o2.order_id


class TestTrade:
    def test_trade_creation(self):
        trade = Trade(
            order_id="ord_1",
            symbol="600000",
            direction=OrderDirection.BUY,
            price=10.5,
            volume=500,
            commission=1.5,
        )
        assert trade.symbol == "600000"
        assert trade.price == 10.5
        assert trade.volume == 500

    def test_trade_value(self):
        trade = Trade(price=10.0, volume=1000)
        assert trade.value == 10000.0


class TestPosition:
    def test_default_position(self):
        pos = Position(symbol="600000")
        assert pos.volume == 0
        assert pos.available == 0
        assert pos.avg_cost == 0.0

    def test_unrealized_pnl_pct(self):
        pos = Position(symbol="600000", volume=1000, avg_cost=10.0, current_price=10.5)
        pos.unrealized_pnl = pos.volume * (pos.current_price - pos.avg_cost)
        assert pos.unrealized_pnl == 500.0
        assert pos.unrealized_pnl_pct == 0.05

    def test_total_pnl(self):
        pos = Position(
            symbol="600000", volume=1000, avg_cost=10.0,
            realized_pnl=200.0, unrealized_pnl=500.0,
        )
        assert pos.total_pnl == 700.0

    def test_zero_cost_returns_zero_pnl_pct(self):
        pos = Position(symbol="600000", volume=0, avg_cost=0.0)
        assert pos.unrealized_pnl_pct == 0.0


class TestAccountInfo:
    def test_default_account(self):
        acc = AccountInfo()
        assert acc.total_equity == 0.0
        assert acc.cash == 0.0
