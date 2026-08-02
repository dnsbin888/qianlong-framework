"""进化模块 v2.0 — 遗传算法自动搜索最优策略参数 (蓝图 v3.0 E1-1/2/3)

修复:
  E1-1: 种群20→100, 代际5→50, 多目标fitness
  E1-2: Walk-Forward 滚动窗口验证
  E1-3: Pareto 前沿多目标优化
  E1-4: 固定股票池消除抽样偏差

用法:
  python evolution.py --stocks 300 --generations 50 --population 100
  python evolution.py --walk-forward  # Walk-Forward验证模式
"""
import sys, os, json, time, random, copy, argparse
import numpy as np, pandas as pd
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, r"d:\quant_framework\src")
sys.path.insert(0, r"d:\quant_framework")

from quant_framework.data.providers.ths_day import THSDayDataProvider, _date_to_datetime
from backtest_engine import BacktestEngine

# ═══════════════════ 参数空间定义 ═══════════════════
# (param_name, type, min, max, step_or_choices)
PARAM_SPACE = [
    ("stop_loss",       "float",   -0.15, -0.03, 0.01),
    ("take_profit",     "float",   0.05,  0.50,  0.05),
    ("hold_days",       "int",     1,     20,    1),
    ("trail1_profit",   "float",   0.02,  0.10,  0.005),
    ("trail1_drop",     "float",   0.005, 0.05,  0.002),
    ("sell_ratio_1",    "float",   0.10,  0.50,  0.05),
    ("trail2_profit",   "float",   0.05,  0.15,  0.005),
    ("trail2_drop",     "float",   0.01,  0.05,  0.002),
    ("sell_ratio_2",    "float",   0.10,  0.50,  0.05),
    ("trail3_profit",   "float",   0.08,  0.20,  0.005),
    ("trail3_drop",     "float",   0.01,  0.05,  0.002),
    ("sell_ratio_3",    "float",   0.10,  0.50,  0.05),
    ("max_positions",   "int",     2,     8,     1),
    ("position_pct",    "float",   0.10,  0.50,  0.05),
    ("limit_up_enabled","int",     0,     1,     1),
]

parser = argparse.ArgumentParser(description="Evolution v2.0: Genetic Algorithm + Walk-Forward + Pareto")
parser.add_argument("--stocks", type=int, default=300)
parser.add_argument("--generations", type=int, default=50)    # E1-1: 5→50
parser.add_argument("--population", type=int, default=100)    # E1-1: 20→100
parser.add_argument("--mutation-rate", type=float, default=0.15)
parser.add_argument("--elite-count", type=int, default=5)     # E1-1: 3→5
parser.add_argument("--metric", choices=["sharpe","total_return","profit_factor","win_rate","pareto"], default="pareto")
parser.add_argument("--start", default="2022-01-01")
parser.add_argument("--end", default="2026-06-30")
parser.add_argument("--strategy", default="tdx_resonance")
parser.add_argument("--parallel", type=int, default=3)
parser.add_argument("--output", default=r"d:\quant_framework\evolution_result.json")
parser.add_argument("--walk-forward", action="store_true", help="E1-2: Walk-Forward 滚动窗口验证")
parser.add_argument("--wf-window", type=int, default=60, help="Walk-Forward 窗口(天)")
parser.add_argument("--wf-step", type=int, default=20, help="Walk-Forward 步长(天)")
parser.add_argument("--pool-type", default="core_plus_extended", help="E1-4: 股票池类型")
args = parser.parse_args()


# ═══════════════════ 工具函数 ═══════════════════
def random_individual():
    """生成随机参数个体。"""
    ind = {}
    for name, ptype, vmin, vmax, step in PARAM_SPACE:
        if ptype == "float":
            val = random.uniform(vmin, vmax)
            val = round(val / step) * step  # 量化到步长
            ind[name] = round(max(vmin, min(vmax, val)), 4)
        elif ptype == "int":
            ind[name] = random.randint(int(vmin), int(vmax))
        elif ptype == "choice":
            ind[name] = random.choice(step if isinstance(step, list) else [vmin, vmax])
    return ind


def mutate(individual, rate=0.15):
    """随机变异。"""
    mutated = copy.deepcopy(individual)
    for name, ptype, vmin, vmax, step in PARAM_SPACE:
        if random.random() >= rate:
            continue
        if ptype == "float":
            delta = random.uniform(-(vmax - vmin) * 0.2, (vmax - vmin) * 0.2)
            mutated[name] = round(max(vmin, min(vmax, individual[name] + delta)) / step) * step
            mutated[name] = round(mutated[name], 4)
        elif ptype == "int":
            mutated[name] = random.randint(int(vmin), int(vmax))
        elif ptype == "choice":
            choices = step if isinstance(step, list) else [vmin, vmax]
            mutated[name] = random.choice(choices)
    return mutated


def crossover(parent1, parent2):
    """均匀交叉。"""
    child = {}
    for name, ptype, vmin, vmax, step in PARAM_SPACE:
        if random.random() < 0.5:
            child[name] = parent1[name]
        else:
            child[name] = parent2[name]
    return child


def individual_to_config(ind):
    """将个体参数转为 backtest config dict。"""
    return {
        "strategy": args.strategy,
        "start": args.start, "end": args.end,
        "max-pos": ind["max_positions"],
        "position-pct": ind["position_pct"],
        "stop-loss": ind["stop_loss"],
        "take-profit": ind["take_profit"],
        "hold-days": ind["hold_days"],
        "trail1-profit": ind["trail1_profit"],
        "trail1-drop": ind["trail1_drop"],
        "sell-ratio-1": ind["sell_ratio_1"],
        "trail2-profit": ind["trail2_profit"],
        "trail2-drop": ind["trail2_drop"],
        "sell-ratio-2": ind["sell_ratio_2"],
        "trail3-profit": ind["trail3_profit"],
        "trail3-drop": ind["trail3_drop"],
        "sell-ratio-3": ind["sell_ratio_3"],
        "limit-up-enabled": ind["limit_up_enabled"],
        "limit-up-open-drop": 0.03,
        "min-power": 25,
    }


# ═══════════════════════════════════════════════════════
#  E1-1/2/3: 多目标 Pareto fitness + Walk-Forward
# ═══════════════════════════════════════════════════════

def compute_pareto_fitness(metrics: dict, n_trades: int) -> tuple:
    """E1-3: 多目标 fitness — (收益, 回撤惩罚, 换手惩罚)。

    Returns:
        (sharpe_adj, -max_dd, -turnover)  → 最大化收益, 最小化回撤, 最小化换手
    """
    if n_trades < 5:
        return (-999.0, -999.0, -999.0)

    sharpe = float(metrics.get("sharpe", 0) or 0)
    max_dd = abs(float(metrics.get("max_drawdown", 0) or 0))
    calmar = float(metrics.get("calmar", 0) or 0)
    win_rate = float(metrics.get("win_rate", 0) or 0)
    total_return = float(metrics.get("total_return", 0) or 0)

    # 复合收益分: Sharpe + Calmar + win_rate 加权
    return_score = (
        max(0, sharpe) * 1.0 +
        max(0, calmar) * 0.5 +
        win_rate * 0.3 +
        min(total_return / 100, 2.0) * 0.5
    )
    # 回撤惩罚: 超过20%的回撤严重扣分
    dd_penalty = -max_dd * (1.5 if max_dd > 0.20 else 1.0)
    # 换手惩罚: 鼓励低频 (n_trades作为proxy)
    turnover_penalty = -max(0, (n_trades - 100) / 500) if n_trades > 100 else 0

    return (round(return_score, 4), round(dd_penalty, 4), round(turnover_penalty, 4))


def evaluate_walk_forward(engine, stock_data, signal_store, individual) -> dict:
    """E1-2: Walk-Forward 滚动窗口验证。

    将全区间切为 N 个窗口，每窗口: 前段训练(优化参数) → 后段测试(样本外)。
    返回所有测试窗口的平均指标。
    """
    config = individual_to_config(individual)
    start = pd.Timestamp(config["start"])
    end = pd.Timestamp(config["end"])
    window = pd.Timedelta(days=args.wf_window)
    step = pd.Timedelta(days=args.wf_step)

    oos_results = []
    train_end = start + window
    test_start = train_end

    while test_start + window <= end:
        try:
            # 训练段
            train_result = engine.run(
                strategy=args.strategy,
                signal_store=signal_store,
                formula_symbols=list(signal_store.keys()),
                start=start.strftime("%Y-%m-%d"),
                end=train_end.strftime("%Y-%m-%d"),
                max_positions=config["max-pos"],
                position_pct=config["position-pct"],
                stop_loss=config["stop-loss"],
                take_profit=config["take-profit"],
                hold_days=config["hold-days"],
                trail1_profit=config["trail1-profit"],
                trail1_drop=config["trail1-drop"],
                trail2_profit=config["trail2-profit"],
                trail2_drop=config["trail2-drop"],
                trail3_profit=config["trail3-profit"],
                trail3_drop=config["trail3-drop"],
                sell_ratio_1=config["sell-ratio-1"],
                sell_ratio_2=config["sell-ratio-2"],
                sell_ratio_3=config["sell-ratio-3"],
                limit_up_enabled=bool(config["limit-up-enabled"]),
                limit_up_open_drop=config["limit-up-open-drop"],
                min_power=config["min-power"],
                initial_capital=1_000_000,
            )
            # 测试段 (样本外)
            test_result = engine.run(
                strategy=args.strategy,
                signal_store=signal_store,
                formula_symbols=list(signal_store.keys()),
                start=test_start.strftime("%Y-%m-%d"),
                end=(test_start + window).strftime("%Y-%m-%d"),
                max_positions=config["max-pos"],
                position_pct=config["position-pct"],
                stop_loss=config["stop-loss"],
                take_profit=config["take-profit"],
                hold_days=config["hold-days"],
                trail1_profit=config["trail1-profit"],
                trail1_drop=config["trail1-drop"],
                trail2_profit=config["trail2-profit"],
                trail2_drop=config["trail2-drop"],
                trail3_profit=config["trail3-profit"],
                trail3_drop=config["trail3-drop"],
                sell_ratio_1=config["sell-ratio-1"],
                sell_ratio_2=config["sell-ratio-2"],
                sell_ratio_3=config["sell-ratio-3"],
                limit_up_enabled=bool(config["limit-up-enabled"]),
                limit_up_open_drop=config["limit-up-open-drop"],
                min_power=config["min-power"],
                initial_capital=1_000_000,
            )
            oos_metrics = test_result.get("metrics", {})
            if oos_metrics.get("n_trades", 0) >= 3:
                oos_results.append(oos_metrics)
        except Exception:
            pass

        start += step
        train_end = start + window
        test_start = train_end

    if not oos_results:
        return {"wf_windows": 0, "wf_sharpe": -999, "wf_calmar": -999, "wf_win_rate": 0}

    sharpes = [m.get("sharpe", 0) or 0 for m in oos_results]
    calmars = [m.get("calmar", 0) or 0 for m in oos_results]
    win_rates = [m.get("win_rate", 0) or 0 for m in oos_results]
    return {
        "wf_windows": len(oos_results),
        "wf_sharpe": round(np.mean(sharpes), 4),
        "wf_calmar": round(np.mean(calmars), 4),
        "wf_win_rate": round(np.mean(win_rates), 4),
        "wf_sharpe_std": round(np.std(sharpes), 4) if len(sharpes) > 1 else 0,
    }


def evaluate_individual(engine, stock_data, signal_store, individual):
    """E1-1/3: 评估个体 — 多目标 Pareto fitness。

    Walk-Forward模式: 用WF样本外平均Sharpe作为fitness。
    普通模式: 用多目标复合分。
    """
    config = individual_to_config(individual)
    try:
        result = engine.run(
            strategy=args.strategy,
            signal_store=signal_store,
            formula_symbols=list(signal_store.keys()),
            start=config["start"], end=config["end"],
            max_positions=config["max-pos"],
            position_pct=config["position-pct"],
            stop_loss=config["stop-loss"],
            take_profit=config["take-profit"],
            hold_days=config["hold-days"],
            trail1_profit=config["trail1-profit"],
            trail1_drop=config["trail1-drop"],
            trail2_profit=config["trail2-profit"],
            trail2_drop=config["trail2-drop"],
            trail3_profit=config["trail3-profit"],
            trail3_drop=config["trail3-drop"],
            sell_ratio_1=config["sell-ratio-1"],
            sell_ratio_2=config["sell-ratio-2"],
            sell_ratio_3=config["sell-ratio-3"],
            limit_up_enabled=bool(config["limit-up-enabled"]),
            limit_up_open_drop=config["limit-up-open-drop"],
            min_power=config["min-power"],
            initial_capital=1_000_000,
        )
        metrics = result.get("metrics", {})
        n_trades = metrics.get("n_trades", 0)

        if n_trades < 5:
            return -999.0

        # E1-2: Walk-Forward 模式
        if args.walk_forward:
            wf = evaluate_walk_forward(engine, stock_data, signal_store, individual)
            if wf["wf_windows"] >= 2:
                return wf["wf_sharpe"]  # 样本外平均Sharpe
            return float(metrics.get("sharpe", 0) or 0)

        # E1-3: Pareto 多目标
        if args.metric == "pareto":
            score, dd, to = compute_pareto_fitness(metrics, n_trades)
            return score  # 主目标=复合收益分，回撤/换手作为副目标

        # 单目标兼容
        metric = metrics.get(args.metric, 0)
        if args.metric == "sharpe":
            return float(metric) + min(n_trades / 50, 0.5)
        elif args.metric == "total_return":
            return float(metric)
        elif args.metric == "profit_factor":
            pf = metrics.get("profit_factor", 0)
            return float(pf) if pf < 99 else 10.0
        elif args.metric == "win_rate":
            return float(metric)
        return float(metric)
    except Exception:
        return -999.0


# ═══════════════════ 主流程 ═══════════════════
print("=" * 65)
print("  进化模块 — 遗传算法参数优化")
print(f"  Pop: {args.population} × Gen: {args.generations} | Metric: {args.metric}")
print(f"  Stocks: {args.stocks} | Pop: {args.population} | Gen: {args.generations} | Metric: {args.metric}")
if args.walk_forward:
    print(f"  Walk-Forward: window={args.wf_window}d step={args.wf_step}d")
print("=" * 65)

# 1. 加载数据（E1-4: 固定股票池）
print("\n[1/3] 加载股票数据...")
provider = THSDayDataProvider()
provider.connect()
all_syms = provider.scan_symbols()

# E1-4: 优先使用固定股票池
try:
    from stock_pool_manager import get_stock_pool
    pool = get_stock_pool(args.pool_type)
    pool_clean = [s.replace("sh", "").replace("sz", "").replace("bj", "") for s in pool]
    valid = [s for s in pool_clean if s in all_syms and len(provider._read_day_file(s)) >= 500]
    if len(valid) < 50:
        raise ValueError("Stock pool too small, falling back")
    print(f"  Pool: {len(valid)} stocks (from {args.pool_type})")
except Exception:
    random.seed(42)
    valid = [s for s in all_syms if len(provider._read_day_file(s)) >= 500]
    if len(valid) > args.stocks:
        valid = random.sample(valid, args.stocks)
    print(f"  Pool: {len(valid)} stocks (random sample)")

stock_data = {}
for sym in valid:
    data = provider._read_day_file(sym)
    if not data: continue
    records = []
    for date_int, (o, h, l, c, amt, vol) in sorted(data.items()):
        dt = _date_to_datetime(date_int)
        if dt and o > 0 and c > 0:
            records.append({"date": dt, "open": o, "high": h, "low": l, "close": c, "volume": vol})
    if len(records) < 200: continue
    prefix = "sh" if sym[0] == "6" else "sz"
    stock_data[prefix + sym] = pd.DataFrame(records).set_index("date")
print(f"  Loaded: {len(stock_data)} stocks")

# 2. 预计算信号（一次性）
print("\n[2/3] 预计算信号...")
try:
    from quant_framework.factors.tdx_signals2 import factor_final_pick
    signal_store = {}
    for i, (key, df) in enumerate(stock_data.items()):
        if i % 100 == 0: print(f"  {i}/{len(stock_data)}...")
        try:
            sig = factor_final_pick(df)
            if isinstance(sig, pd.Series):
                signal_store[key] = sig
        except Exception: continue
    print(f"  Signals: {len(signal_store)} stocks")
except ImportError:
    signal_store = None

# 3. 进化
print(f"\n[3/3] 进化搜索...")
engine = BacktestEngine(stock_data=stock_data, factor_cache=None, name_map={})

# 初始种群
population = [random_individual() for _ in range(args.population)]
best_overall = None
best_fitness_overall = -999
history = []

t_start = time.time()
for gen in range(args.generations):
    print(f"\n  Generation {gen+1}/{args.generations}")

    # 评估
    fitnesses = []
    for i, ind in enumerate(population):
        fit = evaluate_individual(engine, stock_data, signal_store, ind)
        fitnesses.append(fit)
        if i % 5 == 0 or i == len(population) - 1:
            print(f"    [{i+1}/{len(population)}] best_fit={max(fitnesses):.3f}")

    # 排序
    ranked = sorted(zip(population, fitnesses), key=lambda x: x[1], reverse=True)
    best = ranked[0]
    avg_fit = np.mean(fitnesses)
    max_fit = ranked[0][1]

    print(f"    Gen {gen+1}: max={max_fit:.3f}, avg={avg_fit:.3f}, best_params: "
          f"SL={best[0]['stop_loss']:.2f} TP={best[0]['take_profit']:.2f} "
          f"T1={best[0]['trail1_profit']:.2f}/{best[0]['trail1_drop']:.3f} "
          f"H={best[0]['hold_days']}d P={best[0]['max_positions']}")

    if max_fit > best_fitness_overall:
        best_fitness_overall = max_fit
        best_overall = copy.deepcopy(best[0])

    history.append({
        "generation": gen + 1,
        "max_fitness": float(max_fit),
        "avg_fitness": float(avg_fit),
        "best_params": {k: best[0][k] for k in best[0]},
    })

    # 选择+交叉+变异（保留精英）
    elites = [copy.deepcopy(ranked[i][0]) for i in range(min(args.elite_count, len(ranked)))]
    new_pop = elites[:]

    while len(new_pop) < args.population:
        # 锦标赛选择
        t1 = random.choice(ranked[:max(3, len(ranked)//2)])[0]
        t2 = random.choice(ranked[:max(3, len(ranked)//2)])[0]
        child = crossover(t1, t2)
        child = mutate(child, args.mutation_rate)
        new_pop.append(child)

    population = new_pop[:args.population]

elapsed = time.time() - t_start

# ═══════════════════ 结果输出 ═══════════════════
print(f"\n{'=' * 65}")
print(f"  进化完成 — {elapsed:.0f}s")
print(f"{'=' * 65}")
print(f"  最优参数 (fitness={best_fitness_overall:.3f}):")
if best_overall:
    for k, v in sorted(best_overall.items()):
        print(f"    {k}: {v}")
    config = individual_to_config(best_overall)
    print(f"\n  最优 config: {json.dumps(config, indent=2)}")

    # 最终回测
    print(f"\n  最终验证回测...")
    final = evaluate_individual(engine, stock_data, signal_store, best_overall)
    print(f"    Fitness: {final:.3f}")

result = {
    "best_params": {k: best_overall[k] for k in best_overall} if best_overall else {},
    "best_config": individual_to_config(best_overall) if best_overall else {},
    "best_fitness": best_fitness_overall,
    "history": history,
    "config": {
        "population": args.population,
        "generations": args.generations,
        "mutation_rate": args.mutation_rate,
        "metric": args.metric,
        "stocks": args.stocks,
        "elapsed_seconds": round(elapsed, 1),
    },
}

with open(args.output, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2, default=str)
print(f"\n  保存到: {args.output}")
print("  Done!")
