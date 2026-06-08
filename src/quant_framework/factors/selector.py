"""多因子选股 — 因子合成 + 股票评分 + TopK选股。

合成方法:
  - equal_weight: 等权加总
  - ic_weighted: 历史IC加权
  - icir_weighted: 历史ICIR加权
  - score_rank: 打分法 (每因子排序后加总)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant_framework.factors.analysis import compute_factor_returns
from quant_framework.factors.engine import FactorPreprocessor


class FactorCompositor:
    """因子合成器 — 将多个因子合并为综合评分。

    Usage:
        compositor = FactorCompositor(method="icir_weighted")
        scores = compositor.composite(factor_df, ic_series)
        top_stocks = scores.groupby(level=0).apply(lambda g: g.nlargest(30))
    """

    def __init__(
        self,
        method: str = "equal_weight",
        preprocess: bool = True,
        preprocessor: FactorPreprocessor | None = None,
    ) -> None:
        """
        Args:
            method: 'equal_weight' | 'ic_weighted' | 'icir_weighted' | 'rank_score'
            preprocess: 是否预处理因子 (去极值+标准化)。
            preprocessor: 自定义预处理器。
        """
        self.method = method
        self.preprocess = preprocess
        self.preprocessor = preprocessor or FactorPreprocessor()
        self._weights: dict[str, float] = {}

    # ---- 核心: 因子合成 ----

    def composite(
        self,
        factor_df: pd.DataFrame,
        ic_info: pd.DataFrame | None = None,
        top_k: int | None = None,
    ) -> pd.DataFrame:
        """将多个因子合成为综合评分。

        Args:
            factor_df: MultiIndex (date, symbol), columns=因子值。
            ic_info: IC汇总表 (factor_name → IC/ICIR)，用于加权。
            top_k: 如果提供，只返回每期评分最高的K只股票。

        Returns:
            DataFrame with (date, symbol) MultiIndex, column='score'。
        """
        # 1. 预处理
        if self.preprocess:
            processed = {}
            for col in factor_df.columns:
                processed[col] = factor_df[col].groupby(level=0, group_keys=False).apply(
                    lambda g: self.preprocessor.pipeline(g, winsorize=True, standardize=True)
                )
            factor_df = pd.DataFrame(processed)

        # 2. 确定权重
        weights = self._calc_weights(factor_df.columns.tolist(), ic_info)

        # 3. 加权合成
        score = pd.Series(0.0, index=factor_df.index)
        for col in factor_df.columns:
            w = weights.get(col, 1.0 / len(factor_df.columns))
            score += factor_df[col].fillna(0) * w

        result = score.to_frame("score")

        # 4. TopK 选股
        if top_k is not None:
            result = result.groupby(level=0, group_keys=False).apply(
                lambda g: g.nlargest(top_k, "score")
            )

        return result

    def _calc_weights(
        self, factor_names: list[str], ic_info: pd.DataFrame | None
    ) -> dict[str, float]:
        """计算因子合成权重。"""
        n = len(factor_names)
        if n == 0:
            return {}

        if self.method == "equal_weight":
            return {f: 1.0 / n for f in factor_names}

        if ic_info is None or ic_info.empty:
            return {f: 1.0 / n for f in factor_names}

        weights = {}
        for f in factor_names:
            if f in ic_info.index:
                if self.method == "ic_weighted":
                    w = abs(ic_info.loc[f, "IC_mean"])
                elif self.method == "icir_weighted":
                    w = abs(ic_info.loc[f, "ICIR"])
                elif self.method == "rank_score":
                    w = 1.0  # rank doesn't use weights
                else:
                    w = 1.0 / n
                weights[f] = w
            else:
                weights[f] = 1.0 / n

        # Normalize so sum = 1
        total = sum(weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights

    @property
    def weights(self) -> dict[str, float]:
        return self._weights


class StockSelector:
    """股票筛选器 — 按时序执行因子选股。

    Usage:
        selector = StockSelector(compositor)
        portfolio = selector.select(
            factor_df,
            top_k=30,
            rebalance_freq="1M",  # 月度调仓
        )
    """

    def __init__(self, compositor: FactorCompositor | None = None) -> None:
        self.compositor = compositor or FactorCompositor()

    def select(
        self,
        factor_df: pd.DataFrame,
        top_k: int = 30,
        rebalance_freq: str = "1M",
        ic_info: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """执行多因子选股。

        Args:
            factor_df: MultiIndex (date, symbol), columns=因子值。
            top_k: 每期选的股票数。
            rebalance_freq: 调仓频率 ('1M'=月末, '1W'=周末)。
            ic_info: IC汇总表用于加权。

        Returns:
            DataFrame: (date, symbol) × ['score', 'weight'], weight=等权1/k。
        """
        # 合成评分
        scores = self.compositor.composite(factor_df, ic_info)

        # 按调仓频率选择日期
        dates = sorted(scores.index.get_level_values(0).unique())
        rebalance_dates = self._get_rebalance_dates(dates, rebalance_freq)

        # 每个调仓日选TopK
        selections: list[pd.DataFrame] = []
        for date in rebalance_dates:
            if date not in scores.index:
                # 找最近日期
                nearby = [d for d in dates if d <= date]
                if not nearby:
                    continue
                date = nearby[-1]

            daily = scores.xs(date, level=0)
            daily = daily.dropna()
            if len(daily) < top_k:
                continue

            top = daily.nlargest(top_k, "score")
            top["weight"] = 1.0 / top_k
            top["date"] = date
            top = top.reset_index().set_index(["date", "symbol"])
            selections.append(top)

        if not selections:
            return pd.DataFrame(columns=["score", "weight"])

        return pd.concat(selections).sort_index()

    def _get_rebalance_dates(self, dates: list, freq: str) -> list:
        """获取调仓日期列表。"""
        date_series = pd.Series(dates)
        if freq == "1M":
            # 每月最后一个交易日
            df = pd.DataFrame({"date": pd.to_datetime(date_series)})
            df["year_month"] = df["date"].dt.to_period("M")
            return df.groupby("year_month")["date"].max().tolist()
        elif freq == "1W":
            # 每周最后一个交易日
            df = pd.DataFrame({"date": pd.to_datetime(date_series)})
            df["year_week"] = df["date"].dt.to_period("W")
            return df.groupby("year_week")["date"].max().tolist()
        elif freq == "1D":
            return list(dates)
        else:
            return list(dates)


class PortfolioBacktester:
    """选股组合回测 — 按调仓信号计算组合净值曲线。

    输入: 选股结果 (date, symbol, weight) + 日收益数据
    输出: 组合净值 + 绩效指标
    """

    def backtest(
        self,
        selections: pd.DataFrame,
        daily_returns: pd.DataFrame,
        initial_cash: float = 1_000_000.0,
        commission_rate: float = 0.0003,
    ) -> pd.DataFrame:
        """回测选股组合。

        Args:
            selections: (date, symbol) MultiIndex, columns=['weight']
            daily_returns: (date, symbol) MultiIndex, column='ret' (日收益率)
            initial_cash: 初始资金
            commission_rate: 手续费率

        Returns:
            DataFrame: date × [equity, return, turnover]
        """
        # 按调仓日期分配
        rebalance_dates = sorted(selections.index.get_level_values(0).unique())
        all_dates = sorted(daily_returns.index.get_level_values(0).unique())

        equity = initial_cash
        equity_curve = []
        current_portfolio: dict[str, float] = {}  # symbol → weight

        date_idx = 0
        portfolio_date = None

        for date in all_dates:
            # 检查是否调仓日
            if date in rebalance_dates or date >= rebalance_dates[0] if rebalance_dates else False:
                # 找到最近的调仓日
                nearby = [d for d in rebalance_dates if d <= date]
                if nearby and nearby[-1] != portfolio_date:
                    portfolio_date = nearby[-1]
                    sel = selections.xs(portfolio_date, level=0)
                    current_portfolio = dict(zip(sel.index, sel["weight"]))

                    # 计算换手成本
                    # (简化: 每次调仓收0.03%手续费)

            # 当日收益
            try:
                daily = daily_returns.xs(date, level=0)
            except KeyError:
                equity_curve.append({"date": date, "equity": equity})
                continue

            daily_ret = 0.0
            for sym, weight in current_portfolio.items():
                if sym in daily.index:
                    daily_ret += weight * daily.loc[sym, "ret"]

            equity *= (1 + daily_ret)
            equity_curve.append({"date": date, "equity": equity, "daily_ret": daily_ret})

        return pd.DataFrame(equity_curve).set_index("date")
