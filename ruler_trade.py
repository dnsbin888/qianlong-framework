"""交易级尺子 v1.0 — ATR动态止损 + 标准化报告 + OOS验证
基于 backtest_engine.BacktestEngine，薄封装。

用法:
    from ruler_trade import measure, compare_strategies

    report = measure(stock_data, factor_cache, strategy="tdx_resonance")
    print(report["total_return_pct"], report["sharpe"], report["calmar"])

    # 多策略对比
    df = compare_strategies(stock_data, factor_cache,
        strategies=[("tdx_resonance", "signal_resonance"),
                    ("tdx2_final", "signal_final")])
"""
import json, numpy as np
from collections import Counter
from backtest_engine import BacktestEngine
from atr_stop import get_stop_config


def _build_stop_map(stock_data, atr_multiplier=2.0, signal_sl=None, strategy_id=None):
    """统一止损表: 信号级止损 > 策略级 > ATR兜底 (对齐paper_engine D4)"""
    atr_map = {sym: get_stop_config(sym, stock_data, atr_multiplier=atr_multiplier)
               for sym in stock_data}
    # 叠加策略级止损 (D4注册表)
    if strategy_id:
        try:
            cfg = json.load(open(r"D:\quant_framework\signal_config.json", encoding="utf-8"))
            stp = cfg.get("strategy_tp_sl", {}).get(strategy_id, {})
            if stp:
                _sl = stp.get("sl", -0.04)
                for sym in atr_map:
                    atr_map[sym] = min(atr_map[sym], _sl)  # 取更严的
        except: pass
    # 叠加信号自带止损 (最高优先)
    if signal_sl:
        for sym, sl in signal_sl.items():
            if sym in atr_map:
                atr_map[sym] = min(atr_map[sym], sl)
    return atr_map


def _summarize_atr(stop_loss_map):
    """ATR 止损分布统计"""
    pcts = [abs(v) for v in stop_loss_map.values()]
    if not pcts:
        return {"n": 0, "mean_pct": 0, "median_pct": 0, "min_pct": 0, "max_pct": 0}
    return {
        "n": len(pcts),
        "mean_pct": round(np.mean(pcts) * 100, 1),
        "median_pct": round(float(np.median(pcts)) * 100, 1),
        "min_pct": round(np.min(pcts) * 100, 1),
        "max_pct": round(np.max(pcts) * 100, 1),
    }


def _exit_breakdown(trades):
    """退出方式分布"""
    cnt = Counter(t.get("exit_type", "unknown") for t in trades)
    total = len(trades) or 1
    return {k: {"count": v, "pct": round(v / total * 100, 1)} for k, v in cnt.most_common()}


def measure(stock_data, factor_cache=None, name_map=None,
            strategy="tdx_resonance", signal_field="signal_resonance",
            start="2022-01-01", end="2025-12-31",
            max_positions=3, position_pct=0.3,
            take_profit=None, hold_days=1,
            initial_capital=1_000_000,
            atr_multiplier=2.0,
            benchmark_sym='sh000300',
            formula_symbols=None, signal_store=None,
            **engine_kwargs):
    """交易级策略评测 — 一把尺子量到底。

    Args:
        stock_data: {symbol: DataFrame} 日线数据
        factor_cache: [StockInfo] 因子缓存
        name_map: {symbol: name} 名称映射
        strategy: 策略名 (tdx_resonance / tdx2_final / tdx2_xg / …)
        signal_field: 信号字段名
        start/end: 回测区间
        max_positions: 最大持仓数
        position_pct: 单票仓位
        take_profit: 止盈 %
        hold_days: 最大持有天数
        initial_capital: 初始资金
        atr_multiplier: ATR 倍数 (2=正常, 3=宽松, 1.5=严格)
        benchmark_sym: 基准指数
        formula_symbols: 指定股票池 (None=随机采样)
        signal_store: 预计算信号 {symbol: Series}

    Returns:
        dict: 标准化尺子报告
    """
    engine = BacktestEngine(stock_data, factor_cache or [], name_map=name_map)

    # 1. 构建统一止损表 (信号>策略>ATR, 对齐paper_engine)
    stop_loss_map = _build_stop_map(stock_data, atr_multiplier, strategy_id=strategy)

    # 2. 运行回测
    result = engine.run(
        strategy=strategy,
        signal_field=signal_field,
        start=start, end=end,
        max_positions=max_positions, position_pct=position_pct,
        take_profit=take_profit, hold_days=hold_days,
        initial_capital=initial_capital,
        benchmark_sym=benchmark_sym,
        stop_loss_map=stop_loss_map,
        formula_symbols=formula_symbols,
        signal_store=signal_store,
        **engine_kwargs,
    )

    # 3. Walk-Forward OOS (仅TDX代理, strategy_obj模式跳过——WF不支持自定义策略)
    wf = {"folds": [], "summary": {}}
    if not engine_kwargs.get("strategy_obj"):
        wf = engine.walk_forward(
            start=start, end=end,
            strategy=strategy, signal_field=signal_field,
            max_positions=max_positions, position_pct=position_pct,
            initial_capital=initial_capital,
            benchmark_sym=benchmark_sym,
            stop_loss_map=stop_loss_map,
            formula_symbols=formula_symbols,
            signal_store=signal_store,
        )

    # 4. 组装标准化报告
    m = result.get("metrics", {})
    trades = result.get("results", [])
    wf_summary = wf.get("summary", {})

    report = {
        "ruler": "trade_level_v1",
        "period": f"{start} → {end}",
        "strategy": strategy,
        "signal_field": signal_field,
        "atr_config": {
            "multiplier": atr_multiplier,
            "distribution": _summarize_atr(stop_loss_map),
        },

        # ── 核心收益 ──
        "total_return_pct": round(m.get("total_return", 0) * 100, 2),
        "annual_return_pct": round(m.get("annual_return", 0) * 100, 2),
        "max_drawdown_pct": round(m.get("max_drawdown", 0) * 100, 2),
        "calmar": round(m.get("calmar", 0), 2),
        "total_pnl": m.get("total_pnl", 0),

        # ── 风险调整 ──
        "sharpe": round(m.get("sharpe", 0), 2),
        "sortino": round(m.get("sortino", 0), 2),
        "annual_volatility_pct": round(m.get("annual_volatility", 0) * 100, 2),
        "var_95_pct": round(m.get("var_95", 0) * 100, 2),
        "cvar_pct": round(m.get("cvar", 0) * 100, 2),

        # ── 交易统计 ──
        "n_trades": m.get("n_trades", 0),
        "win_rate_pct": round(m.get("win_rate", 0) * 100, 1),
        "profit_factor": round(m.get("profit_factor", 0), 2),
        "avg_return_per_trade_pct": round(
            np.mean([t["return_pct"] for t in trades]) * 100, 2
        ) if trades else 0,
        "best_trade_pct": round(m.get("best_trade", 0) * 100, 2),
        "worst_trade_pct": round(m.get("worst_trade", 0) * 100, 2),

        # ── 退出方式 ──
        "exit_breakdown": _exit_breakdown(trades),

        # ── 基准对比 ──
        "benchmark": {
            "available": m.get("benchmark_available", False),
            "alpha_pct": round(m.get("alpha", 0) * 100, 2),
            "beta": round(m.get("beta", 0), 2),
            "information_ratio": round(m.get("information_ratio", 0), 2),
            "excess_return_pct": round(m.get("excess_return", 0) * 100, 2),
        },

        # ── OOS 验证 ──
        "oos": {
            "n_folds": wf_summary.get("n_folds", 0),
            "avg_test_sharpe": round(wf_summary.get("avg_test_sharpe", 0), 2),
            "avg_test_return_pct": round(wf_summary.get("avg_test_return", 0) * 100, 2),
            "avg_test_max_dd_pct": round(wf_summary.get("avg_test_max_dd", 0) * 100, 2),
            "sharpe_decay_pct": round(wf_summary.get("avg_sharpe_decay", 0) * 100, 1),
        },

        # ── 底层完整数据（供深入分析）──
        "_trades": trades,
        "_equity_curve": result.get("equity_curve", []),
        "_wf_folds": wf.get("folds", []),
    }

    return report


def compare_strategies(stock_data, factor_cache=None, name_map=None,
                       strategies=None, **kwargs):
    """多策略对比 — 同一把尺子，不同策略，输出对比表。

    Args:
        strategies: [("tdx_resonance", "signal_resonance"), ("tdx2_final", "signal_final"), …]

    Returns:
        list[dict]: 每个策略的尺子报告（按 Sharpe 降序）
    """
    if strategies is None:
        strategies = [
            ("tdx_resonance", "signal_resonance"),
            ("tdx2_final", "signal_final"),
        ]

    reports = []
    for strat_name, sig_field in strategies:
        report = measure(
            stock_data, factor_cache=factor_cache, name_map=name_map,
            strategy=strat_name, signal_field=sig_field,
            **kwargs,
        )
        reports.append(report)

    reports.sort(key=lambda r: r["sharpe"], reverse=True)
    return reports


def compare_table(reports):
    """对比报告 → 可打印表格"""
    header = f"{'策略':<20} {'总收益%':>8} {'年化%':>8} {'Sharpe':>7} {'Calmar':>7} {'回撤%':>7} {'胜率%':>7} {'盈亏比':>7} {'笔数':>5} {'OOS Sharpe':>10}"
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for r in reports:
        lines.append(
            f"{r['strategy']:<20} {r['total_return_pct']:>8.1f} {r['annual_return_pct']:>8.1f} "
            f"{r['sharpe']:>7.2f} {r['calmar']:>7.2f} {r['max_drawdown_pct']:>7.1f} "
            f"{r['win_rate_pct']:>7.1f} {r['profit_factor']:>7.2f} {r['n_trades']:>5} "
            f"{r['oos']['avg_test_sharpe']:>10.2f}"
        )
    lines.append(sep)
    return "\n".join(lines)


# ── 快捷入口 ──
if __name__ == "__main__":
    import sys
    sys.path.insert(0, r"D:\quant_web")        # data_loader 所在
    sys.path.insert(0, r"D:\quant_framework")  # 必须在 web 之后 insert，确保新 backtest_engine 优先
    from data_loader import load_stock_data_cache

    print("=" * 60)
    print("  交易级尺子 v1.0 — ATR动态止损 + OOS验证")
    print("=" * 60)

    stock_data = load_stock_data_cache(r"D:\quant_web\stock_data.parquet", keep_days=500)
    factor_cache = []  # 因子缓存可选，回测引擎会从数据计算
    name_map = {}

    strategies = [
        ("tdx_resonance", "signal_resonance"),
        ("tdx2_final", "signal_final"),
        ("tdx2_xg", "signal_xg"),
        ("tdx2_b1", "signal_b1"),
    ]

    reports = compare_strategies(stock_data, factor_cache, name_map,
                                 strategies=strategies,
                                 start="2025-06-01", end="2026-06-30",
                                 max_positions=5, position_pct=0.2)

    print(compare_table(reports))

    # 打印第一个策略的详细报告
    print("\n📊 详细报告:", strategies[0][0])
    r = reports[0]
    for k, v in r.items():
        if not k.startswith("_"):
            print(f"  {k}: {v}")
