"""Position sizer base class and built-in sizing algorithms.

Position sizing is the bridge between a strategy's trading Signal
and a concrete OrderRequest with exact volume/amount. It determines
HOW MUCH to trade based on portfolio state and risk preferences.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from quant_framework.core.constants import OrderDirection, SignalDirection
from quant_framework.core.types import Symbol
from quant_framework.engine.context import PortfolioSnapshot, StrategyContext
from quant_framework.execution.order import OrderRequest
from quant_framework.strategy.signals import Signal


class BasePositionSizer(ABC):
    """Abstract base class for position sizing algorithms.

    Subclasses implement size() to convert a Signal into an OrderRequest
    with concrete volume/amount.

    The sizer receives:
    - signal: The strategy's trading intent
    - portfolio: Current portfolio state (cash, positions, equity)
    - ctx: Strategy context (for accessing market data, config, etc.)

    And returns an OrderRequest with filled-in volume/amount.
    """

    @abstractmethod
    def size(
        self,
        signal: Signal,
        portfolio: PortfolioSnapshot,
        ctx: StrategyContext,
    ) -> OrderRequest:
        """Convert a signal into a sized OrderRequest.

        Args:
            signal: Trading signal from strategy.
            portfolio: Current portfolio snapshot.
            ctx: Strategy runtime context.

        Returns:
            OrderRequest with concrete volume/amount.
        """

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ======================================================================
# Built-in Position Sizers
# ======================================================================


class FixedRatioSizer(BasePositionSizer):
    """Fixed-fraction position sizing.

    Each trade uses a fixed percentage of total equity.
    Example: ratio=0.10 means each buy order uses 10% of equity.

    This is the simplest and most commonly used sizer.
    """

    def __init__(self, ratio: float = 0.10) -> None:
        """
        Args:
            ratio: Fraction of total equity to allocate per trade (0.0-1.0).
        """
        if ratio <= 0 or ratio > 1.0:
            raise ValueError("ratio must be in (0, 1.0]")
        self.ratio = ratio

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        available = portfolio.total_equity * self.ratio
        symbol = signal.symbol

        if signal.direction in (SignalDirection.BUY,):
            # Determine price for volume calculation
            price = signal.price or self._get_current_price(symbol, ctx)
            if price and price > 0:
                # A-share: lot size 100 shares
                volume = int(available / price / 100) * 100
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.BUY,
                    price=signal.price,
                    volume=volume,
                    amount=volume * price,
                    reason=signal.reason,
                    metadata=signal.metadata,
                )
            else:
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.BUY,
                    amount=available,
                    position_pct=self.ratio,
                    reason=signal.reason,
                )

        elif signal.direction in (SignalDirection.SELL, SignalDirection.CLOSE):
            pos = portfolio.positions.get(symbol)
            if pos and pos.available > 0:
                sell_pct = signal.metadata.get("position_pct", 1.0)
                volume = int(pos.available * sell_pct / 100) * 100
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=max(volume, 100),  # At least 1 lot
                    reason=signal.reason,
                    metadata=signal.metadata,
                )

        # HOLD — no action
        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )

    @staticmethod
    def _get_current_price(symbol: str, ctx: StrategyContext) -> float:
        """Get current price from context or position."""
        pos = ctx.portfolio.positions.get(symbol)
        if pos and pos.current_price > 0:
            return pos.current_price
        return 0.0


class KellySizer(BasePositionSizer):
    """Kelly Criterion position sizing.

    f* = (bp - q) / b = (win_prob * (profit_loss_ratio + 1) - 1) / profit_loss_ratio

    Where:
        b = profit_loss_ratio (average win / average loss)
        p = win_rate (probability of winning)
        q = 1 - p (probability of losing)

    Usually a fraction of full Kelly is used (e.g., half-Kelly = 0.5)
    to reduce volatility and account for estimation error.
    """

    def __init__(
        self,
        win_rate: float = 0.50,
        profit_loss_ratio: float = 2.0,
        fraction: float = 0.5,
        max_ratio: float = 0.25,
    ) -> None:
        """
        Args:
            win_rate: Estimated probability of winning (0.0-1.0).
            profit_loss_ratio: Ratio of avg_win / avg_loss.
            fraction: Kelly fraction (0.5 = half-Kelly). Lower = more conservative.
            max_ratio: Cap on position size as fraction of equity.
        """
        self.win_rate = win_rate
        self.profit_loss_ratio = profit_loss_ratio
        self.fraction = fraction
        self.max_ratio = max_ratio

    @property
    def kelly_pct(self) -> float:
        """Full Kelly percentage."""
        p, b = self.win_rate, self.profit_loss_ratio
        q = 1 - p
        kelly = (b * p - q) / b if b > 0 else 0.0
        return max(kelly, 0.0)

    @property
    def effective_pct(self) -> float:
        """Effective position size = Kelly * fraction, capped at max_ratio."""
        return min(self.kelly_pct * self.fraction, self.max_ratio)

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        pct = self.effective_pct
        if pct <= 0:
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                direction=OrderDirection.BUY,
                volume=0,
                reason=f"Kelly pct={pct:.4f} <= 0",
            )

        available = portfolio.total_equity * pct
        price = signal.price or FixedRatioSizer._get_current_price(signal.symbol, ctx)

        if signal.direction == SignalDirection.BUY:
            volume = int(available / price / 100) * 100 if price > 0 else 0
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                direction=OrderDirection.BUY,
                price=signal.price,
                volume=volume,
                amount=volume * price if price > 0 else available,
                reason=f"{signal.reason} (Kelly {pct:.2%})",
                metadata={"kelly_pct": self.kelly_pct, "effective_pct": pct, **signal.metadata},
            )
        elif signal.direction == SignalDirection.SELL:
            pos = portfolio.positions.get(signal.symbol)
            if pos and pos.available > 0:
                sell_vol = int(pos.available * pct / 100) * 100
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=max(sell_vol, 100),
                    reason=f"{signal.reason} (Kelly {pct:.2%})",
                )

        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )


class ATRDynamicSizer(BasePositionSizer):
    """Position sizing based on ATR (Average True Range).

    Position size = (equity * risk_per_trade) / (ATR * multiplier)

    Where:
    - risk_per_trade: Fraction of equity willing to risk per trade (e.g., 2% = 0.02)
    - atr: Average True Range of the symbol
    - multiplier: ATR multiplier for stop distance (e.g., 2 ATR stop)

    This is a volatility-adjusted position sizer that risks a fixed
    percentage of equity per trade regardless of volatility.
    """

    def __init__(
        self,
        atr_period: int = 14,
        risk_per_trade: float = 0.02,
        atr_multiplier: float = 2.0,
        max_ratio: float = 0.25,
    ) -> None:
        """
        Args:
            atr_period: Period for ATR calculation.
            risk_per_trade: Fraction of equity to risk per trade.
            atr_multiplier: Stop distance in ATR multiples.
            max_ratio: Maximum position as fraction of equity.
        """
        self.atr_period = atr_period
        self.risk_per_trade = risk_per_trade
        self.atr_multiplier = atr_multiplier
        self.max_ratio = max_ratio

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        symbol = signal.symbol
        price = signal.price or self._get_price(symbol, ctx)

        # Get ATR
        atr = self._get_atr(symbol, ctx)

        if price <= 0 or atr is None or atr <= 0:
            # Fallback: use fixed ratio
            ratio = min(self.risk_per_trade * 5, self.max_ratio)
            return FixedRatioSizer(ratio).size(signal, portfolio, ctx)

        # Risk-based sizing
        risk_amount = portfolio.total_equity * self.risk_per_trade
        stop_distance = atr * self.atr_multiplier
        position_size_shares = risk_amount / stop_distance

        # Cap at max_ratio
        max_shares_by_ratio = (portfolio.total_equity * self.max_ratio) / price
        position_size_shares = min(position_size_shares, max_shares_by_ratio)

        # Round to lot size (100 shares for A-share)
        volume = max(int(position_size_shares / 100) * 100, 100)

        if signal.direction == SignalDirection.BUY:
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=symbol,
                direction=OrderDirection.BUY,
                price=signal.price,
                volume=volume,
                amount=volume * price,
                reason=f"{signal.reason} (ATR={atr:.3f}, risk={self.risk_per_trade:.2%})",
                metadata={"atr": atr, "stop_distance": stop_distance, **signal.metadata},
            )
        elif signal.direction == SignalDirection.SELL:
            pos = portfolio.positions.get(symbol)
            if pos:
                sell_vol = min(volume, pos.available)
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=sell_vol,
                    reason=signal.reason,
                )

        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )

    def _get_price(self, symbol: str, ctx: StrategyContext) -> float:
        pos = ctx.portfolio.positions.get(symbol)
        return pos.current_price if pos else 0.0

    def _get_atr(self, symbol: str, ctx: StrategyContext) -> float | None:
        """Get current ATR value for a symbol."""
        provider = ctx.data_provider
        if provider is None:
            return None
        try:
            df = provider.get_kline_dataframe([symbol], "1d", self.atr_period + 5)
            if df.empty or "close" not in df.columns:
                return None
            from quant_framework.strategy.indicators import compute_atr
            atr_series = compute_atr(df, self.atr_period)
            return float(atr_series.iloc[-1]) if not atr_series.empty else None
        except Exception:
            return None


class EqualWeightSizer(BasePositionSizer):
    """Equal-weight position sizing.

    Splits available capital equally among all current signals.
    Each position gets the same dollar amount.
    """

    def __init__(self, max_positions: int = 10) -> None:
        self.max_positions = max_positions

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        # Count current + new positions
        current_count = len(portfolio.positions)
        if signal.symbol not in portfolio.positions:
            current_count += 1
        target_count = min(current_count, self.max_positions)

        allocation = portfolio.total_equity / max(target_count, 1)

        # Cap by available cash
        allocation = min(allocation, portfolio.cash * 0.95)  # 5% buffer

        price = signal.price or self._get_price(signal.symbol, ctx)

        if signal.direction == SignalDirection.BUY and price > 0:
            volume = int(allocation / price / 100) * 100
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=signal.symbol,
                direction=OrderDirection.BUY,
                price=signal.price,
                volume=volume,
                amount=volume * price,
                reason=f"{signal.reason} (equal weight, {target_count} positions)",
                metadata={"target_positions": target_count, **signal.metadata},
            )
        elif signal.direction == SignalDirection.SELL:
            pos = portfolio.positions.get(signal.symbol)
            if pos and pos.available > 0:
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=pos.available,
                    reason=signal.reason,
                )

        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=signal.symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )

    @staticmethod
    def _get_price(symbol: str, ctx: StrategyContext) -> float:
        pos = ctx.portfolio.positions.get(symbol)
        return pos.current_price if pos else 0.0


class RiskParitySizer(BasePositionSizer):
    """Risk parity position sizing.

    Allocates capital so that each position contributes equal risk
    to the portfolio. Risk contribution = position_value * volatility.
    Positions with higher volatility get smaller allocations.

    This is a simplified single-period risk parity — for a full
    implementation, use covariance-based risk budgeting.
    """

    def __init__(self, target_risk_pct: float = 0.05, max_ratio: float = 0.20) -> None:
        self.target_risk_pct = target_risk_pct
        self.max_ratio = max_ratio

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        symbol = signal.symbol
        price = signal.price or self._get_price(symbol, ctx)
        volatility = self._estimate_volatility(symbol, ctx)

        if price <= 0 or volatility is None or volatility <= 0:
            return FixedRatioSizer(0.10).size(signal, portfolio, ctx)

        # Risk parity: position_value * vol = target_risk * equity
        target_risk_amount = portfolio.total_equity * self.target_risk_pct
        position_value = target_risk_amount / volatility

        # Cap
        max_value = portfolio.total_equity * self.max_ratio
        position_value = min(position_value, max_value)
        position_value = min(position_value, portfolio.cash * 0.95)

        if signal.direction == SignalDirection.BUY:
            volume = int(position_value / price / 100) * 100
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=symbol,
                direction=OrderDirection.BUY,
                price=signal.price,
                volume=volume,
                amount=volume * price,
                reason=f"{signal.reason} (risk parity, vol={volatility:.3f})",
                metadata={"volatility": volatility, **signal.metadata},
            )
        elif signal.direction == SignalDirection.SELL:
            pos = portfolio.positions.get(symbol)
            if pos and pos.available > 0:
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=pos.available,
                    reason=signal.reason,
                )

        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )

    def _get_price(self, symbol: str, ctx: StrategyContext) -> float:
        pos = ctx.portfolio.positions.get(symbol)
        return pos.current_price if pos else 0.0

    def _estimate_volatility(self, symbol: str, ctx: StrategyContext, window: int = 20) -> float | None:
        """Estimate annualized volatility from daily returns."""
        provider = ctx.data_provider
        if provider is None:
            return None
        try:
            df = provider.get_kline_dataframe([symbol], "1d", window + 5)
            if df.empty or "close" not in df.columns:
                return None
            returns = df["close"].pct_change().dropna()
            if len(returns) < 2:
                return None
            # Annualized volatility (assuming 252 trading days)
            return float(returns.std() * np.sqrt(252))
        except Exception:
            return None


class TargetVolSizer(BasePositionSizer):
    """Target volatility position sizing.

    Adjusts position size to target a specific portfolio volatility.
    Position = target_vol / asset_vol * equity
    """

    def __init__(self, target_annual_vol: float = 0.15, max_leverage: float = 1.0) -> None:
        self.target_annual_vol = target_annual_vol  # e.g., 15% annual vol
        self.max_leverage = max_leverage

    def size(
        self, signal: Signal, portfolio: PortfolioSnapshot, ctx: StrategyContext
    ) -> OrderRequest:
        symbol = signal.symbol
        price = signal.price or self._get_price(symbol, ctx)
        vol = self._estimate_volatility(symbol, ctx)

        if price <= 0 or vol is None or vol <= 0:
            return FixedRatioSizer(0.10).size(signal, portfolio, ctx)

        # Target vol scaling
        scaling = min(self.target_annual_vol / vol, self.max_leverage)
        position_value = portfolio.total_equity * scaling

        if signal.direction == SignalDirection.BUY:
            volume = int(position_value / price / 100) * 100
            return OrderRequest(
                strategy_id=signal.strategy_id,
                symbol=symbol,
                direction=OrderDirection.BUY,
                price=signal.price,
                volume=volume,
                amount=volume * price,
                reason=f"{signal.reason} (target vol={self.target_annual_vol:.1%})",
            )

        elif signal.direction == SignalDirection.SELL:
            pos = portfolio.positions.get(symbol)
            if pos and pos.available > 0:
                return OrderRequest(
                    strategy_id=signal.strategy_id,
                    symbol=symbol,
                    direction=OrderDirection.SELL,
                    price=signal.price,
                    volume=pos.available,
                    reason=signal.reason,
                )

        return OrderRequest(
            strategy_id=signal.strategy_id,
            symbol=symbol,
            direction=OrderDirection.BUY,
            volume=0,
            reason="HOLD",
        )

    def _get_price(self, symbol: str, ctx: StrategyContext) -> float:
        pos = ctx.portfolio.positions.get(symbol)
        return pos.current_price if pos else 0.0

    def _estimate_volatility(self, symbol: str, ctx: StrategyContext, window: int = 20) -> float | None:
        """Estimate annualized volatility."""
        provider = ctx.data_provider
        if provider is None:
            return None
        try:
            df = provider.get_kline_dataframe([symbol], "1d", window + 5)
            if df.empty or "close" not in df.columns:
                return None
            returns = df["close"].pct_change().dropna()
            if len(returns) < 2:
                return None
            return float(returns.std() * np.sqrt(252))
        except Exception:
            return None
