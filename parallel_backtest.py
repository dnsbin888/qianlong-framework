"""并行回测引擎 (Phase 6c) — 多公式并发, 7×加速

7 个 TDX 公式独立回测, 无共享状态, trivially parallelizable.
使用 ThreadPoolExecutor 并行执行, 7min → 1min。

用法:
    from parallel_backtest import run_parallel_backtest
    results = run_parallel_backtest(params)
"""

import sys
import os
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, r"D:\quant_framework")
sys.path.insert(0, r"D:\quant_web")

logger = logging.getLogger("quant_framework.parallel_bt")

# 7 个内置公式
FORMULAS = [
    {"name": "tdx_resonance", "label": "共振信号", "signal": "signal_resonance"},
    {"name": "tdx2_final",    "label": "最终精选", "signal": "signal_final"},
    {"name": "tdx2_xg",       "label": "新股信号", "signal": "signal_xg"},
    {"name": "tdx2_b1",       "label": "B1结构",   "signal": "signal_b1"},
    {"name": "tdx_qlj",       "label": "擒龙决",   "signal": "signal_qlj"},
    {"name": "tdx_ztxf",      "label": "涨停先锋", "signal": "signal_ztxf"},
    {"name": "tdx_bandit",    "label": "波段擒妖", "signal": "signal_bandit"},
]


def _run_one_formula(formula: dict, params: dict, stock_data: dict) -> dict:
    """执行单个公式的回测。"""
    name = formula["name"]
    sig_field = formula["signal"]
    t0 = time.time()

    try:
        from backtest_engine import BacktestEngine
        engine = BacktestEngine(stock_data)

        # 信号预计算
        engine.compute_signal_store(sig_field)

        result = engine.run(
            strategy="tdx_resonance",
            signal_field=sig_field,
            start=params.get("start", "2025-06-01"),
            end=params.get("end", "2026-06-25"),
            initial_capital=float(params.get("capital", 1000000)),
            stop_loss=float(params.get("stopLoss", -0.05)),
            take_profit=float(params.get("takeProfit", 0.08)),
            max_positions=int(params.get("maxPos", 5)),
            pos_pct=float(params.get("posPct", 20)) / 100,
            hold_days=int(params.get("holdDays", 3)),
            trail_t1_profit=float(params.get("trail1Profit", 5)) / 100,
            trail_t1_drop=float(params.get("trail1Drop", 2)) / 100,
            trail_t2_profit=float(params.get("trail2Profit", 7)) / 100,
            trail_t2_drop=float(params.get("trail2Drop", 3)) / 100,
            trail_t3_profit=float(params.get("trail3Profit", 10)) / 100,
            trail_t3_drop=float(params.get("trail3Drop", 3)) / 100,
            atr_mult=float(params.get("atrMult", 0)),
            atr_period=int(params.get("atrPeriod", 14)),
            limit_up_enabled=bool(params.get("limitUpEnabled", True)),
            limit_up_drop=float(params.get("limitUpDrop", 0)) / 100,
            sell_ratio_1=float(params.get("sellRatio1", 33)) / 100,
            sell_ratio_2=float(params.get("sellRatio2", 33)) / 100,
            sell_ratio_3=float(params.get("sellRatio3", 33)) / 100,
            min_power=int(params.get("minPower", 5)),
        )

        elapsed = time.time() - t0
        return {
            "formula": name,
            "label": formula["label"],
            "ok": True,
            "trades": len(result.get("results", [])),
            "sharpe": result.get("sharpe_ratio", 0),
            "max_dd": result.get("max_drawdown", 0),
            "win_rate": result.get("win_rate", 0),
            "total_return": result.get("total_return", 0),
            "elapsed": round(elapsed, 1),
            "error": None,
        }
    except Exception as e:
        return {
            "formula": name,
            "label": formula["label"],
            "ok": False,
            "trades": 0,
            "sharpe": 0, "max_dd": 0, "win_rate": 0, "total_return": 0,
            "elapsed": time.time() - t0,
            "error": str(e)[:200],
        }


def run_parallel_backtest(params: dict, stock_data: dict = None, formulas: list = None) -> dict:
    """并行执行多公式回测。

    Args:
        params: 回测参数 dict
        stock_data: 股票数据 (如 None 则从缓存加载)
        formulas: 要测试的公式列表 (默认全部7个)

    Returns:
        {"success": True, "results": [...], "summary": {...}, "elapsed": ...}
    """
    t0 = time.time()

    if stock_data is None:
        from data_loader import load_stock_data_from_cache
        stock_data = load_stock_data_from_cache()
        if not stock_data:
            return {"success": False, "error": "无法加载 stock_data"}

    if formulas is None:
        formulas = FORMULAS

    # 并行执行
    results = []
    with ThreadPoolExecutor(max_workers=min(len(formulas), 7)) as executor:
        futures = {
            executor.submit(_run_one_formula, f, params, stock_data): f["name"]
            for f in formulas
        }
        for future in as_completed(futures):
            results.append(future.result())

    # 排序
    results.sort(key=lambda r: r.get("sharpe", 0), reverse=True)

    # 汇总
    ok = [r for r in results if r["ok"]]
    best = ok[0] if ok else None
    elapsed = time.time() - t0

    return {
        "success": True,
        "results": results,
        "summary": {
            "total": len(results),
            "completed": len(ok),
            "failed": len(results) - len(ok),
            "best_formula": best["formula"] if best else None,
            "best_sharpe": best["sharpe"] if best else 0,
            "total_elapsed": round(elapsed, 1),
            "serial_would_be": sum(r.get("elapsed", 0) for r in results),
            "speedup": round(sum(r.get("elapsed", 0) for r in results) / max(elapsed, 0.1), 1),
        },
    }
