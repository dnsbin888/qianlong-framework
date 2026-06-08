"""移动止盈规则 — 双层追踪止损。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class AutoTrailingStopRule(BaseRule):
    """双层移动止盈规则。

    Tier 1 (轻仓止盈): 涨幅达到 profit_pct 后，从峰值回落 trail_pct 触发部分卖出
    Tier 2 (重仓止盈): 涨幅达到 profit_pct 后，从峰值回落 trail_pct 触发部分卖出

    Attributes:
        tier: 止盈层级 (1 或 2)
        profit_pct: 触发移动止盈的涨幅阈值 (如 0.05 = 5%)
        trail_pct: 从峰值回落的触发比例 (如 0.01 = 1%)
        sell_ratio: 卖出比例 (如 0.33 = 卖出33%)
        stop_loss: 该层级的止损线 (如 -0.03 = -3%)
        peak_tracker: 外部峰值追踪器 dict {symbol: peak_pnl_pct}
    """

    def __init__(
        self,
        tier: int = 1,
        profit_pct: float = 0.05,
        trail_pct: float = 0.01,
        sell_ratio: float = 0.33,
        stop_loss: float = -0.03,
    ):
        self.tier = tier
        self.profit_pct = profit_pct
        self.trail_pct = abs(trail_pct)
        self.sell_ratio = sell_ratio
        self.stop_loss = stop_loss
        self._peaks: dict[str, float] = {}  # symbol → peak pnl_pct

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is None:
            return None

        sym = position.get("symbol", "")
        avg_cost = position.get("avg_cost", 0)
        qty = position.get("qty", 0)
        price = market_data.get("price", position.get("last_price", 0))
        if avg_cost <= 0 or price <= 0 or qty <= 0:
            return None

        pnl_pct = (price / avg_cost - 1)

        # 更新峰值
        if sym not in self._peaks or pnl_pct > self._peaks[sym]:
            self._peaks[sym] = pnl_pct
        peak = self._peaks.get(sym, pnl_pct)

        # 触发条件: 峰值曾达 profit_pct 阈值 且 从峰值回落 ≥ trail_pct
        if peak >= self.profit_pct and pnl_pct <= peak - self.trail_pct:
            sell_qty = max(100, int(qty * self.sell_ratio) // 100 * 100)
            if sell_qty >= 100:
                # 清理峰值记录
                self._peaks.pop(sym, None)
                return RuleAction(
                    action="sell",
                    symbol=sym,
                    qty=sell_qty,
                    price=price,
                    reason=f"移动止盈T{self.tier}(盈{pnl_pct*100:.1f}% 回落≥{self.trail_pct*100:.0f}%)",
                    meta={
                        "stop_loss_check": True,
                        "stop_loss_threshold": self.stop_loss,
                        "remaining_qty": qty - sell_qty,
                    },
                )

        # 附加: 该层级的止损检查
        if pnl_pct <= self.stop_loss:
            self._peaks.pop(sym, None)
            return RuleAction(
                action="sell",
                symbol=sym,
                qty=qty,
                price=price,
                reason=f"止盈T{self.tier}止损({pnl_pct*100:.1f}%≤{self.stop_loss*100:.0f}%)",
            )

        return None

    def reset_peak(self, symbol: str) -> None:
        """手动重置某股票的峰值记录。"""
        self._peaks.pop(symbol, None)
