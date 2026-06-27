"""StrategyScheduler — 策略池动态调度器 (E246)
===============================================

盘后评估 daily_attribution 历史表现，自动选出最优策略并写入配置。

红线:
    - 盘后执行, 盘中不切换
    - 无法计算时降级回 ma_cross
    - 不抛异常
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("quant_framework.strategy.scheduler")


class StrategyScheduler:
    """策略池动态调度器。

    Args:
        db_service: DBService 实例
        config_path: 配置文件路径
    """

    FALLBACK_STRATEGY: str = "ma_cross"
    WEIGHT_WIN_RATE: float = 0.4
    WEIGHT_AVG_PROFIT: float = 0.4
    WEIGHT_TRADE_COUNT: float = 0.2

    def __init__(
        self,
        db_service: Any,
        config_path: str = r"D:\quant_framework\live_trader_config.json",
    ) -> None:
        self._db = db_service
        self._config_path: str = config_path

    # ═══════════════════════════════════════════════════════
    #  策略评估
    # ═══════════════════════════════════════════════════════

    def evaluate_strategies(self, lookback_days: int = 5) -> str:
        """评估最近 lookback_days 天的策略表现，返回最优策略名。

        评分公式 (E246 修正 #1: trade_count 归一化):
            norm_count = min(count / max_count, 1.0)  (所有策略 trade_count=0 则该分量=0)
            Score = (win_rate * 0.4) + (avg_profit * 0.4) + (norm_count * 0.2)

        降级规则:
            - 无数据 → FALLBACK_STRATEGY
            - 所有策略评分 ≤ 0 → FALLBACK_STRATEGY
            - 异常 → FALLBACK_STRATEGY

        Returns:
            最优策略名 (str)
        """
        try:
            records: list[dict[str, Any]] = self._db.get_attribution_history(
                days=lookback_days
            )
        except Exception as e:
            logger.error(f"[Scheduler] 读取归因数据失败: {e}")
            return self.FALLBACK_STRATEGY

        if not records:
            logger.info("[Scheduler] 无归因数据，降级回 ma_cross")
            return self.FALLBACK_STRATEGY

        # 按 strategy_name 分组
        by_strategy: dict[str, list[dict[str, Any]]] = {}
        for r in records:
            name = r.get("strategy_name", self.FALLBACK_STRATEGY)
            by_strategy.setdefault(name, []).append(r)

        if not by_strategy:
            return self.FALLBACK_STRATEGY

        # 计算各策略指标 (聚合)
        stats: dict[str, dict[str, float]] = {}
        for name, recs in by_strategy.items():
            n: int = len(recs)
            total_win_rate: float = sum(float(r.get("win_rate", 0)) for r in recs)
            total_pnl: float = sum(float(r.get("total_pnl", 0)) for r in recs)
            total_trades: float = sum(int(r.get("total_trades", 0)) for r in recs)

            avg_win_rate: float = total_win_rate / n if n > 0 else 0.0
            avg_pnl_per_trade: float = (
                total_pnl / total_trades if total_trades > 0 else 0.0
            )

            stats[name] = {
                "win_rate": avg_win_rate,
                "avg_profit": avg_pnl_per_trade,
                "trade_count": total_trades,
                "days": n,
            }

        # E246 修正 #1: trade_count 归一化
        max_count: float = max(
            s["trade_count"] for s in stats.values()
        )
        if max_count <= 0:
            max_count = 1.0

        # 评分
        best_name: str = self.FALLBACK_STRATEGY
        best_score: float = -999.0

        for name, s in stats.items():
            norm_count: float = min(s["trade_count"] / max_count, 1.0)
            score: float = (
                s["win_rate"] * self.WEIGHT_WIN_RATE
                + s["avg_profit"] * self.WEIGHT_AVG_PROFIT
                + norm_count * self.WEIGHT_TRADE_COUNT
            )

            logger.info(
                f"[Scheduler] {name}: win_rate={s['win_rate']:.3f} "
                f"avg_profit={s['avg_profit']:.4f} "
                f"trades={int(s['trade_count'])} "
                f"→ score={score:.4f}"
            )

            if score > best_score:
                best_score = score
                best_name = name

        # 降级: 所有评分 ≤ 0 → ma_cross
        if best_score <= 0:
            logger.info(
                f"[Scheduler] 所有策略评分 ≤ 0 (best={best_score:.4f}), "
                f"降级回 {self.FALLBACK_STRATEGY}"
            )
            return self.FALLBACK_STRATEGY

        logger.info(
            f"[Scheduler] 最优策略: {best_name} (score={best_score:.4f})"
        )
        return best_name

    # ═══════════════════════════════════════════════════════
    #  应用策略
    # ═══════════════════════════════════════════════════════

    def apply_strategy(self, strategy_name: str) -> bool:
        """将选中策略写入 live_trader_config.json (E246 修正 #3).

        步骤:
            1. 验证 strategy_name 在 StrategyRegistry 中已注册
            2. 检查 strategy_params 中是否有该策略参数, 无则从 registry 补入
            3. 更新 strategy_scheduler.active_strategy
            4. ConfigLoader.save_config() 原子写入
        """
        from quant_framework.config_loader import ConfigLoader
        from quant_framework.strategy.registry import StrategyRegistry

        # 1. 验证注册
        reg = StrategyRegistry.instance()
        meta = reg.get(strategy_name)
        if meta is None:
            logger.warning(
                f"[Scheduler] 策略 '{strategy_name}' 未注册，无法应用"
            )
            return False

        # 2. 加载配置
        config: dict[str, Any] = ConfigLoader.load_config(self._config_path)

        # 3. 补齐参数 (E246 修正 #3)
        if "strategy_params" not in config:
            config["strategy_params"] = {}

        if strategy_name not in config["strategy_params"]:
            # 从 registry 提取默认参数
            defaults: dict[str, Any] = {}
            for pname, pinfo in meta.params.items():
                defaults[pname] = pinfo.get("default")
            config["strategy_params"][strategy_name] = defaults
            logger.info(
                f"[Scheduler] 已从注册器补入默认参数: "
                f"{strategy_name} → {defaults}"
            )

        # 4. 更新 active_strategy
        if "strategy_scheduler" not in config:
            config["strategy_scheduler"] = {}
        config["strategy_scheduler"]["active_strategy"] = strategy_name

        # 5. 原子写入
        success: bool = ConfigLoader.save_config(self._config_path, config)
        if success:
            logger.info(
                f"[Scheduler] 策略已切换: → {strategy_name}"
            )
        return success

    # ═══════════════════════════════════════════════════════
    #  盘后调度入口
    # ═══════════════════════════════════════════════════════

    def run_daily_selection(self, lookback_days: int = 5) -> str:
        """盘后调度入口 — 评估 + 应用。

        Returns:
            最终选中的策略名
        """
        logger.info("[Scheduler] 盘后策略评估开始...")
        best: str = self.evaluate_strategies(lookback_days=lookback_days)

        if best == self.FALLBACK_STRATEGY:
            logger.info("[Scheduler] 维持保底策略 ma_cross，不切换")
            return best

        applied: bool = self.apply_strategy(best)
        if not applied:
            logger.warning("[Scheduler] 策略应用失败，维持当前策略")

        return best if applied else self.FALLBACK_STRATEGY
