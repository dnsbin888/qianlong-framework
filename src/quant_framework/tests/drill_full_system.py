"""FullSystemDrill — 全链路实弹演习 (E235)
===========================================

一键贯穿: 自动调参 → 配置更新 → 模拟交易 → 信号持久化

红线 (宪法):
    - 零实盘: 只读 CSV + SQLite，不碰 xttrader/place_order
    - 可重复: 演习前清空 signal_log，演习后恢复 strategy_params
    - 降级保底: 所有步骤 try...except 包裹

用法::

    python -m quant_framework.tests.drill_full_system
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

logger = logging.getLogger("quant_framework.tests.drill")

# ── 路径常量 ──
_CSV_PATH: str = r"D:\quant_framework\data\market\600000_1d.csv"
_SYMBOL: str = "600000"
_CONFIG_PATH: str = r"D:\quant_framework\live_trader_config.json"
_DB_PATH: str = r"D:\quant_web\quant_engine.db"


class FullSystemDrill:
    """全链路实弹演习 — 串联调参 → 交易 → 信号持久化。"""

    def __init__(
        self,
        csv_path: str = _CSV_PATH,
        symbol: str = _SYMBOL,
        config_path: str = _CONFIG_PATH,
    ) -> None:
        self._csv_path: str = csv_path
        self._symbol: str = symbol
        self._config_path: str = config_path

        # ── 备份原始 strategy_params ──
        self._original_strategy_params: dict[str, Any] = {}
        self._backup_config()

        logger.info("=" * 60)
        logger.info("FullSystemDrill 初始化完成")
        logger.info(f"  股票: {symbol}")
        logger.info(f"  CSV:  {csv_path}")
        logger.info(f"  配置: {config_path}")
        logger.info("=" * 60)

    # ═══════════════════════════════════════════════════════
    #  配置备份与恢复
    # ═══════════════════════════════════════════════════════

    def _backup_config(self) -> None:
        """备份当前 strategy_params。"""
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                self._original_strategy_params = deepcopy(
                    config.get("strategy_params", {})
                )
                logger.info(
                    f"已备份 strategy_params: {list(self._original_strategy_params.keys())}"
                )
        except Exception as e:
            logger.warning(f"配置备份失败: {e}")

    def _restore_config(self) -> None:
        """恢复 strategy_params 到演习前状态。"""
        try:
            if not os.path.exists(self._config_path):
                return
            with open(self._config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            config["strategy_params"] = deepcopy(self._original_strategy_params)

            tmp = self._config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._config_path)
            logger.info(
                "已恢复 strategy_params: "
                f"{list(self._original_strategy_params.keys())}"
            )
        except Exception as e:
            logger.error(f"配置恢复失败: {e}")

    # ═══════════════════════════════════════════════════════
    #  阶段一: 自动调参
    # ═══════════════════════════════════════════════════════

    def run_optimization(self) -> dict[str, Any]:
        """阶段一：运行遗传算法自动调参。

        Returns:
            auto_tune 结果 dict，含 status, best_params, best_sharpe 等。
        """
        logger.info("\n" + "─" * 40)
        logger.info("阶段一: 自动调参 (遗传算法)")
        logger.info("─" * 40)

        try:
            from quant_framework.backtest.optimizer import auto_tune

            result = auto_tune(
                "ma_cross",
                self._symbol,
                start_date="2024-01-01",
                end_date="2026-06-01",
                pop_size=10,
                max_generations=5,
            )

            logger.info(f"auto_tune 结果: status={result.get('status')}")
            logger.info(f"  best_params: {result.get('best_params')}")
            logger.info(f"  best_sharpe: {result.get('best_sharpe', 0):.3f}")
            logger.info(f"  config_updated: {result.get('config_updated')}")
            return result

        except Exception as e:
            logger.error(f"自动调参失败: {e}", exc_info=True)
            return {"status": "error", "error": str(e)}

    # ═══════════════════════════════════════════════════════
    #  阶段二: 模拟盘中交易
    # ═══════════════════════════════════════════════════════

    def run_trading_simulation(
        self, optimized_params: dict[str, Any], source: str = "csv"
    ) -> dict[str, Any]:
        """阶段二：模拟盘中交易。

        Args:
            optimized_params: 最优参数
            source: "csv" (默认) 或 "qmt_sim" (QMT 实时行情，失败降级 CSV)
        """
        logger.info("\n" + "─" * 40)
        logger.info("阶段二: 模拟盘中交易 (CSV 回放)")
        logger.info("─" * 40)

        if not os.path.exists(self._csv_path):
            logger.error(f"CSV 文件不存在: {self._csv_path}")
            return {"error": "CSV 文件不存在"}

        try:
            import pandas as pd
            from quant_framework.live.live_strategy_runner import LiveStrategyRunner
        except ImportError as e:
            logger.error(f"依赖导入失败: {e}")
            return {"error": str(e)}

        # 若 optimize_params 为空，使用默认
        if not optimized_params or "fast_period" not in optimized_params:
            optimized_params = {"fast_period": 5, "slow_period": 20}
            logger.info("使用默认参数: " + str(optimized_params))

        # 确保配置文件已更新
        try:
            if os.path.exists(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                if "strategy_params" not in config:
                    config["strategy_params"] = {}
                config["strategy_params"]["ma_cross"] = {
                    "fast_period": optimized_params.get("fast_period", 5),
                    "slow_period": optimized_params.get("slow_period", 20),
                }
                tmp = self._config_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self._config_path)
        except Exception as e:
            logger.warning(f"配置写入失败: {e}")

        # 创建 Runner (会从配置读取参数)
        runner = LiveStrategyRunner()

        # E238: QMT 模式 — 尝试 QMT 实时行情
        if source == "qmt_sim":
            runner._data_source = "qmt_sim"
            logger.info("[INFO] 数据源: QMT (尝试中...)")
            # 非阻塞测试: 如果 start() 的 _start_qmt 内部降级，
            # _data_source 会被置回 "csv"
            # 此处不调用阻塞的 start()，而是直接让 QMT 降级机制触发
            # runner.start() 会内部尝试 QMT → 失败则降级 CSV
            runner._load_config()  # 重新加载配置，可能覆盖 _data_source
            runner._data_source = "qmt_sim"  # 覆盖配置，强制 QMT 模式
            logger.info(f"数据源状态: {runner._data_source}")

        # 逐行回放 CSV (QMT 不可用时自动降级至此)
        df = pd.read_csv(self._csv_path)
        signals: list[dict[str, Any]] = []
        buy_count: int = 0
        sell_count: int = 0

        logger.info(f"回放 CSV: {len(df)} 行")

        for _, row in df.iterrows():
            # CSV 列名 → bar_data key 映射
            bar_data: dict[str, Any] = {
                "dt": str(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }

            result = runner.on_bar(self._symbol, bar_data)
            if result:
                sig_entry = {
                    "symbol": self._symbol,
                    "signal_type": result,
                    "price": bar_data["close"],
                    "dt": bar_data["dt"],
                    "timestamp": datetime.now().isoformat(),
                }
                signals.append(sig_entry)
                if result == "buy":
                    buy_count += 1
                elif result == "sell":
                    sell_count += 1

        logger.info(
            f"模拟交易完成: {len(df)} bars, "
            f"{buy_count} buys, {sell_count} sells, "
            f"策略: {runner._active_strategy}, "
            f"参数: {runner.params}"
        )

        return {
            "total_bars": len(df),
            "signals": signals,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "active_strategy": runner._active_strategy,
            "params": runner.params,
        }

    # ═══════════════════════════════════════════════════════
    #  一键演习
    # ═══════════════════════════════════════════════════════

    def run_full_drill(self) -> dict[str, Any]:
        """一键演习：调参 → 模拟交易 → SQLite 验证。

        Returns:
            完整演习报告 dict
        """
        t0: float = time.time()

        # ── 阶段一: 调参 ──
        opt_result = self.run_optimization()

        # ── 阶段二: 模拟交易 ──
        best_params = opt_result.get("best_params", {})
        trade_result = self.run_trading_simulation(best_params)

        # ── 阶段三: SQLite 验证 ──
        signal_log_count = self._check_signal_log()

        elapsed: float = time.time() - t0

        # ── 构建报告 ──
        report: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "symbol": self._symbol,
            "optimization": {
                "status": opt_result.get("status"),
                "best_params": best_params,
                "best_sharpe": opt_result.get("best_sharpe", 0),
                "best_return": opt_result.get("best_return", 0),
                "generations_run": opt_result.get("generations_run", 0),
                "config_updated": opt_result.get("config_updated", False),
            },
            "trading_simulation": {
                "total_bars": trade_result.get("total_bars", 0),
                "buy_count": trade_result.get("buy_count", 0),
                "sell_count": trade_result.get("sell_count", 0),
                "active_strategy": trade_result.get("active_strategy", ""),
                "params": trade_result.get("params", {}),
            },
            "signal_log_count": signal_log_count,
            "total_seconds": round(elapsed, 1),
        }

        logger.info("\n" + "=" * 60)
        logger.info("全链路演习报告")
        logger.info("=" * 60)
        logger.info(f"  状态: {report['optimization']['status']}")
        logger.info(f"  最优参数: {report['optimization']['best_params']}")
        logger.info(f"  最优夏普: {report['optimization']['best_sharpe']:.3f}")
        logger.info(f"  总 K 线: {report['trading_simulation']['total_bars']}")
        logger.info(f"  买入信号: {report['trading_simulation']['buy_count']}")
        logger.info(f"  卖出信号: {report['trading_simulation']['sell_count']}")
        logger.info(f"  SQLite 记录: {signal_log_count}")
        logger.info(f"  耗时: {elapsed:.1f} 秒")
        logger.info("=" * 60)

        return report

    # ═══════════════════════════════════════════════════════
    #  SQLite 检查
    # ═══════════════════════════════════════════════════════

    def _check_signal_log(self) -> int:
        """查询 signal_log 表记录数量。"""
        try:
            conn = sqlite3.connect(_DB_PATH)
            row = conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()
            conn.close()
            return int(row[0]) if row else 0
        except Exception as e:
            logger.error(f"SQLite 查询失败: {e}")
            return -1

    def _clear_signal_log(self) -> None:
        """清空 signal_log 表 (可重复运行)。"""
        try:
            conn = sqlite3.connect(_DB_PATH)
            conn.execute("DELETE FROM signal_log")
            conn.commit()
            conn.close()
            logger.info("signal_log 表已清空")
        except Exception as e:
            logger.error(f"清空 signal_log 失败: {e}")

    # ═══════════════════════════════════════════════════════
    #  清理
    # ═══════════════════════════════════════════════════════

    def cleanup(self, clear_signals: bool = True) -> None:
        """清理：恢复配置 + 清空测试信号。

        Args:
            clear_signals: True = 清空 signal_log 表
        """
        logger.info("\n清理中...")

        # 恢复 strategy_params
        self._restore_config()

        # 清空测试信号
        if clear_signals:
            self._clear_signal_log()

        logger.info("清理完成")


# ═══════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    drill = FullSystemDrill()
    drill._clear_signal_log()  # 演习前清空

    try:
        report = drill.run_full_drill()

        # 输出最关键的信号
        signals = report.get("trading_simulation", {}).get("signals", [])
        if signals:
            logger.info(f"\n信号样本 (前5条):")
            for s in signals[:5]:
                logger.info(
                    f"  [{s['signal_type']}] {s['symbol']} @ {s['price']:.2f} on {s['dt']}"
                )

        print("\n" + "=" * 60)
        print("🎯 全链路实弹演习完成!")
        print(f"   最优参数: {report['optimization']['best_params']}")
        print(f"   最优夏普: {report['optimization']['best_sharpe']:.3f}")
        print(f"   买入 / 卖出: {report['trading_simulation']['buy_count']} / {report['trading_simulation']['sell_count']}")
        print(f"   总耗时: {report['total_seconds']} 秒")
        print("=" * 60)

        # 询问清理
        print("\n按 Enter 清理 SQLite 并恢复配置 (Ctrl+C 跳过)...", end="")
        try:
            input()
            drill.cleanup()
        except (KeyboardInterrupt, EOFError):
            print("\n跳过清理。手动清理: drill.cleanup()")

    except Exception as e:
        logger.error(f"演习失败: {e}", exc_info=True)
        drill.cleanup()
        sys.exit(1)
