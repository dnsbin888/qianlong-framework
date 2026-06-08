"""Tests for risk management rules."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from quant_framework.core.constants import OrderDirection, RiskDecision
from quant_framework.engine.context import PortfolioSnapshot, PositionInfo, StrategyContext
from quant_framework.risk.rules import (
    BlacklistRule,
    DailyLossLimitRule,
    MaxDrawdownRule,
    OrderFrequencyRule,
    PositionLimitRule,
    RiskResult,
    TotalPositionsRule,
)


@dataclass
class FakeOrder:
    direction: str
    symbol: str = "600000"
    price: float = 10.0
    volume: int = 1000
    amount: float = 10000.0


def _make_ctx(equity: float = 100000.0, cash: float = 50000.0,
              positions: dict | None = None) -> StrategyContext:
    """Build a StrategyContext with known portfolio state."""
    portfolio = PortfolioSnapshot(
        total_equity=equity,
        cash=cash,
        positions=positions or {},
    )
    return StrategyContext(strategy_id="test", portfolio=portfolio)


# ---------------------------------------------------------------------------
# RiskResult
# ---------------------------------------------------------------------------

class TestRiskResult:
    def test_allow(self):
        r = RiskResult.allow()
        assert r.decision == RiskDecision.ALLOW

    def test_block(self):
        r = RiskResult.block("test reason")
        assert r.decision == RiskDecision.BLOCK
        assert "test reason" in r.reason

    def test_reduce(self):
        r = RiskResult.reduce("too big", volume=500)
        assert r.decision == RiskDecision.REDUCE
        assert r.adjusted_volume == 500


# ---------------------------------------------------------------------------
# MaxDrawdownRule
# ---------------------------------------------------------------------------

class TestMaxDrawdownRule:
    def test_allows_when_equity_rising(self):
        rule = MaxDrawdownRule(0.20)
        ctx = _make_ctx(equity=110000.0)
        result = rule.check(ctx, FakeOrder("buy"))
        assert result.decision == RiskDecision.ALLOW

    def test_blocks_when_drawdown_exceeded(self):
        rule = MaxDrawdownRule(0.20)
        # Step 1: Set peak by pushing equity above the initial default
        ctx_high = _make_ctx(equity=120000.0)
        rule.check(ctx_high, FakeOrder("buy"))  # peak set to 120k

        # Step 2: Drop to 84k (30% drawdown from 120k > 20% limit)
        ctx_low = _make_ctx(equity=84000.0)
        result = rule.check(ctx_low, FakeOrder("buy"))
        assert result.decision == RiskDecision.BLOCK

    def test_always_allows_sell(self):
        rule = MaxDrawdownRule(0.20)
        ctx_high = _make_ctx(equity=120000.0)
        rule.check(ctx_high, FakeOrder("buy"))  # set peak

        ctx_low = _make_ctx(equity=70000.0)
        result = rule.check(ctx_low, FakeOrder("sell"))
        assert result.decision == RiskDecision.ALLOW


# ---------------------------------------------------------------------------
# PositionLimitRule
# ---------------------------------------------------------------------------

class TestPositionLimitRule:
    def test_allows_when_under_limit(self):
        rule = PositionLimitRule(0.30)
        ctx = _make_ctx(equity=100000.0)
        result = rule.check(ctx, FakeOrder("buy", amount=5000.0))
        assert result.decision == RiskDecision.ALLOW

    def test_blocks_when_over_limit(self):
        rule = PositionLimitRule(0.30)
        ctx = _make_ctx(equity=100000.0)
        # Buy 40000 worth — 40% > 30% limit
        result = rule.check(ctx, FakeOrder("buy", amount=40000.0))
        assert result.decision == RiskDecision.BLOCK

    def test_allows_sell_always(self):
        rule = PositionLimitRule(0.30)
        ctx = _make_ctx(equity=100000.0)
        result = rule.check(ctx, FakeOrder("sell", amount=100000.0))
        assert result.decision == RiskDecision.ALLOW


# ---------------------------------------------------------------------------
# TotalPositionsRule
# ---------------------------------------------------------------------------

class TestTotalPositionsRule:
    def test_allows_under_max(self):
        rule = TotalPositionsRule(10)
        ctx = _make_ctx(equity=100000.0, positions={
            "000001": PositionInfo(symbol="000001", volume=1000),
        })
        result = rule.check(ctx, FakeOrder("buy", symbol="600000"))
        assert result.decision == RiskDecision.ALLOW

    def test_allows_if_already_holding_symbol(self):
        rule = TotalPositionsRule(1)
        ctx = _make_ctx(equity=100000.0, positions={
            "600000": PositionInfo(symbol="600000", volume=500),
        })
        result = rule.check(ctx, FakeOrder("buy", symbol="600000"))
        assert result.decision == RiskDecision.ALLOW

    def test_blocks_when_at_max_and_new_symbol(self):
        rule = TotalPositionsRule(1)
        ctx = _make_ctx(equity=100000.0, positions={
            "000001": PositionInfo(symbol="000001", volume=1000),
        })
        result = rule.check(ctx, FakeOrder("buy", symbol="600000"))
        assert result.decision == RiskDecision.BLOCK

    def test_allows_sell(self):
        rule = TotalPositionsRule(1)
        ctx = _make_ctx(equity=100000.0)
        result = rule.check(ctx, FakeOrder("sell"))
        assert result.decision == RiskDecision.ALLOW


# ---------------------------------------------------------------------------
# DailyLossLimitRule
# ---------------------------------------------------------------------------

class TestDailyLossLimitRule:
    def test_allows_when_no_loss(self):
        rule = DailyLossLimitRule(50000.0)
        ctx = _make_ctx(equity=100000.0)
        result = rule.check(ctx, FakeOrder("buy"))
        assert result.decision == RiskDecision.ALLOW

    def test_blocks_after_loss_exceeded(self):
        rule = DailyLossLimitRule(50000.0)
        ctx = _make_ctx(equity=100000.0)
        # Simulate large loss
        rule.update_pnl("test", -60000.0)
        result = rule.check(ctx, FakeOrder("buy"))
        assert result.decision == RiskDecision.BLOCK


# ---------------------------------------------------------------------------
# OrderFrequencyRule
# ---------------------------------------------------------------------------

class TestOrderFrequencyRule:
    def test_first_order_allowed(self):
        rule = OrderFrequencyRule(5.0)
        ctx = _make_ctx()
        result = rule.check(ctx, FakeOrder("buy"))
        assert result.decision == RiskDecision.ALLOW

    def test_second_order_too_soon_blocked(self):
        rule = OrderFrequencyRule(999.0)  # Very long cooldown
        ctx = _make_ctx()
        rule.check(ctx, FakeOrder("buy"))
        result = rule.check(ctx, FakeOrder("buy"))
        assert result.decision == RiskDecision.BLOCK


# ---------------------------------------------------------------------------
# BlacklistRule
# ---------------------------------------------------------------------------

class TestBlacklistRule:
    def test_allows_normal_symbol(self):
        rule = BlacklistRule(["000001"])
        ctx = _make_ctx()
        result = rule.check(ctx, FakeOrder("buy", symbol="600000"))
        assert result.decision == RiskDecision.ALLOW

    def test_blocks_blacklisted_symbol(self):
        rule = BlacklistRule(["000001"])
        ctx = _make_ctx()
        result = rule.check(ctx, FakeOrder("buy", symbol="000001"))
        assert result.decision == RiskDecision.BLOCK

    def test_add_remove_symbol(self):
        rule = BlacklistRule()
        rule.add("999999")
        ctx = _make_ctx()
        assert rule.check(ctx, FakeOrder("buy", symbol="999999")).decision == RiskDecision.BLOCK
        rule.remove("999999")
        assert rule.check(ctx, FakeOrder("buy", symbol="999999")).decision == RiskDecision.ALLOW
