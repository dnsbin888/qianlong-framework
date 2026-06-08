"""止损规则 — 基本止损 + 动态止损。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class AutoStopLossRule(BaseRule):
    """基本止损规则。

    当持仓亏损达到阈值时触发卖出。

    Attributes:
        threshold: 止损阈值 (负值，如 -0.05 表示 -5%)
    """

    def __init__(self, threshold: float = -0.05):
        self.threshold = threshold

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is None:
            return None

        avg_cost = position.get("avg_cost", 0)
        price = market_data.get("price", position.get("last_price", 0))
        if avg_cost <= 0 or price <= 0:
            return None

        pnl_pct = (price / avg_cost - 1)
        if pnl_pct <= self.threshold:
            return RuleAction(
                action="sell",
                symbol=position.get("symbol", ""),
                qty=position.get("qty", 0),
                price=price,
                reason=f"止损({pnl_pct*100:.1f}%≤{self.threshold*100:.0f}%)",
            )
        return None
