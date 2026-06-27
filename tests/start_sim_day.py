"""E242 — QMT 仿真环境一键启动脚本

用法: python tests/start_sim_day.py
退出: Ctrl+C 自动清理并生成摘要报告
"""

from __future__ import annotations

import json
import logging
import os
import sys

# 确保 quant_framework 包可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__) + "/..")  # qmt_data_provider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("start_sim_day")

_CONFIG_PATH: str = r"D:\quant_framework\live_trader_config.json"


def main() -> None:
    logger.info("=" * 50)
    logger.info("E242 — QMT 仿真环境启动")
    logger.info("=" * 50)

    # ── 1. 加载配置 ──
    if not os.path.exists(_CONFIG_PATH):
        print(f"[FATAL] 配置文件不存在: {_CONFIG_PATH}")
        sys.exit(1)

    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        config = json.load(f)

    # ── 2. 红线检查 ──
    qmt_env: str = config.get("qmt_env", "")
    data_source: str = config.get("data_source", "")

    if qmt_env not in ("SIM", "REAL"):
        print(f"[FATAL] qmt_env={qmt_env!r} 非法，合法值: SIM 或 REAL")
        sys.exit(1)
    if data_source != "api":
        print(f"[FATAL] data_source='{data_source}' != 'api'，拒绝启动！")
        sys.exit(1)

    print(f"[OK] 环境检查通过: qmt_env={qmt_env}, data_source={data_source}")

    # ── 3. 获取订阅标的 ──
    symbols: list[str] = config.get("sim_watch_symbols", ["600000"])

    # ── 4. 初始化 Runner ──
    from quant_framework.live.live_strategy_runner import LiveStrategyRunner

    runner = LiveStrategyRunner(config_path=_CONFIG_PATH)

    # ── 5. 启动 ──
    print(f"[INFO] QMT 实时订阅已启动，正在等待行情... symbols={symbols}")
    try:
        runner.start(symbols[0])
    except KeyboardInterrupt:
        print("\n[INFO] 用户中断，正在清理...")


if __name__ == "__main__":
    main()
