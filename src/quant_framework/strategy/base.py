"""Base Strategy — abstract class that all strategies must inherit from.

The BaseStrategy defines the lifecycle and callback interface that the
framework engine uses to drive strategy execution. Strategies override
specific callbacks (on_bar, on_quote, etc.) to implement their logic.

Lifecycle:
    __init__ -> on_init -> [on_bar/on_quote/on_tick loop] -> on_stop
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

import pandas as pd

from quant_framework.core.constants import OrderDirection, SignalDirection
from quant_framework.core.types import JsonDict, Symbol
from quant_framework.data.models import Bar, Quote
from quant_framework.engine.context import StrategyContext
from quant_framework.strategy.indicators import (
    IndicatorCache,
    compute_atr,
    compute_bollinger,
    compute_ema,
    compute_kdj,
    compute_macd,
    compute_rsi,
    compute_sma,
)
from quant_framework.strategy.signals import Signal

logger = logging.getLogger("quant_framework.strategy")


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies.

    Subclasses must implement at least one of the market data callbacks:
    - on_bar(bar) -> list[Signal] | None
    - on_quote(quote) -> list[Signal] | None
    - on_tick(quote) -> list[Signal] | None

    Convenience buy()/sell() methods generate OrderRequests that flow
    through the RiskEngine → PositionSizer → Broker pipeline.

    Attributes:
        ctx: StrategyContext providing framework services.
        _indicator_cache: Per-strategy indicator result cache.
    """

    def __init__(self, ctx: StrategyContext, symbols: list[str] | None = None) -> None:
        """Initialize strategy with its runtime context.

        Args:
            ctx: StrategyContext from the engine.
            symbols: Optional list of symbols this strategy trades.
                     If not provided, strategies can define their own universe.
        """
        self.ctx = ctx
        self._indicator_cache = IndicatorCache()
        self._started = False
        self._stopped = False
        self.symbols: list[str] = symbols or []

    # ==================================================================
    # Lifecycle callbacks
    # ==================================================================

    def on_init(self) -> None:
        """Called once during strategy initialization.

        Override to:
        - Subscribe to market data symbols
        - Load historical data for warm-up
        - Initialize internal state
        - Register custom indicators
        """
        pass

    def on_start(self) -> None:
        """Called when the engine starts feeding data.

        Override to perform actions right before trading begins.
        """
        self._started = True

    def on_stop(self) -> None:
        """Called when the engine is stopping.

        Override to:
        - Save strategy state for resumption
        - Close positions (if desired)
        - Release external resources
        """
        self._stopped = True

    # ==================================================================
    # Market data callbacks (override at least one)
    # ==================================================================

    def on_bar(self, bar: Bar) -> list[Signal] | None:
        """Called when a new K-line bar is completed.

        Override to implement bar-based strategies.

        Args:
            bar: The completed Bar (OHLCV for one period).

        Returns:
            List of Signal objects, or None if no action.
        """
        return None

    def on_quote(self, quote: Quote) -> list[Signal] | None:
        """Called on every real-time quote update.

        Override to implement tick/quote-based strategies.
        This is the primary callback for the polling engine.

        Args:
            quote: Latest Quote snapshot.

        Returns:
            List of Signal objects, or None if no action.
        """
        return None

    def on_tick(self, quote: Quote) -> list[Signal] | None:
        """Called on every tick (alias for on_quote for high-frequency).

        Override for tick-level strategies. By default delegates to on_quote.
        """
        return self.on_quote(quote)

    # ==================================================================
    # Order callbacks
    # ==================================================================

    def on_order_filled(self, order: Any) -> None:
        """Called when an order is fully filled.

        Args:
            order: The filled Order object.
        """
        pass

    def on_order_partially_filled(self, order: Any) -> None:
        """Called when an order is partially filled."""
        pass

    def on_order_rejected(self, order: Any, reason: str) -> None:
        """Called when an order is rejected (by risk engine or broker).

        Args:
            order: The rejected Order.
            reason: Why it was rejected.
        """
        pass

    def on_order_cancelled(self, order: Any) -> None:
        """Called when an order is cancelled."""
        pass

    # ==================================================================
    # Order convenience methods
    # ==================================================================

    def buy(
        self,
        symbol: str,
        price: float | None = None,
        volume: int | None = None,
        amount: float | None = None,
        position_pct: float | None = None,
        reason: str = "",
        **kwargs: Any,
    ) -> list[Signal]:
        """Create a BUY signal.

        Args:
            symbol: Security code.
            price: Limit price (None = market order).
            volume: Exact number of shares to buy.
            amount: Exact amount of cash to spend.
            position_pct: Fraction of available cash to use (0.0-1.0).
            reason: Human-readable reason for the trade.

        Returns:
            List containing a single BUY Signal.
        """
        signal = Signal(
            strategy_id=self.ctx.strategy_id,
            symbol=symbol,
            direction=SignalDirection.BUY,
            price=price,
            reason=reason or "manual buy",
            metadata={"volume": volume, "amount": amount, "position_pct": position_pct, **kwargs},
        )
        return [signal]

    def sell(
        self,
        symbol: str,
        price: float | None = None,
        volume: int | None = None,
        position_pct: float | None = None,
        reason: str = "",
        **kwargs: Any,
    ) -> list[Signal]:
        """Create a SELL signal.

        Args:
            symbol: Security code.
            price: Limit price (None = market order).
            volume: Exact number of shares to sell.
            position_pct: Fraction of current position to sell (0.0-1.0).
            reason: Human-readable reason.

        Returns:
            List containing a single SELL Signal.
        """
        signal = Signal(
            strategy_id=self.ctx.strategy_id,
            symbol=symbol,
            direction=SignalDirection.SELL,
            price=price,
            reason=reason or "manual sell",
            metadata={"volume": volume, "position_pct": position_pct, **kwargs},
        )
        return [signal]

    def close_position(
        self, symbol: str, reason: str = "", **kwargs: Any
    ) -> list[Signal]:
        """Create a CLOSE signal (close entire position).

        Args:
            symbol: Security code to close.
            reason: Human-readable reason.

        Returns:
            List containing a single CLOSE Signal.
        """
        signal = Signal(
            strategy_id=self.ctx.strategy_id,
            symbol=symbol,
            direction=SignalDirection.CLOSE,
            reason=reason or "close position",
            metadata=kwargs,
        )
        return [signal]

    # ==================================================================
    # Technical indicator helpers
    # ==================================================================

    def sma(self, symbol: str, period: int, lookback: int | None = None) -> pd.Series | None:
        """Simple Moving Average.

        Args:
            symbol: Security code.
            period: MA period.
            lookback: Number of bars to fetch (default: period * 2).

        Returns:
            Series of SMA values, or None if data insufficient.
        """
        lb = lookback or period * 2
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < period:
            return None
        return compute_sma(df, period)

    def ema(self, symbol: str, period: int, lookback: int | None = None) -> pd.Series | None:
        """Exponential Moving Average."""
        lb = lookback or period * 2
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < period:
            return None
        return compute_ema(df, period)

    def macd(
        self,
        symbol: str,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
        period: str = "1d",
        lookback: int | None = None,
    ) -> pd.DataFrame | None:
        """MACD indicator.

        Returns DataFrame with columns: dif, dea, hist.
        """
        lb = lookback or (slow + signal) * 3
        df = self._get_kline_df(symbol, lb, period)
        if df is None or len(df) < slow + signal:
            return None
        return compute_macd(df, fast, slow, signal)

    def rsi(self, symbol: str, period: int = 14, lookback: int | None = None) -> pd.Series | None:
        """Relative Strength Index."""
        lb = lookback or period * 3
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < period:
            return None
        return compute_rsi(df, period)

    def boll(
        self, symbol: str, period: int = 20, std: float = 2.0, lookback: int | None = None
    ) -> pd.DataFrame | None:
        """Bollinger Bands.

        Returns DataFrame with columns: middle, upper, lower, bandwidth, percent_b.
        """
        lb = lookback or period * 2
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < period:
            return None
        return compute_bollinger(df, period, std)

    def atr(self, symbol: str, period: int = 14, lookback: int | None = None) -> pd.Series | None:
        """Average True Range."""
        lb = lookback or period * 3
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < period:
            return None
        return compute_atr(df, period)

    def kdj(
        self, symbol: str, n: int = 9, m1: int = 3, m2: int = 3, lookback: int | None = None
    ) -> pd.DataFrame | None:
        """KDJ indicator.

        Returns DataFrame with columns: k, d, j.
        """
        lb = lookback or n * 3
        df = self._get_kline_df(symbol, lb)
        if df is None or len(df) < n:
            return None
        return compute_kdj(df, n, m1, m2)

    # ==================================================================
    # Internal helpers
    # ==================================================================

    def _get_kline_df(
        self, symbol: str, count: int, period: str = "1d"
    ) -> pd.DataFrame | None:
        """Fetch K-line data as a DataFrame via the data provider.

        Uses the strategy's data_provider (from ctx) to pull historical bars
        and convert to a DataFrame with columns: open, high, low, close, volume, amount.

        Args:
            symbol: Security code.
            count: Number of bars to fetch.
            period: Bar timeframe.

        Returns:
            DataFrame indexed by datetime, or None on failure.
        """
        provider = self.ctx.data_provider
        if provider is None:
            logger.warning("No data provider in context")
            return None

        try:
            return provider.get_kline_dataframe([symbol], period, count)
        except Exception as e:
            logger.error("Failed to get kline for %s: %s", symbol, e)
            return None

    @property
    def is_running(self) -> bool:
        """Whether the strategy is currently active."""
        return self._started and not self._stopped

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(id={self.ctx.strategy_id}, name={self.ctx.name})"
