"""统一因子回测入口 — P0-因子-03

合并 run_factor_backtest.py / run_multi_factor_v2.py / run_smart_factor.py 三个脚本。

三种模式:
  single  — 单因子 IC/ICIR 分析 (时间序列秩相关)
  multi   — 多因子组合回测 (固定权重，月度调仓)
  smart   — 智能多因子回测 (市场自适应权重 + 质量过滤)

用法:
  python run_factor_backtest_unified.py --mode single --factor trend_bottom --stocks 500
  python run_factor_backtest_unified.py --mode multi --factors trend_bottom,add_position --method rank
  python run_factor_backtest_unified.py --mode smart --method ensemble
  python run_factor_backtest_unified.py --mode single --factor ret_20d --stocks 300 --start 2023-01-01
"""

import sys; sys.path.insert(0, r"d:\quant_framework\src")

import time, random, os, pickle, argparse
import numpy as np
import pandas as pd
from collections import Counter
import warnings; warnings.filterwarnings("ignore")

from quant_framework.data.providers.ths_day import THSDayDataProvider
from quant_framework.factors.definitions import FACTOR_LIBRARY, FACTOR_MAP
from quant_framework.factors.tdx_signals import (
    TDX_SIGNAL_FACTORS, factor_trend_bottom, factor_add_position, factor_money_flow,
)
from quant_framework.factors.tdx_signals2 import TDX2_SIGNAL_FACTORS, factor_bull_position


# ======================================================================
# 因子注册表 — 合并所有因子源
# ======================================================================
def _build_all_factors():
    """合并 builtin + tdx_signals + tdx_signals2 因子。"""
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


ALL_FACTORS = _build_all_factors()

# 直接因子函数映射（不在 registry 中的因子名 → 计算函数）
_DIRECT_FACTORS = {
    "trend_bottom":   {"compute": factor_trend_bottom, "direction": 1,  "label": "趋势线底部"},
    "add_position":   {"compute": factor_add_position, "direction": 1,  "label": "加仓信号"},
    "bull_position":  {"compute": factor_bull_position, "direction": -1, "label": "牛线位置"},
    "money_flow":     {"compute": factor_money_flow,   "direction": 1,  "label": "主力资金流"},
}

# 常见简写 → registry 键名映射
_ALIAS_MAP = {
    "bull_position":  "tdx2_bull_line",
    "trend_bottom":   "tdx_trend_bottom",
    "add_position":   "tdx_add_position",
    "money_flow":     "tdx_money_flow",
}


def _resolve_factor(name: str) -> dict | None:
    """解析因子名 — 支持多种命名约定。

    尝试顺序:
      1. 直接函数映射 (_DIRECT_FACTORS)
      2. 别名映射 → registry (_ALIAS_MAP)
      3. 精确匹配 registry
      4. 加 tdx_ / tdx2_ 前缀
    """
    # 1. 直接映射
    if name in _DIRECT_FACTORS:
        return _DIRECT_FACTORS[name]
    # 2. 别名 → registry
    if name in _ALIAS_MAP and _ALIAS_MAP[name] in ALL_FACTORS:
        return ALL_FACTORS[_ALIAS_MAP[name]]
    # 3. 精确匹配
    if name in ALL_FACTORS:
        return ALL_FACTORS[name]
    # 4. 前缀匹配
    for prefix in ["tdx_", "tdx2_"]:
        if prefix + name in ALL_FACTORS:
            return ALL_FACTORS[prefix + name]
    return None


# ======================================================================
# 命令行
# ======================================================================
parser = argparse.ArgumentParser(description="Unified Factor Backtest")
parser.add_argument("--mode", choices=["single", "multi", "smart"], default="multi",
                    help="single=IC分析, multi=多因子组合, smart=自适应多因子")
parser.add_argument("--factor", type=str, default="trend_bottom",
                    help="单因子模式下的因子名")
parser.add_argument("--factors", type=str, default="trend_bottom,add_position,bull_position,ret_20d",
                    help="多因子列表，逗号分隔")
parser.add_argument("--method", choices=["static", "rank", "adaptive"], default="static",
                    help="权重方法: static=固定权重, rank=排序等权, adaptive=市场自适应")
parser.add_argument("--stocks", type=int, default=500,
                    help="采样股票数")
parser.add_argument("--top-k", type=int, default=30,
                    help="每期持仓数")
parser.add_argument("--min-days", type=int, default=500,
                    help="最少K线条数")
parser.add_argument("--start", type=str, default="2022-01-01",
                    help="回测开始日期")
parser.add_argument("--end", type=str, default="2025-12-31",
                    help="回测结束日期")
parser.add_argument("--capital", type=float, default=1_000_000,
                    help="初始资金")
parser.add_argument("--cache", type=str, default=None,
                    help="因子缓存文件路径 (multi/smart模式)")
parser.add_argument("--output", type=str, default=None,
                    help="输出文件路径")
args = parser.parse_args()

# ── 解析因子列表 ──
factor_list = [f.strip() for f in args.factors.split(",") if f.strip()]

# ======================================================================
# 公共: 数据加载
# ======================================================================
def load_data(min_days, n_stocks):
    """加载股票数据，返回 (provider, valid_symbols)。"""
    provider = THSDayDataProvider()
    provider.connect()
    all_syms = provider.scan_symbols()

    random.seed(42)
    valid = [s for s in all_syms if len(provider._read_day_file(s)) >= min_days]
    if len(valid) > n_stocks:
        valid = random.sample(valid, n_stocks)
    print(f"  Stocks loaded: {len(valid)} (min {min_days} days)")
    return provider, valid


# ======================================================================
# 公共: 显示回测结果
# ======================================================================
def display_results(metrics, equity_df, bench_df=None, mode_label=""):
    """格式化输出回测绩效。"""
    print(f"\n{'=' * 65}")
    print(f"  {mode_label} PERFORMANCE")
    print(f"{'=' * 65}")

    rows = [
        ("Total Return",       f"{metrics.get('total_return', 0):.2%}"),
        ("Annual Return",      f"{metrics.get('annual_return', 0):.2%}"),
        ("Sharpe Ratio",       f"{metrics.get('sharpe', 0):.2f}"),
        ("Max Drawdown",       f"{metrics.get('max_drawdown', 0):.2%}"),
        ("Win Rate (monthly)", f"{metrics.get('win_rate', 0):.1%}"),
        ("Alpha (annual)",     f"{metrics.get('alpha', 0):.2%}"),
        ("Info Ratio",         f"{metrics.get('information_ratio', 0):.2f}"),
        ("Total Periods",      f"{metrics.get('total_periods', 0)}"),
        ("Final Equity",       f"{metrics.get('final_equity', 0):,.0f}"),
    ]
    for label, value in rows:
        print(f"  {label:<30} {value:>15}")

    if bench_df is not None and "benchmark_return" in metrics:
        print(f"  {'Benchmark Return':<30} {metrics['benchmark_return']:>15.2%}")

    # 逐年表现
    if equity_df is not None and len(equity_df) >= 12:
        eq = pd.DataFrame(equity_df).set_index("date") if "date" in equity_df else equity_df
        eq.index = pd.to_datetime(eq.index)
        eq["year"] = eq.index.year

        print(f"\n{'=' * 65}")
        print(f"  YEAR-BY-YEAR")
        print(f"{'=' * 65}")
        print(f"  {'Year':<8} {'Return':>10} {'Bench':>10} {'Excess':>10}")
        print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*10}")

        for year in sorted(eq["year"].unique()):
            ey = eq[eq["year"] == year]["equity"]
            if len(ey) < 2:
                continue
            port_ret = ey.iloc[-1] / ey.iloc[0] - 1
            bench_ret_val = 0.0
            if bench_df is not None:
                bn = pd.DataFrame(bench_df).set_index("date") if "date" in bench_df else bench_df
                bn.index = pd.to_datetime(bn.index)
                bn["year"] = bn.index.year
                by = bn[bn["year"] == year]["equity"]
                if len(by) >= 2:
                    bench_ret_val = by.iloc[-1] / by.iloc[0] - 1
            excess_r = port_ret - bench_ret_val
            print(f"  {year:<8} {port_ret:>9.1%} {bench_ret_val:>9.1%} {excess_r:>+9.1%}")
    print()


# ======================================================================
# MODE: single — 因子 IC 分析
# ======================================================================
def run_single(factor_name, n_stocks, start_date, end_date):
    """单因子 IC/ICIR 分析。

    对指定因子，逐股票计算因子值与未来1/5/20日收益的 Spearman 秩相关，
    汇总所有股票的 IC 分布。
    """
    from scipy import stats

    print("=" * 65)
    print(f"  Single Factor IC Analysis: {factor_name}")
    print(f"  Stocks: {n_stocks} | Period: {start_date} ~ {end_date}")
    print("=" * 65)

    fdef = _resolve_factor(factor_name)
    if fdef is None:
        print(f"  ERROR: Factor '{factor_name}' not found.")
        print(f"  Available: {sorted(ALL_FACTORS.keys())[:30]}...")
        return

    provider, valid = load_data(80, n_stocks)

    print(f"\n  Computing time-series IC for '{factor_name}' on {len(valid)} stocks...")
    t0 = time.time()

    ic_1d, ic_5d, ic_20d = [], [], []
    n_pairs = 0

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
                records.append({"open": o, "high": h, "low": l, "close": c, "volume": vol, "amount": amt})
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

        fseries = fseries.dropna()
        if len(fseries) < 20:
            continue

        close = df["close"]
        ret1 = close.pct_change(1).shift(-1)
        ret5 = close.pct_change(5).shift(-5)
        ret20 = close.pct_change(20).shift(-20)

        for ret_name, ret_series, collector in [
            ("1d", ret1, ic_1d), ("5d", ret5, ic_5d), ("20d", ret20, ic_20d)
        ]:
            aligned = pd.concat([fseries, ret_series], axis=1).dropna()
            if len(aligned) < 15:
                continue
            try:
                with np.errstate(invalid="ignore"):
                    ic, _ = stats.spearmanr(aligned.iloc[:, 0], aligned.iloc[:, 1])
                if not np.isnan(ic):
                    collector.append(ic)
                n_pairs += len(aligned)
            except Exception:
                continue

    elapsed = time.time() - t0

    # 输出
    print(f"\n{'=' * 65}")
    print(f"  IC RESULTS — {fdef.get('label', factor_name)}")
    print(f"  {len(valid)} stocks, {n_pairs} obs, {elapsed:.0f}s")
    print(f"{'=' * 65}")
    print(f"  {'Period':<8} {'IC Mean':>10} {'IC Std':>10} {'ICIR':>8} {'IC>0%':>8} {'N':>6}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*6}")

    for period_label, ic_list in [("1d", ic_1d), ("5d", ic_5d), ("20d", ic_20d)]:
        if len(ic_list) < 5:
            continue
        arr = np.array(ic_list)
        mean_ic = np.mean(arr)
        std_ic = np.std(arr)
        icir = mean_ic / std_ic if std_ic > 0 else 0
        ic_pos = (arr > 0).mean()
        print(f"  {period_label:<8} {mean_ic:>10.4f} {std_ic:>10.4f} {icir:>8.2f} {ic_pos:>7.1%} {len(arr):>6}")

    # 保存
    if args.output:
        pd.DataFrame({
            "period": ["1d"] * len(ic_1d) + ["5d"] * len(ic_5d) + ["20d"] * len(ic_20d),
            "ic": ic_1d + ic_5d + ic_20d,
        }).to_csv(args.output, index=False)
        print(f"\n  IC data saved to: {args.output}")

    print("\n  Done!")


# ======================================================================
# MODE: multi / smart — 因子组合回测
# ======================================================================
def _get_stock_data(provider, symbols):
    """将 THSDayDataProvider 数据转为 {symbol: DataFrame} 格式。"""
    stock_data = {}
    for sym in symbols:
        data = provider._read_day_file(sym)
        if not data:
            continue
        dates = sorted(data.keys())
        records = []
        for d in dates:
            o, h, l, c, amt, vol = data[d]
            if o > 0 and c > 0:
                dt = pd.Timestamp(str(d)[:4] + "-" + str(d)[4:6] + "-" + str(d)[6:8])
                records.append({
                    "date": dt, "open": o, "high": h, "low": l,
                    "close": c, "volume": vol, "amount": amt,
                })
        if records:
            df = pd.DataFrame(records).set_index("date")
            stock_data[sym] = df
    return stock_data


def _get_factor_spec(factor_names, method="static"):
    """根据因子名列表构建 factor_spec。

    因子规格: (name, compute_fn, direction, weight) — 含计算函数。
    """
    spec = []
    for name in factor_names:
        fdef = _resolve_factor(name)
        if fdef is None:
            print(f"  WARNING: Factor '{name}' not found, skipping")
            continue
        direction = fdef.get("direction", 1)
        compute_fn = fdef.get("compute")  # 可能是 callable 或 None
        spec.append((name, compute_fn, direction, 1.0))

    # 归一化权重
    if not spec:
        return []
    total = len(spec)
    spec = [(n, fn, d, 1.0 / total) for n, fn, d, _ in spec]
    return spec


def run_portfolio(mode="multi", factor_names=None, method="static",
                  n_stocks=500, top_k=30, start_date="2022-01-01", end_date="2025-12-31",
                  initial_capital=1_000_000, cache_path=None):
    """多因子/智能因子组合回测。

    使用 BacktestEngine.run_factor_portfolio() 执行月度调仓组合回测。
    """
    from backtest_engine import BacktestEngine

    if factor_names is None:
        factor_names = ["trend_bottom", "add_position", "bull_position", "ret_20d"]

    mode_label = {
        "multi": "Multi-Factor (Fixed Weights)",
        "smart": "Smart Adaptive (Market-State Weights)",
    }.get(mode, f"Factor Portfolio ({mode})")

    print("=" * 65)
    print(f"  {mode_label}")
    print(f"  Stocks: {n_stocks} | Top K: {top_k} | Method: {method}")
    print(f"  Factors: {factor_names}")
    print(f"  Period: {start_date} ~ {end_date}")
    print("=" * 65)

    # 1. 加载数据
    print(f"\n[1/4] Loading data...")
    provider, valid = load_data(500, n_stocks)

    # 2. 构建 stock_data 格式
    print(f"\n[2/4] Building stock data...")
    stock_data = _get_stock_data(provider, valid)
    print(f"  {len(stock_data)} stocks ready")

    # 3. 因子规格
    spec = _get_factor_spec(factor_names, method)

    # 4. 市场状态检测器 (smart 模式)
    market_state = None
    quality_filter = None

    if mode == "smart":
        # Market state detector
        class MarketState:
            BULL, BEAR, CHOPPY = "bull", "bear", "choppy"

            def __init__(self, provider):
                self._cache = {}
                self._index = {}
                for sym in ["999999", "1A0001", "000001"]:
                    data = provider._read_day_file(sym)
                    if data and len(data) > 500:
                        self._index = data
                        break

            def get_state(self, date_int):
                if date_int in self._cache:
                    return self._cache[date_int]
                if not self._index:
                    return self.CHOPPY
                dates = sorted(self._index.keys())
                prev = [d for d in dates if d <= date_int]
                if len(prev) < 120:
                    return self.CHOPPY
                closes = np.array([self._index[d][3] for d in prev[-120:]])
                if len(closes) < 60:
                    return self.CHOPPY
                c, ma20, ma60 = closes[-1], closes[-20:].mean(), closes[-60:].mean()
                if c > ma60 and ma20 > ma60:
                    s = self.BULL
                elif c < ma60:
                    s = self.BEAR
                else:
                    s = self.CHOPPY
                self._cache[date_int] = s
                return s

        market_state = MarketState(provider)

        # Quality filter
        def quality_filter(sym, cache, idx):
            if idx < 250 or idx >= len(cache["close"]):
                return False
            price = cache["close"][idx]
            if price < 3.0:
                return False
            if idx >= 20:
                recent = cache["close"][idx - 20:idx + 1]
                if min(recent) == max(recent):
                    return False
            return True

    # 5. 因子缓存 (如提供)
    factor_cache = []
    if cache_path and os.path.exists(cache_path):
        print(f"\n[3/4] Loading factor cache from {cache_path}...")
        with open(cache_path, "rb") as f:
            factor_cache = pickle.load(f)
        print(f"  {len(factor_cache)} entries loaded")
    else:
        print(f"\n[3/4] No factor cache, factors will be computed on-the-fly...")
        factor_cache = []

    # 6. 回测
    print(f"\n[4/4] Running backtest...")
    engine = BacktestEngine(stock_data, factor_cache)
    result = engine.run_factor_portfolio(
        factor_spec=spec,
        method="adaptive" if mode == "smart" else method,
        top_k=top_k,
        start=start_date,
        end=end_date,
        max_positions=top_k,
        initial_capital=initial_capital,
        market_state_detector=market_state,
        quality_filter=quality_filter,
    )

    if result.get("code") != 200:
        print("  ERROR: Backtest failed (insufficient data or periods)")
        return

    # 7. 显示结果
    metrics = result.get("metrics", {})
    eq_df = pd.DataFrame(result.get("equity_curve", []))
    bn_df = pd.DataFrame(result.get("benchmark_curve", []))
    display_results(metrics, eq_df, bn_df, mode_label)

    # 市场状态统计
    states_log = result.get("states_log", [])
    if states_log:
        sc = Counter(states_log)
        print(f"  Market States: ", end="")
        for s in ["bull", "bear", "choppy"]:
            print(f"{s}={sc.get(s, 0)} ", end="")
        print()

    # 保存
    output_path = args.output or f"d:\\quant_framework\\equity_curve_{mode}.csv"
    eq_df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"  Saved: {output_path}")
    print("  Done!")


# ======================================================================
# 主入口
# ======================================================================
if __name__ == "__main__":
    print(f"Unified Factor Backtest — mode={args.mode} | {time.strftime('%Y-%m-%d %H:%M')}")

    if args.mode == "single":
        run_single(
            factor_name=args.factor,
            n_stocks=args.stocks,
            start_date=args.start,
            end_date=args.end,
        )
    elif args.mode in ("multi", "smart"):
        run_portfolio(
            mode=args.mode,
            factor_names=factor_list,
            method=args.method,
            n_stocks=args.stocks,
            top_k=args.top_k,
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            cache_path=args.cache,
        )
