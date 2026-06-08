"""
牛线突破选股策略 — 源自通达信选股公式

核心逻辑：
1. 牛线：DMA加权动态均线 × EMA200平滑 × 1.118倍数
2. 买入条件（XG AND B1）：
   - 价格上穿牛线
   - MACD多头（DIF > DEA）
   - 当日涨幅 > 9%（涨停板附近）
   - 突破55日最高价，且前期有回踩支撑确认

策略行为：
- 买入信号触发次日开盘买入（T+1 规则）
- 持有N日后卖出，或止损/止盈离场
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
class BullLineBreakoutConfig:
    """牛线突破策略配置。"""

    symbol: str = "600000"
    period: str = "1d"

    # 牛线参数
    bull_line_ema_period: int = 200      # 牛线EMA平滑周期
    bull_line_multiplier: float = 1.118  # 牛线倍数

    # 买入条件
    min_change_pct: float = 9.0          # 当日最小涨幅%（接近涨停）
    require_macd_bullish: bool = True    # 要求MACD多头

    # 回踩确认参数
    high_lookback: int = 55              # 高点突破回看天数
    support_atr_mult: float = 2.0        # 支撑位ATR倍数
    support_ma_period: int = 13          # 支撑均线周期

    # 出场规则
    hold_days: int = 5                   # 持有天数（0=不限）
    stop_loss_pct: float = -5.0          # 止损比例%
    stop_profit_pct: float = 15.0        # 止盈比例%
    volume: int = 1000                   # 每笔交易股数


class BullLineBreakoutStrategy(BaseStrategy):
    """牛线突破策略。

    每天计算牛线值，检测突破信号。
    适合日线级别操作，信号稀疏但质量高。
    """

    def __init__(self, ctx: StrategyContext, cfg: BullLineBreakoutConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._entry_price: float | None = None
        self._entry_date: Any = None
        self._bars_since_entry: int = 0

    def on_init(self) -> None:
        self.ctx.logger.info(
            "bull_line_breakout_init",
            symbol=self.cfg.symbol,
            hold_days=self.cfg.hold_days,
        )

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        """日线级别的信号检测。"""
        df = self._get_kline_df(self.cfg.symbol, 300, self.cfg.period)
        if df is None or len(df) < 200:
            return None

        close = df["close"].values
        high = df["high"].values
        low = df["low"].values
        open_p = df["open"].values
        n = len(close)

        if n < self.cfg.high_lookback + 50:
            return None

        # ── 计算牛线 ──
        bull_line = _compute_bull_line(close, high, low, open_p,
                                       self.cfg.bull_line_multiplier,
                                       self.cfg.bull_line_ema_period)

        # ── 计算 MACD ──
        dif, dea = _compute_macd(close)

        # ── 计算 ATR ──
        atr = _compute_atr(high, low, close, 14)

        # ── 计算支撑位相关指标 ──
        hhv20 = _hhv(high, 20)
        aa = hhv20 - 2 * atr                          # AA支撑线

        hhv55 = _hhv(high, self.cfg.high_lookback)
        bb = _cross_above(close, _ref(hhv55, 1))      # BB: 突破55日高点

        ma13 = _sma(close, self.cfg.support_ma_period)
        t_signal = _cross_above(np.minimum(ma13, aa), close)  # T: 回踩支撑

        # ── 计算B1条件 ──
        bbb = _bars_since(bb)                         # 距离上次BB的天数
        r_bars = _bars_since(t_signal)                 # 距离上次T的天数

        # B1: BB今天触发 AND 昨天T比BB更近（即先回踩，后突破）
        b1 = (bbb[-1] == 0) and (r_bars[-2] < bbb[-2] if n >= 2 else False)

        # ── XG条件 ──
        # 价格上穿牛线
        cross_bull = close[-1] > bull_line[-1] and close[-2] <= bull_line[-2] if n >= 2 else False

        # MACD多头
        macd_bullish = dif[-1] > dea[-1] if not self.cfg.require_macd_bullish else True
        if self.cfg.require_macd_bullish:
            macd_bullish = dif[-1] > dea[-1]

        # 今日涨幅
        change_pct = (close[-1] / close[-2] - 1) * 100 if n >= 2 else 0
        change_ok = change_pct >= self.cfg.min_change_pct

        xg = cross_bull and macd_bullish and change_ok

        # ── 最终选股条件 ──
        buy_signal = xg and b1

        # ── 出场逻辑 ──
        if self._entry_price is not None and self._bars_since_entry > 0:
            self._bars_since_entry += 1
            current_price = close[-1]
            pnl_pct = (current_price / self._entry_price - 1) * 100

            # 止损
            if pnl_pct <= self.cfg.stop_loss_pct:
                self._entry_price = None
                self.ctx.logger.info("stop_loss", price=current_price, pnl_pct=pnl_pct)
                return self.sell(self.cfg.symbol, price=current_price,
                                 volume=self.cfg.volume,
                                 reason=f"止损 {pnl_pct:.1f}%")

            # 止盈
            if pnl_pct >= self.cfg.stop_profit_pct:
                self._entry_price = None
                self.ctx.logger.info("stop_profit", price=current_price, pnl_pct=pnl_pct)
                return self.sell(self.cfg.symbol, price=current_price,
                                 volume=self.cfg.volume,
                                 reason=f"止盈 +{pnl_pct:.1f}%")

            # 到期出场
            if self.cfg.hold_days > 0 and self._bars_since_entry >= self.cfg.hold_days:
                self._entry_price = None
                return self.sell(self.cfg.symbol, price=current_price,
                                 volume=self.cfg.volume,
                                 reason=f"持有{self.cfg.hold_days}日到期")

        # ── 买入 ──
        if buy_signal and self._entry_price is None:
            self._entry_price = close[-1]
            self._entry_date = df.index[-1]
            self._bars_since_entry = 1
            self.ctx.logger.info(
                "bull_line_breakout_buy",
                price=close[-1],
                change_pct=change_pct,
                bull_line=bull_line[-1],
            )
            return self.buy(self.cfg.symbol, price=close[-1],
                            volume=self.cfg.volume,
                            reason=f"牛线突破 涨{change_pct:.1f}%")

        return None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        """报价驱动 — 委托给 on_bar（本策略日线级别）。"""
        return None  # 日线策略不需要tick


# ═══════════════════════════════════════════════════════════════════════
# 通达信公式辅助函数（纯向量化实现）
# ═══════════════════════════════════════════════════════════════════════

def _dma(x: np.ndarray, a: np.ndarray) -> np.ndarray:
    """通达信 DMA(X, A) = X * A + (1-A) * prev_DMA。
    递归实现：DMA[0] = X[0]。
    """
    n = len(x)
    result = np.zeros(n)
    result[0] = x[0]
    for i in range(1, n):
        result[i] = a[i] * x[i] + (1 - a[i]) * result[i - 1]
    return result


def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均。"""
    alpha = 2.0 / (period + 1)
    n = len(series)
    result = np.zeros(n)
    result[0] = series[0]
    for i in range(1, n):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均。"""
    result = np.full(len(series), np.nan)
    if len(series) >= period:
        cumsum = np.cumsum(np.insert(series, 0, 0))
        result[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return result


def _ref(series: np.ndarray, n: int) -> np.ndarray:
    """通达信 REF(X, N) — 引用N周期前的值。"""
    result = np.full(len(series), np.nan)
    if n < len(series):
        result[n:] = series[:-n]
    result[:n] = series[0]  # 前N个用首值填充
    return result


def _hhv(series: np.ndarray, period: int) -> np.ndarray:
    """通达信 HHV(X, N) — N周期内最高值。"""
    result = np.zeros(len(series))
    for i in range(len(series)):
        start = max(0, i - period + 1)
        result[i] = np.max(series[start:i + 1])
    return result


def _cross_above(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """通达信 CROSS(A, B) — A上穿B。"""
    result = np.zeros(len(a), dtype=bool)
    for i in range(1, len(a)):
        result[i] = a[i] > b[i] and a[i - 1] <= b[i - 1]
    return result


def _bars_since(condition: np.ndarray) -> np.ndarray:
    """通达信 BARSLAST — 距离上次条件成立的天数。"""
    result = np.zeros(len(condition), dtype=int)
    last_true = -1
    for i in range(len(condition)):
        if condition[i]:
            last_true = i
        result[i] = i - last_true if last_true >= 0 else 9999
    return result


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """计算 ATR（Average True Range）。"""
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return _ema(tr, period)  # 或用SMA，通达信ATR实际用EMA


def _compute_macd(close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9):
    """返回 (DIF, DEA)。"""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    return dif, dea


def _compute_bull_line(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                       open_p: np.ndarray, multiplier: float = 1.118,
                       ema_period: int = 200) -> np.ndarray:
    """
    计算牛线。

    牛线 = EMA(DMA(加权价格, 波动权重), 200) * 1.118

    加权价格 = (2.15*C + L + H) / 4
    波动权重 = abs((3.48*C + H + L) / 4 - EMA(C, 23)) / EMA(C, 23)
    """
    n = len(close)

    # 加权价格
    weighted_price = (2.15 * close + low + high) / 4.0

    # 波动权重
    ema23 = _ema(close, 23)
    ref_price = (3.48 * close + high + low) / 4.0
    volatility_weight = np.abs(ref_price - ema23) / np.maximum(ema23, 1e-8)

    # DMA递归
    dma_result = _dma(weighted_price, volatility_weight)

    # EMA平滑 + 倍数
    bull_line = _ema(dma_result, ema_period) * multiplier

    return bull_line


# ═══════════════════════════════════════════════════════════════════════
# 回测辅助：独立信号生成函数（用于参数优化器）
# ═══════════════════════════════════════════════════════════════════════

def generate_bull_line_signals(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                               open_p: np.ndarray, min_change_pct: float = 9.0,
                               require_macd_bullish: bool = True,
                               high_lookback: int = 55,
                               support_atr_mult: float = 2.0,
                               support_ma_period: int = 13) -> np.ndarray:
    """
    生成牛线突破信号（用于独立回测）。

    返回: numpy array, 1=买入, -1=卖出, 0=持有
    """
    n = len(close)
    if n < 200:
        return np.zeros(n, dtype=int)

    bull_line = _compute_bull_line(close, high, low, open_p)
    dif, dea = _compute_macd(close)
    atr = _compute_atr(high, low, close, 14)
    hhv20 = _hhv(high, 20)
    aa = hhv20 - 2 * atr
    hhv55 = _hhv(high, high_lookback)
    bb = _cross_above(close, _ref(hhv55, 1))
    ma13 = _sma(close, support_ma_period)
    t_signal = _cross_above(np.minimum(ma13, aa), close)
    bbb = _bars_since(bb)
    r_bars = _bars_since(t_signal)

    signals = np.zeros(n, dtype=int)

    for i in range(200, n):
        # B1
        b1 = (bbb[i] == 0) and (i >= 2 and r_bars[i - 1] < bbb[i - 1])

        # XG
        cross_bull = close[i] > bull_line[i] and close[i - 1] <= bull_line[i - 1]
        macd_ok = dif[i] > dea[i] if require_macd_bullish else True
        change_pct = (close[i] / close[i - 1] - 1) * 100
        change_ok = change_pct >= min_change_pct
        xg = cross_bull and macd_ok and change_ok

        if xg and b1:
            signals[i] = 1

    return signals
