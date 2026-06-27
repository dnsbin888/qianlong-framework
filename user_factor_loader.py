"""用户自定义因子加载器 (蓝图 v3.0)

从 user_customizations/ 读取用户定义的因子/策略，
转换为 factor_calculator 和 backtest_engine 可用的格式。
"""

import json, os, logging

logger = logging.getLogger(__name__)

USER_DIR = r"D:\quant_framework\user_customizations"


def load_user_factors() -> list[dict]:
    """加载所有启用的用户自定义因子。

    Returns:
        [{name, display_name, formula, params, ...}, ...]
    """
    path = os.path.join(USER_DIR, "user_factors.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        factors = data.get("factors", [])
        enabled = [f for f in factors if f.get("enabled", True)]
        logger.info(f"加载用户因子: {len(enabled)}/{len(factors)} 个启用")
        return enabled
    except Exception as e:
        logger.warning(f"加载用户因子失败: {e}")
        return []


def load_user_strategies() -> list[dict]:
    """加载所有启用的用户自定义策略。

    Returns:
        [{name, display_name, buy_condition, sell_condition, params, ...}, ...]
    """
    path = os.path.join(USER_DIR, "user_strategies.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        strategies = data.get("strategies", [])
        enabled = [s for s in strategies if s.get("enabled", True)]
        logger.info(f"加载用户策略: {len(enabled)}/{len(strategies)} 个启用")
        return enabled
    except Exception as e:
        logger.warning(f"加载用户策略失败: {e}")
        return []


def load_user_tdx_formulas() -> list[dict]:
    """加载所有启用的用户通达信公式路径。"""
    path = os.path.join(USER_DIR, "user_tdx_formulas.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        formulas = data.get("formulas", [])
        return [f for f in formulas if f.get("enabled", True)]
    except Exception as e:
        logger.warning(f"加载用户公式失败: {e}")
        return []


def get_user_factor_names() -> list[str]:
    """获取所有启用的用户因子名称列表 (供backtest_engine使用)。"""
    return [f["name"] for f in load_user_factors()]
