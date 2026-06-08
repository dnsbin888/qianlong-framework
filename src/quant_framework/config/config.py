"""Pydantic v2 configuration models for the quant framework.

Every field in config/default.yaml is mapped here so that loading
produces a fully validated, typed configuration object.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class FrameworkMode(str, Enum):
    LIVE = "live"
    PAPER = "paper"
    BACKTEST = "backtest"


class EngineType(str, Enum):
    POLLING = "polling"
    EVENT_DRIVEN = "event_driven"


class DataProviderName(str, Enum):
    THS = "ths"
    TUSHARE = "tushare"
    AKSHARE = "akshare"
    XTQUANT = "xtquant"
    CSV = "csv"


class StoreType(str, Enum):
    SQLITE = "sqlite"
    PARQUET = "parquet"
    CSV = "csv"


class BrokerName(str, Enum):
    THS = "ths"
    XTQUANT = "xtquant"
    SIMULATED = "simulated"


class SlippageModel(str, Enum):
    FIXED = "fixed"
    PROPORTIONAL = "proportional"
    NORMAL = "normal"


class LogFormat(str, Enum):
    JSON = "json"
    CONSOLE = "console"


# ---------------------------------------------------------------------------
# Sub-sections
# ---------------------------------------------------------------------------

class FrameworkSection(BaseModel):
    """Top-level framework settings."""

    mode: FrameworkMode = FrameworkMode.PAPER
    engine: EngineType = EngineType.POLLING
    log_level: str = "INFO"
    log_dir: str = "./logs"
    data_dir: str = "./data"


class DataStoreConfig(BaseModel):
    """Data storage backend config."""

    type: StoreType = StoreType.SQLITE
    db_path: str = "./data/market.db"
    bar_cache_days: int = 365


class DataConfig(BaseModel):
    """Market data provider config."""

    provider: DataProviderName = DataProviderName.THS
    store: DataStoreConfig = Field(default_factory=DataStoreConfig)


class RiskConfig(BaseModel):
    """Risk management parameters."""

    enabled: bool = True
    max_drawdown_pct: float = 0.20
    max_daily_loss_pct: float = 0.03          # 日亏损限制: 权益的3%
    max_daily_loss: float = 50_000.0           # DEPRECATED — 保留兼容
    max_single_position_pct: float = 0.30
    max_total_positions: int = 10
    order_cooldown_seconds: int = 5
    single_order_max_pct: float = 0.10         # 单笔金额: 不超过权益的10%
    max_daily_trades: int = 100                # 每日最大交易次数
    blacklist: list[str] = Field(default_factory=list)


class ExecutionConfig(BaseModel):
    """Trade execution / broker config."""

    broker: BrokerName = BrokerName.SIMULATED
    default_slippage: float = 0.001
    commission_rate: float = 0.0003
    min_commission: float = 5.0


class BacktestConfig(BaseModel):
    """Backtest-specific parameters."""

    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003
    slippage_model: SlippageModel = SlippageModel.FIXED
    slippage_value: float = 0.001
    benchmark: str = "000300"
    risk_free_rate: float = 0.03


class NotificationChannel(BaseModel):
    """A single notification channel config."""

    enabled: bool = False
    webhook_url: str = ""


class MonitorNotifications(BaseModel):
    """Notification channels."""

    dingtalk: NotificationChannel = Field(default_factory=NotificationChannel)
    wecom: NotificationChannel = Field(default_factory=NotificationChannel)
    feishu: NotificationChannel = Field(default_factory=NotificationChannel)
    email: NotificationChannel = Field(default_factory=NotificationChannel)


class TradeRecorderConfig(BaseModel):
    """Trade persistence config."""

    type: str = "sqlite"
    db_path: str = "./data/trades.db"


class MonitorConfig(BaseModel):
    """Monitoring & logging config."""

    log_format: LogFormat = LogFormat.JSON
    trade_recorder: TradeRecorderConfig = Field(default_factory=TradeRecorderConfig)
    notifications: MonitorNotifications = Field(default_factory=MonitorNotifications)


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class FrameworkConfig(BaseModel):
    """Root configuration object — mirrors config/default.yaml.

    Usage:
        cfg = FrameworkConfig.from_yaml("config/default.yaml")
        print(cfg.framework.mode)
        print(cfg.backtest.initial_cash)
    """

    framework: FrameworkSection = Field(default_factory=FrameworkSection)
    data: DataConfig = Field(default_factory=DataConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    backtest: BacktestConfig = Field(default_factory=BacktestConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)

    # Derived / runtime paths (set after loading)
    _config_dir: Optional[Path] = None

    @classmethod
    def from_yaml(cls, path: str | Path) -> "FrameworkConfig":
        """Load and validate configuration from a YAML file.

        Args:
            path: Path to a config YAML file.

        Returns:
            FrameworkConfig with all sections validated.
        """
        from quant_framework.config.loader import load_config

        return load_config(path)

    @model_validator(mode="after")
    def _set_default_store_paths(self) -> "FrameworkConfig":
        """Ensure data store paths are consistent with framework.data_dir."""
        data_dir = self.framework.data_dir
        if self.data.store.db_path.startswith("./"):
            self.data.store.db_path = str(Path(data_dir) / self.data.store.db_path[2:])
        if self.monitor.trade_recorder.db_path.startswith("./"):
            self.monitor.trade_recorder.db_path = str(
                Path(data_dir) / self.monitor.trade_recorder.db_path[2:]
            )
        return self

    def resolve_path(self, relative: str) -> Path:
        """Resolve a relative path against the framework data_dir.

        Args:
            relative: A path relative to data_dir (e.g. 'kline/600000.csv').

        Returns:
            Absolute Path.
        """
        return (Path(self.framework.data_dir) / relative).resolve()
