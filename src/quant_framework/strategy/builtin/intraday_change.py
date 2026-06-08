"""Intraday Price Change Strategy.

Migrated from:
- 同花顺 经典策略/5分钟涨跌幅.py
- 同花顺 经典策略/反弹回落幅度.py

Monitors price change over a configurable time window and triggers
when the change exceeds a threshold.

"5分钟涨跌幅" — compares current price vs. price N seconds ago.
"反弹回落幅度" — compares current price vs. the range (high/low) within a time window.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy


@dataclass
class IntradayChangeConfig:
    symbol: str
    window_seconds: float = 300.0          # Time window in seconds (300 = 5 min)
    threshold_pct: float = 0.05            # Change % threshold (0.05 = 5%)
    direction: str = "down"                # "up" (rise) | "down" (fall)
    mode: str = "snapshot"                 # "snapshot" (compare N sec ago) | "range" (compare high/low in window)
    action: str = "sell"                   # "buy" | "sell"
    volume: int = 500
    use_limit_price: bool = False


class IntradayChangeStrategy(BaseStrategy):
    """日内涨跌幅 / 反弹回落幅度策略.

    Snapshot mode: compares price now vs. price window_seconds ago.
    Range mode: compares price now vs. high (for down) or low (for up) in window.
    """

    def __init__(self, ctx: StrategyContext, cfg: IntradayChangeConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._price_history: list[tuple[datetime, float]] = []
        self._triggered: bool = False

    def on_init(self) -> None:
        self.ctx.logger.info(
            "intraday_change_init",
            symbol=self.cfg.symbol,
            window_seconds=self.cfg.window_seconds,
            threshold=self.cfg.threshold_pct,
        )

    def on_quote(self, quote: Quote) -> list:
        if self._triggered:
            return None

        now = datetime.now()
        price = quote.price

        # Maintain rolling price history
        self._price_history.append((now, price))
        cutoff = now - timedelta(seconds=self.cfg.window_seconds)
        self._price_history = [(t, p) for t, p in self._price_history if t >= cutoff]

        if len(self._price_history) < 2:
            return None

        reference_price: float
        if self.cfg.mode == "snapshot":
            # Compare to oldest price in window
            reference_price = self._price_history[0][1]
        else:
            # Range mode: for "down" use max (highest in window),
            # for "up" use min (lowest in window)
            prices_in_window = [p for _, p in self._price_history]
            reference_price = max(prices_in_window) if self.cfg.direction == "down" else min(prices_in_window)

        if reference_price == 0:
            return None

        change_pct = (price - reference_price) / reference_price

        triggered = (
            (self.cfg.direction == "down" and change_pct <= -self.cfg.threshold_pct)
            or (self.cfg.direction == "up" and change_pct >= self.cfg.threshold_pct)
        )

        if not triggered:
            return None

        self._triggered = True
        reason = f"{self.cfg.window_seconds/60:.0f}分钟{'跌幅' if self.cfg.direction == 'down' else '涨幅'} {abs(change_pct):.2%} >= {self.cfg.threshold_pct:.2%}"

        if self.cfg.action == "sell":
            return self.sell(
                self.cfg.symbol,
                price=quote.limit_down if self.cfg.use_limit_price else price,
                volume=self.cfg.volume,
                reason=reason,
            )
        else:
            return self.buy(
                self.cfg.symbol,
                price=quote.limit_up if self.cfg.use_limit_price else price,
                volume=self.cfg.volume,
                reason=reason,
            )
