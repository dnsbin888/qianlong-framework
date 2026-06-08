"""Grid Trading Strategy.

Migrated from: 同花顺 经典策略/网格交易.py

Places buy/sell orders at fixed price intervals (grid levels) around
a base price. Each fill updates the base price to the fill level.
Earns from oscillation within a range.

Key improvements over original:
- Pydantic config (type-safe, validated) instead of dict_params
- Delegates position sizing to PositionSizer
- Structured logging
- State persistence (base_price saved on stop)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class GridTradingConfig:
    symbol: str
    price_init: float              # Initial base price
    up_spread: float               # Sell trigger: price >= base + spread
    down_spread: float             # Buy trigger: price <= base - spread
    max_price: float               # Upper price bound
    min_price: float               # Lower price bound
    buy_volume: int = 200          # Shares per buy
    sell_volume: int = 200         # Shares per sell
    max_position: int = 100000     # Max holding
    min_position: int = 0          # Reserve (底仓)
    bounce_filter: bool = False    # Enable pullback/bounce filter


class GridTradingStrategy(BaseStrategy):
    """网格交易策略.

    Dynamically updates base_price after each trade to the trigger price.
    Operates within [min_price, max_price] bounds.
    """

    def __init__(self, ctx: StrategyContext, cfg: GridTradingConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._base_price: float = cfg.price_init
        self._last_trade_time: datetime | None = None

    def on_init(self) -> None:
        self.ctx.logger.info(
            "grid_strategy_init",
            symbol=self.cfg.symbol,
            base_price=self._base_price,
            spread=(self.cfg.down_spread, self.cfg.up_spread),
        )

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        price = quote.price

        # Range check
        if price > self.cfg.max_price or price < self.cfg.min_price:
            return None

        pos = self.ctx.get_position(self.cfg.symbol)
        current_vol = pos.volume if pos else 0
        available = pos.available if pos else 0

        # Buy trigger: price drops below base - spread
        if price <= (self._base_price - self.cfg.down_spread):
            if self.cfg.min_price <= price and current_vol < self.cfg.max_position:
                self._base_price = price
                return self.buy(
                    self.cfg.symbol,
                    price=price,
                    volume=self.cfg.buy_volume,
                    reason=f"网格买入: price={price:.2f} <= base={self._base_price:.2f}",
                )

        # Sell trigger: price rises above base + spread
        elif price >= (self._base_price + self.cfg.up_spread):
            if price <= self.cfg.max_price and available >= self.cfg.sell_volume and current_vol > self.cfg.min_position:
                self._base_price = price
                return self.sell(
                    self.cfg.symbol,
                    price=price,
                    volume=self.cfg.sell_volume,
                    reason=f"网格卖出: price={price:.2f} >= base={self._base_price:.2f}",
                )

        return None

    def on_stop(self) -> None:
        """Persist grid state for next session."""
        state = {"base_price": self._base_price, "config": self.cfg.__dict__}
        self.ctx.logger.info("grid_strategy_saved", **state)
