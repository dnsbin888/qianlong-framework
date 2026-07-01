"""快速回测 — 数据加载 + 信号预计算 + 调用 BacktestEngine

P0-2修复: 不再自建回测循环,统一调用 BacktestEngine,
确保交易成本/止损止盈/移动止盈等规则与主引擎完全一致。

P1修复: 支持 CLI 参数传入，UI 调整的参数能实际生效。
  python run_backtest_fast.py --stop-loss -0.08 --take-profit 0.10 --max-pos 5
  python run_backtest_fast.py --config '{"stop_loss":-0.08,"take_profit":0.10}'
"""
import sys, os, json, argparse, numpy as np, pandas as pd
from datetime import datetime
sys.path.insert(0, r"d:\quant_framework\src")
sys.path.insert(0, r"d:\quant_framework")
from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from quant_framework.factors.tdx_signals2 import factor_final_pick
from backtest_engine import BacktestEngine
from quant_framework.data.backtest_store import BacktestStore

# ── 命令行参数 ──
parser = argparse.ArgumentParser(description="Fast Backtest (统一引擎)")
parser.add_argument("--data-root", default=r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc")
parser.add_argument("--start", default="2023-01-01")
parser.add_argument("--end", default="2025-06-01")
parser.add_argument("--max-stocks", type=int, default=300)
parser.add_argument("--initial-cash", type=float, default=1_000_000.0)
parser.add_argument("--max-pos", type=int, default=3)
parser.add_argument("--position-pct", type=float, default=0.30)
parser.add_argument("--strategy", default="tdx2_final")
parser.add_argument("--stop-loss", type=float, default=-0.05)
parser.add_argument("--take-profit", type=float, default=0.08)
parser.add_argument("--hold-days", type=int, default=1)
parser.add_argument("--trail1-profit", type=float, default=0.05)
parser.add_argument("--trail1-drop", type=float, default=0.02)
parser.add_argument("--trail2-profit", type=float, default=0.07)
parser.add_argument("--trail2-drop", type=float, default=0.03)
parser.add_argument("--trail3-profit", type=float, default=0.12)
parser.add_argument("--trail3-drop", type=float, default=0.03)
parser.add_argument("--sell-ratio-1", type=float, default=0.25)
parser.add_argument("--sell-ratio-2", type=float, default=0.25)
parser.add_argument("--sell-ratio-3", type=float, default=0.25)
parser.add_argument("--limit-up-enabled", type=int, default=1)
parser.add_argument("--limit-up-open-drop", type=float, default=0.03)
parser.add_argument("--commission", type=float, default=0.00025)
parser.add_argument("--stamp-duty", type=float, default=0.001)
parser.add_argument("--config", type=str, default=None,
                    help="JSON config string that overrides individual args")
args = parser.parse_args()

# JSON config 覆盖 (支持从 UI 传入完整参数)
if args.config:
    try:
        cfg = json.loads(args.config)
        for key, val in cfg.items():
            if hasattr(args, key.replace("-", "_")):
                setattr(args, key.replace("-", "_"), val)
    except json.JSONDecodeError:
        print(f"  WARNING: Invalid JSON config: {args.config}")

DATA_ROOT = args.data_root
START = args.start
END = args.end
MAX_STOCKS = args.max_stocks
INITIAL_CASH = args.initial_cash
MAX_POS = args.max_pos
POS_PCT = args.position_pct
STRATEGY_NAME = args.strategy

print("=" * 60)
print(f"  Fast Backtest — {STRATEGY_NAME} (统一引擎)")
print("=" * 60)

# ---- Step 1: Load ----
print("\n[1/3] Loading data...")
provider = THSDayDataProvider(DATA_ROOT)
provider.connect()
symbols = provider.scan_symbols()
symbols = [s for s in symbols if s[0] in ("6", "0", "3") and len(s) == 6 and s.isdigit()][:MAX_STOCKS]
print(f"  Scanning {len(symbols)} stocks...")

stock_dfs = {}
for sym in symbols:
    data = provider._read_day_file(sym)
    if not data:
        continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
        dt = _date_to_datetime(date_int)
        if dt is None or o <= 0 or c <= 0:
            continue
        records.append({"date": dt, "open": o, "high": h, "low": l,
                        "close": c, "volume": vol})
    if len(records) < 200:
        continue
    # 转换为引擎格式: key加sh/sz前缀
    prefix = "sh" if sym[0] == "6" else "sz"
    stock_dfs[prefix + sym] = pd.DataFrame(records).set_index("date")

print(f"  Loaded {len(stock_dfs)} stocks")

# ---- Step 2: Compute signals (预计算) ----
print("\n[2/3] Computing signals (one-time)...")
signal_store = {}
for i, (sym_6d, df) in enumerate(zip(
    [s[2:] if s.startswith(("sh","sz")) else s for s in stock_dfs],
    stock_dfs.values()
)):
    if i % 50 == 0:
        print(f"  {i}/{len(stock_dfs)}...")
    try:
        sig = factor_final_pick(df)
        if isinstance(sig, pd.Series):
            # signal_store的key与stock_dfs一致
            key = list(stock_dfs.keys())[i]
            signal_store[key] = sig
    except Exception:
        continue

print(f"  Computed signals for {len(signal_store)} stocks")

# ---- Step 3: Run backtest via BacktestEngine ----
print("\n[3/3] Running backtest (unified engine)...")

engine = BacktestEngine(
    stock_data=stock_dfs,
    factor_cache=None,      # 信号由signal_store提供,不需要factor_cache
    name_map={},            # 可后续补充
)

result = engine.run(
    strategy=STRATEGY_NAME,
    signal_store=signal_store,       # 注入预计算信号
    formula_symbols=list(signal_store.keys()),  # 使用信号池作为股票池
    start=START,
    end=END,
    max_positions=MAX_POS,
    position_pct=POS_PCT,
    stop_loss=args.stop_loss,
    take_profit=args.take_profit,
    hold_days=args.hold_days,
    trail1_profit=args.trail1_profit,
    trail1_drop=args.trail1_drop,
    trail2_profit=args.trail2_profit,
    trail2_drop=args.trail2_drop,
    trail3_profit=args.trail3_profit,
    trail3_drop=args.trail3_drop,
    sell_ratio_1=args.sell_ratio_1,
    sell_ratio_2=args.sell_ratio_2,
    sell_ratio_3=args.sell_ratio_3,
    limit_up_enabled=bool(args.limit_up_enabled),
    limit_up_open_drop=args.limit_up_open_drop,
    initial_capital=INITIAL_CASH,
    commission_rate=args.commission,
    stamp_duty=args.stamp_duty,
)

# ---- Results ----
metrics = result.get("metrics", {})
trades = result.get("results", [])

print(f"\n{'='*60}")
print(f"  BACKTEST RESULTS ({STRATEGY_NAME})")
print(f"{'='*60}")
if metrics:
    print(f"  总交易:    {metrics.get('n_trades', 0)} 笔")
    print(f"  胜率:      {metrics.get('win_rate', 0):.1%}")
    print(f"  总收益:    {metrics.get('total_return', 0):+.2%}")
    print(f"  年化收益:  {metrics.get('annual_return', 0):+.2%}")
    print(f"  夏普比率:  {metrics.get('sharpe', 0):.2f}")
    print(f"  最大回撤:  {metrics.get('max_drawdown', 0):.2%}")
    print(f"  盈亏比:    {metrics.get('profit_factor', 0):.2f}")
    print(f"  总盈亏:    {metrics.get('total_pnl', 0):+,.0f} 元")
    print(f"  VaR(95%):  {metrics.get('var_95', 0):.4f}")

    # 退出方式统计
    exit_stats = metrics.get('exit_stats', {})
    if exit_stats:
        print(f"\n  --- 退出方式统计 ---")
        for ext, stats in exit_stats.items():
            names = {"stop_loss":"止损", "take_profit":"止盈",
                     "trail_stop":"追踪止盈", "normal":"正常到期",
                     "force_close":"强制清仓"}
            print(f"  {names.get(ext, ext):<8s}: "
                  f"{stats['count']}笔 | "
                  f"胜率{stats['win_rate']:.0%} | "
                  f"均值{stats['avg_return']:+.2%} | "
                  f"盈亏{stats['total_pnl']:+,.0f}元")
else:
    print("  无交易结果!")

print(f"{'='*60}")

# ---- Save (P1#5: 使用 BacktestStore 避免覆盖) ----
eq = result.get("equity_curve", [])
store = BacktestStore(r"d:\quant_framework")
run_id = store.save_run(
    strategy_name=STRATEGY_NAME,
    equity_curve=eq,
    trades=trades,
    config={
        "strategy": STRATEGY_NAME, "max_positions": MAX_POS,
        "position_pct": POS_PCT, "stop_loss": args.stop_loss,
        "take_profit": args.take_profit, "start": START, "end": END,
    },
)

metrics = store.compute_metrics(
    pd.DataFrame(eq) if eq else pd.DataFrame(),
    pd.DataFrame(trades) if trades else pd.DataFrame(),
)

print(f"\n  run_id: {run_id}")
print(f"  equity_curve: {len(eq)} days -> equity_curve.csv (also equity_{run_id}.csv)")
print(f"  trades: {len(trades)} -> trade_log.csv (also trades_{run_id}.csv)")
print(f"  metrics: total_ret={metrics['total_return']:.2%} sharpe={metrics['sharpe']:.2f}")
print("\nDone!")
