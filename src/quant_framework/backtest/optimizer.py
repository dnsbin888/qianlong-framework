"""策略参数优化器 — 网格搜索 + 遗传算法 (v1.0)
================================================

纯 Python 标准库实现，零外部依赖。
所有搜索仅做回测计算，不向实盘发送下单指令 (宪法 2.3)。

Usage::

    from quant_framework.backtest.optimizer import grid_search, genetic_optimize

    # 网格搜索
    results = grid_search("ma_cross", {"fast_period": [3,5,10], "slow_period": [15,20,30]},
                          "600000", "2025-01-01", "2025-06-01")

    # 遗传算法
    best = genetic_optimize("ma_cross", "600000", "2025-01-01", "2025-06-01",
                            pop_size=20, max_generations=10)
"""

from __future__ import annotations

import itertools
import logging
import random
import time
from typing import Any

from quant_framework.backtest.engine import quick_backtest

logger = logging.getLogger("quant_framework.backtest.optimizer")


# ═══════════════════════════════════════════════════════════════
#  网格搜索
# ═══════════════════════════════════════════════════════════════

def grid_search(
    strategy_name: str,
    param_grid: dict[str, list],
    symbol: str,
    start_date: str = "2025-01-01",
    end_date: str = "",
) -> list[dict[str, Any]]:
    """网格搜索：遍历所有参数组合，返回按夏普比率降序排列的结果。

    Args:
        strategy_name: 策略名 (如 "ma_cross")
        param_grid: 参数网格 (如 {"fast_period": [3,5,10], "slow_period": [15,20,30]})
        symbol: 股票代码
        start_date: 回测开始日期
        end_date: 回测结束日期

    Returns:
        list[dict]: 每项包含 params, total_return, sharpe_ratio,
                    max_drawdown, total_trades，按 sharpe_ratio 降序
        策略未注册时返回 [{"error": "策略未注册", ...}]
    """
    # 预检查策略存在性
    from quant_framework.strategy.registry import StrategyRegistry
    reg = StrategyRegistry.instance()
    if reg.get(strategy_name) is None:
        msg = f"策略 '{strategy_name}' 未注册，无法执行网格搜索"
        logger.warning(msg)
        return [{"error": msg, "strategy_name": strategy_name}]

    # 生成所有参数组合
    keys: list[str] = list(param_grid.keys())
    values: list[list] = list(param_grid.values())
    total: int = 1
    for v in values:
        total *= len(v)

    results: list[dict[str, Any]] = []
    idx: int = 0

    for combo in itertools.product(*values):
        idx += 1
        params: dict[str, Any] = dict(zip(keys, combo))
        logger.info(f"[{idx}/{total}] {params} 回测中...")

        try:
            r = quick_backtest(
                strategy_name, symbol, start_date, end_date, **params
            )
        except Exception as e:
            logger.error(f"回测异常 {params}: {e}")
            continue

        if not r:
            logger.warning(f"[{idx}/{total}] {params} → 无结果，跳过")
            continue

        sharpe = r.get("sharpe_ratio", 0.0)
        ret = r.get("total_return", 0.0)
        dd = r.get("max_drawdown", 0.0)

        results.append({
            "params": params,
            "total_return": ret,
            "sharpe_ratio": sharpe,
            "max_drawdown": dd,
            "total_trades": r.get("total_trades", 0),
        })

        arrow = "↑" if sharpe > 0 else "↓" if sharpe < 0 else "→"
        logger.info(
            f"[{idx}/{total}] {params} → "
            f"sharpe={sharpe:.3f} {arrow} "
            f"return={ret:.4f} dd={dd:.4f}"
        )

    # 按夏普降序
    results.sort(key=lambda x: x.get("sharpe_ratio", 0.0), reverse=True)
    logger.info(f"网格搜索完成: {len(results)}/{total} 组有效结果")
    return results


# ═══════════════════════════════════════════════════════════════
#  遗传算法
# ═══════════════════════════════════════════════════════════════

# 默认参数范围 (ma_cross)
_DEFAULT_PARAM_RANGES: dict[str, tuple[int, int]] = {
    "fast_period": (3, 30),
    "slow_period": (10, 60),
}


def genetic_optimize(
    strategy_name: str,
    symbol: str,
    start_date: str = "2025-01-01",
    end_date: str = "",
    param_ranges: dict[str, tuple[int, int]] | None = None,
    pop_size: int = 20,
    max_generations: int = 10,
    max_seconds: int = 60,
    early_stop_threshold: float = 0.05,
    early_stop_generations: int = 3,
) -> dict[str, Any]:
    """遗传算法优化策略参数。

    纯 Python 标准库实现 (random + itertools)，不依赖 DEAP。

    Args:
        strategy_name: 策略名
        symbol: 股票代码
        start_date / end_date: 回测区间
        param_ranges: 参数范围 (如 {"fast_period": (3, 30), "slow_period": (10, 60)})
                      None = 自动推断
        pop_size: 种群大小
        max_generations: 最大迭代代数 (CPU保底)
        max_seconds: 最大耗时秒数 (超时保底)
        early_stop_threshold: 早停阈值
        early_stop_generations: 早停代数

    Returns:
        {"best_params": {...}, "best_sharpe": float, "best_return": float,
         "history": [...], "generations_run": int, "total_seconds": float}
    """
    import logging as _log
    _ga_logger = _log.getLogger("quant_framework.backtest.optimizer.ga")

    start_time: float = time.time()

    # 预检查策略存在性
    from quant_framework.strategy.registry import StrategyRegistry
    reg = StrategyRegistry.instance()
    if reg.get(strategy_name) is None:
        msg = f"策略 '{strategy_name}' 未注册"
        _ga_logger.warning(msg)
        return {"error": msg}

    # 确定参数范围
    ranges: dict[str, tuple[int, int]] = param_ranges or _DEFAULT_PARAM_RANGES
    keys: list[str] = list(ranges.keys())

    # 初始化种群
    population: list[dict[str, int]] = []
    for _ in range(pop_size):
        individual: dict[str, int] = {}
        for k in keys:
            lo, hi = ranges[k]
            individual[k] = random.randint(lo, hi)
        population.append(individual)

    # 适应度缓存
    fitness_cache: dict[tuple, float] = {}

    def fitness(individual: dict[str, int]) -> float:
        """适应度函数 = shapre_ratio，带缓存."""
        key = tuple(sorted(individual.items()))
        if key in fitness_cache:
            return fitness_cache[key]

        try:
            r = quick_backtest(
                strategy_name, symbol, start_date, end_date, **individual
            )
            val = r.get("sharpe_ratio", 0.0) if r else 0.0
        except Exception:
            val = 0.0

        fitness_cache[key] = val
        return val

    best_individual: dict[str, int] | None = None
    best_fitness: float = -999.0
    best_return: float = 0.0
    history: list[dict[str, Any]] = []
    no_improve_count: int = 0

    for gen in range(max_generations):
        # 超时保底
        if time.time() - start_time > max_seconds:
            _ga_logger.info(f"超时保底触发 → 第 {gen} 代终止")
            break

        # 评估适应度
        scored: list[tuple[float, dict[str, int]]] = []
        for ind in population:
            f = fitness(ind)
            if f > best_fitness:
                best_fitness = f
                best_individual = dict(ind)
                try:
                    r = quick_backtest(
                        strategy_name, symbol, start_date, end_date, **ind
                    )
                    best_return = r.get("total_return", 0.0) if r else 0.0
                except Exception:
                    pass
                no_improve_count = 0
            scored.append((f, ind))

        # 统计
        scored.sort(key=lambda x: x[0], reverse=True)
        gen_best = scored[0][0]
        gen_avg = sum(s[0] for s in scored) / len(scored)
        gen_worst = scored[-1][0]

        history.append({
            "generation": gen + 1,
            "best_sharpe": gen_best,
            "avg_sharpe": gen_avg,
            "worst_sharpe": gen_worst,
            "best_params": dict(scored[0][1]),
        })

        _ga_logger.info(
            f"[Gen {gen+1}/{max_generations}] "
            f"best={gen_best:.3f} avg={gen_avg:.3f} "
            f"best_params={scored[0][1]}"
        )

        # 早停检查
        if gen > 0:
            prev_best = history[-2]["best_sharpe"]
            improvement = abs(gen_best - prev_best) / max(abs(prev_best), 1e-9)
            if improvement < early_stop_threshold:
                no_improve_count += 1
                if no_improve_count >= early_stop_generations:
                    _ga_logger.info(f"早停触发 → 连续 {early_stop_generations} 代无显著提升")
                    break
            else:
                no_improve_count = 0

        # 选择 + 交叉 + 变异 → 新一代
        new_population: list[dict[str, int]] = []

        # 精英保留: 前 2 名直接进入下一代
        new_population.append(dict(scored[0][1]))
        if len(scored) > 1:
            new_population.append(dict(scored[1][1]))

        while len(new_population) < pop_size:
            # 锦标赛选择 (size=3)
            parent1 = _tournament_select(scored, k=3)
            parent2 = _tournament_select(scored, k=3)

            # 单点交叉
            child: dict[str, int] = {}
            crossover_point = random.randint(1, len(keys) - 1) if len(keys) > 1 else 0
            for i, k in enumerate(keys):
                child[k] = parent1[k] if i < crossover_point else parent2[k]

            # 随机变异 (10% 概率)
            if random.random() < 0.10:
                mutate_key = random.choice(keys)
                lo, hi = ranges[mutate_key]
                child[mutate_key] = random.randint(lo, hi)

            new_population.append(child)

        population = new_population

    elapsed = time.time() - start_time

    return {
        "best_params": best_individual or {},
        "best_sharpe": best_fitness,
        "best_return": best_return,
        "history": history,
        "generations_run": len(history),
        "total_seconds": round(elapsed, 1),
    }


def _tournament_select(
    scored: list[tuple[float, dict[str, int]]], k: int = 3
) -> dict[str, int]:
    """锦标赛选择：随机取 k 个个体，返回适应度最高的。"""
    candidates = random.sample(scored, min(k, len(scored)))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return dict(candidates[0][1])


# ═══════════════════════════════════════════════════════════════
#  E231: 自动化反馈回路
# ═══════════════════════════════════════════════════════════════

_CONFIG_PATH: str = r"D:\quant_framework\live_trader_config.json"
_BACKUP_PATH: str = r"D:\quant_framework\config_backup.json"


def update_strategy_config(
    strategy_name: str, best_params: dict[str, Any]
) -> bool:
    """将最优参数写入 live_trader_config.json。

    宪法 2.2 / 5.3: 原子备份 + 严格校验 + 降级保底。

    Args:
        strategy_name: 策略名 (如 "ma_cross")
        best_params: 最优参数字典 (如 {"fast_period": 10, "slow_period": 30})

    Returns:
        True 写入成功，False 失败 (降级，不抛异常)
    """
    import json as _json
    import os as _os
    import shutil as _shutil

    # ── 1. 格式校验 ──
    if not isinstance(best_params, dict) or not best_params:
        logger.error(f"best_params 格式无效: {type(best_params)} 或为空")
        return False

    for key, val in best_params.items():
        if not isinstance(val, (int, float)):
            logger.error(
                f"参数 '{key}' 类型无效: {type(val).__name__} (应为 int/float)"
            )
            return False
        if isinstance(val, float) and (val != val or val in (float("inf"), float("-inf"))):
            logger.error(f"参数 '{key}' 值为 NaN/Inf，拒绝写入")
            return False

    # ── 2. 原子备份 ──
    try:
        if _os.path.exists(_CONFIG_PATH):
            _shutil.copy2(_CONFIG_PATH, _BACKUP_PATH)
            logger.info(f"配置已备份至 {_BACKUP_PATH}")
    except Exception as e:
        logger.error(f"配置备份失败: {e}")
        # 备份失败不阻塞，继续尝试更新

    # ── 3. 读取 → 更新 → 写入 ──
    config: dict[str, Any] = {}
    try:
        if _os.path.exists(_CONFIG_PATH):
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                config = _json.load(f)
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
        return False

    # 确保 strategy_params 层级存在
    if "strategy_params" not in config:
        config["strategy_params"] = {}

    config["strategy_params"][strategy_name] = best_params

    try:
        tmp_path = _CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            _json.dump(config, f, ensure_ascii=False, indent=2)
        _os.replace(tmp_path, _CONFIG_PATH)  # 原子写入
        logger.info(
            f"✅ 最优参数已写入配置: strategy_params.{strategy_name} = {best_params}"
        )
        return True
    except PermissionError:
        logger.warning(
            "[WARN] 无法写入配置，请检查文件权限"
        )
        return False
    except Exception as e:
        logger.error(f"配置写入失败: {e}")
        # 尝试回滚
        try:
            if _os.path.exists(_BACKUP_PATH):
                _shutil.copy2(_BACKUP_PATH, _CONFIG_PATH)
                logger.info("已从备份恢复配置")
        except Exception:
            pass
        return False


def auto_tune(
    strategy_name: str,
    symbol: str,
    start_date: str = "2025-01-01",
    end_date: str = "",
    pop_size: int = 20,
    max_generations: int = 10,
) -> dict[str, Any]:
    """全自动调参流水线：遗传算法 → 验证 → 写入配置。

    用法::

        result = auto_tune("ma_cross", "600000", "2025-01-01", "2025-06-01")
        print(result["status"])  # "applied" | "degraded" | "no_improvement"

    Args:
        strategy_name: 策略名
        symbol: 股票代码
        start_date / end_date: 回测区间
        pop_size: 种群大小
        max_generations: 最大代数

    Returns:
        {
            "status": "applied" | "degraded" | "no_improvement",
            "strategy_name": str,
            "best_params": dict,
            "best_sharpe": float,
            "best_return": float,
            "generations_run": int,
            "config_updated": bool,
        }
    """
    logger.info("=" * 50)
    logger.info(f"auto_tune 启动: {strategy_name} @ {symbol} [{start_date}~{end_date or 'now'}]")
    logger.info("=" * 50)

    # ── 1. 运行遗传算法 ──
    ga_result = genetic_optimize(
        strategy_name=strategy_name,
        symbol=symbol,
        start_date=start_date,
        end_date=end_date,
        pop_size=pop_size,
        max_generations=max_generations,
    )

    if "error" in ga_result:
        logger.error(f"遗传算法失败: {ga_result.get('error')}")
        return {
            "status": "error",
            "strategy_name": strategy_name,
            "error": ga_result.get("error"),
        }

    best_params = ga_result.get("best_params", {})
    best_sharpe = ga_result.get("best_sharpe", 0.0)

    if not best_params or best_sharpe <= -998:
        logger.warning("遗传算法未找到有效参数，跳过配置更新")
        return {
            "status": "no_improvement",
            "strategy_name": strategy_name,
            "best_params": best_params,
            "best_sharpe": best_sharpe,
            "config_updated": False,
        }

    # ── 2. 写入配置 ──
    config_updated = update_strategy_config(strategy_name, best_params)

    logger.info(
        "调参完毕，已应用最优参数" if config_updated
        else "调参完毕，配置更新失败（降级）"
    )

    return {
        "status": "applied" if config_updated else "degraded",
        "strategy_name": strategy_name,
        "best_params": best_params,
        "best_sharpe": best_sharpe,
        "best_return": ga_result.get("best_return", 0.0),
        "generations_run": ga_result.get("generations_run", 0),
        "total_seconds": ga_result.get("total_seconds", 0),
        "config_updated": config_updated,
    }
