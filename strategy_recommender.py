"""策略自动推荐引擎 (蓝图 v5.0 Phase 5.3)

对标: BigQuant 策略工坊 / LEAN Algorithm Framework
基于市场状态 → 推荐策略组合 → 动态权重

数据源:
  market_state.json (市场状态: bull/bear/volatile/unknown)
  factor_registry.json (因子IC)
  strategy_approvals.json (策略绩效)

用法:
    from strategy_recommender import recommend
    rec = recommend()  # → {state, strategies, weights, reason}
"""
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger("quant_framework.strategy_recommender")

MARKET_STATE_PATH = r"D:\quant_framework\market_state.json"
REGISTRY_PATH = r"D:\quant_framework\factor_registry.json"
APPROVALS_PATH = r"D:\quant_framework\strategy_approvals.json"

# ── 市场状态 → 策略映射 ──

STATE_STRATEGY_MAP = {
    "bull": {
        "primary": ["chase_v2", "momentum_score", "qmt_composite"],
        "secondary": ["chip_v2", "trend_score"],
        "avoid": ["defensive_v2"],
        "reason": "牛市: 追涨+动量优先，活跃资金多，QMT复合覆盖多因子",
        "max_positions": 5,
        "signal_level": 3,
    },
    "bear": {
        "primary": ["defensive_v2"],
        "secondary": ["chip_v2", "bull_line"],
        "avoid": ["chase_v2", "momentum_score"],
        "reason": "熊市: 防守优先(低波+股息)，筹码集中股抗跌，牛线突破股有支撑",
        "max_positions": 2,
        "signal_level": 5,
    },
    "volatile": {
        "primary": ["chip_v2", "qmt_composite"],
        "secondary": ["trend_score", "defensive_v2"],
        "avoid": ["chase_v2"],
        "reason": "震荡市: 筹码+QMT复合捕捉结构性机会，趋势+防守兜底",
        "max_positions": 3,
        "signal_level": 4,
    },
    "unknown": {
        "primary": ["chip_v2", "trend_score", "defensive_v2"],
        "secondary": ["qmt_composite"],
        "avoid": [],
        "reason": "市场状态未知: 均衡配置，筹码+趋势+防守三足鼎立",
        "max_positions": 3,
        "signal_level": 4,
    },
}


def recommend() -> dict:
    """基于当前市场状态推荐策略组合。"""
    state = _get_market_state()

    rec = STATE_STRATEGY_MAP.get(state, STATE_STRATEGY_MAP["unknown"])

    # 过滤: 只推荐活跃因子
    active_factors = _get_active_factors()
    primary = [s for s in rec["primary"] if s in active_factors]
    secondary = [s for s in rec["secondary"] if s in active_factors]

    # 权重
    weights = _compute_weights(primary, secondary)

    # 绩效数据
    perf = _get_strategy_performance(primary + secondary)

    return {
        "market_state": state,
        "primary_strategies": primary,
        "secondary_strategies": secondary,
        "avoid_strategies": rec["avoid"],
        "weights": weights,
        "reason": rec["reason"],
        "max_positions": rec["max_positions"],
        "signal_level": rec["signal_level"],
        "performance": perf,
        "updated": datetime.now().strftime("%H:%M:%S"),
    }


def recommend_for_sidebar() -> str:
    """为 trading-v2 侧栏生成推荐文字。"""
    r = recommend()
    state_labels = {"bull": "🐂牛", "bear": "🐻熊", "volatile": "📊震", "unknown": "❓"}
    label = state_labels.get(r["market_state"], "❓")
    primary = "+".join(r["primary_strategies"][:2])
    return f"💡 {label}推荐: {primary} (仓位{r['max_positions']}只, 信号≥Lv{r['signal_level']})"


# ── 内部 ──

def _get_market_state() -> str:
    try:
        with open(MARKET_STATE_PATH, "r") as f:
            data = json.load(f)
        return data.get("state", "unknown")
    except Exception:
        return "unknown"


def _get_active_factors() -> set:
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
        return {f["name"] for f in reg["factors"] if f.get("status") in ("active", "pending")}
    except Exception:
        return set()


def _compute_weights(primary: list, secondary: list) -> dict:
    """基于IC值计算策略权重。"""
    weights = {}
    try:
        with open(REGISTRY_PATH, "r") as f:
            reg = json.load(f)
        factors = {f["name"]: f for f in reg["factors"]}
    except Exception:
        return {}

    all_strategies = primary + secondary
    total_ic = 0
    raw = {}
    for s in all_strategies:
        f = factors.get(s, {})
        ic = abs(f.get("ic_5d", 0.03) or 0.03)
        raw[s] = ic
        total_ic += ic

    if total_ic > 0:
        for s in all_strategies:
            base = raw[s] / total_ic
            weights[s] = round(base * 1.5 if s in primary else base * 0.8, 3)
    return weights


def _get_strategy_performance(strategies: list) -> dict:
    try:
        with open(APPROVALS_PATH, "r") as f:
            approvals = json.load(f)
        result = {}
        for s in strategies:
            a = approvals.get("strategies", {}).get(s, {})
            perf = a.get("performance", {})
            if perf:
                result[s] = {
                    "sharpe": perf.get("sharpe", 0),
                    "win_rate": perf.get("win_rate", 0),
                    "state": a.get("state", "unknown"),
                }
        return result
    except Exception:
        return {}
