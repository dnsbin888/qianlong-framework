"""双均线金叉死叉策略 (MA Cross).

快线上穿慢线 (金叉) → 买入
快线下穿慢线 (死叉) → 卖出

与现有 ``ma_condition`` 的区别:
    - ma_condition: 价格 vs 单条均线 (价格突破MA)
    - ma_cross: 快均线 vs 慢均线 (两条MA交叉)
"""

from __future__ import annotations

from dataclasses import dataclass

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy


@dataclass
class MACrossConfig:
    """双均线策略配置."""
    symbol: str = "600000"
    fast_period: int = 5
    slow_period: int = 20
    volume: int = 1000
    period: str = "1d"


class MACrossStrategy(BaseStrategy):
    """双均线金叉死叉策略.

    快线 (MA_fast) 上穿慢线 (MA_slow) → 买入信号
    快线 (MA_fast) 下穿慢线 (MA_slow) → 卖出信号
    """

    def __init__(self, ctx: StrategyContext, cfg: MACrossConfig) -> None:
        super().__init__(ctx)
        self.cfg: MACrossConfig = cfg

    def on_init(self) -> None:
        self.ctx.logger.info(
            "ma_cross_init",
            symbol=self.cfg.symbol,
            fast=self.cfg.fast_period,
            slow=self.cfg.slow_period,
        )

    def on_quote(self, quote: Quote) -> list | None:
        """每条行情回调: 计算双均线，检测金叉/死叉."""
        fast_ma = self.sma(self.cfg.symbol, self.cfg.fast_period)
        slow_ma = self.sma(self.cfg.symbol, self.cfg.slow_period)

        if fast_ma is None or slow_ma is None:
            return None
        if len(fast_ma) < 2 or len(slow_ma) < 2:
            return None

        curr_fast: float = fast_ma.iloc[-1]
        curr_slow: float = slow_ma.iloc[-1]
        prev_fast: float = fast_ma.iloc[-2]
        prev_slow: float = slow_ma.iloc[-2]

        # 金叉: 快线从下方穿越到上方
        if prev_fast <= prev_slow and curr_fast > curr_slow:
            return self.buy(
                self.cfg.symbol,
                price=quote.price,
                volume=self.cfg.volume,
                reason=(
                    f"金叉: MA{self.cfg.fast_period}={curr_fast:.2f} "
                    f"上穿 MA{self.cfg.slow_period}={curr_slow:.2f}"
                ),
            )

        # 死叉: 快线从上方穿越到下方
        if prev_fast >= prev_slow and curr_fast < curr_slow:
            return self.sell(
                self.cfg.symbol,
                price=quote.price,
                volume=self.cfg.volume,
                reason=(
                    f"死叉: MA{self.cfg.fast_period}={curr_fast:.2f} "
                    f"下穿 MA{self.cfg.slow_period}={curr_slow:.2f}"
                ),
            )

        return None
