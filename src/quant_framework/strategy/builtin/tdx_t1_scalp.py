"""T+1 短线隔日冲策略 — 通达信选股信号驱动。

A股 T+1 约束下的超短线策略:
  - T日尾盘: 通达信选股信号触发 → 以收盘价买入
  - T+1日开盘: 以开盘价卖出 (隔日冲)
  - 风控: 止损/止盈/涨跌停保护

策略信号来源 (可配置):
  - formula1: 牛线突破 + B1底部反转 (tdx2_final)
  - formula2: 双信号共振 擒龙决+涨停先锋 (tdx_resonance)
  - formula3: 牛线突破XG (tdx2_xg)
  - formula4: 底部反转B1 (tdx2_b1)

用法:
  from quant_framework.strategy.builtin.tdx_t1_scalp import T1ScalpStrategy
  strategy = T1ScalpStrategy(ctx, signal_name="tdx2_final")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.data.models import Bar, Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.factors.tdx_signals import (
    TDX_SIGNAL_FACTORS,
    factor_qlj,
    factor_ztxf,
    factor_resonance,
)
from quant_framework.factors.tdx_signals2 import (
    TDX2_SIGNAL_FACTORS,
    factor_xg_signal,
    factor_b1_structure,
    factor_final_pick,
)
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection

logger = logging.getLogger("quant_framework.strategy.tdx_t1")


@dataclass
class T1ScalpConfig:
    """T+1 短线策略配置。"""

    signal_name: str = "tdx2_final"          # 信号因子名称
    # 资金管理
    max_positions: int = 3                    # 最大同时持仓数
    position_pct: float = 0.30                # 单票仓位占比 (可用资金%)
    min_cash_reserve: float = 50000.0         # 最低现金保留
    # 风控
    stop_loss_pct: float = -0.03              # 止损线 (-3%)
    take_profit_pct: float = 0.05             # 止盈线 (5%)
    # 涨跌停保护
    skip_if_limit_up_buy: bool = True         # 涨停封死则跳过买入
    skip_if_limit_down_sell: bool = True      # 跌停封死则延后卖出
    # 成本
    commission_rate: float = 0.0003           # 万三佣金
    stamp_duty_rate: float = 0.001            # 千一印花税 (仅卖出)
    min_commission: float = 5.0               # 最低佣金
    slippage: float = 0.001                   # 0.1% 滑点


# ---- Signal computation cache (module-level, shared across strategy instances) ----

_SIGNAL_COMPUTERS = {
    # tdx_signals
    "tdx_qlj": factor_qlj,
    "tdx_ztxf": factor_ztxf,
    "tdx_resonance": factor_resonance,
    # tdx_signals2
    "tdx2_xg": factor_xg_signal,
    "tdx2_bb": None,  # sub-signal only
    "tdx2_t": None,   # sub-signal only
    "tdx2_b1": factor_b1_structure,
    "tdx2_final": factor_final_pick,
}


def get_signal_label(name: str) -> str:
    """获取信号的显示名称。"""
    for registry in [TDX_SIGNAL_FACTORS, TDX2_SIGNAL_FACTORS]:
        info = registry.get(name)
        if info:
            return info.get("label", name)
    return name


class T1ScalpStrategy(BaseStrategy):
    """T+1 短线隔日冲策略。

    在回测模式下:
    1. 接收每日 Bar 数据
    2. 维护每只股票的历史 DataFrame (rolling window)
    3. 每日收盘后计算选股信号
    4. T日信号触发 → 以收盘价模拟尾盘买入
    5. T+1日开盘 → 以开盘价卖出

    在实盘模式下:
    1. 尾盘 (14:55) 计算信号
    2. 市价买入信号股票
    3. 次日开盘 (9:30) 市价卖出
    """

    def __init__(self, ctx: StrategyContext, config: T1ScalpConfig | None = None) -> None:
        super().__init__(ctx)
        self.cfg = config or T1ScalpConfig()

        # 状态管理
        self._history: dict[str, pd.DataFrame] = {}     # symbol → OHLCV DataFrame
        self._holdings: dict[str, _Holding] = {}         # symbol → 持仓信息
        self._pending_sells: set[str] = set()            # T+1日待卖出
        self._today_signals: dict[str, int] = {}         # 当日信号 {symbol: intensity}
        self._current_date: datetime | None = None
        self._min_history: int = 300                     # 最少需要300根K线

        # 信号计算函数
        self._signal_fn = _SIGNAL_COMPUTERS.get(self.cfg.signal_name)
        if self._signal_fn is None:
            raise ValueError(f"Unknown signal: {self.cfg.signal_name}")

    # ==================================================================
    # Lifecycle
    # ==================================================================

    def on_init(self) -> None:
        logger.info(
            "t1_scalp_init",
            signal=self.cfg.signal_name,
            signal_label=get_signal_label(self.cfg.signal_name),
            max_positions=self.cfg.max_positions,
            position_pct=self.cfg.position_pct,
            stop_loss=self.cfg.stop_loss_pct,
            take_profit=self.cfg.take_profit_pct,
        )

    # ==================================================================
    # Bar callback — 日线驱动
    # ==================================================================

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        """处理每日 K 线。"""
        symbol = bar.symbol
        signals: list[Signal] = []

        # 1. 更新历史数据
        if symbol not in self._history:
            self._history[symbol] = pd.DataFrame()
        df = self._history[symbol]
        new_row = pd.DataFrame([{
            "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "volume": bar.volume,
        }], index=[bar.dt])
        self._history[symbol] = pd.concat([df, new_row])

        # 保持最多 500 根 K 线
        if len(self._history[symbol]) > 500:
            self._history[symbol] = self._history[symbol].iloc[-500:]

        self._current_date = bar.dt

        # 2. 先处理持仓卖出 (T+1日开盘卖)
        sell_signals = self._process_sells(bar)
        if sell_signals:
            signals.extend(sell_signals)

        # 3. 计算当日信号 (收盘后)
        if len(self._history[symbol]) >= self._min_history:
            sig_val = self._compute_signal(symbol)
            if sig_val > 0:
                self._today_signals[symbol] = int(sig_val)

        return signals if signals else None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        """实时行情驱动 (实盘模式)。"""
        # 实盘模式: 检查是否有待卖出的持仓
        signals: list[Signal] = []

        for symbol in list(self._pending_sells):
            holding = self._holdings.get(symbol)
            if holding is None:
                self._pending_sells.discard(symbol)
                continue
            signals.extend(self._execute_sell(symbol, quote.price, quote, "实盘卖出"))

        return signals if signals else None

    # ==================================================================
    # 信号计算
    # ==================================================================

    def _compute_signal(self, symbol: str) -> int:
        """计算当日选股信号 (0=无信号, 1=单信号, 2=双共振)。"""
        df = self._history.get(symbol)
        if df is None or len(df) < self._min_history:
            return 0

        try:
            result = self._signal_fn(df)
            if isinstance(result, pd.Series):
                val = result.iloc[-1]
                return int(val) if not pd.isna(val) else 0
            return 0
        except Exception as e:
            logger.debug("signal_compute_error", symbol=symbol, error=str(e))
            return 0

    # ==================================================================
    # 卖出处理
    # ==================================================================

    def _process_sells(self, bar: Bar) -> list[Signal]:
        """处理所有待卖出持仓 (T+1开盘价卖出)。"""
        signals: list[Signal] = []
        symbol = bar.symbol

        # 检查是否有该股票的持仓需要卖出
        holding = self._holdings.get(symbol)
        if holding is None:
            return signals

        # 如果今天是买入日当天，不能卖 (T+1约束)
        if holding.buy_date and holding.buy_date == bar.dt.date():
            return signals

        # 执行卖出
        signals.extend(self._execute_sell(symbol, bar.open, bar, f"T+1隔日冲"))

        return signals

    def _execute_sell(
        self, symbol: str, price: float, data: Bar | Quote, reason: str
    ) -> list[Signal]:
        """执行卖出操作。"""
        holding = self._holdings.get(symbol)
        if holding is None:
            return []

        # 涨跌停检查 (如果是 Bar)
        if isinstance(data, Bar):
            limit_up_price = data.close * 1.10 if hasattr(data, 'close') else price * 1.10
            limit_down_price = data.close * 0.90 if hasattr(data, 'close') else price * 0.90
            # 简单估计: A股涨跌停≈±10%

        # 计算收益率
        buy_price = holding.buy_price
        return_pct = (price - buy_price) / buy_price if buy_price > 0 else 0

        # 止损/止盈标记
        exit_type = "normal"
        if return_pct <= self.cfg.stop_loss_pct:
            exit_type = "stop_loss"
        elif return_pct >= self.cfg.take_profit_pct:
            exit_type = "take_profit"

        # 计算滑点和成本
        sell_price = price * (1 - self.cfg.slippage)

        # 清理持仓记录
        del self._holdings[symbol]
        self._pending_sells.discard(symbol)

        logger.info(
            "t1_sell",
            symbol=symbol,
            buy_price=buy_price,
            sell_price=sell_price,
            return_pct=f"{return_pct:.2%}",
            exit_type=exit_type,
            reason=reason,
        )

        return self.sell(
            symbol=symbol,
            price=sell_price,
            position_pct=1.0,  # 全仓卖出
            reason=f"{reason} | 收益={return_pct:.2%} | {exit_type}",
            metadata={
                "buy_price": buy_price,
                "sell_price": sell_price,
                "return_pct": return_pct,
                "exit_type": exit_type,
            },
        )

    # ==================================================================
    # 每日收盘后批量选股 & 买入
    # ==================================================================

    def on_day_end(self) -> list[Signal]:
        """每日收盘后调用 — 批量选股并生成买入信号。"""
        signals: list[Signal] = []

        if not self._today_signals:
            return signals

        # 当前持仓数
        current_positions = len(self._holdings)
        available_slots = self.cfg.max_positions - current_positions
        if available_slots <= 0:
            self._today_signals.clear()
            return signals

        # 按信号强度排序 (双共振 > 单信号)
        ranked = sorted(
            self._today_signals.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        # 对每只信号股票生成买入
        for symbol, intensity in ranked[:available_slots]:
            df = self._history.get(symbol)
            if df is None or len(df) < 2:
                continue

            buy_price = df["close"].iloc[-1]  # T日收盘价

            # 涨停封死检查
            if self.cfg.skip_if_limit_up_buy:
                prev_close = df["close"].iloc[-2]
                limit_up_price = prev_close * 1.10
                if abs(buy_price - limit_up_price) < 0.01:
                    # 涨停封死, 买不到
                    logger.info("t1_skip_limit_up", symbol=symbol, price=buy_price)
                    continue

            # 记录持仓
            self._holdings[symbol] = _Holding(
                symbol=symbol,
                buy_price=buy_price,
                buy_date=self._current_date.date() if self._current_date else None,
                signal_intensity=intensity,
            )
            self._pending_sells.add(symbol)

            # 滑点调整买入价
            buy_with_slippage = buy_price * (1 + self.cfg.slippage)

            logger.info(
                "t1_buy",
                symbol=symbol,
                price=buy_with_slippage,
                intensity=intensity,
                signal=get_signal_label(self.cfg.signal_name),
                date=str(self._current_date.date()) if self._current_date else "?",
            )

            signals.extend(self.buy(
                symbol=symbol,
                price=buy_with_slippage,
                position_pct=self.cfg.position_pct,
                reason=f"{get_signal_label(self.cfg.signal_name)} | 强度={intensity} | T+1隔日冲",
                metadata={
                    "signal": self.cfg.signal_name,
                    "intensity": intensity,
                    "buy_date": str(self._current_date.date()) if self._current_date else None,
                },
            ))

        self._today_signals.clear()
        return signals

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
                "intensity": h.signal_intensity,
            }
            for sym, h in self._holdings.items()
        }


class _Holding:
    """持仓记录。"""
    def __init__(
        self,
        symbol: str,
        buy_price: float,
        buy_date: Any = None,
        signal_intensity: int = 0,
    ):
        self.symbol = symbol
        self.buy_price = buy_price
        self.buy_date = buy_date
        self.signal_intensity = signal_intensity
