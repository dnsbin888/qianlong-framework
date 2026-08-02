"""止损规则 — 基本止损 + 动态止损。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class AutoStopLossRule(BaseRule):
    """基本止损规则。

    当持仓亏损达到阈值时触发卖出。
    支持 sell_ratio: 1.0=全卖, 0.5=卖一半

    Attributes:
        threshold: 止损阈值 (负值，如 -0.05 表示 -5%)
        sell_ratio: 卖出比例 (0~1, 默认1.0全卖)
    """

    def __init__(self, threshold: float = -0.05, sell_ratio: float = 1.0):
        self.threshold = threshold
        self.sell_ratio = sell_ratio

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is None:
            return None

        avg_cost = position.get("avg_cost", 0)
        price = market_data.get("price", position.get("last_price", 0))
        if avg_cost <= 0 or price <= 0:
            return None

        pnl_pct = (price / avg_cost - 1)
        if pnl_pct <= self.threshold:
            # 软止损只触发一次: 卖半后标记, 等硬止损清仓
            if self.sell_ratio < 1 and position.get("_soft_triggered"):
                return None
            total_qty = position.get("qty", 0)
            sell_qty = max(100, int(total_qty * self.sell_ratio) // 100 * 100) if self.sell_ratio < 1 else total_qty
            label = f"卖{self.sell_ratio*100:.0f}%" if self.sell_ratio < 1 else "清仓"
            action = RuleAction(
                action="sell",
                symbol=position.get("symbol", ""),
                qty=sell_qty,
                price=price,
                reason=f"止损{label}({pnl_pct*100:.1f}%≤{self.threshold*100:.0f}%)",
            )
            if self.sell_ratio < 1:
                action.meta = {"soft_triggered": True}
            return action
        return None
