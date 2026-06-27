"""P2-3: 多策略信号聚合裁决器 — 加权投票 → 统一买卖决策。"""
from __future__ import annotations

from typing import Any

# 裁决阈值
THRESHOLD_STRONG_BUY = 0.6
THRESHOLD_BUY = 0.3
THRESHOLD_SELL = -0.3
THRESHOLD_STRONG_SELL = -0.6

ACTION_LABELS = {
    "strong_buy": "🚀 强力买入",
    "buy": "📈 买入",
    "hold": "⏸ 观望",
    "sell": "📉 卖出",
    "strong_sell": "🔻 强力卖出",
}


class SignalAggregator:
    """多策略信号加权投票聚合器。"""

    def aggregate(
        self,
        signals: dict[str, int],
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """加权投票聚合。

        Args:
            signals: {strategy_name: signal} 其中 signal ∈ {+1, 0, -1}
            weights: {strategy_name: weight} 默认为等权

        Returns:
            {
                "vote_score": float,     # -1.0 ~ 1.0
                "buy_votes": int,         #
                "sell_votes": int,        #
                "total_votes": int,       #
                "action": str,            # strong_buy/buy/hold/sell/strong_sell
                "action_label": str,      # 中文标签
                "confidence": float,      # 0~1
                "buy_strategies": [str],  # 买入策略列表
                "sell_strategies": [str], # 卖出策略列表
            }
        """
        if weights is None:
            # 默认等权
            n = len(signals) or 1
            weights = {k: 1.0 / n for k in signals}

        total_weight = sum(weights.get(k, 0.0) for k in signals)
        if total_weight == 0:
            total_weight = 1.0

        buy_votes = sell_votes = 0
        vote_sum = 0.0
        buy_strategies: list[str] = []
        sell_strategies: list[str] = []

        for name, sig in signals.items():
            w = weights.get(name, 0.0) / total_weight
            if sig > 0:
                buy_votes += 1
                vote_sum += w
                buy_strategies.append(name)
            elif sig < 0:
                sell_votes += 1
                vote_sum -= w
                sell_strategies.append(name)

        total_votes = buy_votes + sell_votes
        total_strategies = len(signals)

        # 置信度: 基于投票一致性
        if total_votes > 0:
            agreement = max(buy_votes, sell_votes) / total_votes
        else:
            agreement = 0.0  # 全部观望
        confidence = min(agreement * (total_votes / max(total_strategies, 1)), 1.0)

        # 裁决
        if vote_sum >= THRESHOLD_STRONG_BUY:
            action = "strong_buy"
        elif vote_sum >= THRESHOLD_BUY:
            action = "buy"
        elif vote_sum > THRESHOLD_SELL:
            action = "hold"
        elif vote_sum > THRESHOLD_STRONG_SELL:
            action = "sell"
        else:
            action = "strong_sell"

        return {
            "vote_score": round(vote_sum, 3),
            "buy_votes": buy_votes,
            "sell_votes": sell_votes,
            "total_votes": total_votes,
            "total_strategies": total_strategies,
            "action": action,
            "action_label": ACTION_LABELS.get(action, action),
            "confidence": round(confidence, 2),
            "buy_strategies": buy_strategies,
            "sell_strategies": sell_strategies,
        }


# 全局单例
_aggregator: SignalAggregator | None = None


def aggregate_signals(
    signals: dict[str, int],
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    """快捷函数：聚合多策略信号。"""
    global _aggregator
    if _aggregator is None:
        _aggregator = SignalAggregator()
    return _aggregator.aggregate(signals, weights)
