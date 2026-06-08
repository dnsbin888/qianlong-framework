"""Walk-Forward Analysis Runner — 独立WFA执行脚本。

用法:
  python run_wfa.py --stock 600519 --train 252 --test 63
  python run_wfa.py --stock 600519 --n-folds 5 --output wfa_result.json
"""
import sys, os, json, argparse, time, random, numpy as np, pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, r"d:\quant_framework\src")
sys.path.insert(0, r"d:\quant_framework")

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from backtest_engine import BacktestEngine

parser = argparse.ArgumentParser(description="Walk-Forward Analysis Runner")
parser.add_argument("--stock", type=str, default="600519")
parser.add_argument("--train", type=int, default=252, help="训练窗口交易日数")
parser.add_argument("--test", type=int, default=63, help="测试窗口交易日数")
parser.add_argument("--n-folds", type=int, default=4, help="折叠数")
parser.add_argument("--pool-size", type=int, default=80, help="股票池大小")
parser.add_argument("--start", type=str, default="2020-01-01")
parser.add_argument("--end", type=str, default="2025-06-01")
parser.add_argument("--output", type=str, default=r"d:\quant_framework\wfa_result.json")
parser.add_argument("--param-grid", type=str, default=None,
                    help='JSON: {"stop_loss":[-0.03,-0.05,-0.07],"take_profit":[0.05,0.08,0.10],"max_positions":[3,5]}')
args = parser.parse_args()

# ── 参数网格 ──
if args.param_grid:
    param_grid = json.loads(args.param_grid)
else:
    param_grid = {
        "stop_loss": [-0.03, -0.05, -0.07],
        "take_profit": [0.05, 0.08, 0.10],
    }

# ── 从交易日数计算 train_ratio ──
total_window = args.train + args.test
train_ratio = args.train / total_window

print("=" * 60)
print(f"  Walk-Forward Analysis")
print(f"  Stock: {args.stock} | Folds: {args.n_folds} | Train: {args.train}d | Test: {args.test}d")
print(f"  Param grid: {param_grid}")
print("=" * 60)

# ── 1. 加载数据 ──
print("\n[1/3] Loading stock data...")
provider = THSDayDataProvider()
provider.connect()

# 确保目标股票在池中
all_syms = provider.scan_symbols()
target = args.stock
if target not in all_syms:
    # 尝试加前缀
    for prefix in ["sh", "sz"]:
        if prefix + target in all_syms:
            target = prefix + target
            break
    else:
        print(f"  ERROR: Stock {args.stock} not found in data directory")
        sys.exit(1)

# 采样股票池
random.seed(42)
valid = [s for s in all_syms if len(provider._read_day_file(s)) >= 250]
if target not in valid:
    valid.append(target)
if len(valid) > args.pool_size:
    pool = random.sample([s for s in valid if s != target], min(args.pool_size - 1, len(valid) - 1))
    pool.append(target)
else:
    pool = valid

print(f"  Pool: {len(pool)} stocks (includes {target})")

# 转换为引擎格式
stock_dfs = {}
for sym in pool:
    data = provider._read_day_file(sym)
    if not data:
        continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
        dt = _date_to_datetime(date_int)
        if dt is None or o <= 0 or c <= 0:
            continue
        records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    if len(records) < 200:
        continue
    prefix = "sh" if (sym[0] if sym[0].isdigit() else sym[2]) == "6" else "sz"
    key = (prefix + sym) if sym.isdigit() else sym
    stock_dfs[key] = pd.DataFrame(records).set_index("date")

print(f"  Loaded {len(stock_dfs)} stocks for engine")

# ── 2. 预计算信号 ──
print("\n[2/3] Pre-computing signals...")
try:
    from quant_framework.factors.tdx_signals2 import factor_final_pick
    signal_store = {}
    for i, (key, df) in enumerate(stock_dfs.items()):
        if i % 50 == 0:
            print(f"  {i}/{len(stock_dfs)}...")
        try:
            sig = factor_final_pick(df)
            if isinstance(sig, pd.Series):
                signal_store[key] = sig
        except Exception:
            continue
    print(f"  Computed signals for {len(signal_store)} stocks")
except ImportError:
    signal_store = None
    print("  No signal pre-computation (factor_final_pick not available)")

# ── 3. 运行 WFA ──
print("\n[3/3] Running Walk-Forward Analysis...")
t0 = time.time()

engine = BacktestEngine(
    stock_data=stock_dfs,
    factor_cache=None,
    name_map={},
)

try:
    result = engine.walk_forward(
        start=args.start,
        end=args.end,
        n_folds=args.n_folds,
        train_ratio=train_ratio,
        param_grid=param_grid,
        strategy="tdx_resonance",
        signal_field="signal_resonance",
        signal_store=signal_store,
        max_positions=3,
        position_pct=0.3,
        initial_capital=1_000_000,
    )
    elapsed = time.time() - t0
    print(f"  WFA completed in {elapsed:.0f}s")

    # ── 添加 equity_curve 到结果 (从每个训练结果中获取) ──
    # walk_forward 目前只返回 metrics，不返回 equity curves。
    # 作为折中，添加一个简化的 equity 序列（基于每折的 total_return 推算）
    folds = result.get("folds", [])
    for f in folds:
        f["equity_train"] = []  # 暂不包含完整权益曲线（需要修改 walk_forward 内部逻辑）
        f["equity_test"] = []

    # ── 保存 ──
    output = {
        "stock": args.stock,
        "params": {
            "n_folds": args.n_folds,
            "train_days": args.train,
            "test_days": args.test,
            "train_ratio": train_ratio,
            "param_grid": param_grid,
            "period": f"{args.start} ~ {args.end}",
            "pool_size": len(stock_dfs),
            "elapsed_seconds": round(elapsed, 1),
        },
        "folds": result.get("folds", []),
        "summary": result.get("summary", {}),
        "config": result.get("config", {}),
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=str)

    # ── 终端输出 ──
    summary = result.get("summary", {})
    print(f"\n{'=' * 60}")
    print(f"  WFA RESULTS")
    print(f"{'=' * 60}")
    print(f"  Folds completed: {len(folds)}/{args.n_folds}")
    if summary:
        print(f"  Avg Test Sharpe:   {summary.get('avg_test_sharpe', 0):.2f}")
        print(f"  Avg Sharpe Decay:  {summary.get('avg_sharpe_decay', 0):.2f}")
        print(f"  Param Stability:   {summary.get('param_stability', {}).get('overall_grade', '?')}")
        print(f"  Conclusion: {summary.get('conclusion', '')}")
    print(f"\n  Results saved to: {args.output}")

except Exception as e:
    import traceback
    print(f"\n  ERROR: WFA failed: {e}")
    traceback.print_exc()

    # 保存错误信息
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump({"error": str(e), "stock": args.stock}, f)
    sys.exit(1)
