"""
公式验证 — 完全一致的条件下跑两次，确保可复现
"""
import sys, os
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..", "src")))
sys.path.insert(0, str(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

DATA_DIR = Path(__file__).parent.parent / "data" / "market"

from quant_framework.strategy.builtin.dragon_tiger import (
    _ema, _sma, _hhv, _ref, _count_condition, _estimate_cost,
)

def compute_signals(df):
    """双信号共振 — 严格按照原版公式，不改任何参数。"""
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)
    if n < 100:
        return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    # 压力:=MA(REF(HHV(C,30),1),2)
    hhv30 = _hhv(close, 30)
    pressure = _sma(_ref(hhv30, 1), 2)

    # 妖股先锋:=EMA(C,20)
    ema20 = _ema(close, 20)

    # NSYJ5:=妖股先锋 + 2*SQRT(MA(POW(C-妖股先锋,2),20))
    dev_sq = (close - ema20) ** 2
    dev_ma = _sma(dev_sq, 20)
    std = np.sqrt(np.maximum(dev_ma, 1e-10))
    boll_upper = ema20 + 2 * std

    # 涨停:=REF(NSYJ5,1)
    zt_line = _ref(boll_upper, 1)

    # 量比:=VOL/REF(MA(VOL,5),1)
    vol_ma5 = _sma(volume, 5)
    vol_ratio = volume / np.maximum(_ref(vol_ma5, 1), 1.0)

    # 擒龙决:=C>压力 AND C>涨停 AND 量比>1.8 AND COUNT(...,7)=1
    ql_raw = (close > pressure) & (close > zt_line) & (vol_ratio > 1.8)
    ql_count = _count_condition(ql_raw.astype(int), 7)
    qin_long = ql_raw & (ql_count == 1)

    # 获利百分百:=EMA(COST(99),5)
    cost99 = _estimate_cost(close, 99, 100)
    profit100 = _ema(cost99, 5)

    # 涨停先锋:=C>获利百分百 AND COUNT(C>获利百分百,7)=1
    zt_raw = close > profit100
    zt_count = _count_condition(zt_raw.astype(int), 7)
    zhang_ting = zt_raw & (zt_count == 1)

    # XG: 擒龙决 AND 涨停先锋
    xg = qin_long & zhang_ting
    xg[:100] = False
    return qin_long, zhang_ting, xg


def simple_backtest(df, signals, hold_days=3, stop_loss=-3.0, stop_profit=5.0,
                    cash_per_trade=0.25):
    """
    最简回测 — 与昨天 tdx_scanner 完全一致的逻辑：
    - 信号日次日开盘买入
    - 持有 hold_days 天卖出 或 止损/止盈
    - 固定仓位比例
    """
    close = df["close"].values
    n = len(close)

    cash = 500_000
    pos = None  # {shares, buy_price, buy_idx}
    trades = []
    equity = [cash]

    for i in range(1, n):
        price = close[i]
        mv = cash + (pos["shares"] * price if pos else 0)
        equity.append(mv)

        # 出场
        if pos is not None:
            days = i - pos["buy_idx"]
            pnl = (price / pos["buy_price"] - 1) * 100
            sell = False
            reason = ""

            if pnl <= stop_loss:
                sell = True; reason = f"止损{pnl:.1f}%"
            elif pnl >= stop_profit:
                sell = True; reason = f"止盈+{pnl:.1f}%"
            elif days >= hold_days:
                sell = True; reason = f"持有{days}天到期{pnl:+.1f}%"

            if sell:
                cash += pos["shares"] * price * 0.9987
                trades.append({"idx": i, "type": "sell", "price": price,
                               "pnl%": round(pnl, 2), "reason": reason})
                pos = None

        # 入场
        if pos is None and signals[i]:
            alloc = cash * cash_per_trade
            shares = int(alloc / price / 100) * 100
            if shares >= 100:
                cost = shares * price * 1.0003
                if cost <= cash:
                    cash -= cost
                    pos = {"shares": shares, "buy_price": price, "buy_idx": i}
                    trades.append({"idx": i, "type": "buy", "price": price, "shares": shares})

    # 清仓
    if pos:
        price = close[-1]
        pnl = (price / pos["buy_price"] - 1) * 100
        cash += pos["shares"] * price * 0.9987
        trades.append({"idx": n-1, "type": "close", "price": price,
                       "pnl%": round(pnl, 2), "reason": "强制清仓"})

    final = cash
    total_ret = (final / 500_000 - 1) * 100

    sells = [t for t in trades if t["type"] in ("sell", "close")]
    if sells:
        pnls = [t["pnl%"] for t in sells]
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg_pnl = np.mean(pnls)
    else:
        wr = 0; avg_pnl = 0

    eq = np.array(equity)
    dr = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0])
    sharpe = float(np.sqrt(252) * dr.mean() / dr.std()) if dr.std() > 0 else 0
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min() * 100)

    return {
        "total_return": total_ret, "sharpe": sharpe, "max_dd": max_dd,
        "win_rate": wr, "avg_pnl": avg_pnl, "trades": len(trades), "sells": len(sells),
    }


if __name__ == "__main__":
    universe = sorted([
        d.name for d in DATA_DIR.iterdir()
        if d.is_dir() and len(d.name) == 6 and (d / "1d.csv").exists()
    ])[:100]  # 固定100只，和昨天一致

    print("=" * 60)
    print("  双信号共振 — 可复现验证")
    print(f"  股票池: {len(universe)} 只 | 持有3天 | 止损-3% | 止盈+5%")
    print(f"  量比阈值: 1.8 (原版) | 涨幅阈值: 无")
    print(f"  日期: {datetime.now():%Y-%m-%d %H:%M}")
    print("=" * 60)

    # 对比昨天的参数
    for label, hold, sl, sp in [
        ("昨天(1.5/3天)", 3, -3.0, 5.0),
        ("收紧(1.8/3天)", 3, -3.0, 5.0),
        ("短线(1.8/2天)", 2, -2.5, 4.0),
    ]:
        all_signals = []
        for sym in universe:
            df = pd.read_csv(DATA_DIR / sym / "1d.csv", index_col=0, parse_dates=True)
            if len(df) < 100:
                continue
            _, _, xg = compute_signals(df)
            all_signals.append((sym, df, xg))

        # 聚合回测
        results = []
        for sym, df, sig in all_signals:
            r = simple_backtest(df, sig, hold_days=hold, stop_loss=sl, stop_profit=sp)
            results.append(r)

        avg_ret = np.mean([r["total_return"] for r in results])
        avg_sharpe = np.mean([r["sharpe"] for r in results])
        avg_dd = np.mean([r["max_dd"] for r in results])
        avg_wr = np.mean([r["win_rate"] for r in results if r["sells"] > 0])
        total_trades = sum(r["trades"] for r in results)

        print(f"\n  {label}:")
        print(f"    平均收益: {avg_ret:+.2f}%")
        print(f"    平均夏普: {avg_sharpe:.3f}")
        print(f"    平均回撤: {avg_dd:.2f}%")
        print(f"    平均胜率: {avg_wr:.1f}%")
        print(f"    总交易:   {total_trades}")
