"""Limit-Up Chasing Strategy (涨停追单/打板).

Migrated from: 同花顺 经典策略/涨停追单.py

Monitors a watchlist of stocks. When any stock hits its limit-up price,
immediately buys it. Continues until a target number of positions is filled.

"谁先涨停先买谁" — first to hit limit-up, first to buy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class LimitUpChaseConfig:
    watchlist: list[str] = field(default_factory=list)  # Stocks to monitor
    max_positions: int = 3                               # Max stocks to buy
    price: str = "zxjg"                                  # Order price type
    volume: int = 1000                                   # Shares per buy


class LimitUpChaseStrategy(BaseStrategy):
    """涨停追单策略.

    Monitors a watchlist for limit-up hits. Each stock that hits
    limit-up gets bought immediately. Stops after max_positions fills.
    """

    def __init__(self, ctx: StrategyContext, cfg: LimitUpChaseConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._filled_count: int = 0
        self._bought: set[str] = set()

    def on_init(self) -> None:
        self.ctx.logger.info(
            "limit_up_chase_init",
            watchlist=self.cfg.watchlist,
            max_positions=self.cfg.max_positions,
        )

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        symbol = quote.symbol

        # Skip if not in watchlist or already bought or quota filled
        if symbol not in self.cfg.watchlist:
            return None
        if symbol in self._bought:
            return None
        if self._filled_count >= self.cfg.max_positions:
            return None

        # Check limit-up
        if quote.is_limit_up:
            self._bought.add(symbol)
            self._filled_count += 1
            self.ctx.logger.info(
                "limit_up_hit",
                symbol=symbol,
                price=quote.price,
                count=self._filled_count,
            )
            return self.buy(
                symbol,
                price=None,  # Market order at 最新价
                volume=self.cfg.volume,
                reason=f"涨停追单 #{self._filled_count}/{self.cfg.max_positions}",
            )

        return None

    def on_stop(self) -> None:
        self.ctx.logger.info(
            "limit_up_chase_summary",
            bought=list(self._bought),
            total=self._filled_count,
        )
