"""策略信号聚合裁决器 (蓝图 v3.0 S1-5)

整合多策略信号 → 加权投票 → 冲突解决 → 最终决策。
"""

from collections import defaultdict
from typing import Optional

# ═══ 可配置 ═══
BUY_THRESHOLD = 60  # 最低买入分数 (0-100)
MAX_SIGNALS = 10    # 每轮最多输出信号数


def arbitrate_signals(
    raw_signals: list[dict],
    strategy_weights: dict = None,
    threshold: int = None,
) -> list[dict]:
    """聚合多策略信号，输出最终买入决策。

    Args:
        raw_signals: [{code, signal: {strategy, score, ...}}, ...]
        strategy_weights: {'chase': 0.6, 'low_absorb': 0.2, ...}
        threshold: 最低买入分数

    Returns:
        [{code, final_signal, total_score, reason}, ...]  按total_score降序
    """
    if strategy_weights is None:
        strategy_weights = {"chase": 0.4, "low_absorb": 0.3, "defensive": 0.3}
    if threshold is None:
        threshold = BUY_THRESHOLD

    # 1. 按股票分组
    grouped = defaultdict(list)
    for item in raw_signals:
        code = item.get("code", item.get("symbol", ""))
        sig = item.get("signal", item)
        if code and sig:
            grouped[code].append(sig)

    # 2. 逐股裁决
    results = []
    for code, signals in grouped.items():
        if len(signals) == 1:
            sig = signals[0]
            if sig.get("score", 0) >= threshold:
                results.append({
                    "code": code,
                    "final_signal": sig,
                    "total_score": sig["score"],
                    "reason": f"单信号: {sig.get('strategy', '?')} {sig['score']:.0f}分",
                })
        else:
            # 加权投票
            weighted = 0.0
            best = max(signals, key=lambda x: x.get("score", 0))
            for sig in signals:
                w = strategy_weights.get(sig.get("strategy", "chase"), 0.33)
                weighted += sig.get("score", 0) * w

            if weighted >= threshold:
                results.append({
                    "code": code,
                    "final_signal": best,
                    "total_score": round(weighted, 2),
                    "reason": f"多策略: {len(signals)}票 加权{weighted:.0f}分",
                    "signals": signals,  # 调试信息
                })

    # 3. 排序 (高分优先)
    results.sort(key=lambda x: x["total_score"], reverse=True)
    return results[:MAX_SIGNALS]


def resolve_conflicts(
    buy_signals: list[dict],
    sell_signals: list[dict],
) -> tuple[list[dict], list[dict]]:
    """解决买卖冲突 — 优先卖出（风控第一）。

    Args:
        buy_signals: [{code, ...}, ...]
        sell_signals: [{code, ...}, ...]

    Returns:
        (filtered_buy, filtered_sell)
    """
    buy_codes = {s.get("code", s.get("symbol", "")) for s in buy_signals}
    sell_codes = {s.get("code", s.get("symbol", "")) for s in sell_signals}
    conflicts = buy_codes & sell_codes

    if conflicts:
        buy_signals = [s for s in buy_signals
                       if s.get("code", s.get("symbol", "")) not in conflicts]

    return buy_signals, sell_signals


def arbitrate_full(
    raw_signals: list[dict],
    sell_actions: list[dict],
    strategy_weights: dict = None,
    threshold: int = None,
) -> tuple[list[dict], list[dict]]:
    """完整裁决: 信号聚合 + 冲突解决。

    Returns:
        (final_buy_signals, final_sell_signals)
    """
    buy = arbitrate_signals(raw_signals, strategy_weights, threshold)
    buy, sell = resolve_conflicts(buy, sell_actions)
    return buy, sell
