"""Bounce Buying Strategy (反弹买入).

Migrated from: 同花顺 经典策略/反弹买入.py

Two-phase trigger:
1. Price drops from base by >= drop_threshold (下跌触发)
2. Then price rebounds from the low by >= bounce_threshold (反弹触发) → BUY
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class BounceBuyConfig:
    symbol: str
    drop_threshold: float = 0.03        # 3% drop from base price
    bounce_threshold: float = 0.05      # 5% bounce from low point
    volume: int = 100


class BounceBuyStrategy(BaseStrategy):
    """反弹买入策略.

    Monitors price until it drops by drop_threshold from base,
    then waits for a bounce_back of bounce_threshold from the low.
    """

    def __init__(self, ctx: StrategyContext, cfg: BounceBuyConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._base_price: float | None = None
        self._low_price: float | None = None
        self._drop_triggered: bool = False
        self._initialized: bool = False

    def on_init(self) -> None:
        self.ctx.logger.info(
            "bounce_buy_init",
            symbol=self.cfg.symbol,
            drop=self.cfg.drop_threshold,
            bounce=self.cfg.bounce_threshold,
        )

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        price = quote.price

        # Initialize base price
        if not self._initialized:
            self._base_price = price
            self._initialized = True
            return None

        # Phase 1: Check for drop
        if not self._drop_triggered:
            drop_pct = (price - self._base_price) / self._base_price
            if drop_pct <= -self.cfg.drop_threshold:
                self._drop_triggered = True
                self._low_price = price
                self.ctx.logger.info(
                    "bounce_buy_drop_triggered",
                    symbol=self.cfg.symbol,
                    base_price=self._base_price,
                    low_price=price,
                    drop_pct=f"{drop_pct:.2%}",
                )
            return None

        # Phase 2: Track low and check for bounce
        if price < (self._low_price or 0):
            self._low_price = price
            return None

        if self._low_price and self._low_price > 0:
            bounce_pct = (price - self._low_price) / self._low_price
            if bounce_pct >= self.cfg.bounce_threshold:
                # Reset state
                self._drop_triggered = False
                self._base_price = price
                return self.buy(
                    self.cfg.symbol,
                    price=price,
                    volume=self.cfg.volume,
                    reason=f"反弹买入: 从{self._low_price:.2f}反弹{bounce_pct:.2%}",
                )

        return None
