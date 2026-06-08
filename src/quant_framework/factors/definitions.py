"""因子定义 — 30+个常见A股量化因子。

每个因子包含: 名称、分类、方向(正向/反向)、计算函数、参数。
因子越正(大)预期收益越高为正向(+1)，越低为反向(-1)。

因子分类:
  momentum    — 动量类 (趋势跟踪)
  reversal    — 反转类 (均值回归)
  volatility  — 波动类 (风险度量)
  liquidity   — 流动性 (换手/成交)
  technical   — 技术指标 (均线/RSI/MACD)
  volume_price — 量价关系
  pattern     — 形态识别
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class FactorCategory(str, Enum):
    MOMENTUM = "momentum"
    REVERSAL = "reversal"
    VOLATILITY = "volatility"
    LIQUIDITY = "liquidity"
    TECHNICAL = "technical"
    VOLUME_PRICE = "volume_price"
    PATTERN = "pattern"
    FUNDAMENTAL = "fundamental"  # P0-因子-02: 基本面因子 (PE/PB/ROE等)


@dataclass
class FactorDef:
    """因子定义 — 元数据 + 计算函数。

    Attributes:
        name: 因子唯一标识 (英文)
        label: 中文名称
        category: 因子分类
        direction: +1=正向(值越大预期收益越高), -1=反向
        params: 默认参数
        compute: 计算函数 (kline_df: DataFrame) -> pd.Series
        description: 因子描述/参考文献
        factor_type: "kline" (默认，基于K线) | "financial" (基于财报)
    """
    name: str
    label: str
    category: FactorCategory
    direction: int  # +1 or -1
    params: dict = field(default_factory=dict)
    compute: Callable | None = None
    description: str = ""
    factor_type: str = "kline"  # P0-因子-02: "kline" | "financial"


# ======================================================================
# 因子计算函数 — 每个函数输入 kline_df (OHLCV), 返回 Series
# ======================================================================

def _ret(close: pd.Series, period: int) -> pd.Series:
    """N日收益率"""
    return close.pct_change(period)


def _vol(close: pd.Series, period: int) -> pd.Series:
    """N日波动率 (年化)"""
    return close.pct_change().rolling(period).std() * np.sqrt(252)


def _ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def _max_ret(close: pd.Series, period: int) -> pd.Series:
    """N日最大涨幅"""
    return close.pct_change().rolling(period).max()


def _min_ret(close: pd.Series, period: int) -> pd.Series:
    """N日最大跌幅"""
    return close.pct_change().rolling(period).min()


def _downside_vol(close: pd.Series, period: int) -> pd.Series:
    """下行波动率"""
    ret = close.pct_change()
    downside = ret[ret < 0].reindex(ret.index, fill_value=0)
    return downside.rolling(period).std() * np.sqrt(252)


def _turnover(df: pd.DataFrame, period: int) -> pd.Series:
    """N日均换手率 (需要 volume 列)"""
    return df["volume"].rolling(period).mean()


def _turnover_volatility(df: pd.DataFrame, period: int) -> pd.Series:
    """换手率波动 (换手率的标准差)"""
    return df["volume"].rolling(period).std()


def _volume_ratio(df: pd.DataFrame, period: int) -> pd.Series:
    """量比 = 当日成交量 / N日均量"""
    ma_vol = df["volume"].rolling(period).mean()
    return df["volume"] / ma_vol.replace(0, float("nan"))


def _amplitude(df: pd.DataFrame, period: int) -> pd.Series:
    """N日均振幅 = (high-low)/close"""
    amp = (df["high"] - df["low"]) / df["close"]
    return amp.rolling(period).mean()


def _rsi_factor(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI 因子"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100.0 - (100.0 / (1.0 + rs))


def _ma_deviation(close: pd.Series, period: int) -> pd.Series:
    """均线偏离度 = (close - MA) / MA"""
    ma = close.rolling(period).mean()
    return (close - ma) / ma.replace(0, float("nan"))


def _ma_cross(close: pd.Series, fast: int, slow: int) -> pd.Series:
    """均线交叉信号: 快线/慢线 - 1 (正值=多头排列)"""
    fast_ma = close.rolling(fast).mean()
    slow_ma = close.rolling(slow).mean()
    return fast_ma / slow_ma.replace(0, float("nan")) - 1.0


def _bias(close: pd.Series, period: int) -> pd.Series:
    """乖离率 BIAS = (C-MA(N))/MA(N)*100"""
    ma = close.rolling(period).mean()
    return (close - ma) / ma.replace(0, float("nan")) * 100


def _skewness(close: pd.Series, period: int) -> pd.Series:
    """收益偏度 — 正偏度表示右偏(有暴涨倾向)"""
    return close.pct_change().rolling(period).skew()


def _kurtosis(close: pd.Series, period: int) -> pd.Series:
    """收益峰度 — 高峰度=厚尾/极端值多"""
    return close.pct_change().rolling(period).kurt()


def _up_days_ratio(close: pd.Series, period: int) -> pd.Series:
    """N日内上涨天数比例"""
    up = (close.diff() > 0).astype(int)
    return up.rolling(period).sum() / period


def _hl_ratio(df: pd.DataFrame, period: int) -> pd.Series:
    """N日高低价差比率 = (high_max - low_min) / close"""
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    return (high_max - low_min) / df["close"]


def _price_position(df: pd.DataFrame, period: int) -> pd.Series:
    """价格位置 = (close - low_min) / (high_max - low_min)  0~1"""
    high_max = df["high"].rolling(period).max()
    low_min = df["low"].rolling(period).min()
    denom = (high_max - low_min).replace(0, float("nan"))
    return (df["close"] - low_min) / denom


def _volume_price_corr(df: pd.DataFrame, period: int) -> pd.Series:
    """量价相关性 — 正相关=放量上涨(强势)"""
    ret = df["close"].pct_change()
    return ret.rolling(period).corr(df["volume"])


def _volume_breakout(df: pd.DataFrame, period: int, multiple: float = 1.5) -> pd.Series:
    """放量突破 = 今日成交量 / N日均量 >= multiple 则为1"""
    ma_vol = df["volume"].rolling(period).mean()
    ratio = df["volume"] / ma_vol.replace(0, float("nan"))
    return (ratio >= multiple).astype(float)


def _rsi_divergence(close: pd.Series, period: int) -> pd.Series:
    """RSI与价格背离度 (简化)"""
    rsi = _rsi_factor(close, period)
    rsi_delta = rsi.diff(period)
    price_delta = close.pct_change(period)
    # 价格涨RSI跌 = 顶背离(看空,负值), 价格跌RSI涨 = 底背离(看多,正值)
    return -rsi_delta * np.sign(price_delta)


# ======================================================================
# 因子库 — 30+ 因子
# ======================================================================

FACTOR_LIBRARY: list[FactorDef] = [
    # ---- 动量因子 (Momentum) ----
    FactorDef(
        name="ret_5d", label="5日收益率", category=FactorCategory.MOMENTUM, direction=1,
        params={"period": 5},
        compute=lambda df: _ret(df["close"], 5),
        description="短期动量: 过去5日涨幅越高, 未来收益越高(追涨)"),
    FactorDef(
        name="ret_20d", label="20日收益率", category=FactorCategory.MOMENTUM, direction=1,
        params={"period": 20},
        compute=lambda df: _ret(df["close"], 20),
        description="中期动量: 过去1月涨幅"),
    FactorDef(
        name="ret_60d", label="60日收益率", category=FactorCategory.MOMENTUM, direction=1,
        params={"period": 60},
        compute=lambda df: _ret(df["close"], 60),
        description="长期动量: 过去1季度涨幅"),

    # ---- 反转因子 (Reversal) ----
    FactorDef(
        name="ret_1d", label="1日反转", category=FactorCategory.REVERSAL, direction=-1,
        params={"period": 1},
        compute=lambda df: _ret(df["close"], 1),
        description="短期反转: 昨日涨今日跌(均值回归)"),
    FactorDef(
        name="max_ret_20d", label="20日最大涨幅", category=FactorCategory.REVERSAL, direction=-1,
        params={"period": 20},
        compute=lambda df: _max_ret(df["close"], 20),
        description="极端涨幅后反转: 过去1月单日最大涨幅越大, 未来倾向下跌"),
    FactorDef(
        name="min_ret_20d", label="20日最大跌幅", category=FactorCategory.REVERSAL, direction=1,
        params={"period": 20},
        compute=lambda df: _min_ret(df["close"], 20),
        description="超跌反弹: 过去1月单日最大跌幅越大, 未来倾向反弹"),

    # ---- 波动率因子 (Volatility) ----
    FactorDef(
        name="vol_20d", label="20日波动率", category=FactorCategory.VOLATILITY, direction=-1,
        params={"period": 20},
        compute=lambda df: _vol(df["close"], 20),
        description="低波异象: 低波动股票未来收益更高"),
    FactorDef(
        name="downside_vol_20d", label="20日下行波动率", category=FactorCategory.VOLATILITY, direction=-1,
        params={"period": 20},
        compute=lambda df: _downside_vol(df["close"], 20),
        description="下行风险: 只计下跌日的波动, 越低越好"),
    FactorDef(
        name="skewness_20d", label="20日收益偏度", category=FactorCategory.VOLATILITY, direction=1,
        params={"period": 20},
        compute=lambda df: _skewness(df["close"], 20),
        description="正偏度: 有暴涨倾向的股票更受青睐"),
    FactorDef(
        name="kurtosis_20d", label="20日收益峰度", category=FactorCategory.VOLATILITY, direction=-1,
        params={"period": 20},
        compute=lambda df: _kurtosis(df["close"], 20),
        description="高峰度=极端风险高, 应回避"),

    # ---- 流动性因子 (Liquidity) ----
    FactorDef(
        name="turnover_5d", label="5日均换手", category=FactorCategory.LIQUIDITY, direction=-1,
        params={"period": 5},
        compute=lambda df: _turnover(df, 5),
        description="低换手溢价: 低换手率股票长期收益更高"),
    FactorDef(
        name="turnover_vol_20d", label="换手率波动", category=FactorCategory.LIQUIDITY, direction=-1,
        params={"period": 20},
        compute=lambda df: _turnover_volatility(df, 20),
        description="换手率不稳定=筹码松动"),
    FactorDef(
        name="volume_ratio_5d", label="5日量比", category=FactorCategory.LIQUIDITY, direction=1,
        params={"period": 5},
        compute=lambda df: _volume_ratio(df, 5),
        description="放量=资金关注度高"),

    # ---- 技术指标因子 (Technical) ----
    FactorDef(
        name="rsi_14", label="14日RSI", category=FactorCategory.TECHNICAL, direction=1,
        params={"period": 14},
        compute=lambda df: _rsi_factor(df["close"], 14),
        description="RSI越高=动能越强"),
    FactorDef(
        name="ma_dev_20", label="20日均线偏离", category=FactorCategory.TECHNICAL, direction=1,
        params={"period": 20},
        compute=lambda df: _ma_deviation(df["close"], 20),
        description="价格在均线上方=多头排列"),
    FactorDef(
        name="ma_cross_5_20", label="5/20均线交叉", category=FactorCategory.TECHNICAL, direction=1,
        params={"fast": 5, "slow": 20},
        compute=lambda df: _ma_cross(df["close"], 5, 20),
        description="金叉信号: 短均线上穿长均线"),
    FactorDef(
        name="bias_20", label="20日乖离率", category=FactorCategory.TECHNICAL, direction=-1,
        params={"period": 20},
        compute=lambda df: _bias(df["close"], 20),
        description="乖离率过大=超买, 应回调"),
    FactorDef(
        name="up_days_ratio_20", label="20日上涨比例", category=FactorCategory.TECHNICAL, direction=1,
        params={"period": 20},
        compute=lambda df: _up_days_ratio(df["close"], 20),
        description="上涨天数越多=趋势越强"),
    FactorDef(
        name="price_position_20", label="20日价格位置", category=FactorCategory.TECHNICAL, direction=1,
        params={"period": 20},
        compute=lambda df: _price_position(df, 20),
        description="价格在N日区间的位置, 高位=强势"),

    # ---- 量价因子 (Volume-Price) ----
    FactorDef(
        name="amplitude_5d", label="5日均振幅", category=FactorCategory.VOLUME_PRICE, direction=-1,
        params={"period": 5},
        compute=lambda df: _amplitude(df, 5),
        description="振幅大=不稳定/控盘弱"),
    FactorDef(
        name="volume_price_corr_20", label="20日量价相关", category=FactorCategory.VOLUME_PRICE, direction=1,
        params={"period": 20},
        compute=lambda df: _volume_price_corr(df, 20),
        description="量价正相关=放量上涨, 健康"),
    FactorDef(
        name="volume_breakout", label="放量突破", category=FactorCategory.VOLUME_PRICE, direction=1,
        params={"period": 20, "multiple": 1.5},
        compute=lambda df: _volume_breakout(df, 20, 1.5),
        description="今日成交 > 20日均量*1.5, 突破信号"),
    FactorDef(
        name="rsi_divergence_14", label="RSI背离度", category=FactorCategory.VOLUME_PRICE, direction=1,
        params={"period": 14},
        compute=lambda df: _rsi_divergence(df["close"], 14),
        description="底背离=看涨, 顶背离=看跌"),

    # ---- 形态因子 (Pattern) ----
    FactorDef(
        name="hl_ratio_20", label="20日振幅比率", category=FactorCategory.PATTERN, direction=-1,
        params={"period": 20},
        compute=lambda df: _hl_ratio(df, 20),
        description="振幅越大=波动越大, 收益不确定"),

    # ---- 基本面因子 (Fundamental) — P0-因子-02 ----
    FactorDef(
        name="pe_ttm", label="市盈率(TTM)", category=FactorCategory.FUNDAMENTAL, direction=-1,
        factor_type="financial",
        description="滚动市盈率: 越低估值越低。注意亏损企业PE为负需特殊处理"),
    FactorDef(
        name="pb", label="市净率", category=FactorCategory.FUNDAMENTAL, direction=-1,
        factor_type="financial",
        description="市净率: 越低越便宜。金融/重资产行业适用性更好"),
    FactorDef(
        name="roe", label="净资产收益率", category=FactorCategory.FUNDAMENTAL, direction=1,
        factor_type="financial",
        description="ROE: 股东回报率，越高盈利能力越强 (巴菲特核心指标)"),
    FactorDef(
        name="profit_growth", label="利润增速(YoY)", category=FactorCategory.FUNDAMENTAL, direction=1,
        factor_type="financial",
        description="归母净利润同比增长率: 正值=成长，负值=衰退"),
    FactorDef(
        name="revenue_growth", label="营收增速(YoY)", category=FactorCategory.FUNDAMENTAL, direction=1,
        factor_type="financial",
        description="营业收入同比增长率: 业务扩张速度"),
    FactorDef(
        name="debt_ratio", label="资产负债率", category=FactorCategory.FUNDAMENTAL, direction=-1,
        factor_type="financial",
        description="资产负债率: 越低财务越稳健 (但金融行业天然高杠杆)"),
    FactorDef(
        name="net_profit_margin", label="净利率", category=FactorCategory.FUNDAMENTAL, direction=1,
        factor_type="financial",
        description="净利润率: 盈利质量，越高越有定价权"),
]

# 按名称索引
FACTOR_MAP: dict[str, FactorDef] = {f.name: f for f in FACTOR_LIBRARY}

# 按分类索引
FACTORS_BY_CATEGORY: dict[FactorCategory, list[FactorDef]] = {}
for f in FACTOR_LIBRARY:
    FACTORS_BY_CATEGORY.setdefault(f.category, []).append(f)
