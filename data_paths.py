"""统一数据路径 — 代码与数据分离。

所有用户数据读写在 D:\quant_data\，代码在 D:\quant_framework\。
系统升级不影响用户数据。备份只备份数据目录。
"""

import os, shutil

DATA_ROOT = r"D:\quant_data"
OLD_ROOTS = [r"D:\quant_framework", r"D:\quant_web"]

# 目录结构
PATHS = {
    "config":     os.path.join(DATA_ROOT, "config"),
    "state":      os.path.join(DATA_ROOT, "state"),
    "registry":   os.path.join(DATA_ROOT, "registry"),
    "user":       os.path.join(DATA_ROOT, "user"),
    "cache":      os.path.join(DATA_ROOT, "cache"),
    "logs":       os.path.join(DATA_ROOT, "logs"),
    "reports":    os.path.join(DATA_ROOT, "reports"),
    "backup":     os.path.join(DATA_ROOT, "backup"),
}

# 文件映射: 逻辑名 → (新路径, 旧路径)
FILES = {
    "user_config":           ("config/user_config.json",           "user_config.json"),
    "factor_registry":       ("registry/factor_registry.json",     "factor_registry.json"),
    "paper_account":         ("state/paper_account.json",          "paper_account.json"),
    "position_track":        ("state/live_positions_track.json",   "live_positions_track.json"),
    "strategy_approvals":    ("state/strategy_approvals.json",     "strategy_approvals.json"),
    "user_strategies":       ("user/user_strategies.json",         r"user_customizations/user_strategies.json"),
    "user_formulas":         ("user/user_tdx_formulas.json",       r"user_customizations/user_tdx_formulas.json"),
    "user_factors":          ("user/user_factors.json",            r"user_customizations/user_factors.json"),
    "live_trader_config":    ("config/live_trader_config.json",    "live_trader_config.json"),
    "full_market_ic_report": ("reports/full_market_ic_report.json","full_market_ic_report.json"),
    "reconciliation_log":    ("logs/reconciliation.log",           "reconciliation.log"),
    "health_log":            ("logs/factor_health_log.jsonl",      "factor_health_log.jsonl"),
    "ic_history":            ("cache/ic_history",                  "ic_history"),
}


def init():
    """创建数据目录 + 迁移旧文件。"""
    for d in PATHS.values():
        os.makedirs(d, exist_ok=True)

    migrated = 0
    for logical, (rel_new, rel_old) in FILES.items():
        new_path = os.path.join(DATA_ROOT, rel_new)
        if os.path.exists(new_path):
            continue  # 已迁移

        # 查找旧文件
        for old_root in OLD_ROOTS:
            old_path = os.path.join(old_root, rel_old)
            if os.path.exists(old_path):
                try:
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    if os.path.isdir(old_path):
                        shutil.copytree(old_path, new_path)
                    else:
                        shutil.copy2(old_path, new_path)
                    migrated += 1
                except Exception:
                    pass
                break
    return migrated


def get(logical: str) -> str:
    """获取逻辑名对应的路径。新路径优先，不存在回退旧路径。"""
    if logical not in FILES:
        return os.path.join(DATA_ROOT, logical)

    rel_new, rel_old = FILES[logical]
    new_path = os.path.join(DATA_ROOT, rel_new)
    if os.path.exists(new_path) or os.path.exists(os.path.dirname(new_path)):
        return new_path

    # 回退旧路径
    for old_root in OLD_ROOTS:
        old_path = os.path.join(old_root, rel_old)
        if os.path.exists(old_path):
            return old_path
    return new_path  # 都不存在 → 返回新路径 (创建新文件)
