"""ICIR动态权重 — 根据信息系数-信息比自动调整因子权重

ICIR = |IC均值| / IC标准差
ICIR越高 → 因子越稳定有效 → 权重越大

每月/每次IC分析后重算，写入 factor_weights.json
"""

import json, os

WEIGHTS_FILE = r"D:\quant_web\data\factor_weights.json"

# 因子默认权重（无数据时使用）
DEFAULT_WEIGHTS = {
    "trend_score": 1.0, "momentum_score": 1.0, "volume_score": 1.0,
    "chg_score": 1.0, "position_score": 1.0, "rsi_score": 1.0,
    "macd_score": 1.0, "boll_score": 1.0, "atr_score": 1.0,
    "vol_score": 1.0, "bias_score": 1.0, "money_score": 1.0,
    "turnover_score": 1.0, "fund_score": 1.0, "chip_score": 1.0,
    "rating_score": 1.0, "fund_flow_score": 1.0, "chip_struct_score": 1.0,
    "rating_dist_score": 1.0, "tech_score": 1.0,
    "signal_resonance": 1.0, "signal_final": 1.0,
}


def calc_icir_weights(ic_results: dict, min_samples: int = 10, half_life: int = 30) -> dict:
    """从IC分析结果计算ICIR加权权重

    Args:
        ic_results: {"factor_name": {"ic_1d": {"mean_ic": 0.03, "std_ic": 0.05, "samples": 50}, ...}}
        min_samples: 最少样本数，低于此值的因子不参与计算
        half_life: ICIR半衰期（天），越旧的IC结果权重越低

    Returns:
        {"trend_score": 1.5, "volume_score": 0.8, ...}  # 归一化到总和=1
    """
    icir = {}
    for factor, periods in ic_results.items():
        ic1 = periods.get("ic_1d", {})
        if not ic1 or ic1.get("samples", 0) < min_samples:
            continue
        mean = abs(ic1.get("mean_ic", 0))
        std = max(ic1.get("std_ic", 0.001), 0.001)
        icir[factor] = round(mean / std, 4)

    if not icir:
        return dict(DEFAULT_WEIGHTS)

    # 归一化到总和=因子数
    total = sum(icir.values())
    weights = {k: round(v / total * len(icir), 4) for k, v in icir.items()}

    # 钳制: 单个因子权重 0.3-3.0
    for k in weights:
        weights[k] = max(0.3, min(3.0, weights[k]))

    # 未覆盖的因子给默认值1.0
    for k in DEFAULT_WEIGHTS:
        if k not in weights:
            weights[k] = 1.0

    return weights


def update_weights(ic_results: dict):
    """从IC结果更新权重文件"""
    weights = calc_icir_weights(ic_results)
    try:
        os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
        with open(WEIGHTS_FILE, "w") as f:
            json.dump({
                "weights": weights,
                "source": "ICIR",
                "updated": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }, f, ensure_ascii=False)
        print(f"[ICIR] 权重已更新: {len(weights)}个因子")
        return True
    except Exception as e:
        print(f"[ICIR] 权重更新失败: {e}")
        return False


def load_weights() -> dict:
    """加载当前权重"""
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE, "r") as f:
                return json.load(f).get("weights", dict(DEFAULT_WEIGHTS))
        except: pass
    return dict(DEFAULT_WEIGHTS)
