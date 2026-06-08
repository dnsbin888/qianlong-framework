"""Price Condition / Daily Change Strategies.

Migrated from:
- 同花顺 经典策略/股价条件.py
- 同花顺 经典策略/日涨跌幅.py

Triggers when:
1. Price reaches an absolute threshold
2. Daily change % (from pre_close) exceeds a threshold
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy


@dataclass
class PriceConditionConfig:
    symbol: str
    trigger_price: float | None = None      # Absolute price trigger
    change_pct: float | None = None          # Daily change % trigger (e.g. 0.08 = +8%)
    direction: str = "up"                    # "up" | "down"
    action: str = "buy"                      # "buy" | "sell"
    volume: int = 500
    use_limit_price: bool = False            # Use limit-up/limit-down price


class PriceConditionStrategy(BaseStrategy):
    """股价条件 / 日涨跌幅策略.

    Simple threshold-based triggers:
    - Absolute: price >= threshold → action
    - Relative: daily_change_pct >= threshold → action
    """

    def __init__(self, ctx: StrategyContext, cfg: PriceConditionConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._triggered: bool = False  # Fire only once

    def on_init(self) -> None:
        self.ctx.logger.info(
            "price_condition_init",
            symbol=self.cfg.symbol,
            trigger_price=self.cfg.trigger_price,
            change_pct=self.cfg.change_pct,
        )

    def on_quote(self, quote: Quote) -> list:
        if self._triggered:
            return None

        price = quote.price
        triggered = False
        reason = ""

        # Absolute price trigger
        if self.cfg.trigger_price is not None:
            if self.cfg.direction == "up" and price >= self.cfg.trigger_price:
                triggered = True
                reason = f"股价触发: {price:.2f} >= {self.cfg.trigger_price}"
            elif self.cfg.direction == "down" and price <= self.cfg.trigger_price:
                triggered = True
                reason = f"股价触发: {price:.2f} <= {self.cfg.trigger_price}"

        # Daily change % trigger
        if self.cfg.change_pct is not None and not triggered:
            if self.cfg.change_pct > 0 and quote.change_pct >= self.cfg.change_pct:
                triggered = True
                reason = f"涨幅触发: {quote.change_pct:.2%} >= {self.cfg.change_pct:.2%}"
            elif self.cfg.change_pct < 0 and quote.change_pct <= self.cfg.change_pct:
                triggered = True
                reason = f"跌幅触发: {quote.change_pct:.2%} <= {self.cfg.change_pct:.2%}"

        if not triggered:
            return None

        self._triggered = True

        order_price = price
        if self.cfg.use_limit_price:
            order_price = quote.limit_up if self.cfg.action == "buy" else quote.limit_down

        if self.cfg.action == "buy":
            return self.buy(self.cfg.symbol, price=order_price, volume=self.cfg.volume, reason=reason)
        else:
            return self.sell(self.cfg.symbol, price=order_price, volume=self.cfg.volume, reason=reason)
