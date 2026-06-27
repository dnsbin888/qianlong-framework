"""信号质量过滤规则 — 动态调整信号强度。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 的信号过滤逻辑抽取。
"""

from __future__ import annotations

from typing import Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class SignalQualityFilter(BaseRule):
    """信号质量过滤 — 基于量比/涨跌幅调整信号强度。

    Attributes:
        min_strength: 最低买入信号强度
        vol_ratio_threshold: 量比不足阈值
        chase_pct: 追高风险阈值 (当日涨幅>此值降级)
        weak_pct: 弱势阈值 (当日跌幅>此值降级)
    """

    def __init__(
        self,
        min_strength: int = 3,
        vol_ratio_threshold: float = 1.2,
        chase_pct: float = 8.0,
        weak_pct: float = -5.0,
    ):
        self.min_strength = min_strength
        self.vol_ratio_threshold = vol_ratio_threshold
        self.chase_pct = chase_pct
        self.weak_pct = weak_pct

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        if position is not None:
            return None  # 信号过滤是全局规则，用于买入信号

        return None  # 由 PaperAccount 调用 adjust_signal()

    def adjust_signal(self, signal: dict) -> int:
        """调整信号强度 — 返回调整后的 buy_signal 值。

        Args:
            signal: {"symbol": str, "buy_signal": int, "vol_ratio": float, "change_pct": float, ...}

        Returns:
            int — 调整后的信号强度
        """
        bs = signal.get("buy_signal", 0)
        if bs < self.min_strength:
            return 0

        vol_ratio = signal.get("vol_ratio", 1.0) or 1.0
        chg_pct = signal.get("change_pct", 0) or 0

        # 1. 量比不足 → 降1级（跳过新浪默认值1.0）
        if vol_ratio != 1.0 and vol_ratio < self.vol_ratio_threshold and bs >= 4:
            bs -= 1
        # 2. 追高风险 → 降1级（分市场阈值）
        sym = signal.get("symbol", "")
        if sym.startswith("sh688") or sym.startswith("sz30"):
            chase_limit = 15.0  # 科创/创业板 20%涨停
        elif sym.startswith("bj"):
            chase_limit = 25.0  # 北交所 30%涨停
        else:
            chase_limit = self.chase_pct  # 沪深主板 10%涨停
        if chg_pct > chase_limit and bs >= 4:
            bs -= 1
        # 3. 弱势 → 降1级
        if chg_pct < self.weak_pct:
            bs -= 1

        return max(0, bs)

    def is_valid(self, signal: dict) -> bool:
        """检查信号是否通过质量过滤。"""
        return self.adjust_signal(signal) >= self.min_strength
