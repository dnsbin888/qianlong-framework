#!/usr/bin/env python
"""
组合回测引擎 — 多股票 × 多策略 同时运行
======================================
用法:
    python scripts/portfolio_backtest.py                    # 跑默认组合(沪深300+MACD最优参数)
    python scripts/portfolio_backtest.py --strategy macd_cross --top 20
    python scripts/portfolio_backtest.py --report           # 查看最近报告
"""
import sys, os, json, warnings
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Any
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

from optimizer import (
    grid_search, run_backtest, STRATEGIES,
    BacktestResult, OptimizationReport,
)

DATA_DIR = Path(__file__).parent.parent / "data" / "market"
RESULTS_DIR = Path(__file__).parent.parent / "data" / "portfolio_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 1. 组合回测核心
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class PortfolioResult:
    """组合级别回测结果。"""
    strategy: str
    num_stocks: int
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    avg_trades_per_stock: float
    best_stock: str
    worst_stock: str
    stock_results: list[dict] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)


def portfolio_backtest(strategy_name: str, symbols: list[str],
                       initial_cash: float = 1_000_000.0,
                       per_stock_capital: float | None = None,
                       optimize: bool = True) -> PortfolioResult:
    """
    多股票组合回测。

    为每只股票：
    1. 加载数据
    2. (可选) 跑参数优化找最优参数
    3. 用最优参数跑回测
    4. 聚合所有股票结果

    Args:
        strategy_name: 策略名 (macd_cross/grid_trading/ma_condition)
        symbols: 股票列表
        initial_cash: 总资金
        per_stock_capital: 每只股票分配资金 (默认平均分配)
        optimize: 是否对每只股票做参数优化
    """
    if per_stock_capital is None:
        per_stock_capital = initial_cash / len(symbols)

    stock_results = []
    equity_curves = {}

    print(f"\n{'='*70}")
    print(f"  组合回测 — {strategy_name} ({len(symbols)} stocks)")
    print(f"{'='*70}")

    for i, sym in enumerate(symbols):
        path = DATA_DIR / sym / "1d.csv"
        if not path.exists():
            continue

        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) < 200:
                continue

            # 参数优化
            params = {}
            if optimize:
                all_results = grid_search(df, strategy_name)
                all_results.sort(key=lambda r: r.sharpe, reverse=True)
                best = all_results[0]
                params = best.params
            else:
                # 用默认参数
                st = STRATEGIES[strategy_name]
                params = {k: v[0] for k, v in st["param_grid"].items()}
                # fast 必须小于 slow
                if "fast" in params and "slow" in params and params["fast"] >= params["slow"]:
                    params["slow"] = params["fast"] + 16

            # 回测
            result = run_backtest(df, strategy_name, params,
                                  initial_cash=per_stock_capital,
                                  max_position_pct=0.95)

            stock_results.append({
                "symbol": sym, "params": params,
                "return": result.total_return,
                "sharpe": result.sharpe,
                "max_drawdown": result.max_drawdown,
                "win_rate": result.win_rate,
                "trades": result.total_trades,
            })

            # 保存净值曲线
            if result.equity_curve:
                equity_curves[sym] = np.array(result.equity_curve) / per_stock_capital - 1

            if (i + 1) % 20 == 0 or (i + 1) == len(symbols):
                avg_ret = np.mean([r["return"] for r in stock_results])
                avg_sharpe = np.mean([r["sharpe"] for r in stock_results])
                print(f"  [{i+1}/{len(symbols)}] avg_return={avg_ret:+.2f}%  avg_sharpe={avg_sharpe:.3f}")

        except Exception as e:
            print(f"  [{sym}] error: {e}")
            continue

    if not stock_results:
        return PortfolioResult(strategy=strategy_name, num_stocks=0,
                               total_return=0, annual_return=0, sharpe=0,
                               max_drawdown=0, win_rate=0,
                               avg_trades_per_stock=0, best_stock="", worst_stock="")

    # 按收益排序
    stock_results.sort(key=lambda r: r["return"], reverse=True)

    # 组合级别指标
    returns = [r["return"] for r in stock_results]
    sharpes = [r["sharpe"] for r in stock_results]
    mds = [r["max_drawdown"] for r in stock_results]
    wrs = [r["win_rate"] for r in stock_results]
    trades = [r["trades"] for r in stock_results]

    # 组合净值曲线 (等权) — 对齐不同长度
    if equity_curves:
        max_len = max(len(c) for c in equity_curves.values())
        padded = []
        for c in equity_curves.values():
            if len(c) < max_len:
                # Pad with last value
                p = np.full(max_len, c[-1])
                p[:len(c)] = c
                padded.append(p)
            else:
                padded.append(c[:max_len])
        portfolio_curve = (np.mean(padded, axis=0) * 100).tolist()
    else:
        portfolio_curve = []

    return PortfolioResult(
        strategy=strategy_name,
        num_stocks=len(stock_results),
        total_return=round(np.mean(returns), 2),
        annual_return=round(np.mean(returns) / 3, 2),  # 假设3年
        sharpe=round(np.mean(sharpes), 3),
        max_drawdown=round(np.mean(mds), 2),
        win_rate=round(np.mean(wrs), 1),
        avg_trades_per_stock=round(np.mean(trades), 1),
        best_stock=f"{stock_results[0]['symbol']} (+{stock_results[0]['return']:.1f}%)",
        worst_stock=f"{stock_results[-1]['symbol']} ({stock_results[-1]['return']:+.1f}%)",
        stock_results=stock_results,
        equity_curve=portfolio_curve,
    )


# ═══════════════════════════════════════════════════════════════════════
# 2. 报告输出
# ═══════════════════════════════════════════════════════════════════════

def print_portfolio_report(result: PortfolioResult):
    """美化输出组合报告。"""
    print(f"\n{'='*70}")
    print(f"  组合回测报告 — {result.strategy}")
    print(f"{'='*70}")
    print(f"  股票数量:     {result.num_stocks}")
    print(f"  平均收益:     {result.total_return:>+10.2f}%")
    print(f"  平均Sharpe:   {result.sharpe:>10.3f}")
    print(f"  平均最大回撤: {result.max_drawdown:>10.2f}%")
    print(f"  平均胜率:     {result.win_rate:>10.1f}%")
    print(f"  平均交易次数: {result.avg_trades_per_stock:>10.1f}")

    # 收益分布
    returns = [r["return"] for r in result.stock_results]
    positive = sum(1 for r in returns if r > 0)
    print(f"\n  正收益股票:   {positive}/{len(returns)} ({positive/len(returns)*100:.1f}%)")
    print(f"  最佳: {result.best_stock}")
    print(f"  最差: {result.worst_stock}")

    # 收益分布直方图
    print(f"\n  收益分布:")
    bins = [(-100, -20), (-20, -10), (-10, 0), (0, 10), (10, 20), (20, 100)]
    for lo, hi in bins:
        count = sum(1 for r in returns if lo <= r < hi)
        bar = "#" * max(1, count * 3)
        print(f"  {lo:>+5.0f}% ~ {hi:>+5.0f}%: {bar} ({count})")

    # Top/Bottom 10
    if len(result.stock_results) >= 10:
        print(f"\n  Top 10:")
        for r in result.stock_results[:10]:
            p = ", ".join(f"{k}={v}" for k, v in r["params"].items())
            print(f"    {r['symbol']}: Return={r['return']:+.2f}%  "
                  f"Sharpe={r['sharpe']:.3f}  MaxDD={r['max_drawdown']:.1f}%  "
                  f"Trades={r['trades']}  [{p}]")

        print(f"\n  Bottom 10:")
        for r in result.stock_results[-10:]:
            print(f"    {r['symbol']}: Return={r['return']:+.2f}%  "
                  f"Sharpe={r['sharpe']:.3f}  MaxDD={r['max_drawdown']:.1f}%")


# ═══════════════════════════════════════════════════════════════════════
# 3. CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="组合回测引擎")
    parser.add_argument("--strategy", "-s", default="macd_cross",
                        choices=list(STRATEGIES.keys()))
    parser.add_argument("--top", type=int, default=20, help="回测前N只股票")
    parser.add_argument("--capital", type=float, default=1_000_000.0, help="总资金")
    parser.add_argument("--no-optimize", action="store_true", help="跳过参数优化(快速模式)")
    parser.add_argument("--report", action="store_true", help="查看最近报告")
    parser.add_argument("--output", "-o", default="", help="保存JSON报告")

    args = parser.parse_args()

    if args.report:
        reports = sorted(RESULTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        if reports:
            data = json.loads(reports[0].read_text())
            print(f"\n最近组合报告: {reports[0].name}")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print("暂无组合回测报告")
        return

    # 获取可用股票列表
    if DATA_DIR.exists():
        symbols = sorted([d.name for d in DATA_DIR.iterdir()
                          if d.is_dir() and len(d.name) == 6 and
                          (DATA_DIR / d.name / "1d.csv").exists()])
    else:
        print("无数据，请先运行: python scripts/data_pipeline.py download")
        return

    if not symbols:
        print("无可用数据")
        return

    symbols = symbols[:args.top]
    print(f"可用股票: {len(symbols)} 只 (取前 {args.top} 只)")

    result = portfolio_backtest(
        args.strategy, symbols,
        initial_cash=args.capital,
        optimize=not args.no_optimize,
    )

    print_portfolio_report(result)

    # 保存
    out_path = args.output or str(
        RESULTS_DIR / f"portfolio_{args.strategy}_{datetime.now():%Y%m%d_%H%M}.json"
    )
    Path(out_path).write_text(json.dumps({
        "strategy": result.strategy,
        "num_stocks": result.num_stocks,
        "total_return": result.total_return,
        "annual_return": result.annual_return,
        "sharpe": result.sharpe,
        "max_drawdown": result.max_drawdown,
        "win_rate": result.win_rate,
        "best_stock": result.best_stock,
        "worst_stock": result.worst_stock,
        "stock_results": result.stock_results,
    }, indent=2, ensure_ascii=False))
    print(f"\n报告已保存: {out_path}")


if __name__ == "__main__":
    main()
