"""熔断规则 — 日内亏损超限暂停买入。

P0-模拟-01: 从 PaperAccount._circuit_breaker_triggered() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class CircuitBreakerRule(BaseRule):
    """大盘熔断/日内亏损限制规则。

    当日累计亏损超过阈值时，暂停新的买入操作（卖出/止损继续）。

    Attributes:
        max_daily_loss_pct: 日亏损上限 (负值，如 -0.05 = -5%)
        initial_capital: 初始资金，用于计算绝对亏损金额
    """

    def __init__(self, max_daily_loss_pct: float = -0.05, initial_capital: float = 1_000_000):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.initial_capital = initial_capital

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        """检查是否触发熔断 — 仅限制买入，不阻止卖出。"""
        if position is not None:
            return None  # 熔断是全局规则，不检查单条持仓

        daily_loss_total = context.get("daily_loss_total", 0.0)
        max_loss = self.max_daily_loss_pct

        # 计算绝对亏损阈值
        if max_loss < 0:
            loss_threshold = abs(max_loss) * self.initial_capital
            if daily_loss_total <= -loss_threshold:
                return RuleAction(
                    action="reject",
                    reason=f"熔断: 日亏损{daily_loss_total:.0f}≥{loss_threshold:.0f}({max_loss*100:.0f}%)",
                    meta={"circuit_breaker": True},
                )

        return None
