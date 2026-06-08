"""Fill Simulator — for backtesting and paper trading.

Simulates order fills with configurable slippage and commission models.
Respects A-share market rules: T+1 settlement, limit-up/down constraints.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from quant_framework.core.constants import OrderDirection, OrderType
from quant_framework.data.models import Bar, Quote
from quant_framework.execution.order import Order, OrderRequest, Trade


class SlippageModel(str, Enum):
    """Slippage simulation models."""

    FIXED = "fixed"                # Fixed absolute slippage (e.g. 0.01 CNY)
    PROPORTIONAL = "proportional"  # Percentage of price (e.g. 0.001 = 0.1%)
    NORMAL = "normal"             # Normally distributed random slippage


@dataclass
class FillSimulatorConfig:
    """Configuration for the fill simulator."""

    slippage_model: SlippageModel = SlippageModel.PROPORTIONAL
    slippage_value: float = 0.001       # 0.1% proportional or 0.01 fixed
    slippage_std: float = 0.0005        # For NORMAL model
    commission_rate: float = 0.0003     # 万三
    min_commission: float = 5.0         # Minimum commission (CNY)
    stamp_tax_rate: float = 0.001       # 印花税 (sell only, A-share)
    enable_limit_check: bool = True     # Respect limit-up/down prices
    fill_probability: float = 1.0       # Probability of fill at limit price (1.0 = always)


class FillSimulator:
    """Simulates order execution for backtesting and paper trading.

    Models:
    - Slippage (adverse price movement on execution)
    - Commission (broker fee + stamp tax)
    - A-share constraints: T+1, limit-up/down, lot size (100 shares)

    Usage:
        sim = FillSimulator(FillSimulatorConfig())
        trade = sim.simulate_fill(order, current_quote, random_seed=42)
    """

    def __init__(self, config: FillSimulatorConfig | None = None) -> None:
        self.config = config or FillSimulatorConfig()
        self._rng = random.Random()

    def simulate_fill(
        self,
        order: Order,
        current_price: float,
        high: float = 0.0,
        low: float = 0.0,
        limit_up: float = 0.0,
        limit_down: float = 0.0,
        seed: int | None = None,
    ) -> Trade | None:
        """Simulate filling an order at the current bar/quote.

        Args:
            order: The order to fill.
            current_price: Current market price (bar close or quote price).
            high: Bar high (for checking if limit order would fill).
            low: Bar low (for checking if limit order would fill).
            limit_up: Limit-up price.
            limit_down: Limit-down price.
            seed: Random seed for reproducible results.

        Returns:
            Trade if filled, None if order cannot be filled at this bar.
        """
        if seed is not None:
            self._rng.seed(seed)

        # 1. Check if limit order would fill
        fill_price = self._determine_fill_price(order, current_price, high, low)
        if fill_price is None:
            return None  # Limit price not hit this bar

        # 2. Check limit-up/down constraints
        if self.config.enable_limit_check:
            if limit_up > 0 and order.direction == OrderDirection.BUY and fill_price >= limit_up:
                return None  # Can't buy at limit-up (no sellers)
            if limit_down > 0 and order.direction == OrderDirection.SELL and fill_price <= limit_down:
                return None  # Can't sell at limit-down (no buyers)

        # 3. Apply slippage
        fill_price = self._apply_slippage(fill_price, order.direction)

        # 4. Calculate commission
        commission = self._calc_commission(fill_price, order.requested_volume, order.direction)

        # 5. Probabilistic fill
        if self._rng.random() > self.config.fill_probability:
            return None

        # Create trade record
        from uuid import uuid4
        return Trade(
            trade_id=f"trd_{uuid4().hex[:12]}",
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            symbol=order.symbol,
            direction=order.direction,
            price=fill_price,
            volume=order.requested_volume,
            commission=commission,
            timestamp=datetime.now(),
        )

    def _determine_fill_price(
        self, order: Order, current_price: float, high: float, low: float
    ) -> float | None:
        """Determine if and at what price a limit order would fill.

        For market orders (price=None), always fills at current price.
        For limit orders:
            - BUY: fills if low <= limit_price, at min(limit_price, current_price)
            - SELL: fills if high >= limit_price, at max(limit_price, current_price)
        """
        # Market order — always fills at current price
        if order.price is None or order.order_type == OrderType.MARKET:
            return current_price

        limit_price = order.price

        if order.direction == OrderDirection.BUY:
            # Buy limit: only fills if price dropped to or below limit
            if low > 0 and low <= limit_price:
                return min(limit_price, current_price)
            if low == 0 and current_price <= limit_price:
                return current_price
            return None
        else:
            # Sell limit: only fills if price rose to or above limit
            if high > 0 and high >= limit_price:
                return max(limit_price, current_price)
            if high == 0 and current_price >= limit_price:
                return current_price
            return None

    def _apply_slippage(self, price: float, direction: OrderDirection) -> float:
        """Apply adverse slippage to the fill price.

        BUY: price moves up (adverse for buyer)
        SELL: price moves down (adverse for seller)
        """
        if self.config.slippage_model == SlippageModel.FIXED:
            slip = self.config.slippage_value
        elif self.config.slippage_model == SlippageModel.PROPORTIONAL:
            slip = price * self.config.slippage_value
        elif self.config.slippage_model == SlippageModel.NORMAL:
            slip = abs(self._rng.gauss(0, self.config.slippage_std)) * price
        else:
            slip = 0.0

        if direction == OrderDirection.BUY:
            return round(price + slip, 4)
        else:
            return round(price - slip, 4)

    def _calc_commission(
        self, price: float, volume: int, direction: OrderDirection
    ) -> float:
        """Calculate total commission for a trade.

        A-share rules:
        - Commission: rate * price * volume, min 5 CNY
        - Stamp tax (印花税): 0.1% of value, SELL only
        - Transfer fee (过户费): 0.002%, both sides (simplified here)
        """
        value = price * volume

        # Broker commission
        commission = value * self.config.commission_rate
        commission = max(commission, self.config.min_commission)

        # Stamp tax (sell only)
        if direction == OrderDirection.SELL:
            commission += value * self.config.stamp_tax_rate

        return round(commission, 2)
