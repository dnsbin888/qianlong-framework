"""策略权重 — 从 FactorRegistry IC 自动推导 (蓝图 v3.0 Phase C)

不再硬编码权重。每周运行 full_market_ic.py → 更新 Registry → 权重自动更新。
"""

# ═══ 市场状态偏向 (手工调节, 不改IC权重) ═══
# 120天IC验证: 7因子稳定>0.035
MARKET_BIAS = {
    "bull": {"defensive_v2": 0.4, "chase_v2": 1.4, "momentum_score": 1.3, "trend_score": 1.2},
    "bear": {"defensive_v2": 2.5, "chase_v2": 0.2, "trend_score": 0.5, "momentum_score": 0.5},
    "volatile": {"chip_v2": 1.4, "defensive_v2": 1.2, "chase_v2": 0.6, "qmt_composite": 1.2},
    "unknown": {},
}

MIN_WEIGHT = 0.05  # 最小权重 (避免因子完全归零)


def get_strategy_weights(market_state: str = "unknown") -> dict:
    """从 FactorRegistry IC(5d) 自动推导策略权重。

    权重 = normalize(max(IC - 0.02, 0) × market_bias)
    退役因子自动排除。
    """
    try:
        from factor_registry import get_active_factors
        factors = get_active_factors()
    except ImportError:
        factors = []

    if not factors:
        return {"chase": 0.5, "defensive": 0.5}

    bias = MARKET_BIAS.get(market_state, {})
    raw = {}
    for f in factors:
        ic = f.get("ic_5d", 0) or 0
        # E311修复: direction="short"时翻转IC符号
        if f.get("direction") == "short":
            ic = -ic
        # fund_v2 IC_5d=-0.29 → 翻转后=+0.29, 全系统最强
        w = max(0, abs(ic) - 0.02)  # abs确保short方向也参与权重
        w *= bias.get(f["name"], 1.0)
        raw[f["name"]] = w

    total = sum(raw.values())
    if total <= 0:
        n = len(factors)
        return {f["name"]: 1.0 / n for f in factors}

    weights = {k: max(MIN_WEIGHT, round(v / total, 4)) for k, v in raw.items() if v > 0}
    # 归一化
    wt = sum(weights.values())
    return {k: round(v / wt, 4) for k, v in weights.items()}


def adjust_position_size(base_position: float, strategy: str,
                         market_state: str = "unknown") -> float:
    weights = get_strategy_weights(market_state)
    w = weights.get(strategy, min(weights.values()) if weights else 0.33)
    return round(base_position * max(w, MIN_WEIGHT), 2)


def get_market_state_for_strategy() -> str:
    try:
        from market_state_classifier import classify_market_state
        return classify_market_state()
    except ImportError:
        return "unknown"
