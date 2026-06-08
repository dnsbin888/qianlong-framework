"""Tests for core constants and enums."""

import pytest

from quant_framework.core.constants import (
    EngineMode,
    EventType,
    OrderDirection,
    OrderStatus,
    OrderType,
    RiskDecision,
    SignalDirection,
    TimeInForce,
)


class TestOrderDirection:
    def test_buy_value(self):
        assert OrderDirection.BUY.value == "buy"

    def test_sell_value(self):
        assert OrderDirection.SELL.value == "sell"


class TestOrderStatus:
    def test_all_statuses(self):
        statuses = list(OrderStatus)
        assert len(statuses) == 8  # created, pending, submitted, partial, filled, cancelled, rejected, expired

    def test_terminal_states(self):
        terminal = {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED}
        for s in terminal:
            assert s in OrderStatus


class TestSignalDirection:
    def test_all_directions(self):
        directions = list(SignalDirection)
        assert SignalDirection.BUY in directions
        assert SignalDirection.SELL in directions
        assert SignalDirection.CLOSE in directions
        assert SignalDirection.HOLD in directions

    def test_str_value(self):
        assert str(SignalDirection.BUY.value) == "buy"


class TestRiskDecision:
    def test_allow_block_reduce(self):
        assert RiskDecision.ALLOW.value == "allow"
        assert RiskDecision.BLOCK.value == "block"
        assert RiskDecision.REDUCE.value == "reduce"


class TestEngineMode:
    def test_modes(self):
        assert EngineMode.LIVE.value == "live"
        assert EngineMode.PAPER.value == "paper"
        assert EngineMode.BACKTEST.value == "backtest"


class TestEventType:
    def test_unique_values(self):
        """All event types should have unique auto() values."""
        values = [e.value for e in EventType]
        assert len(values) == len(set(values))

    def test_expected_events(self):
        expected = {
            EventType.BAR, EventType.TICK, EventType.QUOTE,
            EventType.ORDER_SUBMITTED, EventType.ORDER_FILLED,
            EventType.ORDER_PARTIALLY_FILLED, EventType.ORDER_CANCELLED,
            EventType.ORDER_REJECTED, EventType.TRADE,
            EventType.RISK_BREACH, EventType.TIMER,
            EventType.SYSTEM, EventType.ENGINE_START, EventType.ENGINE_STOP,
            EventType.PORTFOLIO_UPDATE,
        }
        assert expected.issubset(set(EventType))
