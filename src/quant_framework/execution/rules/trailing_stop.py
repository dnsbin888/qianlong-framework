"""移动止盈规则 — 双层追踪止损。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class AutoTrailingStopRule(BaseRule):
    """三层移动止盈规则。

    T1 (轻仓): 涨幅达到 profit_pct → 回落 trail_pct → 部分卖出
    T2 (重仓): 同上, 更高阈值
    T3 (涨停): 仅涨停封板→炸板回落触发 (非通用涨10%, 而是检测是否触及涨停价)
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
        self._peaks: dict[str, float] = {}

    def _get_limit_pct(self, sym: str) -> float:
        """获取涨停幅度: 主板10%, 科创/创业20%, 北交30%"""
        c = sym.replace('sh','').replace('sz','').replace('bj','')
        if c.startswith(('30','688')): return 0.20
        if c.startswith(('8','4')): return 0.30
        return 0.10

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is None:
            return None

        sym = position.get("symbol", "")
        avg_cost = position.get("avg_cost", 0)
        qty = position.get("qty", 0)
        price = market_data.get("price", position.get("last_price", 0))
        if price <= 0 or qty <= 0:
            return None

        prev_close = market_data.get("prev_close", avg_cost)
        base = prev_close if prev_close > 0 else avg_cost
        if base <= 0:
            return None
        pnl_pct = (price / base - 1)
        # T3专用: 涨停价 = 昨收×(1+涨停幅度)
        limit_up_price = round(base * (1 + self._get_limit_pct(sym)), 2)

        # 更新峰值
        if sym not in self._peaks or pnl_pct > self._peaks[sym]:
            self._peaks[sym] = pnl_pct
        peak = self._peaks.get(sym, pnl_pct)

        # 触发判断
        should_sell = False
        reason_prefix = "移动止盈"
        if self.tier == 3:
            # T3: 必须是涨停封板→炸板 (峰值触及涨停价 + 回落)
            peak_price = base * (1 + peak)
            hit_limit = peak_price >= limit_up_price * 0.995
            if hit_limit and pnl_pct <= peak - self.trail_pct:
                should_sell = True
                reason_prefix = "炸板T3"
        else:
            # T1/T2: 通用移动止盈 (峰值达到阈值 + 回落)
            if peak >= self.profit_pct and pnl_pct <= peak - self.trail_pct:
                should_sell = True

        if should_sell:
            sell_qty = max(100, int(qty * self.sell_ratio) // 100 * 100)
            if sell_qty >= 100:
                self._peaks.pop(sym, None)
                return RuleAction(
                    action="sell",
                    symbol=sym,
                    qty=sell_qty,
                    price=price,
                    reason=f"{reason_prefix}T{self.tier}(盈{pnl_pct*100:.1f}% 回落≥{self.trail_pct*100:.0f}%)",
                    meta={
                        "stop_loss_check": True,
                        "stop_loss_threshold": self.stop_loss,
                        "remaining_qty": qty - sell_qty,
                    },
                )

        # 该层级的止损兜底
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
