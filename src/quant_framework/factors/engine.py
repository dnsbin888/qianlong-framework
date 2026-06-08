"""因子计算引擎 — 对全股票池批量计算因子值。

核心功能:
1. compute(): 对一批股票的K线数据批量计算指定因子
2. 返回 MultiIndex DataFrame: (date, symbol) × factor_values
3. 支持因子缓存、增量更新
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

import numpy as np
import pandas as pd

from quant_framework.core.types import Symbol

logger = logging.getLogger("quant_framework.factors")


class FactorEngine:
    """因子计算引擎。

    输入: 股票池 + 数据获取函数
    输出: Panel-like DataFrame (date × symbol × factor)

    支持两类因子:
      - kline: 基于K线数据计算（技术指标类）
      - financial: 基于财报数据计算（PE/PB/ROE类）— 需传入 FinancialDataLoader

    Usage:
        engine = FactorEngine(data_provider, financial_loader=fin_loader)
        factors_df = engine.compute(
            symbols=["600000", "000001"],
            factor_names=["ret_20d", "pe_ttm", "tdx_qlj"],
            start_date="2024-01-01",
            end_date="2024-12-31",
        )
    """

    def __init__(self, data_provider: Any = None, financial_loader: Any = None) -> None:
        """
        Args:
            data_provider: 实现了 get_kline_dataframe() 的数据提供器。
            financial_loader: FinancialDataLoader 实例 (P0-因子-02)。
                             提供按披露日期对齐的财务因子查询。
        """
        self._provider = data_provider
        self._financial_loader = financial_loader

    # ---- 核心: 批量计算 ----

    def compute(
        self,
        symbols: list[Symbol],
        factor_names: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        period: str = "1d",
        progress: bool = True,
    ) -> pd.DataFrame:
        """批量计算因子值。

        Args:
            symbols: 股票代码列表。
            factor_names: 要计算的因子名称列表。
            start_date: 开始日期 (YYYY-MM-DD)。
            end_date: 结束日期 (YYYY-MM-DD)。
            period: K线周期。
            progress: 是否打印进度。

        Returns:
            DataFrame with MultiIndex (date, symbol), columns=factor_names。
        """
        # 解析因子: 从因子库 + TDX信号库加载
        factors = self._resolve_factors(factor_names)

        all_data: dict[str, pd.DataFrame] = {}
        total = len(symbols)

        for i, sym in enumerate(symbols):
            if progress and i % 100 == 0:
                logger.info("Factor compute: %d/%d", i, total)

            # 获取K线数据
            kline = self._get_kline(sym, period, start_date, end_date)
            if kline is None or kline.empty:
                continue

            # 计算所有因子
            factor_vals = pd.DataFrame(index=kline.index)
            for fname, fdef in factors.items():
                try:
                    # P0-因子-02: 财务因子走 FinancialDataLoader
                    if fdef.get("factor_type") == "financial":
                        if self._financial_loader is None:
                            logger.warning(
                                "Financial factor '%s' requested but no financial_loader set", fname
                            )
                            factor_vals[fname] = np.nan
                            continue
                        # 按日期逐一查询（保持披露日期对齐）
                        values = []
                        for d in kline.index:
                            if isinstance(d, (pd.Timestamp, datetime)):
                                date_int = int(d.strftime("%Y%m%d")) if hasattr(d, 'strftime') else int(f"{d.year:04d}{d.month:02d}{d.day:02d}")
                            elif isinstance(d, str):
                                date_int = int(d.replace("-", ""))
                            else:
                                date_int = int(d)
                            values.append(
                                self._financial_loader.get_financial_factor(sym, date_int, fname)
                            )
                        factor_vals[fname] = values
                    else:
                        result = fdef["compute"](kline)
                        if isinstance(result, pd.Series):
                            factor_vals[fname] = result
                        else:
                            factor_vals[fname] = result
                except Exception as e:
                    logger.warning("Factor %s failed for %s: %s", fname, sym, e)
                    factor_vals[fname] = np.nan

            factor_vals["symbol"] = sym
            all_data[sym] = factor_vals

        if not all_data:
            return pd.DataFrame()

        # 合并所有股票
        df = pd.concat(all_data.values(), keys=all_data.keys(), names=["symbol", "date"])
        df = df.swaplevel(0, 1).sort_index()  # (date, symbol) MultiIndex
        return df

    # ---- 因子解析 ----

    def _resolve_factors(self, factor_names: list[str]) -> dict[str, dict]:
        """从因子库和TDX信号库加载因子定义。

        Returns:
            {name: {name, label, category, direction, compute, ...}}
        """
        from quant_framework.factors.definitions import FACTOR_MAP
        from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
        from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS

        # 合并所有因子源
        all_factors: dict[str, dict] = {}
        all_factors.update({f.name: f.__dict__ for f in FACTOR_MAP.values()})
        all_factors.update(TDX_SIGNAL_FACTORS)
        all_factors.update(TDX2_SIGNAL_FACTORS)

        result = {}
        for name in factor_names:
            if name in all_factors:
                result[name] = all_factors[name]
            else:
                logger.warning("Factor '%s' not found in library", name)

        return result

    def list_available_factors(self) -> list[dict[str, str]]:
        """列出所有可用因子。"""
        from quant_framework.factors.definitions import FACTOR_LIBRARY
        from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
        from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS

        result = []
        for f in FACTOR_LIBRARY:
            result.append({"name": f.name, "label": f.label, "category": f.category.value,
                           "direction": str(f.direction), "source": "builtin"})
        for name, info in TDX_SIGNAL_FACTORS.items():
            result.append({"name": name, "label": info["label"], "category": info["category"],
                           "direction": str(info["direction"]), "source": "tdx1"})
        for name, info in TDX2_SIGNAL_FACTORS.items():
            result.append({"name": name, "label": info["label"], "category": info["category"],
                           "direction": str(info["direction"]), "source": "tdx2"})
        return result

    # ---- 数据获取 ----

    def _get_kline(
        self, symbol: str, period: str, start: str | None, end: str | None
    ) -> pd.DataFrame | None:
        """从 data_provider 获取K线 DataFrame。"""
        if self._provider is None:
            return None

        try:
            # 需要足够的历史数据来计算因子 (至少200天)
            count = 300
            df = self._provider.get_kline_dataframe([symbol], period, count)
            if df is None or df.empty:
                return None

            # Flatten multi-index: (symbol, datetime) -> datetime
            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(symbol, level="symbol")

            # 日期过滤
            if start:
                df = df[df.index >= start]
            if end:
                df = df[df.index <= end]

            return df if not df.empty else None
        except Exception as e:
            logger.debug("Failed to get kline for %s: %s", symbol, e)
            return None


class FactorPreprocessor:
    """因子预处理 — 去极值、标准化、中性化。

    因子在合成之前通常需要:
    1. 去极值 (winsorize / MAD)
    2. 标准化 (z-score)
    3. 行业中性化 (可选)
    4. 市值中性化 (可选)
    """

    @staticmethod
    def winsorize(series: pd.Series, n_sigma: float = 5.0, method: str = "sigma") -> pd.Series:
        """去极值。

        Args:
            series: 原始因子值。
            n_sigma: 偏离标准差的倍数。
            method: 'sigma' (标准差) | 'mad' (中位数绝对偏差) | 'percentile' (百分位)。

        Returns:
            去极值后的 Series。
        """
        result = series.copy()
        if method == "sigma":
            mean, std = series.mean(), series.std()
            upper, lower = mean + n_sigma * std, mean - n_sigma * std
        elif method == "mad":
            median = series.median()
            mad = (series - median).abs().median() * 1.4826
            upper, lower = median + n_sigma * mad, median - n_sigma * mad
        elif method == "percentile":
            upper, lower = series.quantile(0.995), series.quantile(0.005)
        else:
            return result

        result[series > upper] = upper
        result[series < lower] = lower
        return result

    @staticmethod
    def standardize(series: pd.Series) -> pd.Series:
        """Z-score 标准化: (x - mean) / std。"""
        mean, std = series.mean(), series.std()
        if std < 1e-9:
            return pd.Series(0.0, index=series.index)
        return (series - mean) / std

    @staticmethod
    def neutralize(
        factor: pd.Series,
        market_cap: pd.Series | None = None,
        industry: pd.Series | None = None,
    ) -> pd.Series:
        """因子中性化 — 用市值/行业做线性回归取残差。

        Args:
            factor: 因子值。
            market_cap: 市值 (对数)。
            industry: 行业代码 (转为 dummy variable)。

        Returns:
            中性化后的残差因子。
        """
        if market_cap is None and industry is None:
            return factor

        import statsmodels.api as sm

        df = pd.DataFrame({"factor": factor})
        if market_cap is not None:
            df["ln_cap"] = np.log(market_cap)
        if industry is not None:
            dummies = pd.get_dummies(industry, prefix="ind")
            df = pd.concat([df, dummies], axis=1)

        # 只用有效数据回归
        valid = df.dropna()
        if len(valid) < 10:
            return factor

        X = sm.add_constant(valid.drop(columns=["factor"]))
        y = valid["factor"]
        try:
            model = sm.OLS(y, X).fit()
            residual = factor - model.predict(sm.add_constant(df.drop(columns=["factor"])))
            return residual
        except Exception:
            return factor

    def pipeline(
        self,
        factor: pd.Series,
        winsorize: bool = True,
        standardize: bool = True,
        market_cap: pd.Series | None = None,
        industry: pd.Series | None = None,
    ) -> pd.Series:
        """完整的因子预处理流水线。"""
        result = factor.copy()

        if winsorize:
            result = self.winsorize(result)

        if market_cap is not None or industry is not None:
            result = self.neutralize(result, market_cap, industry)

        if standardize:
            result = self.standardize(result)

        return result
