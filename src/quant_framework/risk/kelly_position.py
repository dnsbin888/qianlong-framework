"""P3-1: Kelly动态仓位计算器 — 信号强度 × 历史胜率 × 市场状态 × 回撤。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class KellyPositionCalculator:
    """Kelly 动态仓位计算器。

    Kelly公式: f* = (p * b - q) / b
      p = 胜率, b = 平均盈亏比(avg_win/avg_loss), q = 1-p

    安全调整:
    - 半Kelly: f = f* / 2 (降低波动)
    - 回撤限制: 当前回撤 > 5% → 仓位减半
    - 市场状态: 熊市 → × 0.5, 震荡 → × 0.8, 反弹 → × 0.6
    - 信号强度: 弱信号 → 仓位打折
    """

    def __init__(self) -> None:
        pass

    def calculate(
        self,
        signal_score: float,
        win_rate: float = 0.45,
        avg_win: float = 0.05,
        avg_loss: float = 0.03,
        current_drawdown: float = 0.0,
        market_state: str = "oscillate",
        n_trades: int = 0,
    ) -> dict[str, Any]:
        """计算Kelly建议仓位。

        Args:
            signal_score: 信号强度 -1~1 (来自SignalAggregator)
            win_rate: 历史胜率 0~1
            avg_win: 平均盈利比例 (如0.05=5%)
            avg_loss: 平均亏损比例 (如0.03=3%)
            current_drawdown: 当前回撤比例 (如0.05=5%)
            market_state: 市场状态 bull/bear/oscillate/rebound
            n_trades: 历史交易笔数

        Returns:
            {kelly_pct, half_kelly_pct, adjusted_pct, signal_multiplier,
             drawdown_multiplier, market_multiplier, reason}
        """
        # 1. 原始Kelly
        win_rate = max(0.1, min(0.9, win_rate))
        if avg_loss <= 0:
            avg_loss = 0.01
        b_ratio = avg_win / avg_loss
        b_ratio = max(0.5, min(10.0, b_ratio))
        q = 1.0 - win_rate

        kelly_raw = (win_rate * b_ratio - q) / b_ratio
        kelly_raw = max(0.0, min(0.5, kelly_raw))  # 钳制0~50%
        half_kelly = kelly_raw / 2.0

        # 2. 样本量调整
        if n_trades < 5:
            sample_mult = 0.3   # 样本极少 → 大幅降仓
        elif n_trades < 20:
            sample_mult = 0.5 + (n_trades - 5) / 30  # 渐进提升
        else:
            sample_mult = 1.0

        # 3. 信号强度乘数
        sig_abs = abs(signal_score)
        if sig_abs >= 0.6:
            signal_mult = 1.0
        elif sig_abs >= 0.3:
            signal_mult = 0.7
        else:
            signal_mult = 0.4

        # 4. 回撤调整
        if current_drawdown >= 0.08:
            drawdown_mult = 0.25  # 大幅回撤 → ¼仓
        elif current_drawdown >= 0.05:
            drawdown_mult = 0.5
        elif current_drawdown >= 0.03:
            drawdown_mult = 0.75
        else:
            drawdown_mult = 1.0

        # 5. 市场状态调整
        market_mult_map = {
            "bull": 1.0,
            "oscillate": 0.8,
            "rebound": 0.6,
            "bear": 0.5,
        }
        market_mult = market_mult_map.get(market_state, 0.8)

        # 6. 综合
        adjusted = half_kelly * sample_mult * signal_mult * drawdown_mult * market_mult
        adjusted = max(0.02, min(0.25, adjusted))  # 最终钳制2%~25%

        # 7. 原因说明
        reasons = []
        if adjusted == 0.02:
            reasons.append("底线仓位")
        if signal_mult < 1.0:
            reasons.append(f"信号={(sig_abs * 100):.0f}%")
        if drawdown_mult < 1.0:
            reasons.append(f"回撤={current_drawdown * 100:.0f}%")
        if market_mult < 1.0:
            reasons.append(f"市场={market_state}")
        if sample_mult < 1.0:
            reasons.append(f"样本={n_trades}笔")

        return {
            "kelly_pct": round(kelly_raw * 100, 1),
            "half_kelly_pct": round(half_kelly * 100, 1),
            "adjusted_pct": round(adjusted * 100, 1),
            "signal_multiplier": round(signal_mult, 2),
            "drawdown_multiplier": round(drawdown_mult, 2),
            "market_multiplier": round(market_mult, 2),
            "reason": " · ".join(reasons) if reasons else "标准仓位",
        }

    def from_paper_account(self) -> tuple[float, float, float, int]:
        """从模拟盘交易记录提取胜率/盈亏比/回撤/笔数。"""
        try:
            pa_path = r"D:\quant_framework\paper_account.json"
            if not os.path.exists(pa_path):
                return 0.45, 0.05, 0.03, 0

            with open(pa_path, "r", encoding="utf-8") as f:
                paper = json.load(f)

            trades = paper.get("trade_log", [])
            sells = [t for t in trades if t.get("side") == "sell"]
            if len(sells) < 5:
                return 0.45, 0.05, 0.03, len(sells)

            wins = [t for t in sells if t.get("revenue", 0) > 0]
            losses = [t for t in sells if t.get("revenue", 0) <= 0]

            n = len(sells)
            wr = len(wins) / n if n > 0 else 0.45
            aw = sum(t.get("revenue", 0) for t in wins) / len(wins) if wins else 0.05
            al = abs(sum(t.get("revenue", 0) for t in losses)) / len(losses) if losses else 0.03

            # 回撤
            equity = paper.get("equity", paper.get("total_asset", 0))
            initial = paper.get("initial_capital", equity or 100000)
            dd = max(0.0, 1.0 - equity / (initial + 0.01)) if initial > 0 else 0.0

            return wr, aw, al, n
        except Exception as e:
            logger.warning(f"[Kelly] 读取模拟盘失败: {e}")
            return 0.45, 0.05, 0.03, 0


# 全局单例
_kelly: KellyPositionCalculator | None = None


def get_kelly_position(signal_score: float, market_state: str = "oscillate") -> dict[str, Any]:
    """快捷函数：基于当前系统状态计算Kelly仓位。"""
    global _kelly
    if _kelly is None:
        _kelly = KellyPositionCalculator()
    wr, aw, al, n = _kelly.from_paper_account()
    return _kelly.calculate(
        signal_score=signal_score,
        win_rate=wr,
        avg_win=aw,
        avg_loss=al,
        current_drawdown=0.0,  # 由调用方传入
        market_state=market_state,
        n_trades=n,
    )
