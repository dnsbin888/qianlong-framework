"""Centralized path resolution for the quant framework.

All file-system paths are resolved through this module so that
the framework works on any machine without hardcoded directories.

Configuration sources (in priority order):
1. Environment variables (e.g. TDX_DATA_ROOT, QUANT_DATA_DIR)
2. FrameworkConfig YAML settings
3. Sensible defaults under the project root
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_project_root() -> Path:
    """Return the project root directory (parent of src/)."""
    # Navigate up from this file: config/paths.py -> config -> quant_framework -> src -> root
    return Path(__file__).resolve().parent.parent.parent.parent


def get_data_dir(config_data_dir: Optional[str] = None) -> Path:
    """Resolve the data directory.

    Priority:
    1. QUANT_DATA_DIR environment variable
    2. config_data_dir from FrameworkConfig
    3. ./data relative to project root
    """
    env_dir = os.environ.get("QUANT_DATA_DIR")
    if env_dir:
        return Path(env_dir)

    if config_data_dir and not config_data_dir.startswith("."):
        return Path(config_data_dir)

    return get_project_root() / "data"


def get_log_dir(config_log_dir: Optional[str] = None) -> Path:
    """Resolve the log directory.

    Priority:
    1. QUANT_LOG_DIR environment variable
    2. config_log_dir from FrameworkConfig
    3. ./logs relative to project root
    """
    env_dir = os.environ.get("QUANT_LOG_DIR")
    if env_dir:
        return Path(env_dir)

    if config_log_dir:
        p = Path(config_log_dir)
        return p.resolve() if not p.is_absolute() else p

    return get_project_root() / "logs"


def get_tdx_data_root() -> Optional[Path]:
    """Get the TDX/THS data root directory.

    Searches in order:
    1. TDX_DATA_ROOT environment variable
    2. Common installation paths
    3. Returns None if no directory found

    Returns:
        Path to vipdoc or history directory, or None.
    """
    env_root = os.environ.get("TDX_DATA_ROOT")
    if env_root and os.path.isdir(env_root):
        return Path(env_root)

    # Try common paths
    candidates = [
        # TDX vipdoc
        r"D:\通信达技术指标\六合一竞价擒龙（下载到电脑里解压）\六合一竞价擒龙（下载到电脑里解压）\vipdoc",
        r"D:\通信达技术指标\01、散人竞价擒龙V8.59旗舰版（下载解压即可使用）\散人竞价擒龙V8.59旗舰版（无加密）\vipdoc",
        r"D:\通信达技术指标\竞价擒龙升级版选股软件+盘中首板预警\vipdoc",
        r"C:\new_tdx\vipdoc",
        r"D:\new_tdx\vipdoc",
        # THS history
        r"d:\同花顺软件\同花顺\history",
        r"C:\同花顺软件\同花顺\history",
        # Generic
        r"D:\vipdoc",
        r"D:\tdx\vipdoc",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.is_dir():
            return p

    return None


def get_cache_dir() -> Path:
    """Get the cache directory for downloaded data."""
    cache_dir = os.environ.get("QUANT_CACHE_DIR", str(get_project_root() / ".cache"))
    p = Path(cache_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def get_stock_names_path() -> Path:
    """Get the path to the stock names JSON cache."""
    return get_project_root() / "stock_names.json"


def ensure_dir(path: Path) -> Path:
    """Create a directory if it doesn't exist, returning the path."""
    path.mkdir(parents=True, exist_ok=True)
    return path
