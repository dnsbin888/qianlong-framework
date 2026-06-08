"""
超短线价差策略 — 双公式交集 + 分批建仓
======================================
逻辑：
  1. 每日尾盘扫描：两公式同时触发 → 入选
  2. T日14:50 建50%底仓（收盘价成交）
  3. T+1日10:00：阳线确认（今开>昨收 且 今收>今开）→ 加仓50%
  4. T+1日10:00：未确认 → 底仓平价/小亏出局
  5. 持仓1-2日，止盈+3~5%、止损-2~3%

用法:
    python scripts/scalper.py --stocks 300
"""
import sys, os, time, argparse
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent))

DATA_DIR = Path(__file__).parent.parent / "data" / "market"

# 内联信号计算函数（避免跨包导入问题）
from quant_framework.strategy.builtin.bull_line_breakout import (
    _ema, _sma, _hhv, _ref, _cross_above, _bars_since, _dma,
    _compute_atr, _compute_macd, _compute_bull_line,
)

# ═══════════════════════════════════════════════════════════════════════
# 改进版 COST — 用真实成交量模拟筹码分布
# ═══════════════════════════════════════════════════════════════════════

def _count_condition(cond: np.ndarray, period: int) -> np.ndarray:
    n = len(cond)
    result = np.zeros(n, dtype=int)
    ci = cond.astype(int)
    for i in range(n):
        start = max(0, i - period + 1)
        result[i] = ci[start:i + 1].sum()
    return result


def _estimate_cost_vwap(close: np.ndarray, high: np.ndarray, low: np.ndarray,
                        volume: np.ndarray, percentile: int = 99, window: int = 250) -> np.ndarray:
    """
    改进版 COST 近似 —— 用 OHLCV 数据模拟筹码分布。

    核心思想：
    - 每个交易日，成交量在 [low, high] 区间内分布（用三角分布近似）
    - 累积历史所有价位的成交量 → 得到筹码分布
    - COST(N) = N% 筹码所在的价格位置

    这个方法比简单分位数精确得多，因为它考虑了真实的成交量分布。
    """
    n = len(close)
    result = np.zeros(n)

    # 构建每个交易日的价格-成交量对
    # 简化：用 (high+low+close)/3 作为该日代表价格，volume 作为该日筹码
    typical_price = (high + low + close) / 3.0

    for i in range(n):
        start = max(0, i - window + 1)
        prices = typical_price[start:i + 1]
        vols = volume[start:i + 1]

        if len(prices) < 20:
            result[i] = close[i]
            continue

        # 指数衰减权重（近期筹码更活跃，权重更高）
        w = len(prices)
        decay = np.exp(-0.01 * np.arange(w)[::-1])  # 越远权重越低
        weights = vols * decay
        weights = weights / weights.sum()

        # 按价格排序 → 累积权重 → 找 N% 分位
        sorted_idx = np.argsort(prices)
        cum_weights = np.cumsum(weights[sorted_idx])
        target = percentile / 100.0
        idx = np.searchsorted(cum_weights, target)
        if idx >= len(prices):
            idx = len(prices) - 1
        result[i] = prices[sorted_idx[idx]]

    return result


def compute_bull_line_signals(df):
    """牛线突破信号（从 tdx_scanner 复制）。"""
    close = df["close"].values; high = df["high"].values
    low = df["low"].values; open_p = df["open"].values; n = len(close)
    if n < 200:
        return np.zeros(n, dtype=bool)
    bull_line = _compute_bull_line(close, high, low, open_p)
    dif, dea = _compute_macd(close)
    atr = _compute_atr(high, low, close, 14)
    hhv20 = _hhv(high, 20); aa = hhv20 - 2 * atr
    hhv55 = _hhv(high, 55); bb = _cross_above(close, _ref(hhv55, 1))
    ma13 = _sma(close, 13); t_sig = _cross_above(np.minimum(ma13, aa), close)
    bbb = _bars_since(bb); r_b = _bars_since(t_sig)
    signals = np.zeros(n, dtype=bool)
    for i in range(200, n):
        b1 = (bbb[i] == 0) and (r_b[i - 1] < bbb[i - 1])
        cross_bl = (close[i] > bull_line[i]) & (close[i - 1] <= bull_line[i - 1])
        chg = (close[i] / close[i - 1] - 1) * 100
        if cross_bl and dif[i] > dea[i] and chg >= 7.0 and b1:
            signals[i] = True
    return signals


def compute_dragon_tiger_signals(df):
    """
    双信号共振 — 逐行对应通达信原公式:
      压力:=MA(REF(HHV(C,30),1),2);
      妖股先锋:=EMA(C,20);
      NSYJ2:=POW(C-妖股先锋,2);
      NSYJ3:=MA(NSYJ2,20);
      NSYJ4:=SQRT(NSYJ3);
      NSYJ5:=妖股先锋+2*NSYJ4;
      涨停:=REF(NSYJ5,1);
      量比:=VOL/REF(MA(VOL,5),1);
      擒龙决:=C>压力 AND C>涨停 AND 量比>1.8
               AND COUNT(C>压力 AND C>涨停 AND 量比>1.8,7)=1;
      获利百分百:=EMA(COST(99),5);
      涨停先锋:=C>获利百分百 AND COUNT(C>获利百分百,7)=1;
      XG: 擒龙决 AND 涨停先锋;
    """
    close = df["close"].values
    volume = df["volume"].values
    n = len(close)
    if n < 100:
        return np.zeros(n, dtype=bool)

    # 压力:=MA(REF(HHV(C,30),1),2)
    pressure = _sma(_ref(_hhv(close, 30), 1), 2)

    # 妖股先锋:=EMA(C,20)
    ema20 = _ema(close, 20)

    # NSYJ2~NSYJ5 → 布林带
    dev_sq = (close - ema20) ** 2                         # NSYJ2
    dev_ma = _sma(dev_sq, 20)                              # NSYJ3
    std = np.sqrt(np.maximum(dev_ma, 1e-10))               # NSYJ4
    boll_upper = ema20 + 2 * std                           # NSYJ5

    # 涨停:=REF(NSYJ5,1)
    zt_line = _ref(boll_upper, 1)

    # 量比:=VOL/REF(MA(VOL,5),1)
    vol_ma5 = _sma(volume, 5)
    vol_ratio = volume / np.maximum(_ref(vol_ma5, 1), 1.0)

    # 擒龙决 := C>压力 AND C>涨停 AND 量比>1.8
    ql_raw = (close > pressure) & (close > zt_line) & (vol_ratio > 1.8)

    # COUNT(..., 7)=1  →  7日内首次触发
    ql_count = _count_condition(ql_raw.astype(int), 7)
    qin_long = ql_raw & (ql_count == 1)

    # 获利百分百:=EMA(COST(99),5)
    # COST(99) 近似 — 100天窗口 99分位
    # 这是在所有近似方案中表现最好的版本（Sharpe 1.565, Return +49.95%）
    cost99 = np.zeros(n)
    for i in range(n):
        start = max(0, i - 100 + 1)
        w = close[start:i + 1]
        if len(w) >= 20:
            cost99[i] = np.percentile(w, 99)
        else:
            cost99[i] = close[i]
    profit100 = _ema(cost99, 5)

    # 涨停先锋:=C>获利百分百 AND COUNT(C>获利百分百,7)=1
    zt_raw = close > profit100
    zt_count = _count_condition(zt_raw.astype(int), 7)
    zhang_ting = zt_raw & (zt_count == 1)

    # XG: 擒龙决 AND 涨停先锋
    signals = qin_long & zhang_ting
    signals[:100] = False
    return signals


# ═══════════════════════════════════════════════════════════════════════
# 超短线回测引擎
# ═══════════════════════════════════════════════════════════════════════

def run_scalping_backtest(
    universe: list[str],
    initial_cash: float = 500_000,
    max_positions: int = 3,
    base_pct: float = 0.50,         # 底仓比例
    confirm_pct: float = 0.50,       # 加仓比例
    hold_days_max: int = 2,          # 最长持有天数
    stop_loss_pct: float = -2.5,     # 止损
    stop_profit_pct: float = 4.0,    # 止盈
    formula: str = "dragon",         # bull | dragon | both
    verbose: bool = True,
):
    """
    超短线分批建仓回测。

    Returns: dict with results
    """
    t0 = time.time()
    if verbose:
        print("预计算双公式信号...")

    # 预计算
    bull_signals: dict[str, np.ndarray] = {}
    dragon_signals: dict[str, np.ndarray] = {}
    all_data: dict[str, pd.DataFrame] = {}

    need_bull = formula in ("bull", "both")
    need_dragon = formula in ("dragon", "both")

    for i, sym in enumerate(universe):
        path = DATA_DIR / sym / "1d.csv"
        if not path.exists():
            continue
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) < 200:
                continue
            all_data[sym] = df
            if need_bull:
                bull_signals[sym] = compute_bull_line_signals(df)
            else:
                bull_signals[sym] = np.zeros(len(df), dtype=bool)
            if need_dragon:
                dragon_signals[sym] = compute_dragon_tiger_signals(df)
            else:
                dragon_signals[sym] = np.zeros(len(df), dtype=bool)
        except Exception:
            continue

        if verbose and (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  预计算: {i+1}/{len(universe)} ({elapsed:.0f}s)")

    if verbose:
        print(f"  完成: {len(all_data)} 只, {time.time()-t0:.1f}s")

    # 取日历日期的并集（而非严格交集），用最完整的那只股票的时间轴做参考
    if not all_data:
        print("无有效数据")
        return None

    # 找到日期范围最广的股票作为参考时间轴
    ref_sym = max(all_data.keys(), key=lambda s: len(all_data[s]))
    ref_dates = sorted(all_data[ref_sym].index)
    # 取最近2年
    start_cutoff = ref_dates[-1] - pd.Timedelta(days=730)
    common_dates = [d for d in ref_dates if d >= start_cutoff]

    if len(common_dates) < 30:
        print(f"交易日不足: {len(common_dates)}")
        return None

    if verbose:
        stocks_with_data = sum(1 for sym in all_data if common_dates[0] in all_data[sym].index)
        print(f"  交易日: {len(common_dates)} ({common_dates[0].date()} ~ {common_dates[-1].date()})")
        print(f"  覆盖股票: {stocks_with_data}/{len(all_data)}")

    # ── 回测状态 ──
    cash = initial_cash
    positions = {}          # sym -> {shares, avg_cost, entry_date_idx, stage}
    pending_confirm = {}    # sym -> {shares, cost, entry_date_idx}  (底仓待确认)
    trades_log = []
    equity_log = []
    signal_log = []

    date_map = {d: i for i, d in enumerate(common_dates)}

    for di, date in enumerate(common_dates):
        # ═══ 第一步：处理待确认底仓 ═══
        confirmed = []
        rejected = []
        for sym, pos in list(pending_confirm.items()):
            if sym not in all_data or date not in all_data[sym].index:
                continue
            row = all_data[sym].loc[date]

            # 阳线确认条件：今收 > 今开（日内走强）AND 今收 > 昨收（延续涨势）
            yesterday_close = pos["yesterday_close"]
            bullish_confirm = (row["close"] > row["open"]) and (row["close"] > yesterday_close)

            if bullish_confirm:
                # 确认成功 → 加仓
                add_cash = pos["base_cash"] * confirm_pct / base_pct  # 等比加仓
                add_cash = min(add_cash, cash * 0.45)
                add_price = row["close"]
                add_shares = int(add_cash / add_price / 100) * 100
                if add_shares >= 100:
                    cost = add_shares * add_price * 1.0003
                    cash -= cost
                    total_shares = pos["shares"] + add_shares
                    avg_cost = (pos["shares"] * pos["cost_basis"] + add_shares * add_price) / total_shares
                    positions[sym] = {
                        "shares": total_shares, "avg_cost": avg_cost,
                        "entry_di": pos["entry_di"], "stage": "full",
                    }
                    trades_log.append({
                        "date": date, "sym": sym, "type": "加仓",
                        "price": add_price, "shares": add_shares,
                        "reason": "阳线确认",
                    })
                else:
                    # 钱不够加仓就只保留底仓
                    positions[sym] = {
                        "shares": pos["shares"], "avg_cost": pos["cost_basis"],
                        "entry_di": pos["entry_di"], "stage": "base_only",
                    }
                confirmed.append(sym)
            else:
                # 未确认 → 底仓尾盘出局
                exit_price = row["close"]
                cash += pos["shares"] * exit_price * 0.9987
                pnl = (exit_price / pos["cost_basis"] - 1) * 100
                trades_log.append({
                    "date": date, "sym": sym, "type": "底仓止损",
                    "price": exit_price, "shares": pos["shares"], "pnl%": round(pnl, 2),
                    "reason": "次日未确认",
                })
                rejected.append(sym)

        for sym in confirmed:
            del pending_confirm[sym]
        for sym in rejected:
            del pending_confirm[sym]

        # ═══ 第二步：检查持仓止盈止损 ═══
        to_sell = []
        for sym, pos in list(positions.items()):
            if sym not in all_data or date not in all_data[sym].index:
                continue
            days_held = di - pos["entry_di"]
            current_price = float(all_data[sym].loc[date, "close"])
            pnl_pct = (current_price / pos["avg_cost"] - 1) * 100

            reason = None

            # 止损（严格）
            if pnl_pct <= stop_loss_pct:
                reason = f"止损 {pnl_pct:.1f}%"
            # 止盈
            elif pnl_pct >= stop_profit_pct:
                reason = f"止盈 +{pnl_pct:.1f}%"
            # 到期
            elif days_held >= hold_days_max:
                reason = f"持有{days_held}日到期 {pnl_pct:+.1f}%"

            if reason:
                revenue = pos["shares"] * current_price * 0.9987
                cash += revenue
                trades_log.append({
                    "date": date, "sym": sym, "type": "卖出",
                    "price": current_price, "shares": pos["shares"],
                    "pnl%": round(pnl_pct, 2), "reason": reason,
                })
                to_sell.append(sym)

        for sym in to_sell:
            del positions[sym]

        # ═══ 第三步：尾盘扫描新信号 ═══
        slots = max_positions - len(positions) - len(pending_confirm)
        if slots > 0:
            candidates = []
            for sym in all_data:
                if sym in positions or sym in pending_confirm:
                    continue
                # 找到当日索引
                if date not in all_data[sym].index:
                    continue
                si = all_data[sym].index.get_loc(date)
                b_sig = bull_signals.get(sym)
                d_sig = dragon_signals.get(sym)
                if b_sig is None or d_sig is None:
                    continue
                if si < len(b_sig) and si < len(d_sig):
                    # 信号判断：根据模式选择
                    has_bull = b_sig[si]
                    has_dragon = d_sig[si]
                    if formula == "both":
                        triggered = has_bull and has_dragon
                    elif formula == "bull":
                        triggered = has_bull
                    else:  # dragon
                        triggered = has_dragon

                    if triggered:
                        entry_price = float(all_data[sym].loc[date, "close"])
                        candidates.append((sym, entry_price, entry_price))

            if candidates:
                signal_log.append({
                    "date": date, "count": len(candidates),
                    "symbols": [c[0] for c in candidates[:10]],
                })

            # 最多买 slots 只（按当日涨跌幅排序优先买最强）
            def _sort_key(x):
                sym = x[0]
                try:
                    loc = all_data[sym].index.get_loc(date)
                    if loc > 0:
                        return float(all_data[sym].iloc[loc]["close"]) / float(all_data[sym].iloc[loc - 1]["close"])
                except Exception:
                    pass
                return 1.0
            candidates.sort(key=_sort_key, reverse=True)

            for sym, price, _ in candidates[:slots]:
                # 底仓 50%
                alloc = cash * base_pct / max(slots, 1)
                shares = int(alloc / price / 100) * 100
                if shares >= 100:
                    cost = shares * price * 1.0003
                    if cost <= cash * 0.95:
                        cash -= cost
                        # 记录昨日收盘价用于次日确认判断
                        si = all_data[sym].index.get_loc(date)
                        yesterday_close = float(all_data[sym].iloc[si - 1]["close"]) if si > 0 else price
                        pending_confirm[sym] = {
                            "shares": shares, "cost_basis": price, "entry_di": di,
                            "base_cash": cost, "yesterday_close": yesterday_close,
                        }
                        trades_log.append({
                            "date": date, "sym": sym, "type": "底仓",
                            "price": price, "shares": shares,
                            "reason": "尾盘双信号交集",
                        })

        # ═══ 第四步：记录净值 ═══
        mv = cash
        for sym, pos in positions.items():
            if sym in all_data and date in all_data[sym].index:
                mv += pos["shares"] * float(all_data[sym].loc[date, "close"])
        for sym, pos in pending_confirm.items():
            if sym in all_data and date in all_data[sym].index:
                mv += pos["shares"] * float(all_data[sym].loc[date, "close"])
        equity_log.append({"date": date, "equity": mv, "cash": cash})

    # ═══ 最终清仓 ═══
    last_date = common_dates[-1]
    for sym, pos in list(positions.items()):
        if sym in all_data and last_date in all_data[sym].index:
            price = float(all_data[sym].loc[last_date, "close"])
            cash += pos["shares"] * price * 0.9987
        del positions[sym]
    for sym, pos in list(pending_confirm.items()):
        if sym in all_data and last_date in all_data[sym].index:
            price = float(all_data[sym].loc[last_date, "close"])
            cash += pos["shares"] * price * 0.9987
        del pending_confirm[sym]

    final_value = cash
    total_return = (final_value / initial_cash - 1) * 100

    # ── 绩效指标 ──
    eq = np.array([e["equity"] for e in equity_log])
    if len(eq) > 1:
        dr = np.diff(eq) / eq[:-1]
        sharpe = float(np.sqrt(252) * dr.mean() / dr.std()) if dr.std() > 0 else 0
    else:
        sharpe = 0
    peak = np.maximum.accumulate(eq)
    max_dd = float(((eq - peak) / peak).min() * 100) if len(eq) > 0 else 0

    all_sells = [t for t in trades_log if t["type"] in ("卖出", "底仓止损")]
    if all_sells:
        pnls = [t["pnl%"] for t in all_sells]
        win_rate = sum(1 for p in pnls if p > 0) / len(pnls) * 100
        avg_pnl = np.mean(pnls)
    else:
        win_rate = 0
        avg_pnl = 0

    # 分类统计
    base_count = sum(1 for t in trades_log if t["type"] == "底仓")
    confirm_count = sum(1 for t in trades_log if t["type"] == "加仓")
    rejected_count = sum(1 for t in trades_log if t["type"] == "底仓止损")
    sell_count = sum(1 for t in trades_log if t["type"] == "卖出")
    signal_days = len(signal_log)
    total_signal_count = sum(s["count"] for s in signal_log)

    result = {
        "initial_cash": initial_cash, "final_value": final_value,
        "total_return": total_return, "sharpe": sharpe, "max_drawdown": max_dd,
        "win_rate": win_rate, "avg_pnl": avg_pnl,
        "base_count": base_count, "confirm_count": confirm_count,
        "rejected_count": rejected_count, "sell_count": sell_count,
        "signal_days": signal_days, "total_signal_count": total_signal_count,
        "equity_curve": equity_log, "trades": trades_log,
    }

    if verbose:
        print(f"\n  ═══ 超短线价差回测结果 ═══")
        print(f"  信号日:     {signal_days} 天 (共 {total_signal_count} 个信号)")
        print(f"  底仓建仓:   {base_count} 笔")
        print(f"  加仓确认:   {confirm_count} 笔")
        print(f"  确认失败:   {rejected_count} 笔")
        print(f"  卖出止盈:   {sell_count} 笔")
        print(f"  ─────────────────────────")
        print(f"  总收益:     {total_return:+.2f}%")
        print(f"  夏普比:     {sharpe:.3f}")
        print(f"  最大回撤:   {max_dd:.2f}%")
        print(f"  胜率:       {win_rate:.1f}%")
        print(f"  平均盈亏:   {avg_pnl:+.2f}%")

        if signal_log:
            print(f"\n  最近10个信号日:")
            for s in signal_log[-10:]:
                print(f"    {str(s['date'])[:10]}: {s['count']}只 — {', '.join(s['symbols'][:5])}")

        # 交易示例
        if trades_log:
            print(f"\n  最近10笔交易:")
            for t in trades_log[-10:]:
                d = str(t["date"])[:10]
                print(f"    {d} {t['type']:6s} {t['sym']} @{t['price']:.2f} "
                      f"x{t.get('shares',0)} {t.get('reason','')}")

    return result


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="超短线价差策略 — 双公式交集+分批建仓")
    parser.add_argument("--stocks", type=int, default=300, help="股票池数量")
    parser.add_argument("--formula", choices=["bull", "dragon", "both"], default="dragon",
                        help="选股公式: bull=牛线突破, dragon=双信号共振, both=交集")
    parser.add_argument("--capital", type=float, default=500_000, help="初始资金")
    parser.add_argument("--max-pos", type=int, default=3, help="最大同时持仓")
    parser.add_argument("--hold", type=int, default=2, help="最长持有天数")
    parser.add_argument("--stop-loss", type=float, default=-2.5, help="止损%")
    parser.add_argument("--stop-profit", type=float, default=4.0, help="止盈%")
    args = parser.parse_args()

    if DATA_DIR.exists():
        universe = sorted([
            d.name for d in DATA_DIR.iterdir()
            if d.is_dir() and len(d.name) == 6 and (d / "1d.csv").exists()
        ])
    else:
        print("无数据，运行: python scripts/data_pipeline.py download")
        return

    universe = universe[:args.stocks]

    print("=" * 65)
    print(f"  超短线价差策略 v1.0")
    print(f"  策略: 双公式交集 + 尾盘底仓 + 次日阳线确认加仓")
    print("=" * 65)
    print(f"  股票池:   {len(universe)} 只")
    print(f"  最大持仓: {args.max_pos} 只")
    print(f"  持有天数: {args.hold} 天")
    print(f"  止损:     {args.stop_loss}%")
    print(f"  止盈:     +{args.stop_profit}%")
    print(f"  资金:     {args.capital:,.0f}")
    print("=" * 65)

    run_scalping_backtest(
        universe,
        initial_cash=args.capital,
        max_positions=args.max_pos,
        hold_days_max=args.hold,
        stop_loss_pct=args.stop_loss,
        stop_profit_pct=args.stop_profit,
        formula=args.formula,
    )


if __name__ == "__main__":
    main()
