"""事件驱动因子回测 — 模拟实盘选股→买入→持有→卖出全链路。

与 IC 分析的本质区别：
  IC 分析: 全市场因子值与未来收益的秩相关 (99.9%的行是0, IC被稀释)
  事件驱动: 只看因子触发日(N条信号), 统计买入后的真实胜率/盈亏比

用法:
  python run_event_backtest.py --factor tdx_yaogu_resonance_bandit --stocks 500
  python run_event_backtest.py --factor tdx_bandit_sniper --stocks 500 --holds 3,5,7,12,20
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")

import time, argparse, random
import numpy as np
import pandas as pd
import warnings; warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.definitions import FACTOR_LIBRARY
from quant_framework.factors.tdx_signals import TDX_SIGNAL_FACTORS
from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS


def _build_events_factors():
    """构建因子注册表 (与 run_factor_backtest_unified 一致)。"""
    all_f: dict[str, dict] = {}
    for f in FACTOR_LIBRARY:
        all_f[f.name] = {
            "name": f.name, "label": f.label, "compute": f.compute,
            "direction": f.direction, "category": f.category.value,
            "factor_type": f.factor_type,
        }
    for name, info in TDX_SIGNAL_FACTORS.items():
        all_f[name] = info
    for name, info in TDX2_SIGNAL_FACTORS.items():
        all_f[name] = info
    return all_f


ALL_FACTORS = _build_events_factors()


def load_data(min_bars, n_stocks):
    """复用统一回测入口的数据加载逻辑。"""
    provider = THSDayDataProvider()
    provider.connect()
    all_syms = provider.scan_symbols()
    if not all_syms:
        raise RuntimeError("数据目录未找到或为空")

    random.seed(42)
    valid = [s for s in all_syms if len(provider._read_day_file(s)) >= min_bars]
    if len(valid) > n_stocks:
        valid = random.sample(valid, n_stocks)
    print(f"  Stocks loaded: {len(valid)} (min {min_bars} bars)")
    return provider, valid


def _resolve_factor(name):
    if name in ALL_FACTORS:
        return ALL_FACTORS[name]
    return None


def event_backtest(factor_name, n_stocks, start_date, end_date, hold_days):
    """事件驱动回测核心。

    逻辑:
        1. 逐股票计算因子值
        2. 找到 signal==1 的日期 (触发日)
        3. 记录触发日后 N 天的收益率
        4. 汇总: 胜率 / 平均收益 / 盈亏比 / 最大回撤
    """
    from scipy import stats

    fdef = _resolve_factor(factor_name)
    if fdef is None:
        print(f"  ERROR: Factor '{factor_name}' not found.")
        return

    provider, valid = load_data(80, n_stocks)

    print("=" * 75)
    print(f"  事件驱动回测 — {fdef.get('label', factor_name)}")
    print(f"  持仓周期: {hold_days} 天 | 股票池: {len(valid)} 只")
    print("=" * 75)

    t0 = time.time()

    # 按持仓周期收集所有交易
    trades = {h: [] for h in hold_days}  # h -> [(ret, symbol, entry_date), ...]
    signal_counts = []  # 每天触发信号数

    for si, sym in enumerate(valid):
        if si % 100 == 0:
            print(f"  {si}/{len(valid)} ...")

        data = provider._read_day_file(sym)
        if not data or len(data) < 80:
            continue

        dates = sorted(data.keys())
        records = []
        for d in dates:
            o, h, l, c, amt, vol = data[d]
            if o > 0 and c > 0:
                records.append({
                    "date": d, "open": o, "high": h, "low": l,
                    "close": c, "volume": vol, "amount": amt,
                })
        if len(records) < 80:
            continue

        df = pd.DataFrame(records)

        # 计算因子
        try:
            result = fdef["compute"](df)
            if isinstance(result, pd.Series):
                fseries = result
            elif isinstance(result, pd.DataFrame) and not result.empty:
                fseries = result.iloc[:, -1]
            else:
                continue
        except Exception:
            continue

        # 找到信号触发位置 (signal == 1)
        signal_mask = fseries.fillna(0).astype(bool)
        trigger_indices = np.where(signal_mask.values)[0]

        if len(trigger_indices) == 0:
            continue

        close = df["close"].values
        n_rows = len(close)

        for idx in trigger_indices:
            # 确保有足够的历史数据 + 未来数据
            if idx < 20 or idx >= n_rows - max(hold_days) - 1:
                continue

            entry_price = close[idx]
            entry_date = df.iloc[idx]["date"]
            signal_counts.append(1)

            for h in hold_days:
                exit_idx = idx + h
                if exit_idx >= n_rows:
                    continue
                exit_price = close[exit_idx]
                ret = (exit_price / entry_price) - 1.0
                trades[h].append((ret, sym, entry_date))

    elapsed = time.time() - t0

    # ── 汇总 ──
    print(f"\n{'=' * 75}")
    print(f"  回测完成 — {elapsed:.0f}s | 总信号: {sum(len(v) for v in trades.values())} 条")
    print(f"{'=' * 75}")

    results = []
    for h in hold_days:
        tlist = trades[h]
        if len(tlist) < 5:
            results.append({
                "hold": h, "n": len(tlist),
                "msg": f"信号不足 ({len(tlist)}条)"
            })
            continue

        rets = np.array([t[0] for t in tlist])
        wins = (rets > 0).sum()
        losses = (rets < 0).sum()
        flats = (rets == 0).sum()
        win_rate = wins / len(rets)

        mean_ret = np.mean(rets)
        median_ret = np.median(rets)
        std_ret = np.std(rets, ddof=1)
        max_ret = np.max(rets)
        min_ret = np.min(rets)

        avg_win = np.mean(rets[rets > 0]) if wins > 0 else 0
        avg_loss = np.mean(rets[rets < 0]) if losses > 0 else 0
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        # 胜率>50%的t检验
        t_stat, p_value = stats.ttest_1samp(rets, 0)

        results.append({
            "hold": h, "n": len(rets),
            "win_rate": win_rate, "mean_ret": mean_ret,
            "median_ret": median_ret, "std_ret": std_ret,
            "max_ret": max_ret, "min_ret": min_ret,
            "avg_win": avg_win, "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "t_stat": t_stat, "p_value": p_value,
        })

        print(f"\n  ── 持有 {h} 天 ──")
        print(f"  交易次数: {len(rets)}")
        print(f"  胜率:     {win_rate:.1%}  ({wins}胜/{losses}负/{flats}平)")
        print(f"  平均收益: {mean_ret:+.2%}")
        print(f"  中位收益: {median_ret:+.2%}")
        print(f"  标准差:   {std_ret:.2%}")
        print(f"  最大盈利: {max_ret:+.2%}")
        print(f"  最大亏损: {min_ret:+.2%}")
        print(f"  平均盈利: {avg_win:+.2%}")
        print(f"  平均亏损: {avg_loss:+.2%}")
        print(f"  盈亏比:   {profit_factor:.2f}")
        print(f"  t统计量:  {t_stat:+.2f} (p={p_value:.4f})")

    # ── 比较表 ──
    print(f"\n{'=' * 75}")
    print(f"  {'持有':<6} {'次数':>6} {'胜率':>8} {'均值':>8} {'中位':>8} {'盈亏比':>8} {'p值':>8}")
    print(f"  {'-'*6} {'-'*6} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for r in results:
        if "win_rate" in r:
            print(f"  {r['hold']:<6d} {r['n']:>6d} {r['win_rate']:>7.1%} "
                  f"{r['mean_ret']:>+7.2%} {r['median_ret']:>+7.2%} "
                  f"{r['profit_factor']:>7.2f} {r['p_value']:>7.4f}")
        else:
            print(f"  {r['hold']:<6d} {r['n']:>6d}   {r['msg']}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="事件驱动因子回测")
    parser.add_argument("--factor", type=str, default="tdx_yaogu_resonance_bandit",
                        help="因子名")
    parser.add_argument("--stocks", type=int, default=500,
                        help="股票数量")
    parser.add_argument("--start", type=str, default="2023-01-01",
                        help="起始日期 (暂用于文件名过滤)")
    parser.add_argument("--end", type=str, default="2025-12-31",
                        help="结束日期")
    parser.add_argument("--holds", type=str, default="3,5,7,12,20",
                        help="持仓周期(逗号分隔)")
    args = parser.parse_args()

    hold_days = [int(x.strip()) for x in args.holds.split(",")]

    event_backtest(
        factor_name=args.factor,
        n_stocks=args.stocks,
        start_date=args.start,
        end_date=args.end,
        hold_days=hold_days,
    )
