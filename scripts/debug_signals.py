"""
信号诊断 — 逐条件检查，找出哪里丢失了信号
"""
import sys, os
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "src")))

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data" / "market"

from quant_framework.strategy.builtin.dragon_tiger import (
    _ema, _sma, _hhv, _ref, _count_condition, _estimate_cost,
)


def diagnose_all(universe: list[str], date_str: str = ""):
    """逐条件诊断所有股票的信号丢失情况。"""
    stats = {
        "total": 0,
        "ql_pressure": 0,      # C > 压力
        "ql_boll": 0,          # C > 涨停(布林)
        "ql_vol": 0,           # 量比 > 1.8
        "ql_all3": 0,          # 三个都满足
        "ql_unique": 0,        # 7日首次
        "zt_cost": 0,          # C > COST(99)EMA
        "zt_unique": 0,        # 7日首次
        "xg": 0,               # 最终信号
    }

    today = datetime.now().strftime("%Y-%m-%d")

    for sym in universe:
        path = DATA_DIR / sym / "1d.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
        except Exception:
            continue

        close = df["close"].values
        volume = df["volume"].values
        n = len(close)
        if n < 150:
            continue

        stats["total"] += 1

        # 只看最近一段（最近60天）
        i = n - 1
        if i < 100:
            continue

        # ── 擒龙决条件分解 ──
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

        # 最近60天每个条件满足的天数
        window = slice(max(100, n - 60), n)

        c_pressure = (close > pressure)[window].sum()
        c_boll = (close > zt_line)[window].sum()
        c_vol = (vol_ratio > 1.8)[window].sum()
        c_all3 = ((close > pressure) & (close > zt_line) & (vol_ratio > 1.8))[window].sum()

        ql_raw = (close > pressure) & (close > zt_line) & (vol_ratio > 1.8)
        ql_count = _count_condition(ql_raw.astype(int), 7)
        qin_long = ql_raw & (ql_count == 1)
        c_ql_unique = qin_long[window].sum()

        # ── 涨停先锋条件分解 ──
        cost99 = _estimate_cost(close, 99, 100)
        profit100 = _ema(cost99, 5)
        zt_raw = close > profit100
        c_cost = zt_raw[window].sum()

        zt_count = _count_condition(zt_raw.astype(int), 7)
        zt_tf = zt_raw & (zt_count == 1)
        c_zt_unique = zt_tf[window].sum()

        # ── 最终信号 ──
        xg = qin_long & zt_tf
        c_xg = xg[window].sum()

        # 汇总
        if c_pressure > 0:
            stats["ql_pressure"] += 1
        if c_boll > 0:
            stats["ql_boll"] += 1
        if c_vol > 0:
            stats["ql_vol"] += 1
        if c_all3 > 0:
            stats["ql_all3"] += 1
        if c_ql_unique > 0:
            stats["ql_unique"] += 1
        if c_cost > 0:
            stats["zt_cost"] += 1
        if c_zt_unique > 0:
            stats["zt_unique"] += 1
        if c_xg > 0:
            stats["xg"] += 1

        # 打印有信号的股票（最近信号）
        if c_xg > 0:
            last_xg_idx = np.where(xg[window])[0][-1] + window.start
            date = df.index[last_xg_idx]
            print(f"  {sym}: {c_xg}个信号, 最近@{str(date)[:10]} "
                  f"price={close[last_xg_idx]:.2f} chg={(close[last_xg_idx]/close[last_xg_idx-1]-1)*100:.1f}%")

    print(f"\n信号漏斗分析 (共{stats['total']}只股票, 最近60天):")
    print(f"  ┌ 擒龙决 ─────────────────────")
    print(f"  │ C>压力(MA(HHV30)):    {stats['ql_pressure']:>4} 只")
    print(f"  │ C>涨停(布林上轨):     {stats['ql_boll']:>4} 只")
    print(f"  │ 量比>1.8:            {stats['ql_vol']:>4} 只")
    print(f"  │ 三条件同时满足:       {stats['ql_all3']:>4} 只  ← 瓶颈!")
    print(f"  │ 7日内首次(擒龙决):    {stats['ql_unique']:>4} 只")
    print(f"  ├ 涨停先锋 ────────────────────")
    print(f"  │ C>COST(99)EMA5:      {stats['zt_cost']:>4} 只")
    print(f"  │ 7日内首次(涨停先锋):  {stats['zt_unique']:>4} 只")
    print(f"  └ 最终 ────────────────────────")
    print(f"    擒龙决 AND 涨停先锋: {stats['xg']:>4} 只  ← 最终信号")


if __name__ == "__main__":
    universe = sorted([
        d.name for d in DATA_DIR.iterdir()
        if d.is_dir() and len(d.name) == 6 and (d / "1d.csv").exists()
    ])
    print(f"诊断 {len(universe)} 只股票...")
    diagnose_all(universe)
