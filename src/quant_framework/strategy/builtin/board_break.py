"""Board Break Pre-Sell / Limit-Up Break Prediction.

Migrated from:
- 同花顺 经典策略/炸板预卖.py
- 同花顺 经典策略/开板预撤单.py

When a held stock is at limit-up but the buy-1 order book value drops
below a threshold, the limit-up board is likely to break (开板).
Pre-sell or cancel pending buy orders before the break happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from quant_framework.data.models import Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class BoardBreakConfig:
    mode: str = "sell"                    # "sell" (预卖) | "cancel" (预撤单)
    board_value_threshold: float = 10_000_000.0  # 1000万 封单金额阈值
    sell_rate: float = 0.50               # 减仓比例 (only for "sell" mode)
    monitor_holdings: bool = True         # Monitor current holdings
    monitor_orders: bool = False          # Monitor pending buy orders


class BoardBreakStrategy(BaseStrategy):
    """炸板预卖 / 开板预撤单策略.

    Monitors limit-up positions/orders. If buy-1 order book value
    (封单金额 = b1_p * b1_v) drops below threshold, the board is
    likely breaking — take defensive action.
    """

    def __init__(self, ctx: StrategyContext, cfg: BoardBreakConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._triggered: set[str] = set()  # Already-actioned symbols

    def on_init(self) -> None:
        self.ctx.logger.info(
            "board_break_init",
            mode=self.cfg.mode,
            threshold=self.cfg.board_value_threshold,
        )

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        symbol = quote.symbol

        if symbol in self._triggered:
            return None

        # Only care about stocks at limit-up
        if not quote.is_limit_up:
            return None

        # Calculate buy-1 order book value (封单金额)
        board_value = quote.bid1_value

        if board_value >= self.cfg.board_value_threshold:
            return None

        self._triggered.add(symbol)

        if self.cfg.mode == "sell":
            return self._pre_sell(symbol, quote, board_value)
        elif self.cfg.mode == "cancel":
            return self._pre_cancel(symbol, quote, board_value)

        return None

    def _pre_sell(self, symbol: str, quote: Quote, board_value: float) -> list[Signal]:
        """Pre-sell a held position before the board breaks."""
        pos = self.ctx.get_position(symbol)
        if not pos or pos.available <= 0:
            return []

        self.ctx.logger.info(
            "board_break_sell",
            symbol=symbol,
            board_value=board_value,
            threshold=self.cfg.board_value_threshold,
        )
        return self.sell(
            symbol,
            price=None,  # Market
            position_pct=self.cfg.sell_rate,
            reason=f"炸板预警: 封单{board_value/10000:.0f}万 < {self.cfg.board_value_threshold/10000:.0f}万阈值",
        )

    def _pre_cancel(self, symbol: str, quote: Quote, board_value: float) -> list[Signal]:
        """Cancel pending buy orders for this symbol."""
        self.ctx.logger.info(
            "board_break_cancel",
            symbol=symbol,
            board_value=board_value,
        )
        # Cancel is handled by broker directly — we return empty signals
        # and the strategy context will handle cancellation
        return self.sell(
            symbol,
            price=None,
            volume=0,  # Zero volume = cancel signal
            reason=f"开板预撤单: 封单{boad_value/10000:.0f}万 < 阈值",
        )
