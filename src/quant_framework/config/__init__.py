"""Configuration management for the quant framework.

Provides:
- FrameworkConfig: Pydantic model mirroring config/default.yaml
- load_config(): YAML file loader
- resolve_paths(): Convert relative paths to absolute
"""

from quant_framework.config.config import (
    BacktestConfig,
    DataConfig,
    DataStoreConfig,
    ExecutionConfig,
    FrameworkConfig,
    FrameworkSection,
    MonitorConfig,
    MonitorNotifications,
    NotificationChannel,
    RiskConfig,
)
from quant_framework.config.loader import load_config

__all__ = [
    "FrameworkConfig",
    "FrameworkSection",
    "DataConfig",
    "DataStoreConfig",
    "RiskConfig",
    "ExecutionConfig",
    "BacktestConfig",
    "MonitorConfig",
    "MonitorNotifications",
    "NotificationChannel",
    "load_config",
]
