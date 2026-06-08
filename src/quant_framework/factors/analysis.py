"""因子分析 — IC/ICIR/分位数收益/相关性/换手率。

核心指标:
  - IC (Information Coefficient): 当期因子值与下期收益的秩相关系数
  - ICIR: IC均值/IC标准差 (信息比率)
  - Quantile Returns: 按因子值分10组，各组平均收益
  - Factor Correlation: 因子间相关性矩阵
  - Turnover: 因子选股换手率
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field


@dataclass
class ICAnalysis:
    """单因子 IC 分析结果。"""
    factor_name: str = ""
    ic_mean: float = 0.0
    ic_std: float = 0.0
    icir: float = 0.0            # IC Information Ratio
    ic_positive_ratio: float = 0.0  # IC>0 的比例
    ic_series: pd.Series | None = None
    quantile_returns: pd.DataFrame | None = None  # 10分组 × N期收益
    long_short_return: float = 0.0  # 多空收益 (Q10 - Q1)
    t_stat: float = 0.0          # IC t统计量


def compute_factor_returns(
    factor_df: pd.DataFrame,
    forward_returns: pd.DataFrame,
    periods: list[int] = [1, 5, 20],
) -> dict[int, pd.DataFrame]:
    """计算因子未来N期收益。

    Args:
        factor_df: MultiIndex (date, symbol), columns=factor_names, values=因子值
        forward_returns: MultiIndex (date, symbol), columns=return_periods, values=未来收益
        periods: 预测周期列表

    Returns:
        {period: DataFrame(date × factor)} — 每期每个因子的IC
    """
    results = {}
    for period in periods:
        col = f"ret_{period}d"
        if col not in forward_returns.columns:
            continue
        fwd = forward_returns[col]
        ic_series = pd.DataFrame(index=factor_df.index.get_level_values(0).unique())

        for fname in factor_df.columns:
            factor = factor_df[fname]
            aligned = pd.concat([factor, fwd], axis=1).dropna()
            if len(aligned) < 10:
                continue
            # Rank IC (Spearman)
            ic_series[fname] = aligned.groupby(level=0).apply(
                lambda g: g.iloc[:, 0].corr(g.iloc[:, 1], method="spearman")
            )

        results[period] = ic_series
    return results


def analyze_factor(
    factor_values: pd.Series,
    forward_return: pd.Series,
    n_quantiles: int = 10,
) -> ICAnalysis:
    """分析单个因子 — IC + 分位数收益。

    Args:
        factor_values: Series with MultiIndex (date, symbol)。
        forward_return: 同期对齐的未来收益 Series。

    Returns:
        ICAnalysis with full statistics。
    """
    aligned = pd.concat([
        factor_values.rename("factor"),
        forward_return.rename("fwd_ret")
    ], axis=1).dropna()

    if len(aligned) < 10:
        return ICAnalysis()

    # IC per period
    ic_by_date = aligned.groupby(level=0).apply(
        lambda g: g["factor"].corr(g["fwd_ret"], method="spearman")
    ).dropna()

    if len(ic_by_date) == 0:
        return ICAnalysis()

    analysis = ICAnalysis()
    analysis.ic_mean = float(ic_by_date.mean())
    analysis.ic_std = float(ic_by_date.std())
    analysis.icir = analysis.ic_mean / analysis.ic_std if analysis.ic_std > 0 else 0.0
    analysis.ic_positive_ratio = float((ic_by_date > 0).mean())
    analysis.ic_series = ic_by_date
    analysis.t_stat = analysis.ic_mean / (analysis.ic_std / np.sqrt(len(ic_by_date))) if analysis.ic_std > 0 else 0.0

    # Quantile returns
    try:
        quantile_returns = _compute_quantile_returns(factor_values, forward_return, n_quantiles)
        analysis.quantile_returns = quantile_returns
        if quantile_returns is not None and not quantile_returns.empty:
            q_cols = [c for c in quantile_returns.columns if c.startswith("Q")]
            if len(q_cols) >= 2:
                top_q = q_cols[-1]
                bot_q = q_cols[0]
                analysis.long_short_return = float(quantile_returns[top_q].mean() - quantile_returns[bot_q].mean())
    except Exception:
        pass

    return analysis


def _compute_quantile_returns(
    factor: pd.Series,
    forward_return: pd.Series,
    n_quantiles: int = 10,
) -> pd.DataFrame | None:
    """计算因子分位数收益 — 每个截面日期分组，计算各组下期平均收益。

    Returns:
        DataFrame: 行=日期, 列=Q1~Q10 (1=最小因子值, 10=最大)
    """
    aligned = pd.concat([factor.rename("factor"), forward_return.rename("fwd")], axis=1).dropna()

    results = {}
    for date, group in aligned.groupby(level=0):
        if len(group) < n_quantiles * 5:
            continue
        try:
            group["quantile"] = pd.qcut(group["factor"], n_quantiles, labels=False, duplicates="drop") + 1
            avg_ret = group.groupby("quantile")["fwd"].mean()
            results[date] = avg_ret
        except Exception:
            continue

    if not results:
        return None
    df = pd.DataFrame(results).T
    df.columns = [f"Q{int(c)}" for c in df.columns]
    df = df.sort_index(axis=1)
    return df


def factor_correlation(factor_df: pd.DataFrame) -> pd.DataFrame:
    """因子间相关系数矩阵。

    Args:
        factor_df: MultiIndex DataFrame, columns=factor values。

    Returns:
        Symmetric correlation matrix。
    """
    return factor_df.corr(method="spearman")


def factor_turnover(factor_df: pd.DataFrame, top_pct: float = 0.20) -> pd.Series:
    """因子选股换手率 — 每期Top股票的变化比例。

    Args:
        factor_df: MultiIndex (date, symbol), one factor column。
        top_pct: 选取前多少比例的股票。

    Returns:
        Series (date → turnover rate 0~1)。
    """
    turnovers = {}
    dates = sorted(factor_df.index.get_level_values(0).unique())

    prev_top: set | None = None
    for date in dates:
        daily = factor_df.xs(date, level=0)
        daily = daily.dropna()
        if daily.empty:
            continue

        n_top = max(1, int(len(daily) * top_pct))
        current_top = set(daily.nlargest(n_top).index)

        if prev_top is not None:
            intersection = len(current_top & prev_top)
            turnover = 1.0 - intersection / n_top
        else:
            turnover = 0.0

        turnovers[date] = turnover
        prev_top = current_top

    return pd.Series(turnovers)


def batch_analyze(
    factor_df: pd.DataFrame,
    forward_return: pd.Series,
    n_quantiles: int = 10,
) -> dict[str, ICAnalysis]:
    """批量因子分析。

    Args:
        factor_df: MultiIndex DataFrame, columns=因子值。
        forward_return: 未来收益 Series。
        n_quantiles: 分组数。

    Returns:
        {factor_name: ICAnalysis}
    """
    results = {}
    for col in factor_df.columns:
        try:
            analysis = analyze_factor(factor_df[col], forward_return, n_quantiles)
            analysis.factor_name = col
            results[col] = analysis
        except Exception as e:
            print(f"Factor {col} analysis failed: {e}")
    return results


def ic_summary_table(analyses: dict[str, ICAnalysis]) -> pd.DataFrame:
    """因子IC汇总表 — 用于横向比较因子表现。

    Returns:
        DataFrame with index=factor_name, columns=[IC_mean, IC_std, ICIR, IC>0%, t-stat, LS_ret]
    """
    rows = []
    for name, a in analyses.items():
        rows.append({
            "factor": name,
            "IC_mean": round(a.ic_mean, 4),
            "IC_std": round(a.ic_std, 4),
            "ICIR": round(a.icir, 2),
            "IC>0%": f"{a.ic_positive_ratio:.1%}",
            "t_stat": round(a.t_stat, 2),
            "LS_ret": f"{a.long_short_return:.4f}",
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("ICIR", ascending=False)
    return df
