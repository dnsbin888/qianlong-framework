"""Tests for core event definitions."""

from datetime import datetime

import pytest

from quant_framework.core.events import (
    BarEvent,
    Event,
    EventType,
    OrderEvent,
    QuoteEvent,
    RiskBreachEvent,
    SystemEvent,
    TimerEvent,
    TradeEvent,
)


class TestEvent:
    def test_base_event_creation(self):
        e = Event(type=EventType.SYSTEM, timestamp=datetime(2024, 1, 1))
        assert e.type == EventType.SYSTEM
        assert e.timestamp == datetime(2024, 1, 1)

    def test_event_timestamp_required(self):
        """timestamp is a required positional arg (dataclass, no default)."""
        with pytest.raises(TypeError):
            Event(type=EventType.BAR)  # missing timestamp


class TestBarEvent:
    def test_bar_event_creation(self):
        e = BarEvent(
            type=EventType.BAR,
            timestamp=datetime(2024, 1, 1),
            symbol="600000",
            period="1d",
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=1000000,
            amount=10200000.0,
        )
        assert e.symbol == "600000"
        assert e.close == 10.2


class TestQuoteEvent:
    def test_quote_event_creation(self):
        e = QuoteEvent(
            type=EventType.QUOTE,
            timestamp=datetime(2024, 1, 1),
            symbol="600000",
            price=10.5,
            bid1_price=10.49,
            ask1_price=10.51,
        )
        assert e.symbol == "600000"
        assert e.price == 10.5
        assert e.bid1_price == 10.49
        assert e.ask1_price == 10.51


class TestOrderEvent:
    def test_order_event_creation(self):
        e = OrderEvent(
            type=EventType.ORDER_SUBMITTED,
            timestamp=datetime(2024, 1, 1),
            order_id="ord_1",
            symbol="600000",
            direction="buy",
            status="submitted",
            filled_volume=0,
        )
        assert e.symbol == "600000"
        assert e.direction == "buy"
        assert e.status == "submitted"


class TestTradeEvent:
    def test_trade_event_creation(self):
        e = TradeEvent(
            type=EventType.TRADE,
            timestamp=datetime(2024, 1, 1),
            trade_id="trd_1",
            order_id="ord_1",
            symbol="600000",
            direction="buy",
            price=10.0,
            volume=500,
            commission=1.5,
        )
        assert e.price == 10.0
        assert e.commission == 1.5
        assert e.volume == 500


class TestRiskBreachEvent:
    def test_risk_breach_event(self):
        e = RiskBreachEvent(
            type=EventType.RISK_BREACH,
            timestamp=datetime(2024, 1, 1),
            strategy_id="test",
            rule_name="max_drawdown",
            reason="Drawdown 25% > 20%",
        )
        assert e.rule_name == "max_drawdown"
        assert "25%" in e.reason


class TestTimerEvent:
    def test_timer_event(self):
        e = TimerEvent(
            type=EventType.TIMER,
            timestamp=datetime(2024, 1, 1),
            timer_id=1,
            interval_seconds=60.0,
        )
        assert e.timer_id == 1
        assert e.interval_seconds == 60.0


class TestSystemEvent:
    def test_system_event(self):
        e = SystemEvent(
            type=EventType.ENGINE_START,
            timestamp=datetime(2024, 1, 1),
            message="Engine started",
        )
        assert e.message == "Engine started"
