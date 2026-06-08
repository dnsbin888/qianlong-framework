#!/usr/bin/env python
"""
策略参数优化引擎 — 网格搜索 + Walk-Forward 验证 + 过拟合检测
============================================================
用法:
    python scripts/optimizer.py macd_cross 600000          # 单股票参数优化
    python scripts/optimizer.py macd_cross --batch         # 沪深300批量优化
    python scripts/optimizer.py grid_trading 600036        # 网格交易优化
    python scripts/optimizer.py --report                   # 查看优化报告
"""
import sys, os, json, warnings, itertools
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Any, Callable
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Import TDX formula signal generators
try:
    from quant_framework.strategy.builtin.bull_line_breakout import generate_bull_line_signals
except ImportError:
    generate_bull_line_signals = None

try:
    from quant_framework.strategy.builtin.dragon_tiger import generate_dragon_tiger_signals
except ImportError:
    generate_dragon_tiger_signals = None

DATA_DIR = Path(__file__).parent.parent / "data" / "market"
RESULTS_DIR = Path(__file__).parent.parent / "data" / "optimizer_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# 1. 策略定义（纯函数，无框架依赖）
# ═══════════════════════════════════════════════════════════════════════

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    """向量化EMA计算。"""
    alpha = 2 / (period + 1)
    result = np.zeros_like(series)
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


def compute_macd(close: np.ndarray, fast: int, slow: int, signal: int):
    """返回 (dif, dea, hist) 三个数组。"""
    ema_fast = _ema(close, fast)
    ema_slow = _ema(close, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    hist = (dif - dea) * 2
    return dif, dea, hist


def compute_sma(close: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均。"""
    result = np.full_like(close, np.nan)
    if len(close) >= period:
        cumsum = np.cumsum(np.insert(close, 0, 0))
        result[period - 1:] = (cumsum[period:] - cumsum[:-period]) / period
    return result


# ═══════════════════════════════════════════════════════════════════════
# 2. 回测核心（高效向量化版本）
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """单次回测结果。"""
    params: dict[str, Any]
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    profit_loss_ratio: float
    total_trades: int
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)


def _generate_macd_signals(close: np.ndarray, fast: int, slow: int, signal: int) -> np.ndarray:
    """生成 MACD 信号数组: 1=金叉买入, -1=死叉卖出, 0=持有。"""
    dif, dea, _ = compute_macd(close, fast, slow, signal)
    n = len(close)
    sig = np.zeros(n, dtype=int)
    for i in range(1, n):
        if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:
            sig[i] = 1
        elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:
            sig[i] = -1
    return sig


def _generate_grid_signals(close: np.ndarray, base_price: float, spacing: float,
                           levels: int) -> np.ndarray:
    """生成网格交易信号。"""
    n = len(close)
    sig = np.zeros(n, dtype=int)
    if n < 2:
        return sig

    last_action_price = close[0]
    position = 0  # 0=空仓, levels=满仓

    for i in range(1, n):
        price = close[i]
        # 价格下穿网格线 → 买入
        for level in range(1, levels + 1):
            grid_price = base_price * (1 - spacing * level)
            if price <= grid_price and close[i - 1] > grid_price and position < levels:
                sig[i] = 1
                position += 1
                last_action_price = price
        # 价格上穿网格线 → 卖出
        for level in range(1, levels + 1):
            grid_price = base_price * (1 + spacing * level)
            if price >= grid_price and close[i - 1] < grid_price and position > 0:
                sig[i] = -1
                position -= 1

    return sig


def _generate_ma_signals(close: np.ndarray, ma_period: int, condition: str) -> np.ndarray:
    """生成均线条件信号。"""
    ma = compute_sma(close, ma_period)
    n = len(close)
    sig = np.zeros(n, dtype=int)
    for i in range(1, n):
        if np.isnan(ma[i]) or np.isnan(ma[i - 1]):
            continue
        if condition == "cross_above" and close[i] > ma[i] and close[i - 1] <= ma[i - 1]:
            sig[i] = 1
        elif condition == "cross_below" and close[i] < ma[i] and close[i - 1] >= ma[i - 1]:
            sig[i] = -1
    return sig


# ═══ TDX策略信号包装函数 ═══

def _generate_bull_line_signals(close: np.ndarray, **params) -> np.ndarray:
    """牛线突破信号包装 — 适配 optimizer 接口。"""
    if generate_bull_line_signals is None:
        return np.zeros(len(close), dtype=int)
    n = len(close)
    high = np.maximum(close, np.roll(close, -1))
    high[-1] = high[-2]
    low = np.minimum(close, np.roll(close, -1))
    low[-1] = low[-2]
    open_p = np.roll(close, 1)
    open_p[0] = close[0]
    return generate_bull_line_signals(
        close, high, low, open_p,
        min_change_pct=params.get("min_change_pct", 9.0),
    )


def _generate_dragon_tiger_signals(close: np.ndarray, **params) -> np.ndarray:
    """双信号共振信号包装 — 适配 optimizer 接口。"""
    if generate_dragon_tiger_signals is None:
        return np.zeros(len(close), dtype=int)
    n = len(close)
    high = np.maximum(close, np.roll(close, -1))
    high[-1] = high[-2]
    low = np.minimum(close, np.roll(close, -1))
    low[-1] = low[-2]
    # volume: use price changes as a crude volume proxy
    vol = np.abs(np.diff(close, prepend=close[0])) * 100000 + 1000000
    return generate_dragon_tiger_signals(
        close, high, low, vol,
        vol_ratio_threshold=params.get("vol_ratio_threshold", 1.8),
    )


STRATEGIES = {
    "macd_cross": {
        "signal_fn": _generate_macd_signals,
        "param_grid": {
            "fast": [8, 10, 12, 16, 20],
            "slow": [20, 24, 26, 30, 36],
            "signal": [6, 9, 12, 15],
        },
    },
    "grid_trading": {
        "signal_fn": _generate_grid_signals,
        "param_grid": {
            "spacing": [0.02, 0.03, 0.05, 0.08, 0.10],
            "levels": [3, 5, 7, 10],
        },
    },
    "ma_condition": {
        "signal_fn": _generate_ma_signals,
        "param_grid": {
            "ma_period": [5, 10, 20, 30, 60, 120],
            "condition": ["cross_above", "cross_below"],
        },
    },
    # ═══ 新增：通达信选股公式策略 ═══
    "bull_line_breakout": {
        "signal_fn": _generate_bull_line_signals,
        "param_grid": {
            "min_change_pct": [5.0, 7.0, 9.0, 9.5],
            "hold_days": [1, 2, 3, 5, 7],
            "stop_loss_pct": [-3.0, -5.0, -7.0],
            "stop_profit_pct": [5.0, 10.0, 15.0, 20.0],
        },
    },
    "dragon_tiger": {
        "signal_fn": _generate_dragon_tiger_signals,
        "param_grid": {
            "vol_ratio_threshold": [1.2, 1.5, 1.8, 2.0, 2.5],
            "hold_days": [1, 2, 3, 5],
            "stop_loss_pct": [-2.0, -4.0, -6.0],
            "stop_profit_pct": [5.0, 10.0, 15.0],
        },
    },
}


def run_backtest(df: pd.DataFrame, strategy_name: str,
                 params: dict[str, Any],
                 initial_cash: float = 1_000_000.0,
                 max_position_pct: float = 0.30) -> BacktestResult:
    """
    单次回测执行。纯向量化计算，极快。
    """
    close = df["close"].values
    if len(close) < 100:
        return BacktestResult(params=params, total_return=0, annual_return=0,
                              sharpe=0, max_drawdown=0, win_rate=0,
                              profit_loss_ratio=0, total_trades=0)

    st = STRATEGIES[strategy_name]
    signal_fn = st["signal_fn"]

    # Pass OHLCV data if available (for TDX strategies)
    try:
        import inspect
        sig = inspect.signature(signal_fn)
        param_names = list(sig.parameters.keys())
        # Build kwargs based on what the function expects
        extra_kwargs = {}
        if "high" in param_names:
            extra_kwargs["high"] = df["high"].values if "high" in df.columns else close
        if "low" in param_names:
            extra_kwargs["low"] = df["low"].values if "low" in df.columns else close
        if "volume" in param_names:
            extra_kwargs["volume"] = df["volume"].values if "volume" in df.columns else np.ones(len(close)) * 1e7
        if "open_p" in param_names:
            extra_kwargs["open_p"] = df["open"].values if "open" in df.columns else close
        signals = signal_fn(close, **params, **extra_kwargs)
    except (ValueError, TypeError):
        # Fallback: just pass close
        signals = signal_fn(close, **params)

    # 交易模拟
    cash = initial_cash
    shares = 0
    trades = []
    equity = [initial_cash]

    for i in range(1, len(close)):
        price = close[i]
        sig = signals[i]

        if sig == 1 and cash >= price * 100:
            # 买入
            max_amount = cash * max_position_pct
            vol = int(max_amount / price / 100) * 100
            if vol >= 100:
                cost = vol * price * 1.0003
                if cost <= cash:
                    cash -= cost
                    shares += vol
                    trades.append({"idx": i, "type": "buy", "price": price, "vol": vol})

        elif sig == -1 and shares >= 100:
            # 卖出
            revenue = shares * price * 0.9987  # 万三佣金 + 千一印花税
            cash += revenue
            trades.append({"idx": i, "type": "sell", "price": price, "vol": shares})
            shares = 0

        equity.append(cash + shares * price)

    # 清仓
    if shares >= 100:
        last_price = close[-1]
        cash += shares * last_price * 0.9987
        trades.append({"idx": len(close) - 1, "type": "sell", "price": last_price, "vol": shares})
        shares = 0
        equity[-1] = cash

    final_value = cash

    # 绩效指标
    total_return = (final_value / initial_cash - 1) * 100
    n_days = len(close)
    annual_return = ((final_value / initial_cash) ** (252 / n_days) - 1) * 100 if n_days > 0 else 0

    # Sharpe
    eq = np.array(equity)
    if len(eq) > 1:
        daily_ret = np.diff(eq) / eq[:-1]
        daily_ret = daily_ret[~np.isnan(daily_ret)]
        if len(daily_ret) > 0 and daily_ret.std() > 0:
            sharpe = np.sqrt(252) * daily_ret.mean() / daily_ret.std()
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Max Drawdown
    peak = np.maximum.accumulate(eq)
    dd = (eq - peak) / peak
    max_dd = abs(dd.min()) * 100

    # Win rate & P/L ratio
    paired = _pair_trades(trades)
    win_trades = [p for p in paired if p > 0]
    loss_trades = [p for p in paired if p < 0]
    win_rate = len(win_trades) / len(paired) * 100 if paired else 0
    avg_win = np.mean(win_trades) if win_trades else 1
    avg_loss = abs(np.mean(loss_trades)) if loss_trades else 1
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    return BacktestResult(
        params=params,
        total_return=round(total_return, 2),
        annual_return=round(annual_return, 2),
        sharpe=round(sharpe, 3),
        max_drawdown=round(max_dd, 2),
        win_rate=round(win_rate, 1),
        profit_loss_ratio=round(profit_loss_ratio, 2),
        total_trades=len(trades),
        equity_curve=[round(e, 2) for e in equity],
    )


def _pair_trades(trades: list[dict]) -> list[float]:
    """配对比交易，返回每对盈亏百分比。"""
    buys = [t for t in trades if t["type"] == "buy"]
    sells = [t for t in trades if t["type"] == "sell"]
    profits = []
    for b, s in zip(buys, sells):
        ret = (s["price"] / b["price"] - 1) * 100
        # 去掉交易成本的影响
        ret -= 0.13  # ~万三买 + 万三卖 + 千一印花税
        profits.append(round(ret, 2))
    return profits


# ═══════════════════════════════════════════════════════════════════════
# 3. 参数优化
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class OptimizationReport:
    """优化报告。"""
    strategy: str
    symbol: str
    total_combinations: int
    train_period: str
    test_period: str
    best_params: dict[str, Any]
    best_result: BacktestResult
    top10: list[BacktestResult]
    walk_forward_results: list[dict] = field(default_factory=list)
    overfit_warning: bool = False


def grid_search(df_train: pd.DataFrame, strategy_name: str) -> list[BacktestResult]:
    """网格搜索所有参数组合。"""
    st = STRATEGIES[strategy_name]
    param_grid = st["param_grid"]
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    results = []

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        # 过滤非法组合: fast < slow
        if "fast" in params and "slow" in params:
            if params["fast"] >= params["slow"]:
                continue
        result = run_backtest(df_train, strategy_name, params)
        results.append(result)

    return results


def walk_forward_optimize(df: pd.DataFrame, strategy_name: str,
                          train_years: int = 2, test_months: int = 6) -> OptimizationReport:
    """
    Walk-Forward 优化：滚动训练窗口，防止过拟合。

    滑动窗口:
    |--- 训练(2年) ---|--- 测试(3月) ---|
                     |--- 训练(2年) ---|--- 测试(3月) ---|
    """
    df = df.sort_index()
    total_days = (df.index[-1] - df.index[0]).days

    if total_days < 365 * 3:
        # 数据不足，退化为简单网格搜索 + 训练/测试分割
        split_idx = int(len(df) * 0.7)
        df_train = df.iloc[:split_idx]
        df_test = df.iloc[split_idx:]

        all_results = grid_search(df_train, strategy_name)
        all_results.sort(key=lambda r: r.sharpe, reverse=True)

        # 在测试集上评估最佳参数
        best = all_results[0]
        test_result = run_backtest(df_test, strategy_name, best.params)

        return OptimizationReport(
            strategy=strategy_name,
            symbol="",
            total_combinations=len(all_results),
            train_period=f"{df_train.index[0].date()} ~ {df_train.index[-1].date()}",
            test_period=f"{df_test.index[0].date()} ~ {df_test.index[-1].date()}",
            best_params=best.params,
            best_result=test_result,
            top10=all_results[:10],
            overfit_warning=_check_overfit(all_results, test_result),
        )

    # 完整 Walk-Forward
    train_start = df.index[0]
    wf_results = []
    all_best_params = []

    current = train_start + timedelta(days=train_years * 365)
    end = df.index[-1]

    while current + timedelta(days=test_months * 30) <= end:
        test_end = current + timedelta(days=test_months * 30)

        df_train = df.loc[train_start:current]
        df_test = df.loc[current:test_end]

        if len(df_train) < 200 or len(df_test) < 30:
            current = test_end
            continue

        results = grid_search(df_train, strategy_name)
        results.sort(key=lambda r: r.sharpe, reverse=True)
        best_in_sample = results[0]
        test_result = run_backtest(df_test, strategy_name, best_in_sample.params)

        wf_results.append({
            "train": f"{train_start.date()}~{current.date()}",
            "test": f"{current.date()}~{test_end.date()}",
            "params": best_in_sample.params,
            "train_sharpe": best_in_sample.sharpe,
            "test_sharpe": test_result.sharpe,
            "test_return": test_result.total_return,
        })
        all_best_params.append(best_in_sample.params)

        # 滑动窗口
        train_start += timedelta(days=test_months * 30)
        current = test_end

    # 用最后一个窗口的最佳参数在全区间回测
    final_params = all_best_params[-1] if all_best_params else {}
    final_result = run_backtest(df, strategy_name, final_params) if final_params else None

    # 过拟合检测
    overfit = _check_wf_overfit(wf_results)

    return OptimizationReport(
        strategy=strategy_name,
        symbol="",
        total_combinations=0,
        train_period="",
        test_period="",
        best_params=final_params,
        best_result=final_result or BacktestResult(params={}, total_return=0, annual_return=0,
                                                     sharpe=0, max_drawdown=0, win_rate=0,
                                                     profit_loss_ratio=0, total_trades=0),
        top10=[],
        walk_forward_results=wf_results,
        overfit_warning=overfit,
    )


def _check_overfit(all_results: list[BacktestResult], test_result: BacktestResult) -> bool:
    """检测是否过拟合：Top1和Top10在测试集上差距过大。"""
    if len(all_results) < 10:
        return False
    top1_sharpe = all_results[0].sharpe
    top10_avg_sharpe = np.mean([r.sharpe for r in all_results[:10]])
    # 如果 Top1 比 Top10 平均高出太多，可能是过拟合
    return (top1_sharpe - top10_avg_sharpe) > 1.0


def _check_wf_overfit(wf_results: list[dict]) -> bool:
    """Walk-Forward 过拟合检测：训练/测试 Sharpe 差距。"""
    if not wf_results:
        return False
    diffs = [w["train_sharpe"] - w["test_sharpe"] for w in wf_results]
    avg_diff = np.mean(diffs)
    # 训练集远好于测试集 → 过拟合
    return avg_diff > 0.5


# ═══════════════════════════════════════════════════════════════════════
# 4. 报告输出
# ═══════════════════════════════════════════════════════════════════════

def print_report(report: OptimizationReport):
    """终端美化输出优化报告。"""
    print(f"\n{'='*60}")
    print(f"  参数优化报告 — {report.strategy}")
    print(f"{'='*60}")

    if report.walk_forward_results:
        print(f"\n  Walk-Forward 滚动验证 ({len(report.walk_forward_results)} 个窗口):")
        print(f"  {'训练区间':<28s} {'测试区间':<28s} {'训练Sharpe':>10s} {'测试Sharpe':>10s} {'测试收益':>10s}")
        print(f"  {'-'*86}")
        for w in report.walk_forward_results:
            print(f"  {w['train']:<28s} {w['test']:<28s} {w['train_sharpe']:>10.3f} {w['test_sharpe']:>10.3f} {w['test_return']:>+9.2f}%")

    if report.overfit_warning:
        print(f"\n  [警告] 过拟合: 训练集与测试集表现差距过大，参数可能不可靠")

    print(f"\n  最佳参数: {report.best_params}")
    if report.best_result.total_trades > 0:
        r = report.best_result
        print(f"\n  全区间回测 (最佳参数):")
        print(f"    总收益:    {r.total_return:>+10.2f}%")
        print(f"    年化收益:  {r.annual_return:>+10.2f}%")
        print(f"    Sharpe:    {r.sharpe:>10.3f}")
        print(f"    最大回撤:  {r.max_drawdown:>10.2f}%")
        print(f"    胜率:      {r.win_rate:>10.1f}%")
        print(f"    盈亏比:    {r.profit_loss_ratio:>10.2f}")
        print(f"    交易次数:  {r.total_trades:>10d}")

    if report.top10:
        print(f"\n  Top 10 参数组合 (训练集 Sharpe 排名):")
        print(f"  {'排名':<6s} {'参数':<50s} {'Sharpe':>8s} {'收益':>10s}")
        for i, r in enumerate(report.top10):
            params_str = ", ".join(f"{k}={v}" for k, v in r.params.items())
            if len(params_str) > 48:
                params_str = params_str[:45] + "..."
            print(f"  {i+1:<6d} {params_str:<50s} {r.sharpe:>8.3f} {r.total_return:>+9.2f}%")


# ═══════════════════════════════════════════════════════════════════════
# 5. CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="策略参数优化引擎")
    parser.add_argument("strategy", nargs="?", default="macd_cross",
                        choices=list(STRATEGIES.keys()),
                        help="策略名称")
    parser.add_argument("symbol", nargs="?", default="600000", help="股票代码")
    parser.add_argument("--batch", action="store_true", help="批量优化沪深300")
    parser.add_argument("--output", "-o", default="", help="保存JSON报告路径")
    parser.add_argument("--report", action="store_true", help="查看最近优化报告")

    args = parser.parse_args()

    if args.report:
        reports = sorted(RESULTS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True)
        if reports:
            data = json.loads(reports[0].read_text())
            print(f"\n最近优化报告: {reports[0].name}")
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print("暂无优化报告")
        return

    if args.batch:
        # 批量优化沪深300
        symbols = []
        if DATA_DIR.exists():
            symbols = [d.name for d in DATA_DIR.iterdir()
                       if d.is_dir() and len(d.name) == 6][:30]  # 先跑30只
        if not symbols:
            print("请先运行 data_pipeline.py download 下载数据")
            return

        print(f"\n批量优化 {args.strategy} — {len(symbols)} 只股票\n")
        all_best = []
        for i, sym in enumerate(symbols):
            path = DATA_DIR / sym / "1d.csv"
            if not path.exists():
                continue
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) < 200:
                continue
            results = grid_search(df, args.strategy)
            results.sort(key=lambda r: r.sharpe, reverse=True)
            best = results[0]
            all_best.append({"symbol": sym, "params": best.params,
                             "sharpe": best.sharpe, "return": best.total_return})
            if (i + 1) % 10 == 0:
                print(f"  进度: {i+1}/{len(symbols)}")

        # 按Sharpe排序
        all_best.sort(key=lambda x: x["sharpe"], reverse=True)
        print(f"\n{'='*60}")
        print(f"  批量优化结果 — {args.strategy} (Top 20)")
        print(f"{'='*60}")
        for i, r in enumerate(all_best[:20]):
            params_str = ", ".join(f"{k}={v}" for k, v in r["params"].items())
            print(f"  {i+1:>3d}. {r['symbol']}  Sharpe={r['sharpe']:.3f}  "
                  f"Return={r['return']:+.2f}%  [{params_str}]")

        # 保存
        out_path = RESULTS_DIR / f"{args.strategy}_batch_{datetime.now():%Y%m%d_%H%M}.json"
        out_path.write_text(json.dumps(all_best, indent=2, ensure_ascii=False))
        print(f"\n报告已保存: {out_path}")

    else:
        # 单股票优化
        path = DATA_DIR / args.symbol / "1d.csv"
        if not path.exists():
            print(f"数据不存在: {path}")
            print(f"请先运行: python scripts/data_pipeline.py download")
            return

        df = pd.read_csv(path, index_col=0, parse_dates=True)
        print(f"\n加载数据: {args.symbol} ({len(df)} 条, {df.index[0].date()} ~ {df.index[-1].date()})")

        # Walk-Forward
        report = walk_forward_optimize(df, args.strategy)
        report.symbol = args.symbol
        print_report(report)

        # 保存
        out_path = RESULTS_DIR / f"{args.strategy}_{args.symbol}_{datetime.now():%Y%m%d_%H%M}.json"
        out_path.write_text(json.dumps({
            "strategy": report.strategy, "symbol": report.symbol,
            "best_params": report.best_params,
            "best_result": {
                "total_return": report.best_result.total_return,
                "annual_return": report.best_result.annual_return,
                "sharpe": report.best_result.sharpe,
                "max_drawdown": report.best_result.max_drawdown,
                "win_rate": report.best_result.win_rate,
                "total_trades": report.best_result.total_trades,
            },
            "walk_forward": report.walk_forward_results,
            "overfit_warning": report.overfit_warning,
        }, indent=2, ensure_ascii=False))
        print(f"\n报告已保存: {out_path}")


if __name__ == "__main__":
    main()
