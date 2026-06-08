"""因子选股模块 — 因子库 · 计算引擎 · IC分析 · 多因子合成 · 选股回测。

Factors:
  30+ builtin    — 动量/反转/波动/流动性/技术/量价/形态因子
  13 tdx1        — 用户通达信主图指标因子 (擒龙决/起爆点/强庄...)
  6  tdx2        — 用户通达信选股公式因子 (牛线突破/底部反转...)

Pipeline:
  FactorEngine → 批量计算因子值
  FactorPreprocessor → 去极值/标准化/中性化
  analyze_factor → IC/ICIR/分位数收益
  FactorCompositor → 等权/IC加权/ICIR加权合成
  StockSelector → 月度调仓/TopK选股
  PortfolioBacktester → 组合净值曲线
"""

from quant_framework.factors.analysis import (
    ICAnalysis,
    analyze_factor,
    batch_analyze,
    compute_factor_returns,
    factor_correlation,
    factor_turnover,
    ic_summary_table,
)
from quant_framework.factors.definitions import (
    FACTOR_LIBRARY,
    FACTOR_MAP,
    FACTORS_BY_CATEGORY,
    FactorCategory,
    FactorDef,
)
from quant_framework.factors.engine import FactorEngine, FactorPreprocessor
from quant_framework.factors.selector import (
    FactorCompositor,
    PortfolioBacktester,
    StockSelector,
)
from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS

__all__ = [
    # Definitions
    "FactorDef", "FactorCategory", "FACTOR_LIBRARY", "FACTOR_MAP", "FACTORS_BY_CATEGORY",
    # TDX
    "TDX_SIGNAL_FACTORS", "TDX2_SIGNAL_FACTORS",
    # Engine
    "FactorEngine", "FactorPreprocessor",
    # Analysis
    "ICAnalysis", "compute_factor_returns", "analyze_factor",
    "batch_analyze", "factor_correlation", "factor_turnover", "ic_summary_table",
    # Selection
    "FactorCompositor", "StockSelector", "PortfolioBacktester",
]
