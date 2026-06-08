"""Performance metrics calculation.

Computes standard quantitative finance metrics from an equity curve
and trade history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant_framework.execution.order import Trade


@dataclass
class PerformanceMetrics:
    """Comprehensive performance metrics for a backtest or live strategy."""

    # Returns
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0

    # Risk-adjusted returns
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    information_ratio: float = 0.0

    # Drawdown
    max_drawdown: float = 0.0
    max_drawdown_duration: int = 0      # Days

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    profit_loss_ratio: float = 0.0      # avg_win / avg_loss
    avg_trade_return: float = 0.0       # Average return per trade
    best_trade: float = 0.0
    worst_trade: float = 0.0

    # Risk
    var_95: float = 0.0                 # 95% Value at Risk (daily)
    cvar_95: float = 0.0                # 95% Conditional VaR
    max_leverage: float = 0.0

    # Other
    total_commission: float = 0.0
    start_date: str = ""
    end_date: str = ""
    total_days: int = 0


def compute_metrics(
    equity_curve: pd.Series,
    trades: list[Trade] | None = None,
    daily_returns: pd.Series | None = None,
    benchmark_returns: pd.Series | None = None,
    risk_free_rate: float = 0.03,
) -> PerformanceMetrics:
    """Compute full performance metrics from backtest results.

    Args:
        equity_curve: Time-indexed Series of portfolio equity.
        trades: List of all trades executed.
        daily_returns: Pre-computed daily returns (computed if not provided).
        benchmark_returns: Benchmark daily returns for IR calculation.
        risk_free_rate: Annual risk-free rate (default 3%).

    Returns:
        PerformanceMetrics with all calculated values.
    """
    if equity_curve.empty:
        return PerformanceMetrics()

    # Daily returns
    if daily_returns is None:
        daily = equity_curve.resample("D").last().dropna()
        daily_returns = daily.pct_change().dropna()

    if daily_returns.empty:
        return PerformanceMetrics()

    metrics = PerformanceMetrics()

    # Basic info
    metrics.start_date = str(equity_curve.index[0].date()) if hasattr(equity_curve.index[0], "date") else str(equity_curve.index[0])
    metrics.end_date = str(equity_curve.index[-1].date()) if hasattr(equity_curve.index[-1], "date") else str(equity_curve.index[-1])
    metrics.total_days = len(daily_returns)

    # Total & Annual Return
    if len(equity_curve) > 1:
        metrics.total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1

    trading_days = len(daily_returns)
    if trading_days > 0:
        years = trading_days / 252
        if years > 0 and len(equity_curve) > 1:
            metrics.annual_return = (1 + metrics.total_return) ** (1 / years) - 1

    # Volatility
    metrics.annual_volatility = float(daily_returns.std() * np.sqrt(252))

    # Sharpe Ratio
    excess = daily_returns - risk_free_rate / 252
    if metrics.annual_volatility > 0:
        metrics.sharpe_ratio = float(excess.mean() / daily_returns.std() * np.sqrt(252))

    # Sortino Ratio (downside deviation only)
    downside = daily_returns[daily_returns < 0]
    downside_std = downside.std() if len(downside) > 0 else 0.0
    if downside_std > 0:
        metrics.sortino_ratio = float(excess.mean() / downside_std * np.sqrt(252))

    # Max Drawdown & Duration
    peak = equity_curve.expanding().max()
    drawdown = (equity_curve - peak) / peak
    metrics.max_drawdown = float(drawdown.min())

    # Drawdown duration (longest consecutive period in drawdown)
    in_drawdown = (equity_curve < peak).astype(int)
    if len(in_drawdown) > 0:
        # Count consecutive periods in drawdown
        groups = (in_drawdown.diff() != 0).cumsum()
        drawdown_durations = in_drawdown.groupby(groups).sum()
        if len(drawdown_durations) > 0:
            metrics.max_drawdown_duration = int(drawdown_durations.max())

    # Calmar Ratio
    if abs(metrics.max_drawdown) > 1e-9:
        metrics.calmar_ratio = metrics.annual_return / abs(metrics.max_drawdown)

    # Information Ratio (vs benchmark)
    if benchmark_returns is not None and len(benchmark_returns) > 0:
        aligned = pd.concat([daily_returns, benchmark_returns], axis=1).dropna()
        if len(aligned) > 1:
            tracking_error = (aligned.iloc[:, 0] - aligned.iloc[:, 1]).std()
            if tracking_error > 0:
                metrics.information_ratio = float(
                    (aligned.iloc[:, 0].mean() - aligned.iloc[:, 1].mean()) / tracking_error * np.sqrt(252)
                )

    # VaR & CVaR
    if len(daily_returns) > 0:
        metrics.var_95 = float(np.percentile(daily_returns, 5))
        metrics.cvar_95 = float(daily_returns[daily_returns <= metrics.var_95].mean())

    # Trade statistics
    if trades and len(trades) > 0:
        metrics.total_trades = len(trades)
        metrics.total_commission = sum(t.commission for t in trades)

        # Calculate trade returns (approximate from trade prices)
        trade_returns: list[float] = []
        for trade in trades:
            if trade.volume > 0 and trade.price > 0:
                # Use commission as cost impact
                trade_returns.append(-trade.commission / (trade.price * trade.volume))

        if trade_returns:
            wins = [r for r in trade_returns if r > 0]
            losses = [r for r in trade_returns if r < 0]
            metrics.winning_trades = len(wins)
            metrics.losing_trades = len(losses)
            metrics.win_rate = len(wins) / len(trade_returns) if trade_returns else 0.0

            avg_win = np.mean(wins) if wins else 0.0
            avg_loss = abs(np.mean(losses)) if losses else 0.0
            metrics.profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0
            metrics.avg_trade_return = float(np.mean(trade_returns))
            metrics.best_trade = float(max(trade_returns))
            metrics.worst_trade = float(min(trade_returns))

    return metrics
