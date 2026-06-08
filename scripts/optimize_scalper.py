"""
超短线策略参数优化 — 散户版
扫描 止盈/止损/持有天数/仓位比例 找到最优组合
"""
import sys, os, itertools
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data" / "market"

from quant_framework.strategy.builtin.dragon_tiger import (
    _ema, _sma, _hhv, _ref, _count_condition, _estimate_cost,
)


def compute_signals_all(df):
    """逐日计算双信号共振。"""
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)
    if n < 150:
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    hhv30 = _hhv(close, 30)
    pressure = _sma(_ref(hhv30, 1), 2)
    ema20 = _ema(close, 20)
    dev_sq = (close - ema20) ** 2
    dev_ma = _sma(dev_sq, 20)
    std = np.sqrt(np.maximum(dev_ma, 1e-10))
    boll_upper = ema20 + 2 * std
    zt_line = _ref(boll_upper, 1)
    vol_ma5 = _sma(volume, 5)
    vol_ratio = volume / np.maximum(_ref(vol_ma5, 1), 1.0)
    ql_raw = (close > pressure) & (close > zt_line) & (vol_ratio > 1.8)
    ql_count = _count_condition(ql_raw.astype(int), 7)
    qin_long = ql_raw & (ql_count == 1)
    cost99 = _estimate_cost(close, 99, 100)
    profit100 = _ema(cost99, 5)
    zt_raw = close > profit100
    zt_count = _count_condition(zt_raw.astype(int), 7)
    zhang_ting = zt_raw & (zt_count == 1)
    xg = qin_long & zhang_ting
    xg[:150] = False
    return qin_long, zhang_ting, xg


def simple_backtest(close, signals, hold=2, sl=-2.5, tp=4.0, pos_pct=0.30):
    """单股票简化回测。"""
    n = len(close)
    cash = 200_000
    pos = None  # {shares, price, idx}
    trades = []

    for i in range(1, n):
        price = close[i]

        # 出场
        if pos:
            pnl = (price / pos["price"] - 1) * 100
            days = i - pos["idx"]
            sell = False
            if pnl <= sl: sell = True
            elif pnl >= tp: sell = True
            elif days >= hold: sell = True

            if sell:
                cash += pos["shares"] * price * 0.9987
                trades.append({"pnl": pnl, "days": days, "win": pnl > 0})
                pos = None

        # 入场
        if not pos and signals[i]:
            alloc = cash * pos_pct
            shares = int(alloc / price / 100) * 100
            if shares >= 100 and shares * price * 1.0003 <= cash:
                cash -= shares * price * 1.0003
                pos = {"shares": shares, "price": price, "idx": i}

    if pos:
        pnl = (close[-1] / pos["price"] - 1) * 100
        cash += pos["shares"] * close[-1] * 0.9987
        trades.append({"pnl": pnl, "days": len(close) - pos["idx"], "win": pnl > 0})

    if not trades:
        return {"wr": 0, "avg_pnl": 0, "count": 0, "total_pnl": 0}

    pnls = [t["pnl"] for t in trades]
    return {
        "wr": sum(1 for p in pnls if p > 0) / len(pnls) * 100,
        "avg_pnl": np.mean(pnls),
        "count": len(trades),
        "total_pnl": sum(pnls),
        "pnls": pnls,
    }


def grid_search(universe, param_grid):
    """暴力网格搜索最优参数。"""
    keys = list(param_grid.keys())
    values = list(param_grid.values())
    combos = list(itertools.product(*values))

    print(f"参数组合: {len(combos)} 种")
    print(f"股票池:   {len(universe)} 只")

    # 预计算所有信号
    print("预计算信号...")
    all_data = {}
    for i, sym in enumerate(universe):
        path = DATA_DIR / sym / "1d.csv"
        if not path.exists():
            continue
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) < 200:
            continue
        _, _, xg = compute_signals_all(df)
        all_data[sym] = (df["close"].values, xg)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(universe)}")

    print(f"有效股票: {len(all_data)}")

    best = None
    best_score = -999

    results = []
    for combo in combos:
        params = dict(zip(keys, combo))
        hold, sl, tp, pos_pct = params["hold"], params["sl"], params["tp"], params["pos_pct"]

        all_trades = []
        for sym, (close, sig) in all_data.items():
            r = simple_backtest(close, sig, hold=hold, sl=-abs(sl), tp=tp, pos_pct=pos_pct)
            all_trades.extend(r.get("pnls", []))

        if not all_trades:
            continue

        wr = sum(1 for p in all_trades if p > 0) / len(all_trades) * 100
        avg = np.mean(all_trades)
        total = sum(all_trades)

        # 综合评分：胜率 × 盈亏总额 × 交易频率
        score = wr * total * np.sqrt(len(all_trades))

        results.append({**params, "wr": wr, "avg": avg, "total": total,
                        "trades": len(all_trades), "score": score})

        short = f"hold={hold} sl={sl} tp={tp} pct={pos_pct}"
        print(f"  {short:<35s} wr={wr:.1f}% avg={avg:+.2f}% total={total:+.0f}% trades={len(all_trades)}")

        if score > best_score:
            best_score = score
            best = params.copy()
            best["wr"] = wr
            best["avg_pnl"] = avg
            best["total_pnl"] = total
            best["trades"] = len(all_trades)

    return best, sorted(results, key=lambda r: r["score"], reverse=True)


if __name__ == "__main__":
    universe = sorted([
        d.name for d in DATA_DIR.iterdir()
        if d.is_dir() and len(d.name) == 6 and (d / "1d.csv").exists()
    ])[:100]  # 100只做参数扫描

    param_grid = {
        "hold": [1, 2, 3, 5],
        "sl": [1.5, 2.0, 2.5, 3.0, 4.0],
        "tp": [2.0, 3.0, 4.0, 5.0, 7.0, 10.0],
        "pos_pct": [0.20, 0.30, 0.50],
    }

    print("=" * 65)
    print("  超短线策略参数优化")
    print("=" * 65)

    best, all_results = grid_search(universe, param_grid)

    print(f"\n{'='*65}")
    print(f"  最优参数")
    print(f"{'='*65}")
    print(f"  持有天数: {best['hold']}天")
    print(f"  止损:     -{best['sl']}%")
    print(f"  止盈:     +{best['tp']}%")
    print(f"  仓位:     {best['pos_pct']*100:.0f}%")
    print(f"  胜率:     {best['wr']:.1f}%")
    print(f"  平均盈亏: {best['avg_pnl']:+.2f}%")
    print(f"  盈亏总额: {best['total_pnl']:+.0f}%")
    print(f"  总交易:   {best['trades']} 笔")

    print(f"\n  Top 20 参数组合:")
    for i, r in enumerate(all_results[:20]):
        print(f"  {i+1:>2d}. hold={r['hold']} sl=-{r['sl']} tp=+{r['tp']} pct={r['pos_pct']}  "
              f"wr={r['wr']:.1f}% total={r['total']:+.0f}% trades={r['trades']}")
