"""
通达信选股公式 — 全市场扫描回测（高性能版）
==========================================
预计算所有指标 → 逐日扫描 → 组合轮动回测

用法:
    python scripts/tdx_scanner.py bull_line          # 牛线突破
    python scripts/tdx_scanner.py dragon_tiger        # 双信号共振
    python scripts/tdx_scanner.py both --stocks 300   # 两者对比
"""
import sys, os, time, argparse
from pathlib import Path
from collections import defaultdict
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

DATA_DIR = Path(__file__).parent.parent / "data" / "market"

# ═══════════════════════════════════════════════════════════════════════
# 向量化指标计算（批量预计算，只跑一次）
# ═══════════════════════════════════════════════════════════════════════

def _ema(series: np.ndarray, period: int) -> np.ndarray:
    alpha = 2.0 / (period + 1)
    result = np.zeros(len(series))
    result[0] = series[0]
    for i in range(1, len(series)):
        result[i] = alpha * series[i] + (1 - alpha) * result[i - 1]
    return result


def _sma(series: np.ndarray, period: int) -> np.ndarray:
    result = np.full(len(series), np.nan)
    if len(series) >= period:
        cs = np.cumsum(np.insert(series, 0, 0))
        result[period - 1:] = (cs[period:] - cs[:-period]) / period
    mask = np.isnan(result)
    last = series[0]
    for i in range(len(result)):
        if not np.isnan(result[i]):
            last = result[i]
        else:
            result[i] = last
    return result


def _hhv(series: np.ndarray, period: int) -> np.ndarray:
    result = np.zeros(len(series))
    for i in range(len(series)):
        start = max(0, i - period + 1)
        result[i] = np.max(series[start:i + 1])
    return result


def _ref(series: np.ndarray, n: int) -> np.ndarray:
    result = np.full(len(series), series[0])
    if n < len(series):
        result[n:] = series[:-n]
    return result


def _cross_above(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    result = np.zeros(len(a), dtype=bool)
    result[1:] = (a[1:] > b[1:]) & (a[:-1] <= b[:-1])
    return result


def _bars_since(cond: np.ndarray) -> np.ndarray:
    result = np.zeros(len(cond), dtype=int)
    last = -1
    for i in range(len(cond)):
        if cond[i]:
            last = i
        result[i] = i - last if last >= 0 else 9999
    return result


def _count_condition(cond: np.ndarray, period: int) -> np.ndarray:
    n = len(cond)
    result = np.zeros(n, dtype=int)
    ci = cond.astype(int)
    for i in range(n):
        start = max(0, i - period + 1)
        result[i] = ci[start:i + 1].sum()
    return result


def _dma(x: np.ndarray, a: np.ndarray) -> np.ndarray:
    result = np.zeros(len(x))
    result[0] = x[0]
    for i in range(1, len(x)):
        result[i] = a[i] * x[i] + (1 - a[i]) * result[i - 1]
    return result


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    return _ema(tr, period)


def _estimate_cost(close: np.ndarray, pct: int, window: int = 100) -> np.ndarray:
    n = len(close)
    result = np.zeros(n)
    for i in range(n):
        start = max(0, i - window + 1)
        w = close[start:i + 1]
        result[i] = np.percentile(w, pct) if len(w) >= 20 else close[i]
    return result


# ═══════════════════════════════════════════════════════════════════════
# 信号预计算
# ═══════════════════════════════════════════════════════════════════════

def compute_bull_line_signals(df: pd.DataFrame) -> np.ndarray:
    """返回每日买入信号 (bool array)。"""
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    open_p = df["open"].values
    n = len(close)

    if n < 200:
        return np.zeros(n, dtype=bool)

    # 牛线
    wp = (2.15 * close + low + high) / 4.0
    ema23 = _ema(close, 23)
    rp = (3.48 * close + high + low) / 4.0
    vw = np.abs(rp - ema23) / np.maximum(ema23, 1e-8)
    dma_r = _dma(wp, vw)
    bull_line = _ema(dma_r, 200) * 1.118

    # MACD
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)

    # ATR, HHV, BB, T
    atr = _compute_atr(high, low, close, 14)
    hhv20 = _hhv(high, 20)
    aa = hhv20 - 2 * atr
    hhv55 = _hhv(high, 55)
    bb = _cross_above(close, _ref(hhv55, 1))
    ma13 = _sma(close, 13)
    t_sig = _cross_above(np.minimum(ma13, aa), close)
    bbb = _bars_since(bb)
    r_b = _bars_since(t_sig)

    signals = np.zeros(n, dtype=bool)

    for i in range(200, n):
        b1 = (bbb[i] == 0) and (r_b[i - 1] < bbb[i - 1])
        cross_bl = (close[i] > bull_line[i]) & (close[i - 1] <= bull_line[i - 1])
        macd_ok = dif[i] > dea[i]
        chg = (close[i] / close[i - 1] - 1) * 100
        chg_ok = chg >= 7.0
        if cross_bl and macd_ok and chg_ok and b1:
            signals[i] = True

    return signals


def compute_dragon_tiger_signals(df: pd.DataFrame) -> np.ndarray:
    """返回每日买入信号 (bool array)。"""
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)

    if n < 100:
        return np.zeros(n, dtype=bool)

    # 擒龙决
    hhv30 = _hhv(close, 30)
    pressure = _sma(_ref(hhv30, 1), 2)
    ema20 = _ema(close, 20)
    dev = close - ema20
    dev_ma = _sma(dev ** 2, 20)
    std = np.sqrt(np.maximum(dev_ma, 0))
    boll_up = ema20 + 2 * std
    ref_boll = _ref(boll_up, 1)
    vol_ma5 = _sma(volume, 5)
    ref_vol_ma = _ref(vol_ma5, 1)
    vol_ratio = volume / np.maximum(ref_vol_ma, 1)

    ql = (close > pressure) & (close > ref_boll) & (vol_ratio > 1.5)
    ql_u = _count_condition(ql.astype(int), 7) == 1

    # 涨停先锋
    cost99 = _estimate_cost(close, 99, 100)
    profit_line = _ema(cost99, 5)
    zt = close > profit_line
    zt_u = _count_condition(zt.astype(int), 7) == 1

    signals = ql_u & zt_u
    signals[:100] = False
    return signals


# ═══════════════════════════════════════════════════════════════════════
# 组合轮动回测
# ═══════════════════════════════════════════════════════════════════════

def run_portfolio_scan(
    signal_func,
    universe: list[str],
    initial_cash: float = 1_000_000,
    max_positions: int = 5,
    hold_days: int = 3,
    per_pos_pct: float = 0.20,
    verbose: bool = True,
):
    """
    全市场扫描 + 组合轮动回测。

    每天:
    1. 卖出持仓到期的股票
    2. 扫描全市场信号
    3. 买入触发的股票（等权重，不超过最大持仓数）
    """
    # 预计算所有信号
    if verbose:
        t0 = time.time()
        print("预计算全市场信号...")

    all_signals: dict[str, np.ndarray] = {}
    all_data: dict[str, pd.DataFrame] = {}

    for i, sym in enumerate(universe):
        path = DATA_DIR / sym / "1d.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) < 200:
                continue
            all_data[sym] = df
            all_signals[sym] = signal_func(df)
        except Exception:
            continue

        if verbose and (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(universe)}")

    if verbose:
        elapsed = time.time() - t0
        print(f"  完成: {len(all_data)} 只股票, 耗时 {elapsed:.1f}s")

    if not all_data:
        print("无有效数据")
        return None

    # 对齐日期
    ref_sym = list(all_data.keys())[0]
    common_dates = all_data[ref_sym].index
    for sym in list(all_data.keys()):
        common_dates = common_dates.intersection(all_data[sym].index)
    common_dates = sorted(common_dates)

    if verbose:
        print(f"  共同交易日: {len(common_dates)} ({common_dates[0].date()} ~ {common_dates[-1].date()})")

    # 回测循环
    cash = initial_cash
    positions: dict[str, dict] = {}  # sym -> {shares, price, buy_idx}
    trades = []
    equity = []
    date_idx_map = {d: i for i, d in enumerate(common_dates)}

    total_signals = 0

    for di, date in enumerate(common_dates):
        # 1. 卖出到期持仓
        to_sell = []
        for sym, pos in positions.items():
            if di - pos["buy_idx"] >= hold_days:
                if sym in all_data and date in all_data[sym].index:
                    price = float(all_data[sym].loc[date, "close"])
                    cash += pos["shares"] * price * 0.9987
                    pnl = (price / pos["buy_price"] - 1) * 100
                    trades.append({
                        "date": date, "sym": sym, "type": "卖出",
                        "price": price, "pnl%": round(pnl, 2),
                    })
                    to_sell.append(sym)
        for sym in to_sell:
            del positions[sym]

        # 2. 扫描买入
        slots = max_positions - len(positions)
        if slots > 0:
            candidates = []
            for sym in all_data:
                if sym in positions:
                    continue
                sig_arr = all_signals.get(sym)
                if sig_arr is None:
                    continue
                # 找到对应日期索引
                sym_dates = all_data[sym].index
                if date in sym_dates:
                    si = sym_dates.get_loc(date)
                    if si < len(sig_arr) and sig_arr[si]:
                        # 获取当日收盘价
                        price = float(all_data[sym].loc[date, "close"])
                        candidates.append((sym, price))

            # 最多买入 slots 只
            for sym, price in candidates[:slots]:
                allocation = cash * per_pos_pct
                shares = int(allocation / price / 100) * 100
                if shares >= 100:
                    cost = shares * price * 1.0003
                    if cost <= cash:
                        cash -= cost
                        positions[sym] = {"shares": shares, "buy_price": price, "buy_idx": di}
                        trades.append({
                            "date": date, "sym": sym, "type": "买入",
                            "price": price, "shares": shares,
                        })
                        total_signals += 1

        # 3. 记录净值
        mv = cash
        for sym, pos in positions.items():
            if sym in all_data and date in all_data[sym].index:
                mv += pos["shares"] * float(all_data[sym].loc[date, "close"])
        equity.append({"date": date, "equity": mv})

    # 最终清仓
    last_date = common_dates[-1]
    for sym, pos in list(positions.items()):
        if sym in all_data and last_date in all_data[sym].index:
            price = float(all_data[sym].loc[last_date, "close"])
            cash += pos["shares"] * price * 0.9987
        del positions[sym]

    final_value = cash
    total_return = (final_value / initial_cash - 1) * 100

    # 绩效指标
    eq = np.array([e["equity"] for e in equity])
    daily_ret = np.diff(eq) / eq[:-1] if len(eq) > 1 else np.array([0])
    sharpe = float(np.sqrt(252) * daily_ret.mean() / daily_ret.std()) if daily_ret.std() > 0 else 0
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min() * 100)

    sell_trades = [t for t in trades if t["type"] == "卖出"]
    if sell_trades:
        pnls = [t["pnl%"] for t in sell_trades]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg_pnl = np.mean(pnls)
    else:
        win_rate = 0
        avg_pnl = 0

    result = {
        "total_signals": total_signals,
        "total_trades": len(trades),
        "sell_count": len(sell_trades),
        "initial_cash": initial_cash,
        "final_value": final_value,
        "total_return": total_return,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "equity_curve": equity,
        "trades": trades,
    }

    if verbose:
        print(f"\n  === 回测结果 ===")
        print(f"  触发信号: {total_signals} 次")
        print(f"  交易笔数: {len(trades)}")
        print(f"  卖出笔数: {len(sell_trades)}")
        print(f"  总收益:   {total_return:+.2f}%")
        print(f"  夏普比:   {sharpe:.3f}")
        print(f"  最大回撤: {max_dd:.2f}%")
        print(f"  胜率:     {win_rate:.1f}%")
        print(f"  平均盈亏: {avg_pnl:+.2f}%")

    return result


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="通达信选股公式全市场扫描回测")
    parser.add_argument("formula", choices=["bull_line", "dragon_tiger", "both"],
                        help="选股公式")
    parser.add_argument("--stocks", type=int, default=100, help="股票数量")
    parser.add_argument("--hold", type=int, default=3, help="持有天数")
    parser.add_argument("--max-pos", type=int, default=5, help="最大持仓")
    parser.add_argument("--capital", type=float, default=1_000_000, help="初始资金")
    args = parser.parse_args()

    # 获取股票列表
    if DATA_DIR.exists():
        universe = sorted([
            d.name for d in DATA_DIR.iterdir()
            if d.is_dir() and len(d.name) == 6 and (d / "1d.csv").exists()
        ])
    else:
        print("无数据目录，运行: python scripts/data_pipeline.py download")
        return

    universe = universe[:args.stocks]

    print("=" * 65)
    print(f"  通达信选股公式 · 全市场扫描回测")
    print("=" * 65)
    print(f"  股票池:   {len(universe)} 只")
    print(f"  持有天数: {args.hold}")
    print(f"  最大持仓: {args.max_pos}")
    print(f"  初始资金: {args.capital:,.0f}")
    print("=" * 65)

    if args.formula in ("bull_line", "both"):
        print(f"\n{'='*65}")
        print(f"  [牛线突破] 压力+DMA动态均线+55日高点突破+回踩确认")
        print(f"{'='*65}")
        run_portfolio_scan(
            compute_bull_line_signals, universe,
            initial_cash=args.capital, max_positions=args.max_pos,
            hold_days=args.hold,
        )

    if args.formula in ("dragon_tiger", "both"):
        print(f"\n{'='*65}")
        print(f"  [双信号共振] 擒龙决（量价突破）+涨停先锋（成本突破）")
        print(f"{'='*65}")
        run_portfolio_scan(
            compute_dragon_tiger_signals, universe,
            initial_cash=args.capital, max_positions=args.max_pos,
            hold_days=args.hold,
        )


if __name__ == "__main__":
    main()
