"""Scheduled/Timed Trading Strategy (定时交易).

Migrated from: 同花顺 经典策略/定时.py

Executes a trade at a specific time of day.
Example: Buy 100 shares of 000725 at 14:30:00.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy


@dataclass
class ScheduledTradeConfig:
    symbol: str
    target_time: str = "14:30:00"     # "HH:MM:SS"
    action: str = "buy"                # "buy" | "sell"
    price: str = "zxjg"               # "zxjg" | "ztjg" | "dtjg" | float
    volume: int = 100


class ScheduledTradeStrategy(BaseStrategy):
    """定时交易策略.

    Waits until the specified time of day, then executes the trade once.
    """

    def __init__(self, ctx: StrategyContext, cfg: ScheduledTradeConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._fired: bool = False
        # Convert target time to seconds since midnight
        h, m, s = map(int, cfg.target_time.split(":"))
        self._target_seconds = h * 3600 + m * 60 + s

    def on_init(self) -> None:
        self.ctx.logger.info(
            "scheduled_trade_init",
            symbol=self.cfg.symbol,
            target_time=self.cfg.target_time,
            action=self.cfg.action,
        )

    def on_quote(self, quote: Quote) -> list:
        if self._fired:
            return None

        now = datetime.now()
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second

        if current_seconds >= self._target_seconds:
            self._fired = True
            reason = f"定时交易: {self.cfg.target_time}"

            if self.cfg.action == "buy":
                return self.buy(
                    self.cfg.symbol,
                    price=None,  # Market/zxjg
                    volume=self.cfg.volume,
                    reason=reason,
                )
            else:
                return self.sell(
                    self.cfg.symbol,
                    price=None,
                    volume=self.cfg.volume,
                    reason=reason,
                )

        return None
