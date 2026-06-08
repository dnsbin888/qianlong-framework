"""MACD Crossover/Divergence Strategy.

Migrated from: 同花顺 经典策略/MACD条件.py

Triggers on:
- Single golden/death cross
- Double cross within a lookback window
- Top/bottom divergence (price makes new high/low but DIF doesn't confirm)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from quant_framework.data.models import Bar
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal, SignalDirection


@dataclass
class MACDCrossConfig:
    symbol: str
    period: str = "15m"
    fast: int = 12
    slow: int = 26
    signal_period: int = 9
    cross_type: str = "golden"    # "golden" | "death"
    cross_count: int = 1          # 1=single, 2=double, 3=divergence
    divergence_window: int = 50
    volume: int = 500


class MACDCrossStrategy(BaseStrategy):
    """MACD 金叉/死叉/背离策略."""

    def __init__(self, ctx: StrategyContext, cfg: MACDCrossConfig) -> None:
        super().__init__(ctx)
        self.cfg = cfg
        self._cross_history: list[str] = []

    def on_init(self) -> None:
        self.ctx.logger.info(
            "macd_cross_init",
            symbol=self.cfg.symbol,
            period=self.cfg.period,
            cross_type=self.cfg.cross_type,
        )

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        window = self.cfg.divergence_window + 100
        macd_df = self.macd(
            self.cfg.symbol,
            fast=self.cfg.fast,
            slow=self.cfg.slow,
            signal=self.cfg.signal_period,
            period=self.cfg.period,
            lookback=window,
        )
        if macd_df is None or len(macd_df) < self.cfg.divergence_window + 2:
            return None

        hist = macd_df["hist"].values
        prev_hist, curr_hist = hist[-2], hist[-1]

        # Single cross
        if self.cfg.cross_count == 1:
            if self.cfg.cross_type == "golden" and prev_hist < 0 and curr_hist > 0:
                return self.buy(self.cfg.symbol, volume=self.cfg.volume, reason="MACD一次金叉")
            if self.cfg.cross_type == "death" and prev_hist > 0 and curr_hist < 0:
                return self.sell(self.cfg.symbol, volume=self.cfg.volume, reason="MACD一次死叉")

        # Double cross
        if self.cfg.cross_count == 2:
            window_slice = hist[-self.cfg.divergence_window:]
            cross_count = sum(
                1 for i in range(1, len(window_slice))
                if (self.cfg.cross_type == "golden" and window_slice[i-1] < 0 and window_slice[i] > 0)
                or (self.cfg.cross_type == "death" and window_slice[i-1] > 0 and window_slice[i] < 0)
            )
            if cross_count >= 1:
                if self.cfg.cross_type == "golden" and prev_hist < 0 and curr_hist > 0:
                    return self.buy(self.cfg.symbol, volume=self.cfg.volume, reason="MACD二次金叉")
                if self.cfg.cross_type == "death" and prev_hist > 0 and curr_hist < 0:
                    return self.sell(self.cfg.symbol, volume=self.cfg.volume, reason="MACD二次死叉")

        # Divergence
        if self.cfg.cross_count == 3:
            return self._check_divergence(macd_df)

        return None

    def _check_divergence(self, macd_df: Any) -> list[Signal] | None:
        window = self.cfg.divergence_window

        provider = self.ctx.data_provider
        if provider is None:
            return None
        try:
            df = provider.get_kline_dataframe([self.cfg.symbol], self.cfg.period, window + 10)
            if df.empty:
                return None
        except Exception:
            return None

        # Flatten multi-index if present
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(self.cfg.symbol, level="symbol")

        highs = df["high"].values[-window:]
        lows = df["low"].values[-window:]
        difs = macd_df["dif"].values[-window:]

        # Top divergence: price new high, DIF lower high
        if highs[-1] >= highs[:-1].max() and difs[-1] < difs[:-1].max():
            if self.cfg.cross_type == "death":
                return self.sell(self.cfg.symbol, volume=self.cfg.volume, reason="MACD顶背离")

        # Bottom divergence: price new low, DIF higher low
        if lows[-1] <= lows[:-1].min() and difs[-1] > difs[:-1].min():
            if self.cfg.cross_type == "golden":
                return self.buy(self.cfg.symbol, volume=self.cfg.volume, reason="MACD底背离")

        return None


import pandas as pd  # noqa: E402
