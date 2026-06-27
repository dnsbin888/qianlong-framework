"""P2-4: 市场状态 → 最优策略组合 + 权重映射表。"""
from __future__ import annotations

from typing import Any

# ═══════════════════════════════════════════════════════════
# 市场状态 → 策略映射
# ═══════════════════════════════════════════════════════════

STATE_STRATEGY_MAP: dict[str, dict[str, Any]] = {
    "bull": {
        "emoji": "🐂",
        "label": "牛市上涨",
        "primary": ["momentum", "breakout", "channel", "ma_cross", "high_turnover"],
        "secondary": ["volatility_expansion", "limit_up_follow"],
        "avoid": ["mean_reversion", "rsi_reversal", "pairs_mean_reversion"],
        "weights": {
            "momentum": 0.25,
            "breakout": 0.25,
            "channel": 0.20,
            "ma_cross": 0.15,
            "high_turnover": 0.15,
        },
        "sizing_factor": 1.0,    # 满仓
        "stop_multiplier": 1.5,  # 放宽止损
    },
    "bear": {
        "emoji": "🐻",
        "label": "熊市下跌",
        "primary": ["mean_reversion", "rsi_reversal", "volume_mean_reversion"],
        "secondary": ["ma_cross", "pairs_mean_reversion"],
        "avoid": ["momentum", "breakout", "channel"],
        "weights": {
            "mean_reversion": 0.30,
            "rsi_reversal": 0.30,
            "volume_mean_reversion": 0.20,
            "ma_cross": 0.10,
            "pairs_mean_reversion": 0.10,
        },
        "sizing_factor": 0.5,    # 半仓
        "stop_multiplier": 0.8,  # 收紧止损
    },
    "oscillate": {
        "emoji": "〰️",
        "label": "震荡",
        "primary": ["mean_reversion", "ma_condition", "channel"],
        "secondary": ["rsi_reversal", "volume_mean_reversion", "gap_trading"],
        "avoid": ["momentum", "breakout"],
        "weights": {
            "mean_reversion": 0.25,
            "ma_condition": 0.20,
            "channel": 0.20,
            "rsi_reversal": 0.15,
            "volume_mean_reversion": 0.10,
            "gap_trading": 0.10,
        },
        "sizing_factor": 0.7,    # 七成仓
        "stop_multiplier": 1.0,  # 标准止损
    },
    "rebound": {
        "emoji": "↕️",
        "label": "反弹/调整",
        "primary": ["limit_up_follow", "institutional_tracking", "breakout", "volatility_expansion"],
        "secondary": ["gap_trading", "high_turnover", "momentum"],
        "avoid": ["mean_reversion"],
        "weights": {
            "limit_up_follow": 0.20,
            "institutional_tracking": 0.20,
            "breakout": 0.20,
            "volatility_expansion": 0.15,
            "gap_trading": 0.10,
            "high_turnover": 0.10,
            "momentum": 0.05,
        },
        "sizing_factor": 0.6,    # 六成仓
        "stop_multiplier": 0.9,  # 稍紧
    },
}


def get_active_strategies(state: str) -> dict[str, Any]:
    """获取当前市场状态下应激活的策略列表和权重。

    Args:
        state: 市场状态 'bull'/'bear'/'oscillate'/'rebound'

    Returns:
        {
            "primary": [...],    # 主力策略
            "secondary": [...],  # 辅助策略
            "avoid": [...],      # 禁用策略
            "weights": {...},    # 策略权重
            "sizing_factor": float,  # 仓位系数
            "stop_multiplier": float,  # 止损乘数
        }
    """
    if state not in STATE_STRATEGY_MAP:
        state = "oscillate"  # 未知状态默认震荡
    return STATE_STRATEGY_MAP[state]


def get_all_states() -> dict[str, str]:
    """返回所有状态及其emoji/标签。"""
    return {k: {"emoji": v["emoji"], "label": v["label"]}
            for k, v in STATE_STRATEGY_MAP.items()}
