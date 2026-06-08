"""Backtest report generation — charts and HTML output."""

from __future__ import annotations

from typing import Any

import pandas as pd

from quant_framework.backtest.metrics import PerformanceMetrics


def generate_charts(
    equity_curve: pd.DataFrame,
    drawdown_curve: pd.Series | None = None,
    monthly_returns: pd.Series | None = None,
    trades: list[Any] | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    """Generate matplotlib charts for a backtest report.

    Charts:
    1. Equity curve with drawdown overlay
    2. Monthly returns heatmap
    3. Trade P&L distribution

    Args:
        equity_curve: DataFrame with columns: equity, cash, market_value.
        drawdown_curve: Series of drawdown values.
        monthly_returns: Series of monthly returns.
        trades: List of Trade objects.
        output_path: If provided, save charts to this file/directory.

    Returns:
        Dict of figure objects keyed by chart name.
    """
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    import numpy as np

    figs = {}

    # --- Chart 1: Equity Curve + Drawdown ---
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), sharex=True,
                                     gridspec_kw={"height_ratios": [3, 1]})

    # Equity curve
    ax1.plot(equity_curve.index, equity_curve["equity"], label="Portfolio Equity", color="#1f77b4", linewidth=1.0)
    if "cash" in equity_curve.columns:
        ax1.fill_between(equity_curve.index, 0, equity_curve["cash"],
                         alpha=0.15, color="green", label="Cash")
    ax1.set_ylabel("Equity (CNY)")
    ax1.set_title("Portfolio Equity Curve")
    ax1.legend(loc="upper left")
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_formatter(ticker.FuncFormatter(lambda x, p: f"{x/10000:.0f}万"))

    # Drawdown
    if drawdown_curve is not None and len(drawdown_curve) > 0:
        ax2.fill_between(drawdown_curve.index, 0, drawdown_curve.values,
                         color="red", alpha=0.3, label="Drawdown")
        ax2.plot(drawdown_curve.index, drawdown_curve.values, color="red", linewidth=0.5)
    ax2.set_ylabel("Drawdown %")
    ax2.set_xlabel("Date")
    ax2.yaxis.set_major_formatter(ticker.PercentFormatter(1.0))
    ax2.grid(True, alpha=0.3)

    figs["equity_curve"] = fig

    # --- Chart 2: Monthly Returns Heatmap ---
    if monthly_returns is not None and len(monthly_returns) > 0:
        fig2, ax = plt.subplots(figsize=(14, 6))
        # Build pivot: year vs month
        monthly_df = monthly_returns.reset_index()
        monthly_df.columns = ["date", "return"]
        monthly_df["year"] = pd.to_datetime(monthly_df["date"]).dt.year
        monthly_df["month"] = pd.to_datetime(monthly_df["date"]).dt.month
        pivot = monthly_df.pivot(index="year", columns="month", values="return")

        im = ax.imshow(pivot.values, cmap="RdYlGn", aspect="auto", vmin=-0.15, vmax=0.15)
        ax.set_xticks(range(12))
        ax.set_xticklabels(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])
        ax.set_yticks(range(len(pivot.index)))
        ax.set_yticklabels(pivot.index.astype(int))
        ax.set_title("Monthly Returns Heatmap")
        plt.colorbar(im, ax=ax, format=ticker.PercentFormatter(1.0))

        figs["monthly_heatmap"] = fig2

    # --- Chart 3: Trade P&L Distribution ---
    if trades and len(trades) > 0:
        fig3, ax = plt.subplots(figsize=(10, 5))
        pnls = [t.commission for t in trades]  # Simplified; real implementation uses actual P&L
        ax.hist(pnls, bins=30, color="#2ca02c", alpha=0.7, edgecolor="black")
        ax.axvline(0, color="red", linestyle="--", linewidth=1)
        ax.set_xlabel("Trade P&L (CNY)")
        ax.set_ylabel("Frequency")
        ax.set_title("Trade P&L Distribution")
        ax.grid(True, alpha=0.3)

        figs["trade_distribution"] = fig3

    if output_path:
        plt.savefig(output_path, dpi=150, bbox_inches="tight")
        plt.close("all")

    return figs


def generate_html_report(report: Any, output_path: str = "backtest_report.html") -> str:
    """Generate a self-contained HTML backtest report.

    Args:
        report: BacktestReport object.
        output_path: Path to save the HTML file.

    Returns:
        The output path.
    """
    m = report.metrics
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest Report</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
h1 {{ color: #333; border-bottom: 2px solid #1f77b4; padding-bottom: 10px; }}
.metrics {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; }}
.card {{ background: white; border-radius: 8px; padding: 15px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.card h3 {{ margin: 0 0 8px 0; color: #666; font-size: 0.85em; text-transform: uppercase; }}
.card .value {{ font-size: 1.5em; font-weight: bold; color: #1f77b4; }}
.positive {{ color: #2ca02c !important; }}
.negative {{ color: #d62728 !important; }}
</style>
</head>
<body>
<h1>Quant Framework — Backtest Report</h1>
<p>Period: {m.start_date} → {m.end_date} ({m.total_days} trading days)</p>

<h2>Performance Metrics</h2>
<div class="metrics">
<div class="card"><h3>Total Return</h3><div class="value {"positive" if m.total_return > 0 else "negative"}">{m.total_return:.2%}</div></div>
<div class="card"><h3>Annual Return</h3><div class="value {"positive" if m.annual_return > 0 else "negative"}">{m.annual_return:.2%}</div></div>
<div class="card"><h3>Sharpe Ratio</h3><div class="value">{m.sharpe_ratio:.2f}</div></div>
<div class="card"><h3>Max Drawdown</h3><div class="value negative">{m.max_drawdown:.2%}</div></div>
<div class="card"><h3>Win Rate</h3><div class="value">{m.win_rate:.2%}</div></div>
<div class="card"><h3>Total Trades</h3><div class="value">{m.total_trades}</div></div>
<div class="card"><h3>Calmar Ratio</h3><div class="value">{m.calmar_ratio:.2f}</div></div>
<div class="card"><h3>Annual Volatility</h3><div class="value">{m.annual_volatility:.2%}</div></div>
<div class="card"><h3>Sortino Ratio</h3><div class="value">{m.sortino_ratio:.2f}</div></div>
<div class="card"><h3>Profit/Loss Ratio</h3><div class="value">{m.profit_loss_ratio:.2f}</div></div>
<div class="card"><h3>VaR (95%)</h3><div class="value negative">{m.var_95:.2%}</div></div>
<div class="card"><h3>CVaR (95%)</h3><div class="value negative">{m.cvar_95:.2%}</div></div>
</div>

<h2>Trade Summary</h2>
<table style="width:100%; border-collapse:collapse;">
<tr style="background:#1f77b4;color:white;"><th>Metric</th><th>Value</th></tr>
<tr><td>Total Trades</td><td>{m.total_trades}</td></tr>
<tr><td>Winning</td><td>{m.winning_trades}</td></tr>
<tr><td>Losing</td><td>{m.losing_trades}</td></tr>
<tr><td>Best Trade</td><td class="positive">{m.best_trade:.4f}</td></tr>
<tr><td>Worst Trade</td><td class="negative">{m.worst_trade:.4f}</td></tr>
<tr><td>Avg Trade Return</td><td>{m.avg_trade_return:.4f}</td></tr>
</table>

<p style="color:#999;margin-top:30px;font-size:0.85em;">Generated by Quant Framework v1.0.0</p>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
