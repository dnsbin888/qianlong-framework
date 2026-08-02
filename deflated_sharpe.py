"""Deflated Sharpe Ratio v1.0 — 统计显著性检验 (Prado 2014)
对标: Prado "The Deflated Sharpe Ratio" + QuantConnect Research

原理:
  回测试了N组参数, 即使纯随机策略的最高Sharpe也会>0
  DSR修正了这个"多重测试偏误", 告诉你的策略是真Alpha还是参数过拟合

用法:
  from deflated_sharpe import deflated_sharpe_ratio, estimate_min_backtest_length
  dsr = deflated_sharpe_ratio(returns, n_trials=100)
  # dsr > 0.95 → 统计显著 (真Alpha概率>95%)
  # dsr < 0.80 → 可能过拟合
"""
import numpy as np
from scipy.stats import norm


def annualized_sharpe(returns, periods_per_year=252):
    """年化Sharpe (无风险利率=0)"""
    if len(returns) < 2:
        return 0.0
    mu = np.mean(returns)
    sig = np.std(returns, ddof=1)
    if sig < 1e-9:
        return 0.0
    return float(mu / sig * np.sqrt(periods_per_year))


def _expected_max_sharpe(n_trials: int, var_sharpe: float) -> float:
    """期望最大Sharpe (E[max(SR_k)]) — Prado Eq.3

    Args:
        n_trials: 独立试错次数 (参数组合数或策略变体数)
        var_sharpe: Sharpe方差 ≈ (1 + 0.5*SR²)/T
    """
    if n_trials <= 1:
        return 0.0
    gamma = 0.5772156649  # Euler-Mascheroni
    std_sr = np.sqrt(var_sharpe)
    term1 = (1 - gamma) * norm.ppf(1 - 1.0 / n_trials)
    term2 = gamma * norm.ppf(1 - 1.0 / (n_trials * np.e))
    return float(std_sr * (term1 + term2))


def _sharpe_variance(sr: float, n_obs: int) -> float:
    """Sharpe估计量的渐近方差 (IID returns) — Prado Eq.4"""
    if n_obs <= 1:
        return float('inf')
    return float((1 + 0.5 * sr * sr) / n_obs)


def deflated_sharpe_ratio(returns, n_trials: int = 100, periods_per_year=252) -> dict:
    """计算Deflated Sharpe Ratio

    Args:
        returns: 日收益率序列 (np.array or list)
        n_trials: 独立试错次数 (你尝试过的不同参数组合数, 默认100)
        periods_per_year: 年化周期数 (日线=252, 分钟线=...)

    Returns:
        {sr, dsr, p_value, significant, min_obs_needed, ...}
    """
    returns = np.asarray(returns, dtype=float)
    returns = returns[~np.isnan(returns)]
    n_obs = len(returns)

    if n_obs < 20:
        return {"sr": 0.0, "dsr": 0.0, "p_value": 1.0,
                "significant": False, "n_obs": n_obs,
                "error": f"样本不足({n_obs}<20)"}

    sr = annualized_sharpe(returns, periods_per_year)
    var_sr = _sharpe_variance(sr, n_obs)
    e_max = _expected_max_sharpe(n_trials, var_sr)

    # Deflated SR = (SR - E[max SR]) / sqrt(Var(SR))
    if var_sr <= 0:
        dsr_val = 0.0
    else:
        dsr_val = (sr - e_max) / np.sqrt(var_sr)

    # P-value: probability that a random strategy would achieve this SR
    p_value = 1.0 - norm.cdf(dsr_val)

    # 策略显著条件: DSR > 1.645 (95%置信) 或 DSR > 2.33 (99%置信)
    significant_95 = dsr_val > 1.645
    significant_99 = dsr_val > 2.33

    # 最小回测长度: 需要多少样本才能达到显著
    min_obs = estimate_min_backtest_length(sr, n_trials, periods_per_year) if sr > 0 else float('inf')

    return {
        "sr": round(sr, 4),
        "dsr": round(dsr_val, 4),
        "p_value": round(float(p_value), 4),
        "significant_95": bool(significant_95),
        "significant_99": bool(significant_99),
        "n_obs": n_obs,
        "n_trials": n_trials,
        "e_max_sr": round(float(e_max), 4),
        "min_obs_needed": int(min_obs) if min_obs != float('inf') else None,
        "verdict": ("✅ 显著(99%)" if significant_99 else
                    "✅ 显著(95%)" if significant_95 else
                    "⚠️ 不显著" if dsr_val > 0 else
                    "❌ 可能过拟合"),
    }


def estimate_min_backtest_length(target_sr: float, n_trials: int = 100, periods_per_year=252) -> int:
    """估计达到统计显著所需的最小回测长度

    Args:
        target_sr: 目标年化Sharpe
        n_trials: 试错次数
        periods_per_year: 年化周期

    Returns:
        最少需要的观测数
    """
    # 解: (SR - E[max]) / sqrt(Var(SR)) > 1.645
    # → SR - std_sr*(term) > 1.645 * sqrt(Var)
    # → 迭代求解

    if target_sr <= 0:
        return float('inf')

    for n in range(20, 10000, 10):
        var_sr = _sharpe_variance(target_sr, n)
        e_max = _expected_max_sharpe(n_trials, var_sr)
        dsr = (target_sr - e_max) / np.sqrt(var_sr) if var_sr > 0 else 0
        if dsr > 1.645:
            return n
    return 10000


# ═══════════════════════════════════════
# 快速诊断: 从现有回测数据检验
# ═══════════════════════════════════════

def evaluate_from_backtest(bt_results: dict) -> dict:
    """从回测结果dict计算DSR

    bt_results应包含:
      - daily_returns: list[float] 日收益序列
      - n_trials: int (可选) 试错次数
    """
    returns = bt_results.get("daily_returns", [])
    n_trials = bt_results.get("n_trials", 100)
    return deflated_sharpe_ratio(returns, n_trials)


if __name__ == "__main__":
    print("=" * 60)
    print("  Deflated Sharpe Ratio — 统计显著性检验")
    print("=" * 60)

    # 测试1: 好策略 (SR=1.5, 252天)
    np.random.seed(42)
    good_rets = np.random.normal(0.001, 0.015, 252)  # 日收益~0.1%, 波动1.5%
    r1 = deflated_sharpe_ratio(good_rets, n_trials=50)
    print(f"\n  好策略 (SR≈1.0): DSR={r1['dsr']}, {r1['verdict']}")
    print(f"    最小回测长度需求: {r1['min_obs_needed']}天")

    # 测试2: 弱策略 (SR=0.5, 252天)
    weak_rets = np.random.normal(0.0003, 0.015, 252)
    r2 = deflated_sharpe_ratio(weak_rets, n_trials=50)
    print(f"\n  弱策略 (SR≈0.3): DSR={r2['dsr']}, {r2['verdict']}")

    # 测试3: 过拟合 (SR=1.0, 但试了1000组参数)
    r3 = deflated_sharpe_ratio(good_rets, n_trials=1000)
    print(f"\n  同策略, 试1000组后: DSR={r3['dsr']}, {r3['verdict']}")
    print(f"    (试错越多, 期望最大SR越高, DSR越低)")

    # 测试4: 从trade_log.csv读真实数据
    import csv, os
    csv_path = r"d:\quant_framework\trade_log.csv"
    if os.path.exists(csv_path):
        try:
            pnls = []
            with open(csv_path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    pnl = float(row.get("net_profit", 0) or 0)
                    if pnl != 0:
                        pnls.append(pnl / 100000)  # 近似日收益
            if pnls:
                r4 = deflated_sharpe_ratio(pnls, n_trials=20)
                print(f"\n  真实交易 ({len(pnls)}笔): DSR={r4['dsr']}, {r4['verdict']}")
        except Exception as e:
            print(f"\n  真实数据读取失败: {e}")

    print(f"\n{'='*60}")
    print("  ✅ Deflated Sharpe就绪")
    print(f"{'='*60}")
