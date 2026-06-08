"""T+1 短线隔日冲策略 V2 — 多维度升级版。

相比 V1 原版的 8 大升级:
  1. 市场环境过滤 — 大盘牛熊/波动率判断，熊市减仓或暂停
  2. 自适应止损止盈 — 基于 ATR 动态计算，替代固定百分比
  3. 信号质量评分 — 多维度打分(强度+趋势+量能+位置)，择优入场
  4. 量能确认 — 量比+量价配合，过滤无量假突破
  5. 智能持仓 — 强势股持有 T+2/T+3，跟踪止盈替代固定隔日卖
  6. 缺口过滤 — 检测潜在向下跳空风险，提前避开
  7. 波动率自适应仓位 — 高波小仓，低波大仓
  8. 连续信号冷却期 — 同股票短期不重复交易，避免追高

信号来源 (与原版兼容):
  - tdx2_final:  牛线突破 + B1底部反转
  - tdx_resonance: 双信号共振 擒龙决+涨停先锋
  - tdx2_xg:     牛线突破XG
  - tdx_qlj:     擒龙决
  - tdx_ztxf:    涨停先锋

用法:
  from quant_framework.strategy.builtin.tdx_t1_scalp_v2 import T1ScalpV2Strategy
  strategy = T1ScalpV2Strategy(ctx, signal_name="tdx2_final")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.data.models import Bar, Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.factors.tdx_signals import (
    factor_qlj,
    factor_ztxf,
    factor_resonance,
    factor_dmi_trend,
    factor_money_flow,
)
from quant_framework.factors.tdx_signals2 import (
    factor_xg_signal,
    factor_b1_structure,
    factor_final_pick,
)
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal

logger = logging.getLogger("quant_framework.strategy.tdx_t1_v2")


# ======================================================================
# Configuration
# ======================================================================

@dataclass
class T1ScalpV2Config:
    """T+1 短线策略 V2 配置。"""

    # ── 信号 ──
    signal_name: str = "tdx2_final"

    # ── 资金管理 ──
    max_positions: int = 3
    base_position_pct: float = 0.30        # 基础仓位 (波动率自适应调整)
    max_position_pct: float = 0.40          # 最大仓位上限
    min_position_pct: float = 0.10          # 最小仓位下限
    min_cash_reserve: float = 50000.0

    # ── 风控 (自适应 ATR 止损止盈) ──
    atr_stop_mult: float = 2.0              # ATR 止损倍数 (跌破买入价-N倍ATR)
    atr_profit_mult: float = 3.0            # ATR 止盈倍数 (超过买入价+N倍ATR)
    atr_period: int = 14                    # ATR 计算周期
    max_stop_loss_pct: float = -0.05        # 硬止损下限 (-5%)
    min_take_profit_pct: float = 0.03       # 硬止盈下限 (+3%)

    # ── 智能持仓 ──
    enable_smart_hold: bool = True          # 启用智能持仓 (强势股持有超过T+1)
    trailing_stop_atr: float = 1.5          # 跟踪止盈 ATR 倍数 (从最高点回撤)
    max_hold_days: int = 5                  # 最大持仓天数
    momentum_threshold: float = 0.02        # 强势判断阈值 (当日涨幅超过2%视为强势)

    # ── 市场环境过滤 ──
    enable_regime_filter: bool = True       # 启用市场环境过滤
    index_symbol: str = "999999"            # 大盘指数代码 (上证)
    bear_market_position_pct: float = 0.50  # 熊市时仓位缩至原仓位的50%
    high_vol_position_pct: float = 0.70     # 高波动时仓位缩至70%
    regime_ma_short: int = 20               # 短期均线判断牛熊
    regime_ma_long: int = 60                # 长期均线判断牛熊
    regime_vol_window: int = 20             # 波动率判断窗口
    regime_vol_threshold: float = 1.5       # 高波动阈值 (当前波动/历史波动的倍数)

    # ── 量能确认 ──
    enable_volume_filter: bool = True       # 启用量能过滤
    min_vol_ratio: float = 1.2              # 最小量比 (当日量/5日均量)
    require_price_volume_match: bool = True # 阳线放量/阴线缩量确认

    # ── 信号质量评分 ──
    enable_quality_scoring: bool = True     # 启用信号质量评分
    min_quality_score: float = 0.55         # 最低质量分 (0~1), 低于此分不买入

    # ── 缺口过滤 ──
    enable_gap_filter: bool = True          # 启用缺口过滤
    max_gap_down_pct: float = -0.02         # 最大允许跳空低开幅度

    # ── 冷却期 ──
    cooldown_days: int = 5                  # 同股票冷却天数 (不重复买入)

    # ── 成本 ──
    commission_rate: float = 0.0003
    stamp_duty_rate: float = 0.001
    min_commission: float = 5.0
    slippage: float = 0.001


# ======================================================================
# Market Regime Enum
# ======================================================================

class MarketRegime:
    """市场环境分类。"""
    BULL = "bull"            # 牛市: 价格>短期MA>长期MA, 低波动
    BULL_VOLATILE = "bull_volatile"  # 牛市高波: 价格>MA, 但波动放大
    SIDEWAYS = "sideways"    # 震荡: 价格在MA附近缠绕
    BEAR = "bear"            # 熊市: 价格<短期MA<长期MA
    BEAR_RALLY = "bear_rally"  # 熊市反弹: 价格<长期MA 但>短期MA
    HIGH_VOLATILITY = "high_vol"  # 高波动 (无论牛熊)

    @classmethod
    def classify(
        cls,
        price: float,
        ma_short: float,
        ma_long: float,
        vol_ratio: float,       # current volatility / historical
        vol_threshold: float,
    ) -> str:
        """根据价格位置和波动率分类市场环境。"""
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
# Holding record
# ======================================================================

class _HoldingV2:
    """V2 持仓记录 — 支持智能持仓和跟踪止盈。"""

    def __init__(
        self,
        symbol: str,
        buy_price: float,
        buy_date: Any = None,
        signal_intensity: int = 0,
        quality_score: float = 0.0,
        atr_value: float = 0.0,
        max_price_since_buy: float = 0.0,
    ):
        self.symbol = symbol
        self.buy_price = buy_price
        self.buy_date = buy_date
        self.signal_intensity = signal_intensity
        self.quality_score = quality_score
        self.atr_value = atr_value           # 买入时的 ATR
        self.max_price_since_buy = buy_price # 持仓期间最高价 (跟踪止盈用)
        self.hold_days = 0                   # 已持有天数


# ======================================================================
# Signal computers (compatible with V1)
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
    """获取信号的显示名称。"""
    from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
    from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS

    for registry in [TDX_SIGNAL_FACTORS, TDX2_SIGNAL_FACTORS]:
        info = registry.get(name)
        if info:
            return info.get("label", name)
    return name


# ======================================================================
# T1ScalpV2Strategy
# ======================================================================

class T1ScalpV2Strategy(BaseStrategy):
    """T+1 短线隔日冲策略 V2 — 多维度升级版。

    在 V1 基础上的核心改进:
      - 市场环境自适应: 熊市减仓/暂停、高波动降仓
      - 智能持仓: 强势股持有超过T+1, 跟踪止盈
      - 自适应止损止盈: ATR动态计算
      - 信号质量评分: 多维度打分择优
      - 量能确认: 过滤无量假突破
      - 波动率仓位: 高波小仓、低波大仓
    """

    def __init__(
        self,
        ctx: StrategyContext,
        config: T1ScalpV2Config | None = None,
    ) -> None:
        super().__init__(ctx)
        self.cfg = config or T1ScalpV2Config()

        # ── 状态 ──
        self._history: dict[str, pd.DataFrame] = {}       # symbol → OHLCV
        self._holdings: dict[str, _HoldingV2] = {}        # symbol → 持仓
        self._today_signals: dict[str, dict] = {}          # {symbol: {intensity, score, ...}}
        self._current_date: datetime | None = None
        self._min_history: int = 300

        # ── 新增状态 ──
        self._last_trade_date: dict[str, datetime] = {}    # 冷却期追踪
        self._index_history: pd.DataFrame | None = None    # 大盘数据
        self._current_regime: str = MarketRegime.SIDEWAYS  # 当前市场环境
        self._score_details: dict[str, dict] = {}          # 信号评分明细(调试用)

        # ── 信号函数 ──
        self._signal_fn = _SIGNAL_COMPUTERS.get(self.cfg.signal_name)
        if self._signal_fn is None:
            raise ValueError(f"Unknown signal: {self.cfg.signal_name}")

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def on_init(self) -> None:
        logger.info(
            "t1_v2_init",
            signal=self.cfg.signal_name,
            signal_label=get_signal_label(self.cfg.signal_name),
            max_positions=self.cfg.max_positions,
            base_position_pct=self.cfg.base_position_pct,
            smart_hold=self.cfg.enable_smart_hold,
            regime_filter=self.cfg.enable_regime_filter,
            volume_filter=self.cfg.enable_volume_filter,
            quality_scoring=self.cfg.enable_quality_scoring,
            gap_filter=self.cfg.enable_gap_filter,
            cooldown_days=self.cfg.cooldown_days,
        )

    # ==================================================================
    # Bar callback — 日线驱动
    # ==================================================================

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        """处理每日 K 线。"""
        symbol = bar.symbol
        signals: list[Signal] = []

        # 1. 更新历史数据
        self._update_history(symbol, bar)
        self._current_date = bar.dt

        # 2. 更新市场环境 (如果是大盘指数)
        if symbol == self.cfg.index_symbol or symbol.endswith(self.cfg.index_symbol):
            self._update_market_regime(bar)

        # 3. 处理持仓 — 检查是否卖出
        sell_signals = self._process_sells(bar)
        if sell_signals:
            signals.extend(sell_signals)

        # 4. 计算当日信号
        if len(self._history.get(symbol, pd.DataFrame())) >= self._min_history:
            sig_result = self._compute_signal_v2(symbol)
            if sig_result and sig_result.get("signal", 0) > 0:
                self._today_signals[symbol] = sig_result

        return signals if signals else None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        """实时行情驱动 (实盘模式)。"""
        signals: list[Signal] = []

        for symbol, holding in list(self._holdings.items()):
            # 更新持仓最高价
            if quote.price > holding.max_price_since_buy:
                holding.max_price_since_buy = quote.price

            # 检查是否需要卖出
            sell_sig = self._check_smart_exit(symbol, holding, quote.price, quote)
            if sell_sig:
                signals.extend(sell_sig)

        return signals if signals else None

    # ==================================================================
    # 数据更新
    # ==================================================================

    def _update_history(self, symbol: str, bar: Bar) -> None:
        """更新股票历史数据。"""
        if symbol not in self._history:
            self._history[symbol] = pd.DataFrame()

        df = self._history[symbol]
        new_row = pd.DataFrame([{
            "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
        }], index=[bar.dt])
        self._history[symbol] = pd.concat([df, new_row])

        # 滚动窗口 500 根
        if len(self._history[symbol]) > 500:
            self._history[symbol] = self._history[symbol].iloc[-500:]

    def _update_market_regime(self, bar: Bar) -> None:
        """更新市场环境判断。"""
        if not self.cfg.enable_regime_filter:
            return

        symbol = self.cfg.index_symbol
        df = self._history.get(symbol)
        if df is None or len(df) < self.cfg.regime_ma_long + 10:
            return

        c = df["close"]
        ma_short = c.iloc[-self.cfg.regime_ma_short:].mean()
        ma_long = c.iloc[-self.cfg.regime_ma_long:].mean()
        price = bar.close

        # 波动率对比
        returns = c.pct_change().dropna()
        current_vol = returns.iloc[-self.cfg.regime_vol_window:].std()
        historical_vol = returns.iloc[-120:].std() if len(returns) >= 120 else current_vol
        vol_ratio = current_vol / historical_vol if historical_vol > 0 else 1.0

        self._current_regime = MarketRegime.classify(
            price=price,
            ma_short=ma_short,
            ma_long=ma_long,
            vol_ratio=vol_ratio,
            vol_threshold=self.cfg.regime_vol_threshold,
        )

        logger.debug(
            "regime_update",
            regime=self._current_regime,
            price=price,
            ma_short=ma_short,
            ma_long=ma_long,
            vol_ratio=vol_ratio,
        )

    # ==================================================================
    # 信号计算 V2 — 多维度评分
    # ==================================================================

    def _compute_signal_v2(self, symbol: str) -> dict | None:
        """计算信号并返回多维度评分结果。"""
        df = self._history.get(symbol)
        if df is None or len(df) < self._min_history:
            return None

        try:
            sig_val = self._signal_fn(df)
            if isinstance(sig_val, pd.Series):
                raw_signal = sig_val.iloc[-1]
                if pd.isna(raw_signal) or raw_signal <= 0:
                    return None
                raw_signal = int(raw_signal)
            else:
                return None
        except Exception as e:
            logger.debug("signal_compute_error", symbol=symbol, error=str(e))
            return None

        # ── 多维度评分 ──
        scores = self._score_signal_quality(symbol, df, raw_signal)
        quality_score = scores["total"]

        # 最低质量分过滤
        if self.cfg.enable_quality_scoring and quality_score < self.cfg.min_quality_score:
            logger.debug(
                "signal_filtered_by_quality",
                symbol=symbol,
                quality_score=quality_score,
                min_score=self.cfg.min_quality_score,
                details=scores,
            )
            return None

        # 量能过滤
        if self.cfg.enable_volume_filter:
            if not self._check_volume(df, raw_signal):
                logger.debug("signal_filtered_by_volume", symbol=symbol)
                return None

        # 冷却期过滤
        if symbol in self._last_trade_date:
            last_dt = self._last_trade_date[symbol]
            if self._current_date and (self._current_date - last_dt).days < self.cfg.cooldown_days:
                logger.debug("signal_filtered_by_cooldown", symbol=symbol,
                             days_since=(self._current_date - last_dt).days)
                return None

        return {
            "signal": raw_signal,
            "quality_score": quality_score,
            "close": df["close"].iloc[-1],
            "atr": scores.get("atr", 0.02),
            "scores": scores,
        }

    # ==================================================================
    # 信号质量评分 (升级 #3)
    # ==================================================================

    def _score_signal_quality(
        self, symbol: str, df: pd.DataFrame, raw_signal: int
    ) -> dict:
        """多维度信号质量评分。

        四个维度各 0~1 分，加权求和:
          1. 信号强度 (0.25) — 信号值大小和共振程度
          2. 趋势质量 (0.30) — 价格相对均线位置、MACD 状态
          3. 量能质量 (0.25) — 量比、量价配合
          4. 位置质量 (0.20) — 相对近期高低点位置 (避免追高)
        """
        c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
        scores = {}

        # ── 1. 信号强度分 (0~1) ──
        # 双共振=满分, 单信号=0.6
        if raw_signal >= 2:
            scores["intensity"] = 1.0
        elif raw_signal == 1:
            scores["intensity"] = 0.6
        else:
            scores["intensity"] = 0.3

        # ── 2. 趋势质量分 (0~1) ──
        ma5 = c.iloc[-5:].mean()
        ma10 = c.iloc[-10:].mean()
        ma20 = c.iloc[-20:].mean()
        current = c.iloc[-1]

        trend_score = 0.0
        # 多头排列加分
        if current > ma5 > ma10 > ma20:
            trend_score = 0.9
        elif current > ma5 and current > ma10:
            trend_score = 0.7
        elif current > ma20:
            trend_score = 0.5
        elif current > ma5:
            trend_score = 0.3
        else:
            trend_score = 0.1

        # MACD 辅助
        ema12 = c.ewm(span=12, adjust=False).mean()
        ema26 = c.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0:
            trend_score = min(1.0, trend_score + 0.1)

        scores["trend"] = trend_score

        # ── 3. 量能质量分 (0~1) ──
        vol_current = v.iloc[-1]
        vol_ma5 = v.iloc[-6:-1].mean() if len(v) >= 6 else v.iloc[:-1].mean()
        vol_ma20 = v.iloc[-21:-1].mean() if len(v) >= 21 else vol_ma5

        vol_ratio_5 = vol_current / vol_ma5 if vol_ma5 > 0 else 1.0
        vol_ratio_20 = vol_current / vol_ma20 if vol_ma20 > 0 else 1.0

        # 温和放量最理想 (1.5~3倍5日均量)
        if 1.5 <= vol_ratio_5 <= 3.0:
            volume_score = 1.0
        elif 1.2 <= vol_ratio_5 < 1.5:
            volume_score = 0.7
        elif vol_ratio_5 > 3.0:
            volume_score = 0.6  # 过度放量可能有出货嫌疑
        elif vol_ratio_5 >= 0.8:
            volume_score = 0.4
        else:
            volume_score = 0.2  # 缩量信号不可靠

        # 量价配合: 阳线+放量 OR 阴线+缩量
        price_change = c.iloc[-1] - c.iloc[-2] if len(c) >= 2 else 0
        if price_change > 0 and vol_ratio_5 > 1.0:
            volume_score = min(1.0, volume_score + 0.1)
        elif price_change < 0 and vol_ratio_5 < 1.0:
            volume_score = min(1.0, volume_score + 0.05)

        scores["volume"] = volume_score

        # ── 4. 位置质量分 (0~1) — 避免追高 ──
        high_20 = h.iloc[-20:].max()
        low_20 = l.iloc[-20:].min()
        range_20 = high_20 - low_20

        if range_20 > 0:
            position_in_range = (current - low_20) / range_20
        else:
            position_in_range = 0.5

        # 最佳: 中低位突破 (0.3~0.7区间), 不在极高位置追高
        if 0.2 <= position_in_range <= 0.5:
            position_score = 1.0
        elif 0.5 < position_in_range <= 0.7:
            position_score = 0.8
        elif 0.7 < position_in_range <= 0.85:
            position_score = 0.5  # 偏高，但不极端
        elif position_in_range > 0.85:
            position_score = 0.2  # 接近20日高点，追高风险大
        else:
            position_score = 0.6  # 低位 (可能还在下跌趋势)

        # 检查是否连续大涨 (远离MA5)
        if current > ma5 * 1.05:
            position_score *= 0.7  # 短期乖离过大，回调风险

        scores["position"] = position_score

        # ── ATR 计算 (用于后续仓位和止损) ──
        tr = pd.concat([
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ], axis=1).max(axis=1)
        atr = tr.iloc[-self.cfg.atr_period:].mean()
        atr_pct = atr / current if current > 0 else 0.02
        scores["atr"] = atr_pct

        # ── 加权总分 ──
        scores["total"] = (
            scores["intensity"] * 0.25 +
            scores["trend"] * 0.30 +
            scores["volume"] * 0.25 +
            scores["position"] * 0.20
        )

        # 存储评分明细
        self._score_details[symbol] = scores

        return scores

    # ==================================================================
    # 量能确认 (升级 #4)
    # ==================================================================

    def _check_volume(self, df: pd.DataFrame, raw_signal: int) -> bool:
        """量能确认: 量比+量价配合。"""
        v = df["volume"]
        c = df["close"]

        if len(v) < 6 or len(c) < 2:
            return True  # 数据不足，放行

        vol_current = v.iloc[-1]
        vol_ma5 = v.iloc[-6:-1].mean()

        vol_ratio = vol_current / vol_ma5 if vol_ma5 > 0 else 0

        # 量比过滤
        if vol_ratio < self.cfg.min_vol_ratio:
            return False

        # 量价配合: 阳线需要放量
        if self.cfg.require_price_volume_match:
            price_up = c.iloc[-1] > c.iloc[-2]
            if price_up and vol_ratio < 1.0:
                return False  # 无量上涨，假突破嫌疑

        return True

    # ==================================================================
    # 缺口过滤 (升级 #6)
    # ==================================================================

    def _check_gap_risk(self, symbol: str) -> bool:
        """检查是否有向下跳空风险。

        检查项:
          - 近期是否有跳空低开缺口未被回补
          - 尾盘是否出现跳水
        """
        if not self.cfg.enable_gap_filter:
            return True  # 不过滤

        df = self._history.get(symbol)
        if df is None or len(df) < 5:
            return True

        # 检查最近3天是否有未回补的向下跳空
        for i in range(-3, 0):
            if abs(i) > len(df):
                break
            today_open = df["open"].iloc[i]
            prev_close = df["close"].iloc[i - 1]
            gap = (today_open - prev_close) / prev_close

            # 向下跳空 > 1%
            if gap < self.cfg.max_gap_down_pct:
                # 检查是否已回补 (当日最高价是否超过前日收盘价)
                today_high = df["high"].iloc[i]
                if today_high < prev_close:
                    return False  # 未回补的向下跳空，风险大

        return True

    # ==================================================================
    # 卖出处理 V2 — 智能持仓
    # ==================================================================

    def _process_sells(self, bar: Bar) -> list[Signal]:
        """处理持仓卖出 (V2 智能持仓逻辑)。"""
        signals: list[Signal] = []
        symbol = bar.symbol

        holding = self._holdings.get(symbol)
        if holding is None:
            return signals

        # T+1 约束: 买入当天不能卖
        if holding.buy_date and holding.buy_date == bar.dt.date():
            return signals

        # 更新持仓天数
        holding.hold_days += 1

        # 更新持仓期间最高价
        if bar.high > holding.max_price_since_buy:
            holding.max_price_since_buy = bar.high

        # 决定是否卖出
        should_sell, reason = self._should_sell(holding, bar)

        if should_sell:
            signals.extend(self._execute_sell_v2(
                symbol=symbol,
                price=bar.close,  # T+1用开盘价，之后用收盘价
                bar=bar,
                holding=holding,
                reason=reason,
            ))

        return signals

    def _should_sell(self, holding: _HoldingV2, bar: Bar) -> tuple[bool, str]:
        """智能卖出判断 (升级 #5)。

        卖出条件 (满足任一即卖出):
          1. 达到跟踪止盈线: 从最高点回撤超过 trailing_stop_atr 倍 ATR
          2. 达到硬止损: 亏损超过 max_stop_loss_pct
          3. 达到硬止盈: 盈利超过自适应止盈线
          4. 持仓超时: 达到最大持仓天数
          5. 非强势且已持T+1: 不够强势则在T+1日正常卖出
        """
        current_price = bar.close
        buy_price = holding.buy_price
        atr = holding.atr_value * buy_price  # ATR in price terms
        if atr <= 0:
            atr = buy_price * 0.02  # fallback 2%

        return_pct = (current_price - buy_price) / buy_price

        # ── 1. 跟踪止盈 ──
        if self.cfg.enable_smart_hold and holding.max_price_since_buy > buy_price:
            # 从最高点回撤幅度
            pullback = (current_price - holding.max_price_since_buy) / holding.max_price_since_buy
            trailing_stop = -self.cfg.trailing_stop_atr * (atr / buy_price)

            if pullback < trailing_stop and return_pct > 0:
                return True, (
                    f"跟踪止盈 | 最高={holding.max_price_since_buy:.2f} "
                    f"回撤={pullback:.2%} | 收益={return_pct:.2%}"
                )

        # ── 2. 硬止损 ──
        if return_pct <= self.cfg.max_stop_loss_pct:
            return True, f"硬止损 | 收益={return_pct:.2%}"

        # ── 3. 自适应止盈 ──
        adaptive_tp = self.cfg.atr_profit_mult * (atr / buy_price)
        adaptive_tp = max(adaptive_tp, self.cfg.min_take_profit_pct)
        if return_pct >= adaptive_tp:
            return True, f"自适应止盈 | 收益={return_pct:.2%} | ATR止盈线={adaptive_tp:.2%}"

        # ── 4. 持仓超时 ──
        if holding.hold_days >= self.cfg.max_hold_days:
            return True, f"持仓超时({holding.hold_days}天) | 收益={return_pct:.2%}"

        # ── 5. T+1 正常退出 (非强势股) ──
        if not self.cfg.enable_smart_hold and holding.hold_days >= 1:
            return True, f"T+1隔日冲 | 收益={return_pct:.2%}"

        if self.cfg.enable_smart_hold:
            # T+1 日判断: 今天涨幅是否超过动量阈值
            # 如果是T+1且不够强势，卖出
            if holding.hold_days >= 1:
                day_return = (bar.close - bar.open) / bar.open if bar.open > 0 else 0
                if day_return < self.cfg.momentum_threshold:
                    return True, f"T+1弱势退出 | 当日涨幅={day_return:.2%} | 总收益={return_pct:.2%}"

        return False, ""

    def _execute_sell_v2(
        self,
        symbol: str,
        price: float,
        bar: Bar,
        holding: _HoldingV2,
        reason: str,
    ) -> list[Signal]:
        """执行卖出。"""
        return_pct = (price - holding.buy_price) / holding.buy_price if holding.buy_price > 0 else 0

        # 确定退出类型
        exit_type = "normal"
        if return_pct <= self.cfg.max_stop_loss_pct:
            exit_type = "stop_loss"
        elif return_pct >= self.cfg.atr_profit_mult * (holding.atr_value if holding.atr_value > 0 else 0.02):
            exit_type = "take_profit"
        elif "跟踪止盈" in reason:
            exit_type = "trailing_stop"
        elif "弱势" in reason:
            exit_type = "weak_exit"
        elif "超时" in reason:
            exit_type = "timeout"

        # 滑点
        sell_price = price * (1 - self.cfg.slippage)

        # 记录最后交易日期 (冷却期)
        self._last_trade_date[symbol] = bar.dt if hasattr(bar, 'dt') else datetime.now()

        # 清理
        del self._holdings[symbol]

        logger.info(
            "t1_v2_sell",
            symbol=symbol,
            buy_price=holding.buy_price,
            sell_price=sell_price,
            return_pct=f"{return_pct:.2%}",
            hold_days=holding.hold_days,
            exit_type=exit_type,
            reason=reason,
        )

        return self.sell(
            symbol=symbol,
            price=sell_price,
            position_pct=1.0,
            reason=f"{reason} | 持有{holding.hold_days}天 | {exit_type}",
            metadata={
                "buy_price": holding.buy_price,
                "sell_price": sell_price,
                "return_pct": return_pct,
                "hold_days": holding.hold_days,
                "exit_type": exit_type,
                "quality_score": holding.quality_score,
                "max_price": holding.max_price_since_buy,
            },
        )

    def _check_smart_exit(
        self, symbol: str, holding: _HoldingV2, price: float, quote: Quote
    ) -> list[Signal] | None:
        """实盘模式下的智能退出检查。"""
        # 简化: 实盘中使用 on_quote + bar 逻辑结合
        return_pct = (price - holding.buy_price) / holding.buy_price if holding.buy_price > 0 else 0
        atr_price = holding.atr_value * holding.buy_price if holding.atr_value > 0 else holding.buy_price * 0.02

        # 跟踪止盈
        if holding.max_price_since_buy > holding.buy_price:
            pullback = (price - holding.max_price_since_buy) / holding.max_price_since_buy
            trailing_stop = -self.cfg.trailing_stop_atr * holding.atr_value
            if pullback < trailing_stop and return_pct > 0:
                return self._execute_sell_v2(
                    symbol, price, quote, holding,
                    f"实盘跟踪止盈 | 收益={return_pct:.2%}",
                )

        return None

    # ==================================================================
    # 收盘批量选股 & 买入
    # ==================================================================

    def on_day_end(self) -> list[Signal]:
        """每日收盘后 — 批量选股并生成买入信号。"""
        signals: list[Signal] = []

        if not self._today_signals:
            return signals

        # 当前持仓和可用仓位
        current_positions = len(self._holdings)
        available_slots = self.cfg.max_positions - current_positions
        if available_slots <= 0:
            self._today_signals.clear()
            return signals

        # ── 按质量评分排序 (V2: 不再仅按强度) ──
        ranked = sorted(
            self._today_signals.items(),
            key=lambda x: x[1].get("quality_score", 0),
            reverse=True,
        )

        for symbol, sig_info in ranked[:available_slots]:
            df = self._history.get(symbol)
            if df is None or len(df) < 2:
                continue

            # ── V2 过滤 ──

            # 缺口风险检查
            if not self._check_gap_risk(symbol):
                logger.info("t1_v2_skip_gap_risk", symbol=symbol)
                continue

            # 冷却期检查
            if symbol in self._last_trade_date:
                last_dt = self._last_trade_date[symbol]
                if self._current_date and (self._current_date - last_dt).days < self.cfg.cooldown_days:
                    logger.info("t1_v2_skip_cooldown", symbol=symbol)
                    continue

            buy_price = df["close"].iloc[-1]
            quality_score = sig_info.get("quality_score", 0.5)
            atr_pct = sig_info.get("atr", 0.02)

            # 涨停封死检查
            if self._is_limit_up_locked(df):
                logger.info("t1_v2_skip_limit_up", symbol=symbol, price=buy_price)
                continue

            # ── 波动率自适应仓位 (升级 #7) ──
            position_pct = self._calc_position_size(atr_pct)

            # ── 市场环境仓位调整 (升级 #1) ──
            position_pct = self._adjust_for_regime(position_pct)

            # 滑点调整
            buy_with_slippage = buy_price * (1 + self.cfg.slippage)

            # 记录持仓
            self._holdings[symbol] = _HoldingV2(
                symbol=symbol,
                buy_price=buy_with_slippage,
                buy_date=self._current_date.date() if self._current_date else None,
                signal_intensity=sig_info.get("signal", 1),
                quality_score=quality_score,
                atr_value=atr_pct,
                max_price_since_buy=buy_with_slippage,
            )

            # 记录冷却期
            if self._current_date:
                self._last_trade_date[symbol] = self._current_date

            logger.info(
                "t1_v2_buy",
                symbol=symbol,
                price=buy_with_slippage,
                quality_score=quality_score,
                position_pct=position_pct,
                regime=self._current_regime,
                atr_pct=atr_pct,
                signal=get_signal_label(self.cfg.signal_name),
                date=str(self._current_date.date()) if self._current_date else "?",
            )

            signals.extend(self.buy(
                symbol=symbol,
                price=buy_with_slippage,
                position_pct=position_pct,
                reason=(
                    f"{get_signal_label(self.cfg.signal_name)} | "
                    f"评分={quality_score:.0%} | "
                    f"仓位={position_pct:.0%} | "
                    f"环境={self._current_regime}"
                ),
                metadata={
                    "signal": self.cfg.signal_name,
                    "intensity": sig_info.get("signal", 1),
                    "quality_score": quality_score,
                    "position_pct": position_pct,
                    "regime": self._current_regime,
                    "atr_pct": atr_pct,
                    "buy_date": str(self._current_date.date()) if self._current_date else None,
                    "score_detail": sig_info.get("scores", {}),
                },
            ))

        self._today_signals.clear()
        return signals

    # ==================================================================
    # 仓位计算
    # ==================================================================

    def _calc_position_size(self, atr_pct: float) -> float:
        """波动率自适应仓位 (升级 #7)。

        高波动 → 小仓位，低波动 → 大仓位。
        基准: ATR% = 2% 时使用 base_position_pct
        """
        if atr_pct <= 0:
            return self.cfg.base_position_pct

        # 以 2% ATR 为基准
        vol_adjustment = 0.02 / atr_pct
        vol_adjustment = max(0.5, min(1.5, vol_adjustment))  # 限制 0.5x ~ 1.5x

        position_pct = self.cfg.base_position_pct * vol_adjustment
        return max(self.cfg.min_position_pct, min(self.cfg.max_position_pct, position_pct))

    def _adjust_for_regime(self, position_pct: float) -> float:
        """市场环境仓位调整 (升级 #1)。"""
        if not self.cfg.enable_regime_filter:
            return position_pct

        regime_multipliers = {
            MarketRegime.BULL: 1.0,
            MarketRegime.BULL_VOLATILE: self.cfg.high_vol_position_pct,
            MarketRegime.SIDEWAYS: 0.85,
            MarketRegime.BEAR: self.cfg.bear_market_position_pct,
            MarketRegime.BEAR_RALLY: 0.60,
            MarketRegime.HIGH_VOLATILITY: self.cfg.high_vol_position_pct,
        }

        multiplier = regime_multipliers.get(self._current_regime, 0.85)
        adjusted = position_pct * multiplier

        logger.debug(
            "position_regime_adjust",
            regime=self._current_regime,
            multiplier=multiplier,
            before=position_pct,
            after=adjusted,
        )

        return max(self.cfg.min_position_pct, adjusted)

    # ==================================================================
    # 辅助函数
    # ==================================================================

    def _is_limit_up_locked(self, df: pd.DataFrame) -> bool:
        """检查是否涨停封死。"""
        if len(df) < 2:
            return False
        prev_close = df["close"].iloc[-2]
        current = df["close"].iloc[-1]
        limit_up_price = prev_close * 1.10
        return abs(current - limit_up_price) < 0.01

    # ==================================================================
    # 状态查询
    # ==================================================================

    @property
    def active_holdings(self) -> dict[str, dict]:
        """当前持仓摘要。"""
        return {
            sym: {
                "buy_price": h.buy_price,
                "buy_date": h.buy_date,
                "hold_days": h.hold_days,
                "quality_score": h.quality_score,
                "return_pct": (
                    (self._history.get(sym, pd.DataFrame())["close"].iloc[-1] - h.buy_price) / h.buy_price
                    if sym in self._history and len(self._history[sym]) > 0
                    else 0
                ),
                "max_price": h.max_price_since_buy,
            }
            for sym, h in self._holdings.items()
        }

    @property
    def market_regime(self) -> str:
        """当前市场环境。"""
        return self._current_regime

    @property
    def signal_stats(self) -> dict:
        """信号统计 (调试用)。"""
        return {
            "total_holdings": len(self._holdings),
            "today_signals": len(self._today_signals),
            "regime": self._current_regime,
            "last_score_details": dict(self._score_details) if self._score_details else {},
        }
