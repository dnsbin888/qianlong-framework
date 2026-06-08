"""Moving Average Condition Strategy (均线条件).

Migrated from: 同花顺 经典策略/均线条件.py

Triggers when price crosses a specified moving average.
- Price crosses below MA → sell
- Price crosses above MA → buy
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy


@dataclass
class MAConditionConfig:
    symbol: str
    ma_period: int = 20
    direction: str = "down"     # "down" = price falls to MA (sell), "up" = price rises to MA (buy)
    volume: int = 500
    period: str = "1d"          # K-line period for MA calculation


class MAConditionStrategy(BaseStrategy):
    """均线条件策略.

    Monitors price vs. moving average and triggers when they cross.
    """

    def __init__(self, ctx: StrategyContext, cfg: MAConditionConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg

    def on_init(self) -> None:
        self.ctx.logger.info(
            "ma_condition_init",
            symbol=self.cfg.symbol,
            period=self.cfg.ma_period,
            direction=self.cfg.direction,
        )

    def on_quote(self, quote: Quote) -> list:
        price = quote.price
        ma = self.sma(self.cfg.symbol, self.cfg.ma_period)
        if ma is None or len(ma) < self.cfg.ma_period:
            return None

        ma_value = ma.iloc[-1]

        if self.cfg.direction == "down" and price <= ma_value:
            return self.sell(
                self.cfg.symbol,
                price=price,
                volume=self.cfg.volume,
                reason=f"均线卖出: price={price:.2f} <= MA{self.cfg.ma_period}={ma_value:.2f}",
            )
        elif self.cfg.direction == "up" and price > ma_value:
            return self.buy(
                self.cfg.symbol,
                price=price,
                volume=self.cfg.volume,
                reason=f"均线买入: price={price:.2f} > MA{self.cfg.ma_period}={ma_value:.2f}",
            )

        return None
