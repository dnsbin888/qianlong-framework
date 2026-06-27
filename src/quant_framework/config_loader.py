"""统一配置加载器 — 降级保底 + 原子写入 (v1.0)
================================================

宪法 2.2 / 5.3: JSON 损坏或缺失时返回 DEFAULT_CONFIG，绝不抛异常。

Usage::

    from quant_framework.config_loader import ConfigLoader

    config = ConfigLoader.load_config(r"D:\quant_framework\live_trader_config.json")
    active = config["strategy_scheduler"]["active_strategy"]
"""

from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── 默认配置 (最小保底) ──
DEFAULT_CONFIG: dict[str, Any] = {
    "strategy_params": {
        "ma_cross": {"fast_period": 5, "slow_period": 20}
    },
    "strategy_scheduler": {
        "enabled": True,
        "active_strategy": "ma_cross",
    },
}


class ConfigLoader:
    """统一配置加载入口 — 带降级保底 + 原子写入。"""

    @staticmethod
    def load_config(file_path: str) -> dict[str, Any]:
        """加载 JSON 配置，失败时返回 DEFAULT_CONFIG。

        宪法 2.2: 文件不存在 / JSON 损坏 → 日志警告 + 返回保底配置。

        Args:
            file_path: 配置文件路径

        Returns:
            配置字典。如果文件不存在或 JSON 损坏，
            返回 DEFAULT_CONFIG 的深拷贝。
        """
        if not os.path.exists(file_path):
            logger.warning(f"[WARN] 配置文件不存在: {file_path}，使用 DEFAULT_CONFIG")
            return copy.deepcopy(DEFAULT_CONFIG)

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                config: dict[str, Any] = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(
                f"[WARN] 配置文件读取失败: {e}，使用 DEFAULT_CONFIG"
            )
            return copy.deepcopy(DEFAULT_CONFIG)

        # 合并默认字段 (向后兼容: 不存在的字段补入默认值)
        changed: bool = False
        for key, default_val in DEFAULT_CONFIG.items():
            if key not in config:
                config[key] = copy.deepcopy(default_val)
                changed = True

        if changed:
            logger.info("配置文件缺少部分字段，已补入默认值")

        return config

    @staticmethod
    def save_config(file_path: str, config: dict[str, Any]) -> bool:
        """原子写入配置 (.tmp + os.replace)。

        宪法 5.3: 写入失败时记录日志，返回 False，不抛异常。

        Args:
            file_path: 配置文件路径
            config: 完整配置字典

        Returns:
            True 写入成功，False 失败
        """
        try:
            tmp_path = file_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, file_path)
            logger.info(f"配置已保存: {file_path}")
            return True
        except PermissionError:
            logger.warning("[WARN] 无法写入配置，请检查文件权限")
            return False
        except Exception as e:
            logger.error(f"配置保存失败: {e}")
            return False
