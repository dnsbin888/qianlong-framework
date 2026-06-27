"""Walk-Forward 前向推进回测运行器.

在滚动窗口中回测多个策略，对比各策略在近期行情中的表现，
辅助判断"当下哪个策略最赚钱"。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_framework.backtest.engine import quick_backtest

logger = logging.getLogger("quant_framework.backtest.walk_forward")


@dataclass
class WindowResult:
    """单窗口回测结果."""
    strategy_name: str = ""
    window_start: str = ""
    window_end: str = ""
    symbol: str = ""
    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0


@dataclass
class ComparisonReport:
    """多策略对比报告."""
    symbol: str = ""
    window_days: int = 30
    results: list[WindowResult] = field(default_factory=list)

    def rank_by_sharpe(self) -> list[WindowResult]:
        """按夏普比率降序排序."""
        return sorted(self.results, key=lambda r: r.sharpe_ratio, reverse=True)

    def rank_by_return(self) -> list[WindowResult]:
        """按总收益降序排序."""
        return sorted(self.results, key=lambda r: r.total_return, reverse=True)

    def best_strategy(self) -> WindowResult | None:
        """返回夏普比率最高的策略."""
        ranked = self.rank_by_sharpe()
        return ranked[0] if ranked else None

    def summary(self) -> str:
        """打印对比摘要."""
        lines: list[str] = [
            "=" * 60,
            f"  Walk-Forward 对比报告 | {self.symbol} | 窗口={self.window_days}天",
            "=" * 60,
        ]
        ranked = self.rank_by_sharpe()
        for i, r in enumerate(ranked, 1):
            arrow = "←" if i == 1 else " "
            lines.append(
                f"  #{i} {arrow} {r.strategy_name:<20} "
                f"收益={r.total_return:>8.2%}  "
                f"夏普={r.sharpe_ratio:>6.2f}  "
                f"回撤={r.max_drawdown:>8.2%}  "
                f"胜率={r.win_rate:>6.2%}  "
                f"交易={r.total_trades:>3d}"
            )
        lines.append("=" * 60)
        return "\n".join(lines)


class WalkForwardRunner:
    """Walk-Forward 回测运行器.

    用法::

        runner = WalkForwardRunner()
        report = runner.run_comparison(
            strategy_names=["ma_cross", "macd_cross", "ma_condition"],
            symbol="600000",
            start_date="2025-01-01",
            end_date="2025-06-01",
            window_days=30,
        )
        print(report.summary())
        best = report.best_strategy()
    """

    def run_comparison(
        self,
        strategy_names: list[str],
        symbol: str,
        start_date: str = "2025-01-01",
        end_date: str = "",
        window_days: int = 30,
    ) -> ComparisonReport:
        """对多个策略在指定区间内运行回测对比.

        Args:
            strategy_names: 策略注册名列表
            symbol: 股票代码
            start_date: 回测开始日期
            end_date: 回测结束日期 (空=至今)
            window_days: 窗口天数 (用于报告标注)

        Returns:
            ComparisonReport 对比报告

        防死循环: 数据不足时跳过该策略，记录 Warning。
        """
        report = ComparisonReport(symbol=symbol, window_days=window_days)

        for name in strategy_names:
            logger.info(f"Walk-Forward 回测: {name} @ {symbol}")

            try:
                result: dict[str, Any] = quick_backtest(
                    strategy_name=name,
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                )
            except Exception as e:
                logger.warning(f"策略 '{name}' 回测异常: {e}")
                continue

            if not result:
                logger.warning(f"策略 '{name}' 回测无结果，跳过")
                continue

            report.results.append(WindowResult(
                strategy_name=name,
                window_start=start_date,
                window_end=result.get("end_date", end_date or datetime.now().strftime("%Y-%m-%d")),
                symbol=symbol,
                total_return=result.get("total_return", 0.0),
                annual_return=result.get("annual_return", 0.0),
                sharpe_ratio=result.get("sharpe_ratio", 0.0),
                max_drawdown=result.get("max_drawdown", 0.0),
                win_rate=result.get("win_rate", 0.0),
                total_trades=result.get("total_trades", 0),
            ))

        return report
