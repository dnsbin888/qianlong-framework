"""仓位计算规则 — 根据信号强度确定开仓比例。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 的仓位逻辑抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class PositionSizingRule(BaseRule):
    """信号强度加权仓位规则。

    Attributes:
        sizing_map: 信号强度 → 仓位比例映射
        min_qty: 最小股数
        lot_size: 每手股数 (A股=100)
    """

    def __init__(
        self,
        sizing_map: dict[int, float] | None = None,
        min_qty: int = 100,
        lot_size: int = 100,
    ):
        self.sizing_map = sizing_map or {5: 0.12, 4: 0.10, 3: 0.08, 2: 0.06, 1: 0.04}
        self.min_qty = min_qty
        self.lot_size = lot_size

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        return None  # 仓位规则由 PaperAccount 显式调用

    def calculate_shares(self, signal_strength: int, available_cash: float, price: float) -> int:
        """根据信号强度计算应买入的股数。

        Args:
            signal_strength: 信号强度 (1-5)
            available_cash: 可用资金
            price: 当前股价

        Returns:
            int — 建议股数 (整手)
        """
        pct = self.sizing_map.get(signal_strength, 0.15)
        raw_qty = int(available_cash * pct / max(price, 0.01) / self.lot_size) * self.lot_size
        return max(self.min_qty, raw_qty)
