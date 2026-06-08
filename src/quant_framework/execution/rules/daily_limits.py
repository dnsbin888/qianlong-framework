"""日交易限制规则 — 日下单次数 + 日亏损额度。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class MaxDailyTradesRule(BaseRule):
    """下单频率限制规则。

    当日成交次数达到上限后，拒绝新开仓（卖出/止损不受限）。

    Attributes:
        max_trades: 单日最大成交次数
    """

    def __init__(self, max_trades: int = 4):
        self.max_trades = max_trades

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is not None:
            return None  # 频率限制是全局规则

        daily_count = context.get("daily_trade_count", 0)
        if daily_count >= self.max_trades:
            return RuleAction(
                action="reject",
                reason=f"日交易次数达上限({daily_count}/{self.max_trades})",
            )
        return None


class DailyLossLimitRule(BaseRule):
    """日内亏损限制规则。

    当日累计亏损超限后，立即平掉所有持仓并停止交易。

    Attributes:
        max_loss_pct: 日亏损上限 (负值)
        initial_capital: 初始资金
    """

    def __init__(self, max_loss_pct: float = -0.05, initial_capital: float = 1_000_000):
        self.max_loss_pct = max_loss_pct
        self.initial_capital = initial_capital

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is not None:
            return None  # 全局规则

        daily_loss = context.get("daily_loss_total", 0.0)
        if self.max_loss_pct < 0:
            loss_limit = self.max_loss_pct * self.initial_capital
            if daily_loss <= loss_limit:
                return RuleAction(
                    action="liquidate_all",
                    reason=f"日亏损超限({daily_loss:.0f}≤{loss_limit:.0f})",
                )
        return None


class ConsecutiveLossRule(BaseRule):
    """连续亏损降仓规则。

    连续亏损 N 笔后，降低每次开仓比例。

    Attributes:
        max_consecutive: 连续亏损次数触发阈值
        reduction_factor: 仓位缩减因子 (如 0.5 = 减半)
        loss_tracker: 外部追踪器 [bool, ...] 最近交易盈亏列表
    """

    def __init__(self, max_consecutive: int = 3, reduction_factor: float = 0.5):
        self.max_consecutive = max_consecutive
        self.reduction_factor = reduction_factor
        self._recent_losses: list[bool] = []  # True=盈, False=亏

    def record_trade(self, is_win: bool) -> None:
        """记录一次交易结果。"""
        self._recent_losses.append(is_win)
        if len(self._recent_losses) > 20:
            self._recent_losses = self._recent_losses[-20:]

    def get_position_multiplier(self) -> float:
        """获取当前仓位倍数 (1.0=正常, <1.0=缩减)。"""
        consecutive = 0
        for loss in reversed(self._recent_losses):
            if not loss:  # 亏损
                consecutive += 1
            else:
                break
        if consecutive >= self.max_consecutive:
            return self.reduction_factor ** (consecutive - self.max_consecutive + 1)
        return 1.0

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is not None:
            return None
        m = self.get_position_multiplier()
        if m < 1.0:
            return RuleAction(
                action="reduce",
                reason=f"连续亏损降仓 (x{m:.2f})",
                meta={"position_multiplier": m},
            )
        return None
