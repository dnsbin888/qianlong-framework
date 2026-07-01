"""组合优化 (P8-3: 对标QuantConnect均值-方差)
方法: 风险调整后权重优化
  - 输入: N只股票, 各自预期收益 + 协方差矩阵
  - 约束: 单票≤20%, 总仓位=100%, 每只≥0 (无做空)
  - 输出: 优化后权重向量
"""
import numpy as np
from scipy.optimize import minimize

def optimize_weights(
    symbols: list[str],
    scores: list[float],
    returns_matrix: np.ndarray = None,
    max_single: float = 0.20,
    max_positions: int = 5
) -> dict[str, float]:
    """均值-方差优化 + 权重约束。

    Args:
        symbols: 股票代码
        scores: 因子评分 (0-100, 越高越好)
        returns_matrix: 历史收益矩阵 (可选, 用于协方差)
        max_single: 单票最大权重
        max_positions: 最大持仓数

    Returns:
        {symbol: weight}
    """
    n = len(symbols)
    if n == 0:
        return {}
    if n == 1:
        return {symbols[0]: 1.0}

    scores = np.array(scores, dtype=float)
    scores = np.clip(scores, 0, 100)

    # 预期收益: 归一化评分
    if np.std(scores) > 1e-8:
        mu = (scores - np.mean(scores)) / np.std(scores)
    else:
        mu = np.zeros(n)

    # 协方差: 有历史数据用样本协方差, 否则用对角矩阵
    if returns_matrix is not None and returns_matrix.shape[1] >= 10:
        cov = np.cov(returns_matrix, rowvar=False)
        cov = (cov + np.eye(n) * 0.01)  # 正则化
    else:
        cov = np.eye(n) * 0.01

    # 目标: max Σ(权重×收益) - λ × 风险
    lam = 1.0  # 风险厌恶系数

    def objective(w):
        return -(np.dot(w, mu) - lam * np.dot(w, np.dot(cov, w)))

    # 约束
    cons = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1.0}]  # 全仓
    bounds = [(0, max_single) for _ in range(n)]
    for i in range(n):
        if scores[i] < 30:
            bounds[i] = (0, 0)  # 过滤低分

    # 求解
    w0 = np.ones(n) / n
    try:
        result = minimize(objective, w0, method='SLSQP', bounds=bounds,
                         constraints=cons, options={'maxiter': 200, 'ftol': 1e-8})
        if result.success:
            w = result.x
        else:
            w = w0
    except Exception:
        w = w0

    # 取前max_positions只
    sorted_idx = np.argsort(-w)
    top_idx = sorted_idx[:max_positions]
    w_top = w[top_idx] / np.sum(w[top_idx])

    return {symbols[i]: round(float(w_top[list(top_idx).index(i)]), 4)
            for i in top_idx}

def equal_weight(symbols: list[str], max_positions: int = 5) -> dict[str, float]:
    """等权 (兜底方案)"""
    n = min(len(symbols), max_positions)
    if n == 0: return {}
    return {s: round(1.0/n, 4) for s in symbols[:n]}

def score_weighted(symbols: list[str], scores: list[float], max_positions: int = 5) -> dict[str, float]:
    """按因子评分加权 (简单有效, 多数时候比优化更好)"""
    if not symbols: return {}
    top = sorted(zip(symbols, scores), key=lambda x: -x[1])[:max_positions]
    total = sum(s for _, s in top)
    if total <= 0: return equal_weight(symbols, max_positions)
    return {sym: round(s/total, 4) for sym, s in top}
