"""
双信号共振选股策略 — 打板 + 分歧低吸
源自通达信选股公式

信号1 — 擒龙决：
  - 突破30日最高价压力位
  - 突破布林带上轨（20EMA + 2σ）
  - 量比 > 1.8（放量）
  - 7日内首次触发

信号2 — 涨停先锋：
  - 突破99%获利盘成本线
  - 7日内首次触发

买入条件：两个信号同时触发（XG = 擒龙决 AND 涨停先锋）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.data.models import Bar, Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class DragonTigerConfig:
    """双信号共振策略配置。"""

    symbol: str = "600000"
    period: str = "1d"

    # 擒龙决参数
    breakout_lookback: int = 30          # 压力位回看天数
    boll_period: int = 20               # 布林带周期
    boll_std: float = 2.0               # 布林带标准差倍数
    vol_ratio_threshold: float = 1.8    # 量比阈值
    unique_days: int = 7                # 信号唯一性天数

    # 涨停先锋参数
    cost_pct: int = 99                  # 获利盘百分比
    cost_ema_period: int = 5            # 成本线EMA平滑

    # 出场规则
    hold_days: int = 3                  # 持有天数
    stop_loss_pct: float = -4.0         # 止损
    stop_profit_pct: float = 10.0       # 止盈
    volume: int = 1000                  # 每笔股数


class DragonTigerStrategy(BaseStrategy):
    """双信号共振策略。

    擒龙决（打板信号） + 涨停先锋（趋势确认） 同时触发时买入。
    """

    def __init__(self, ctx: StrategyContext, cfg: DragonTigerConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._position_open = False
        self._entry_price: float = 0.0
        self._bars_held: int = 0

    def on_init(self) -> None:
        self.ctx.logger.info("dragon_tiger_init", symbol=self.cfg.symbol)

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        df = self._get_kline_df(self.cfg.symbol, 200, self.cfg.period)
        if df is None or len(df) < 100:
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        volume = df["volume"].values
        n = len(close)

        # ── 擒龙决 ──
        # 压力位
        hhv30 = _hhv(close, self.cfg.breakout_lookback)
        ref_hhv = _ref(hhv30, 1)
        pressure = _sma(ref_hhv, 2)      # MA(REF(HHV(C,30),1),2)

        # 布林带上轨
        ema20 = _ema(close, self.cfg.boll_period)
        deviation = close - ema20
        variance_ma = _sma(deviation ** 2, self.cfg.boll_period)
        std_dev = np.sqrt(np.maximum(variance_ma, 0))
        boll_upper = ema20 + self.cfg.boll_std * std_dev
        ref_boll = _ref(boll_upper, 1)   # 昨日布林上轨（涨停线）

        # 量比
        vol_ma5 = _sma(volume, 5)
        ref_vol_ma5 = _ref(vol_ma5, 1)
        vol_ratio = volume / np.maximum(ref_vol_ma5, 1)

        # 擒龙决条件
        cond_pressure = close > pressure
        cond_boll = close > ref_boll
        cond_vol = vol_ratio > self.cfg.vol_ratio_threshold

        qin_long = cond_pressure.astype(int) & cond_boll.astype(int) & cond_vol.astype(int)
        qin_long_unique = _count_condition(qin_long, self.cfg.unique_days) == 1

        # ── 涨停先锋 ──
        cost99 = _estimate_cost(close, self.cfg.cost_pct, 100)
        profit_line = _ema(cost99, self.cfg.cost_ema_period)
        cond_breakout = close > profit_line
        zhang_ting_unique = _count_condition(cond_breakout.astype(int), self.cfg.unique_days) == 1

        # ── XG ──
        xg = qin_long_unique & zhang_ting_unique

        # ── 出场 ──
        if self._position_open and self._bars_held > 0:
            self._bars_held += 1
            current = close[-1]
            pnl = (current / self._entry_price - 1) * 100

            if pnl <= self.cfg.stop_loss_pct:
                self._position_open = False
                return self.sell(self.cfg.symbol, price=current,
                                 volume=self.cfg.volume, reason=f"止损 {pnl:.1f}%")

            if pnl >= self.cfg.stop_profit_pct:
                self._position_open = False
                return self.sell(self.cfg.symbol, price=current,
                                 volume=self.cfg.volume, reason=f"止盈 +{pnl:.1f}%")

            if self.cfg.hold_days > 0 and self._bars_held >= self.cfg.hold_days:
                self._position_open = False
                return self.sell(self.cfg.symbol, price=current,
                                 volume=self.cfg.volume,
                                 reason=f"持有{self.cfg.hold_days}日到期")

        # ── 买入 ──
        if xg[-1] and not self._position_open:
            self._position_open = True
            self._entry_price = close[-1]
            self._bars_held = 1
            self.ctx.logger.info(
                "dragon_tiger_buy",
                price=close[-1],
                vol_ratio=vol_ratio[-1],
            )
            return self.buy(self.cfg.symbol, price=close[-1],
                            volume=self.cfg.volume,
                            reason="双信号共振: 擒龙决+涨停先锋")

        return None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        return None


# ═══════════════════════════════════════════════════════════════════════
# 通达信辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(series))
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(series), np.nan)
    if len(series) >= period:
        cumsum = np.cumsum(np.insert(series, 0, 0))
        result[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    # Forward-fill NaN
    mask = np.isnan(result)
    if mask.any():
        last_valid = series[0]
        for i in range(len(result)):
            if not np.isnan(result[i]):
                last_valid = result[i]
            else:
                result[i] = last_valid
    return result


def _ref(series: np.ndarray, n: int) -> np.ndarray:
    result = np.full(len(series), series[0])
    if n < len(series):
        result[n:] = series[:-n]
    return result


def _hhv(series: np.ndarray, period: int) -> np.ndarray:
    result = np.zeros(len(series))
    for i in range(len(series)):
        start = max(0, i - period + 1)
        result[i] = np.max(series[start:i + 1])
    return result


def _count_condition(condition: np.ndarray, period: int) -> np.ndarray:
    """COUNT(COND, N) — N周期内条件成立次数。"""
    n = len(condition)
    result = np.zeros(n, dtype=int)
    cond_int = condition.astype(int)
    for i in range(n):
        start = max(0, i - period + 1)
        result[i] = cond_int[start:i + 1].sum()
    return result


def _estimate_cost(close: np.ndarray, percentile: int, window: int = 100) -> np.ndarray:
    """
    改进的 COST(N) 近似 —— 模拟通达信筹码分布。

    通达信 COST(N) 基于全量 CYQ数据（每个价位的成交量分布）。
    此处用指数衰减加权分位数近似：
    - 近期价格权重更高
    - 窗口内按成交量加权（用价格波动模拟量价关系）
    """
    n = len(close)
    result = np.zeros(n)
    # 衰减因子：越近的交易日权重越高
    alpha = 0.95

    for i in range(n):
        start = max(0, i - window + 1)
        w = close[start:i + 1]
        if len(w) < 10:
            result[i] = close[i]
            continue

        # 指数衰减权重（最近数据权重最高）
        weights = np.array([alpha ** (len(w) - 1 - j) for j in range(len(w))])
        weights = weights / weights.sum()

        # 按价格排序，累积权重 → 找N%分位价格
        sorted_idx = np.argsort(w)
        cum_weights = np.cumsum(weights[sorted_idx])

        # 找到权重累积达到 percentile/100 的价格
        target = percentile / 100.0
        idx = np.searchsorted(cum_weights, target)
        if idx >= len(w):
            idx = len(w) - 1
        result[i] = w[sorted_idx[idx]]

    return result


# ═══════════════════════════════════════════════════════════════════════
# 回测信号生成函数（用于参数优化器）
# ═══════════════════════════════════════════════════════════════════════

def generate_dragon_tiger_signals(
    close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray,
    breakout_lookback: int = 30, boll_period: int = 20,
    boll_std: float = 2.0, vol_ratio_threshold: float = 1.8,
    unique_days: int = 7, cost_pct: int = 99, cost_ema_period: int = 5,
) -> np.ndarray:
    """生成双信号共振信号。返回: 1=买入, -1=卖出, 0=持有。"""
    n = len(close)
    if n < 100:
        return np.zeros(n, dtype=int)

    # 擒龙决
    hhv30 = _hhv(close, breakout_lookback)
    ref_hhv = _ref(hhv30, 1)
    pressure = _sma(ref_hhv, 2)
    ema20 = _ema(close, boll_period)
    deviation = close - ema20
    variance_ma = _sma(deviation ** 2, boll_period)
    std_dev = np.sqrt(np.maximum(variance_ma, 0))
    boll_upper = ema20 + boll_std * std_dev
    ref_boll = _ref(boll_upper, 1)
    vol_ma5 = _sma(volume, 5)
    ref_vol_ma5 = _ref(vol_ma5, 1)
    vol_ratio = volume / np.maximum(ref_vol_ma5, 1)

    qin_long = (close > pressure) & (close > ref_boll) & (vol_ratio > vol_ratio_threshold)
    qin_long_unique = _count_condition(qin_long.astype(int), unique_days) == 1

    # 涨停先锋
    cost99 = _estimate_cost(close, cost_pct, 100)
    profit_line = _ema(cost99, cost_ema_period)
    cond_breakout = close > profit_line
    zhang_ting_unique = _count_condition(cond_breakout.astype(int), unique_days) == 1

    xg = qin_long_unique & zhang_ting_unique

    signals = np.zeros(n, dtype=int)
    signals[xg] = 1
    return signals
