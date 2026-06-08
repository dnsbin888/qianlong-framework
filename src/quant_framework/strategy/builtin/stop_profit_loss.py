"""Stop Profit / Stop Loss Strategy.

Migrated from: 同花顺 经典策略/止盈止损.py

Triggers:
1. Price-based: rise/fall to a specific price level
2. MA-based: price crosses a moving average
3. Percentage-based: rise/fall by a certain percentage

Example: Monitor stock, profit-take at +10%, stop-loss at 5-day MA.
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class StopProfitLossConfig:
    symbol: str
    take_profit_pct: float | None = None     # e.g. 0.10 = +10%
    stop_loss_pct: float | None = None       # e.g. -0.05 = -5%
    take_profit_price: float | None = None   # Absolute price trigger
    stop_loss_price: float | None = None
    ma_period: int | None = None             # MA-based trigger
    ma_direction: str = "cross_below"        # "cross_below" | "cross_above"
    volume: int = 500
    close_position: bool = True              # True = close all, False = partial


class StopProfitLossStrategy(BaseStrategy):
    """止盈止损策略."""

    def __init__(self, ctx: StrategyContext, cfg: StopProfitLossConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._entry_price: float | None = None

    def on_init(self) -> None:
        pos = self.ctx.get_position(self.cfg.symbol)
        if pos and pos.avg_cost > 0:
            self._entry_price = pos.avg_cost
        self.ctx.logger.info(
            "stop_profit_loss_init",
            symbol=self.cfg.symbol,
            entry_price=self._entry_price,
        )

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        price = quote.price
        pos = self.ctx.get_position(self.cfg.symbol)
        if not pos or pos.available <= 0:
            return None

        if self._entry_price is None:
            self._entry_price = pos.avg_cost

        # --- Percentage-based triggers ---
        if self.cfg.take_profit_pct is not None and self._entry_price:
            pct_change = (price - self._entry_price) / self._entry_price
            if pct_change >= self.cfg.take_profit_pct:
                return self._close(
                    price,
                    f"止盈: +{pct_change:.2%} >= {self.cfg.take_profit_pct:.2%}",
                )

        if self.cfg.stop_loss_pct is not None and self._entry_price:
            pct_change = (price - self._entry_price) / self._entry_price
            if pct_change <= self.cfg.stop_loss_pct:
                return self._close(
                    price,
                    f"止损: {pct_change:.2%} <= {self.cfg.stop_loss_pct:.2%}",
                )

        # --- Absolute price triggers ---
        if self.cfg.take_profit_price and price >= self.cfg.take_profit_price:
            return self._close(price, f"止盈: price >= {self.cfg.take_profit_price}")

        if self.cfg.stop_loss_price and price <= self.cfg.stop_loss_price:
            return self._close(price, f"止损: price <= {self.cfg.stop_loss_price}")

        # --- MA-based trigger ---
        if self.cfg.ma_period:
            ma = self.sma(self.cfg.symbol, self.cfg.ma_period)
            if ma is not None and len(ma) >= 2:
                prev_price = price  # approximation — ideally use prev close
                prev_ma, curr_ma = ma.iloc[-2], ma.iloc[-1]

                if self.cfg.ma_direction == "cross_below":
                    if prev_price > prev_ma and price <= curr_ma:
                        return self._close(price, f"止损: price crossed below MA{self.cfg.ma_period}")
                elif self.cfg.ma_direction == "cross_above":
                    if prev_price < prev_ma and price >= curr_ma:
                        return self.buy(
                            self.cfg.symbol,
                            price=price,
                            volume=self.cfg.volume,
                            reason=f"买入: price crossed above MA{self.cfg.ma_period}",
                        )

        return None

    def _close(self, price: float, reason: str) -> list[Signal]:
        if self.cfg.close_position:
            return self.close_position(self.cfg.symbol, reason=reason)
        return self.sell(self.cfg.symbol, price=price, volume=self.cfg.volume, reason=reason)
