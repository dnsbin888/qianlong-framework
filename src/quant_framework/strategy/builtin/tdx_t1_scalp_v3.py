"""T+1 短线隔日冲策略 V3 — 底仓+日内确认加仓。

V2 → V3 核心升级:
  9. 底仓+加仓模式 (金字塔加仓)
     - T日尾盘: 信号触发 → 买入底仓 (60%仓位)
     - T+1日 9:45-10:00: 5分钟K线连续3根放量阳线突破昨收 → 加仓 (40%仓位)
     - 如果日内不满足加仓条件 → 只持有底仓

V1/V2 保留的升级:
  1. 市场环境过滤
  2. 自适应ATR止损止盈
  3. 信号质量评分
  4. 量能确认
  5. 智能持仓 (跟踪止盈)
  6. 缺口过滤
  7. 波动率自适应仓位
  8. 冷却期

5分钟K线加仓逻辑 (9:45-10:00):
  条件 (全部满足才加仓):
    a. 连续3根5分钟K线都是阳线 (close > open)
    b. 每根K线成交量逐根放大 (vol[i] > vol[i-1])
    c. 第3根K线收盘价 > T日收盘价 (突破确认)
    d. 加仓价格不超过底仓成本的+3% (不追高)

用法:
  from quant_framework.strategy.builtin.tdx_t1_scalp_v3 import T1ScalpV3Strategy
  strategy = T1ScalpV3Strategy(ctx, signal_name="tdx2_final")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.data.models import Bar, Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.factors.tdx_signals import (
    factor_qlj, factor_ztxf, factor_resonance,
    factor_dmi_trend, factor_money_flow,
)
from quant_framework.factors.tdx_signals2 import (
    factor_xg_signal, factor_b1_structure, factor_final_pick,
)
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal

logger = logging.getLogger("quant_framework.strategy.tdx_t1_v3")


# ======================================================================
# Configuration
# ======================================================================

@dataclass
class T1ScalpV3Config:
    """T+1 短线策略 V3 配置 — 底仓+日内加仓。"""

    # ── 信号 ──
    signal_name: str = "tdx2_final"

    # ── 资金管理 ──
    max_positions: int = 3
    base_position_pct: float = 0.30          # 总仓位基准
    base_ratio: float = 0.60                  # 底仓占比 (总仓位的60%)
    addon_ratio: float = 0.40                 # 加仓占比 (总仓位的40%)
    max_position_pct: float = 0.40            # 总仓位上限
    min_position_pct: float = 0.10            # 总仓位下限
    min_cash_reserve: float = 50000.0

    # ── 日内加仓条件 ──
    addon_bar_count: int = 3                  # 连续阳线数量
    addon_volume_expand: bool = True          # 要求量能逐根放大
    addon_above_yclose: bool = True           # 要求突破昨收
    addon_max_chase_pct: float = 0.03         # 加仓不超底仓成本+3%
    addon_time_start: str = "09:45"           # 加仓窗口开始
    addon_time_end: str = "10:00"             # 加仓窗口结束

    # ── 风控 ──
    atr_stop_mult: float = 2.0
    atr_profit_mult: float = 3.0
    atr_period: int = 14
    max_stop_loss_pct: float = -0.05
    min_take_profit_pct: float = 0.03

    # ── 智能持仓 ──
    enable_smart_hold: bool = True
    trailing_stop_atr: float = 1.5
    max_hold_days: int = 5
    momentum_threshold: float = 0.02

    # ── 市场环境过滤 ──
    enable_regime_filter: bool = True
    index_symbol: str = "999999"
    bear_market_position_pct: float = 0.50
    high_vol_position_pct: float = 0.70
    regime_ma_short: int = 20
    regime_ma_long: int = 60

    # ── 量能确认 ──
    enable_volume_filter: bool = True
    min_vol_ratio: float = 1.2
    require_price_volume_match: bool = True

    # ── 信号质量评分 ──
    enable_quality_scoring: bool = True
    min_quality_score: float = 0.55

    # ── 缺口过滤 ──
    enable_gap_filter: bool = True
    max_gap_down_pct: float = -0.02

    # ── 冷却期 ──
    cooldown_days: int = 5

    # ── 成本 ──
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.001
    min_commission: float = 5.0
    slippage: float = 0.001


# ======================================================================
# Market Regime
# ======================================================================

class MarketRegime:
    BULL = "bull"
    BULL_VOLATILE = "bull_volatile"
    SIDEWAYS = "sideways"
    BEAR = "bear"
    BEAR_RALLY = "bear_rally"
    HIGH_VOLATILITY = "high_vol"

    @classmethod
    def classify(cls, price, ma_short, ma_long, vol_ratio, vol_threshold=1.5):
        above_short = price > ma_short
        above_long = price > ma_long
        short_above_long = ma_short > ma_long
        high_vol = vol_ratio > vol_threshold
        if high_vol:
            return cls.HIGH_VOLATILITY
        if above_short and above_long and short_above_long:
            return cls.BULL
        elif above_short and above_long and not short_above_long:
            return cls.BULL_VOLATILE
        elif not above_short and not above_long:
            return cls.BEAR
        elif above_short and not above_long:
            return cls.BEAR_RALLY
        else:
            return cls.SIDEWAYS


# ======================================================================
# Holding Record V3
# ======================================================================

class _HoldingV3:
    """V3 持仓记录 — 支持底仓+加仓两阶段。"""

    def __init__(
        self,
        symbol: str,
        # 底仓
        base_price: float,
        base_volume: int,
        base_cost: float,
        base_date: Any = None,
        # 加仓 (初始为空)
        addon_price: float = 0.0,
        addon_volume: int = 0,
        addon_cost: float = 0.0,
        addon_date: Any = None,
        # 元数据
        signal_intensity: int = 0,
        quality_score: float = 0.0,
        atr_value: float = 0.0,
    ):
        self.symbol = symbol
        # 底仓
        self.base_price = base_price
        self.base_volume = base_volume
        self.base_cost = base_cost
        self.base_date = base_date
        # 加仓
        self.addon_price = addon_price
        self.addon_volume = addon_volume
        self.addon_cost = addon_cost
        self.addon_date = addon_date
        # 元数据
        self.signal_intensity = signal_intensity
        self.quality_score = quality_score
        self.atr_value = atr_value
        self.max_price_since_buy = base_price  # 含加仓的最高价
        self.hold_days = 0
        self.addon_executed = False             # 是否已执行加仓
        self.addon_checked = False              # 是否已检查过加仓
        self.yesterday_close = base_price       # T日收盘价 (用于加仓判断)

    @property
    def total_volume(self) -> int:
        return self.base_volume + self.addon_volume

    @property
    def total_cost(self) -> float:
        return self.base_cost + self.addon_cost

    @property
    def avg_price(self) -> float:
        total_vol = self.total_volume
        if total_vol == 0:
            return self.base_price
        # 使用 total_cost（含买入手续费）计算真实持仓成本
        return self.total_cost / total_vol


# ======================================================================
# Signal Computers
# ======================================================================

_SIGNAL_COMPUTERS = {
    "tdx_qlj": factor_qlj,
    "tdx_ztxf": factor_ztxf,
    "tdx_resonance": factor_resonance,
    "tdx2_xg": factor_xg_signal,
    "tdx2_bb": None,
    "tdx2_t": None,
    "tdx2_b1": factor_b1_structure,
    "tdx2_final": factor_final_pick,
}


def get_signal_label(name: str) -> str:
    from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
    from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS
    for registry in [TDX_SIGNAL_FACTORS, TDX2_SIGNAL_FACTORS]:
        info = registry.get(name)
        if info:
            return info.get("label", name)
    return name


# ======================================================================
# 5-Min Bar Ring Buffer (for intraday add-on detection)
# ======================================================================

class _IntradayBarTracker:
    """跟踪单只股票当日5分钟K线，用于检测加仓信号。

    只保留最近 N 根5分钟K线 (N = addon_bar_count + 2)。
    """

    def __init__(self, bar_count: int = 3):
        self.bar_count = bar_count
        self.bars: list[dict] = []  # [{open, high, low, close, volume, time}]

    def add_bar(self, bar: dict) -> None:
        self.bars.append(bar)
        # 只保留最近的
        if len(self.bars) > self.bar_count + 5:
            self.bars = self.bars[-(self.bar_count + 5):]

    def check_addon_signal(self, yesterday_close: float, max_chase_pct: float) -> bool:
        """检查是否满足加仓条件。

        条件:
          a. 最近 N 根K线全是阳线 (close > open)
          b. 成交量逐根放大 (vol[i] > vol[i-1])
          c. 最后一根K线 close > T日收盘价 (突破确认)
          d. 加仓价格不超过底仓成本的 +max_chase_pct%
        """
        if len(self.bars) < self.bar_count:
            return False

        recent = self.bars[-self.bar_count:]

        # (a) 全部阳线
        if not all(b["close"] > b["open"] for b in recent):
            return False

        # (b) 量能逐根放大
        for i in range(1, len(recent)):
            if recent[i]["volume"] <= recent[i - 1]["volume"]:
                return False

        # (c) 突破昨收
        last_close = recent[-1]["close"]
        if last_close <= yesterday_close:
            return False

        # (d) 加仓价不超昨收+max_chase_pct
        if (last_close - yesterday_close) / yesterday_close > max_chase_pct:
            return False

        return True


# ======================================================================
# T1ScalpV3Strategy
# ======================================================================

class T1ScalpV3Strategy(BaseStrategy):
    """T+1 短线隔日冲策略 V3 — 底仓+日内确认加仓。

    V2 → V3 新增:
      - T日尾盘买入底仓 (60%仓位)
      - T+1日 9:45-10:00 用5分钟K线检测连续放量阳线突破
      - 满足条件→加仓40%，不满足→只持有底仓
      - 加仓后统一管理 (跟踪止盈/止损)
    """

    def __init__(
        self,
        ctx: StrategyContext,
        config: T1ScalpV3Config | None = None,
    ) -> None:
        super().__init__(ctx)
        self.cfg = config or T1ScalpV3Config()

        # ── 状态 ──
        self._history: dict[str, pd.DataFrame] = {}       # 日线
        self._5min_trackers: dict[str, _IntradayBarTracker] = {}  # 5分钟
        self._holdings: dict[str, _HoldingV3] = {}
        self._today_signals: dict[str, dict] = {}
        self._current_date: datetime | None = None
        self._min_history: int = 300

        # ── V2 状态 ──
        self._last_trade_date: dict[str, datetime] = {}
        self._index_history: pd.DataFrame | None = None
        self._current_regime: str = MarketRegime.SIDEWAYS
        self._score_details: dict[str, dict] = {}

        # ── 信号 ──
        self._signal_fn = _SIGNAL_COMPUTERS.get(self.cfg.signal_name)
        if self._signal_fn is None:
            raise ValueError(f"Unknown signal: {self.cfg.signal_name}")

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def on_init(self) -> None:
        logger.info(
            "t1_v3_init",
            signal=self.cfg.signal_name,
            signal_label=get_signal_label(self.cfg.signal_name),
            base_ratio=self.cfg.base_ratio,
            addon_ratio=self.cfg.addon_ratio,
            addon_bars=self.cfg.addon_bar_count,
            addon_window=f"{self.cfg.addon_time_start}-{self.cfg.addon_time_end}",
            smart_hold=self.cfg.enable_smart_hold,
        )

    # ==================================================================
    # Daily Bar Callback
    # ==================================================================

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        """处理日线 (回测模式)。"""
        symbol = bar.symbol
        signals: list[Signal] = []

        # 1. 更新日线历史
        self._update_daily_history(symbol, bar)
        self._current_date = bar.dt

        # 2. 更新市场环境
        if symbol == self.cfg.index_symbol or symbol.endswith(self.cfg.index_symbol):
            self._update_market_regime(bar)

        # 3. 处理持仓卖出
        sell_signals = self._process_sells(bar)
        if sell_signals:
            signals.extend(sell_signals)

        # 4. T+1 加仓检查 (如果是持仓股票的T+1日)
        addon_signals = self._check_intraday_addon_daily(symbol, bar)
        if addon_signals:
            signals.extend(addon_signals)

        # 5. 计算当日信号 (T日选股)
        if len(self._history.get(symbol, pd.DataFrame())) >= self._min_history:
            sig_result = self._compute_signal_v3(symbol)
            if sig_result and sig_result.get("signal", 0) > 0:
                self._today_signals[symbol] = sig_result

        return signals if signals else None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        """实时行情驱动 (实盘模式) — 处理5分钟K线和卖出。"""
        signals: list[Signal] = []

        # 更新持仓最高价
        for symbol, holding in list(self._holdings.items()):
            if quote.price > holding.max_price_since_buy:
                holding.max_price_since_buy = quote.price

            # 检查智能卖出
            sell_sig = self._check_smart_exit(symbol, holding, quote.price, quote)
            if sell_sig:
                signals.extend(sell_sig)

        return signals if signals else None

    def on_5min_bar(self, bar: Bar) -> list[Signal] | None:
        """5分钟K线回调 (实盘模式) — 检测加仓信号。"""
        symbol = bar.symbol
        signals: list[Signal] = []

        # 只处理有底仓且在加仓窗口内的股票
        holding = self._holdings.get(symbol)
        if holding is None:
            return None

        if holding.addon_executed or holding.addon_checked:
            return None  # 已加仓或已过窗口

        # 检查时间窗口
        bar_time = bar.dt.time() if hasattr(bar.dt, 'time') else bar.dt
        window_start = time.fromisoformat(self.cfg.addon_time_start)
        window_end = time.fromisoformat(self.cfg.addon_time_end)

        if not self._is_time_in_window(bar_time, window_start, window_end):
            if bar_time > window_end:
                holding.addon_checked = True  # 窗口过了，不再检查
            return None

        # 更新5分钟K线跟踪
        tracker = self._5min_trackers.get(symbol)
        if tracker is None:
            tracker = _IntradayBarTracker(self.cfg.addon_bar_count)
            self._5min_trackers[symbol] = tracker

        tracker.add_bar({
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "time": bar_time,
        })

        # 检查加仓条件
        if tracker.check_addon_signal(
            holding.yesterday_close,
            self.cfg.addon_max_chase_pct,
        ):
            addon_sig = self._execute_addon(symbol, bar.close, bar)
            if addon_sig:
                signals.extend(addon_sig)
                holding.addon_executed = True

        return signals if signals else None

    # ==================================================================
    # 数据更新
    # ==================================================================

    def _update_daily_history(self, symbol: str, bar: Bar) -> None:
        if symbol not in self._history:
            self._history[symbol] = pd.DataFrame()
        df = self._history[symbol]
        new_row = pd.DataFrame([{
            "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
        }], index=[bar.dt])
        self._history[symbol] = pd.concat([df, new_row])
        if len(self._history[symbol]) > 500:
            self._history[symbol] = self._history[symbol].iloc[-500:]

    def _update_market_regime(self, bar: Bar) -> None:
        if not self.cfg.enable_regime_filter:
            return
        df = self._history.get(self.cfg.index_symbol)
        if df is None or len(df) < self.cfg.regime_ma_long + 10:
            return
        c = df["close"]
        ma_short = c.iloc[-self.cfg.regime_ma_short:].mean()
        ma_long = c.iloc[-self.cfg.regime_ma_long:].mean()
        price = bar.close
        returns = c.pct_change().dropna()
        cur_vol = returns.iloc[-20:].std()
        hist_vol = returns.iloc[-120:].std() if len(returns) >= 120 else cur_vol
        vol_ratio = cur_vol / hist_vol if hist_vol > 0 else 1.0
        self._current_regime = MarketRegime.classify(price, ma_short, ma_long, vol_ratio)

    # ==================================================================
    # 信号计算 (V2 多维度评分)
    # ==================================================================

    def _compute_signal_v3(self, symbol: str) -> dict | None:
        df = self._history.get(symbol)
        if df is None or len(df) < self._min_history:
            return None

        try:
            sig_val = self._signal_fn(df)
            if isinstance(sig_val, pd.Series):
                raw = sig_val.iloc[-1]
                if pd.isna(raw) or raw <= 0:
                    return None
                raw_signal = int(raw)
            else:
                return None
        except Exception:
            return None

        scores = self._score_quality(df, raw_signal)
        quality = scores["total"]

        if self.cfg.enable_quality_scoring and quality < self.cfg.min_quality_score:
            return None

        if self.cfg.enable_volume_filter and not self._check_volume(df):
            return None

        if symbol in self._last_trade_date and self._current_date:
            days_since = (self._current_date - self._last_trade_date[symbol]).days
            if days_since < self.cfg.cooldown_days:
                return None

        return {
            "signal": raw_signal,
            "quality_score": quality,
            "close": df["close"].iloc[-1],
            "atr": scores.get("atr", 0.02),
            "scores": scores,
        }

    # ==================================================================
    # 质量评分
    # ==================================================================

    def _score_quality(self, df: pd.DataFrame, raw_signal: int) -> dict:
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
        scores = {}

        scores["intensity"] = 1.0 if raw_signal >= 2 else (0.6 if raw_signal == 1 else 0.3)

        ma5 = c.iloc[-5:].mean()
        ma10 = c.iloc[-10:].mean()
        ma20 = c.iloc[-20:].mean()
        cur = c.iloc[-1]
        if cur > ma5 > ma10 > ma20:
            s_trend = 0.9
        elif cur > ma5 and cur > ma10:
            s_trend = 0.7
        elif cur > ma20:
            s_trend = 0.5
        elif cur > ma5:
            s_trend = 0.3
        else:
            s_trend = 0.1
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0:
            s_trend = min(1.0, s_trend + 0.1)
        scores["trend"] = s_trend

        v_cur = v.iloc[-1]
        v_ma5 = v.iloc[-6:-1].mean() if len(v) >= 6 else v.iloc[:-1].mean()
        vr = v_cur / v_ma5 if v_ma5 > 0 else 1.0
        if 1.5 <= vr <= 3.0:
            s_vol = 1.0
        elif 1.2 <= vr < 1.5:
            s_vol = 0.7
        elif vr > 3.0:
            s_vol = 0.6
        elif vr >= 0.8:
            s_vol = 0.4
        else:
            s_vol = 0.2
        if c.iloc[-1] > c.iloc[-2] and vr > 1.0:
            s_vol = min(1.0, s_vol + 0.1)
        scores["volume"] = s_vol

        h20 = h.iloc[-20:].max()
        l20 = l.iloc[-20:].min()
        pos = (cur - l20) / (h20 - l20) if h20 > l20 else 0.5
        if 0.2 <= pos <= 0.5:
            s_pos = 1.0
        elif 0.5 < pos <= 0.7:
            s_pos = 0.8
        elif 0.7 < pos <= 0.85:
            s_pos = 0.5
        elif pos > 0.85:
            s_pos = 0.2
        else:
            s_pos = 0.6
        if cur > ma5 * 1.05:
            s_pos *= 0.7
        scores["position"] = s_pos

        tr = pd.concat([
            h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()
        ], axis=1).max(axis=1)
        atr = tr.iloc[-self.cfg.atr_period:].mean()
        scores["atr"] = atr / cur if cur > 0 else 0.02

        scores["total"] = (
            scores["intensity"] * 0.25 + scores["trend"] * 0.30 +
            scores["volume"] * 0.25 + scores["position"] * 0.20
        )
        return scores

    def _check_volume(self, df: pd.DataFrame) -> bool:
        v, c = df["volume"], df["close"]
        if len(v) < 6:
            return True
        vr = v.iloc[-1] / v.iloc[-6:-1].mean() if v.iloc[-6:-1].mean() > 0 else 0
        if vr < self.cfg.min_vol_ratio:
            return False
        if self.cfg.require_price_volume_match and len(c) >= 2:
            if c.iloc[-1] > c.iloc[-2] and vr < 1.0:
                return False
        return True

    # ==================================================================
    # 日内加仓检测 (V3 核心新增)
    # ==================================================================

    def _check_intraday_addon_daily(
        self, symbol: str, bar: Bar
    ) -> list[Signal] | None:
        """回测模式: 用日线OHLC近似模拟5分钟K线加仓检测。

        由于回测没有5分钟数据，我们根据日线的开盘价和高低价来近似判断:
          - 如果开盘价 > T日收盘价 (高开)
          - 且当日最低价没有跌破T日收盘价 (支撑有效)
          - 且当日最高价相对开盘继续上攻 (有冲高)
          - 且当日成交量 > 近5日均量 (量能确认，对齐实盘量能逐根放大要求)
          → 近似认为满足了"连续阳线放量突破"的条件

        实盘模式下此逻辑由 on_5min_bar() 精确处理。
        """
        holding = self._holdings.get(symbol)
        if holding is None:
            return None

        if holding.addon_executed or holding.addon_checked:
            return None

        # 必须是底仓买入的下一个交易日
        if holding.base_date is None:
            return None
        if bar.dt is None:
            return None

        # T+1 加仓窗口
        if holding.base_date == bar.dt.date():
            return None  # 同一天不操作

        # 只在T+1执行加仓 (加仓窗口过了就标记已检查)
        if holding.base_date != bar.dt.date() - pd.Timedelta(days=1):
            holding.addon_checked = True
            return None

        # ── 回测近似判断 ──
        yclose = holding.yesterday_close
        if yclose <= 0:
            return None

        # 条件1: 高开 (open > 昨收)
        gap_up = bar.open > yclose

        # 条件2: 回调不破昨收 (low > 昨收 OR low 在昨收附近)
        support_holds = bar.low > yclose * 0.995  # 允许0.5%的下影线

        # 条件3: 盘中上攻 (high > open, 上影线表明多头有力量)
        upward_momentum = bar.high > bar.open * 1.005  # 至少0.5%的上攻

        # 条件4: 不追高 (加仓价不超过底仓+3%)
        not_chasing = (bar.open - yclose) / yclose < self.cfg.addon_max_chase_pct

        # 条件5: 量能确认 (成交量 > 近5日均量，对齐实盘5分钟量能逐根放大)
        vol_confirmed = True
        df = self._history.get(symbol)
        if df is not None and len(df) >= 6:
            vol_ma5 = df["volume"].iloc[-6:-1].mean()
            vol_confirmed = bar.volume > vol_ma5 if vol_ma5 > 0 else True

        # 综合判断
        if gap_up and support_holds and upward_momentum and not_chasing and vol_confirmed:
            return self._execute_addon(symbol, bar.open, bar)

        # 窗口过了，标记
        holding.addon_checked = True
        return None

    def _execute_addon(
        self, symbol: str, price: float, bar_or_quote: Any
    ) -> list[Signal] | None:
        """执行加仓操作。"""
        holding = self._holdings.get(symbol)
        if holding is None:
            return None

        # 计算加仓量
        addon_price = price * (1 + self.cfg.slippage)

        # 加仓仓位 = 底仓仓位 * (addon_ratio / base_ratio)
        addon_pct = self.cfg.base_position_pct * (self.cfg.addon_ratio / self.cfg.base_ratio)
        addon_pct = self._adjust_for_regime(addon_pct)

        available = self.ctx.portfolio.cash if hasattr(self.ctx, 'portfolio') else 1e6
        addon_volume = int(available * addon_pct / addon_price / 100) * 100

        if addon_volume < 100:
            return None

        # 更新持仓
        holding.addon_price = addon_price
        holding.addon_volume = addon_volume
        holding.addon_cost = addon_price * addon_volume * (1 + self.cfg.commission_rate)
        holding.addon_date = bar_or_quote.dt if hasattr(bar_or_quote, 'dt') else datetime.now()
        holding.addon_executed = True

        # 更新最高价
        if addon_price > holding.max_price_since_buy:
            holding.max_price_since_buy = addon_price

        logger.info(
            "t1_v3_addon",
            symbol=symbol,
            base_price=holding.base_price,
            addon_price=addon_price,
            addon_volume=addon_volume,
            total_volume=holding.total_volume,
            avg_price=holding.avg_price,
        )

        return self.buy(
            symbol=symbol,
            price=addon_price,
            volume=addon_volume,
            reason=(
                f"V3日内加仓 | 昨收={holding.yesterday_close:.2f} "
                f"加仓价={addon_price:.2f} | "
                f"总仓={holding.total_volume}股 均价={holding.avg_price:.2f}"
            ),
            metadata={
                "addon": True,
                "base_price": holding.base_price,
                "addon_price": addon_price,
                "avg_price": holding.avg_price,
            },
        )

    def _is_time_in_window(self, bar_time, start, end) -> bool:
        """判断当前时间是否在加仓窗口内。"""
        if hasattr(bar_time, 'time'):
            bar_time = bar_time.time()
        if isinstance(bar_time, str):
            bar_time = time.fromisoformat(bar_time)
        if isinstance(start, str):
            start = time.fromisoformat(start)
        if isinstance(end, str):
            end = time.fromisoformat(end)
        return start <= bar_time <= end

    # ==================================================================
    # 卖出处理
    # ==================================================================

    def _process_sells(self, bar: Bar) -> list[Signal]:
        signals: list[Signal] = []
        symbol = bar.symbol
        holding = self._holdings.get(symbol)
        if holding is None:
            return signals

        if holding.base_date and holding.base_date == bar.dt.date():
            return signals

        holding.hold_days += 1
        if bar.high > holding.max_price_since_buy:
            holding.max_price_since_buy = bar.high

        should_sell, reason = self._should_sell(holding, bar)
        if should_sell:
            signals.extend(self._execute_sell_v3(symbol, bar.close, bar, holding, reason))

        return signals

    def _should_sell(self, holding: _HoldingV3, bar: Bar) -> tuple[bool, str]:
        """智能卖出判断 (继承V2)。"""
        avg_price = holding.avg_price
        current = bar.close
        atr = holding.atr_value * avg_price
        if atr <= 0:
            atr = avg_price * 0.02

        return_pct = (current - avg_price) / avg_price if avg_price > 0 else 0

        # 跟踪止盈
        if self.cfg.enable_smart_hold and holding.max_price_since_buy > avg_price:
            pullback = (current - holding.max_price_since_buy) / holding.max_price_since_buy
            ts = -self.cfg.trailing_stop_atr * holding.atr_value
            if pullback < ts and return_pct > 0:
                return True, f"跟踪止盈 | 从最高回撤={pullback:.2%} | 收益={return_pct:.2%}"

        # 硬止损 (基于均价)
        if return_pct <= self.cfg.max_stop_loss_pct:
            return True, f"硬止损 | 收益={return_pct:.2%} | 均价={avg_price:.2f}"

        # 自适应止盈
        adaptive_tp = self.cfg.atr_profit_mult * holding.atr_value
        adaptive_tp = max(adaptive_tp, self.cfg.min_take_profit_pct)
        if return_pct >= adaptive_tp:
            return True, f"自适应止盈 | 收益={return_pct:.2%}"

        # 超时
        if holding.hold_days >= self.cfg.max_hold_days:
            return True, f"持仓超时({holding.hold_days}天)"

        # T+1弱势退出
        if self.cfg.enable_smart_hold and holding.hold_days >= 1:
            day_return = (bar.close - bar.open) / bar.open if bar.open > 0 else 0
            if day_return < self.cfg.momentum_threshold:
                return True, f"T+1弱势 | 当日={day_return:.2%} | 总={return_pct:.2%}"

        return False, ""

    def _execute_sell_v3(
        self, symbol: str, price: float, bar: Bar,
        holding: _HoldingV3, reason: str,
    ) -> list[Signal]:
        """执行卖出 (全部平仓)。"""
        avg_price = holding.avg_price
        return_pct = (price - avg_price) / avg_price if avg_price > 0 else 0

        exit_type = "normal"
        if return_pct <= self.cfg.max_stop_loss_pct:
            exit_type = "stop_loss"
        elif "跟踪止盈" in reason:
            exit_type = "trailing_stop"
        elif "自适应止盈" in reason:
            exit_type = "take_profit"

        sell_price = price * (1 - self.cfg.slippage)

        if self._current_date:
            self._last_trade_date[symbol] = self._current_date

        del self._holdings[symbol]
        self._5min_trackers.pop(symbol, None)

        has_addon = holding.addon_executed

        logger.info(
            "t1_v3_sell",
            symbol=symbol,
            avg_price=avg_price,
            sell_price=sell_price,
            return_pct=f"{return_pct:.2%}",
            hold_days=holding.hold_days,
            exit_type=exit_type,
            had_addon=has_addon,
            reason=reason,
        )

        return self.sell(
            symbol=symbol,
            price=sell_price,
            position_pct=1.0,
            reason=f"{reason} | {holding.hold_days}天 | {'有加仓' if has_addon else '无加仓'} | {exit_type}",
            metadata={
                "avg_price": avg_price,
                "base_price": holding.base_price,
                "addon_price": holding.addon_price,
                "has_addon": has_addon,
                "sell_price": sell_price,
                "return_pct": return_pct,
                "hold_days": holding.hold_days,
                "exit_type": exit_type,
            },
        )

    def _check_smart_exit(
        self, symbol: str, holding: _HoldingV3, price: float, data: Any
    ) -> list[Signal] | None:
        """实盘模式智能退出。"""
        avg_price = holding.avg_price
        return_pct = (price - avg_price) / avg_price if avg_price > 0 else 0

        if holding.max_price_since_buy > avg_price:
            pullback = (price - holding.max_price_since_buy) / holding.max_price_since_buy
            ts = -self.cfg.trailing_stop_atr * holding.atr_value
            if pullback < ts and return_pct > 0:
                return self._execute_sell_v3(symbol, price, data, holding, f"实盘跟踪止盈")

        return None

    # ==================================================================
    # 收盘批量买入
    # ==================================================================

    def on_day_end(self) -> list[Signal]:
        """每日收盘后 — 批量选股 + 买入底仓 (V3: 只买底仓,加仓在T+1日内执行)。"""
        signals: list[Signal] = []

        if not self._today_signals:
            return signals

        current_positions = len(self._holdings)
        available_slots = self.cfg.max_positions - current_positions
        if available_slots <= 0:
            self._today_signals.clear()
            return signals

        ranked = sorted(
            self._today_signals.items(),
            key=lambda x: x[1].get("quality_score", 0),
            reverse=True,
        )

        for symbol, sig_info in ranked[:available_slots]:
            df = self._history.get(symbol)
            if df is None or len(df) < 2:
                continue

            buy_price = df["close"].iloc[-1]
            quality_score = sig_info.get("quality_score", 0.5)
            atr_pct = sig_info.get("atr", 0.02)

            # 涨停封死跳过
            if self._is_limit_up_locked(df, symbol):
                continue

            # ── V3: 底仓仓位 = 总仓位 * base_ratio ──
            total_position_pct = self._calc_position_size(atr_pct)
            total_position_pct = self._adjust_for_regime(total_position_pct)
            base_pct = total_position_pct * self.cfg.base_ratio

            buy_with_slippage = buy_price * (1 + self.cfg.slippage)

            # 计算底仓量
            available = self.ctx.portfolio.cash if hasattr(self.ctx, 'portfolio') else self.cfg.base_position_pct * 1e6
            if hasattr(self.ctx, 'portfolio'):
                base_cash = available * base_pct
            else:
                base_cash = 1e6 * base_pct

            base_volume = int(base_cash / buy_with_slippage / 100) * 100
            if base_volume < 100:
                continue

            base_cost = buy_with_slippage * base_volume * (1 + self.cfg.commission_rate)

            holding = _HoldingV3(
                symbol=symbol,
                base_price=buy_with_slippage,
                base_volume=base_volume,
                base_cost=base_cost,
                base_date=self._current_date.date() if self._current_date else None,
                signal_intensity=sig_info.get("signal", 1),
                quality_score=quality_score,
                atr_value=atr_pct,
            )
            holding.yesterday_close = buy_price  # T日收盘价，用于加仓判断

            self._holdings[symbol] = holding

            if self._current_date:
                self._last_trade_date[symbol] = self._current_date

            # 初始化5分钟K线跟踪器
            self._5min_trackers[symbol] = _IntradayBarTracker(self.cfg.addon_bar_count)

            logger.info(
                "t1_v3_buy_base",
                symbol=symbol,
                price=buy_with_slippage,
                volume=base_volume,
                pct=f"{base_pct:.1%}",
                quality=f"{quality_score:.0%}",
                regime=self._current_regime,
                note=f"加仓将在T+1 {self.cfg.addon_time_start}-{self.cfg.addon_time_end}检测",
            )

            signals.extend(self.buy(
                symbol=symbol,
                price=buy_with_slippage,
                volume=base_volume,
                reason=(
                    f"V3底仓 | {get_signal_label(self.cfg.signal_name)} | "
                    f"评分={quality_score:.0%} | "
                    f"仓位={base_pct:.0%} | "
                    f"环境={self._current_regime}"
                ),
                metadata={
                    "signal": self.cfg.signal_name,
                    "leg": "base",
                    "quality_score": quality_score,
                    "position_pct": base_pct,
                    "regime": self._current_regime,
                    "buy_date": str(self._current_date.date()) if self._current_date else None,
                },
            ))

        self._today_signals.clear()
        return signals

    # ==================================================================
    # 仓位计算
    # ==================================================================

    def _calc_position_size(self, atr_pct: float) -> float:
        if atr_pct <= 0:
            return self.cfg.base_position_pct
        vol_adj = max(0.5, min(1.5, 0.02 / atr_pct))
        pct = self.cfg.base_position_pct * vol_adj
        return max(self.cfg.min_position_pct, min(self.cfg.max_position_pct, pct))

    def _adjust_for_regime(self, pct: float) -> float:
        if not self.cfg.enable_regime_filter:
            return pct
        multipliers = {
            MarketRegime.BULL: 1.0,
            MarketRegime.BULL_VOLATILE: self.cfg.high_vol_position_pct,
            MarketRegime.SIDEWAYS: 0.85,
            MarketRegime.BEAR: self.cfg.bear_market_position_pct,
            MarketRegime.BEAR_RALLY: 0.60,
            MarketRegime.HIGH_VOLATILITY: self.cfg.high_vol_position_pct,
        }
        mult = multipliers.get(self._current_regime, 0.85)
        return max(self.cfg.min_position_pct, pct * mult)

    @staticmethod
    def _get_limit_pct(symbol: str) -> float:
        """根据股票代码返回涨跌停幅度 (主板10%/创业板科创板20%/北交所30%)"""
        code = symbol.replace(".day", "")
        nums = "".join(c for c in code if c.isdigit())
        if nums.startswith("688") or nums.startswith("3"):
            return 0.20  # 科创板 / 创业板
        elif nums.startswith("8") or nums.startswith("4"):
            return 0.30  # 北交所
        else:
            return 0.10  # 主板 (6xxxxx沪市, 0xxxxx深市)

    def _is_limit_up_locked(self, df: pd.DataFrame, symbol: str = "") -> bool:
        if len(df) < 2:
            return False
        limit_pct = self._get_limit_pct(symbol) if symbol else 0.10
        limit_up = df["close"].iloc[-2] * (1 + limit_pct)
        return abs(df["close"].iloc[-1] - limit_up) < 0.01

    @staticmethod
    def _is_limit_down(symbol: str, prev_close: float, price: float) -> bool:
        """判断是否跌停"""
        code = symbol.replace(".day", "")
        nums = "".join(c for c in code if c.isdigit())
        if nums.startswith("688") or nums.startswith("3"):
            limit_pct = 0.20
        elif nums.startswith("8") or nums.startswith("4"):
            limit_pct = 0.30
        else:
            limit_pct = 0.10
        return price <= prev_close * (1 - limit_pct)

    # ==================================================================
    # 状态查询
    # ==================================================================

    @property
    def active_holdings(self) -> dict[str, dict]:
        return {
            sym: {
                "avg_price": h.avg_price,
                "base_price": h.base_price,
                "addon_price": h.addon_price if h.addon_executed else None,
                "has_addon": h.addon_executed,
                "total_volume": h.total_volume,
                "hold_days": h.hold_days,
                "quality_score": h.quality_score,
            }
            for sym, h in self._holdings.items()
        }

    @property
    def market_regime(self) -> str:
        return self._current_regime
