"""Strategy Registry — discover and manage available trading strategies.

Provides a central registry where strategies self-register with metadata
(name, description, parameter schema, applicable market), enabling UIs
to list, filter, and combine strategies dynamically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Type

from quant_framework.strategy.base import BaseStrategy


@dataclass
class StrategyMeta:
    """Metadata for a registered strategy."""

    name: str                                    # Unique key (e.g. "macd_cross")
    label: str                                   # Display name (e.g. "MACD 金叉死叉")
    strategy_cls: Type[BaseStrategy]
    config_cls: Type[Any]                        # Config dataclass
    description: str = ""
    category: str = "通用"                        # 趋势跟踪 / 网格交易 / 风控 / 打板 ...
    market: str = "A股"                           # A股 / 期货 / 加密货币
    recommended_interval: str = "1d"             # 推荐K线周期
    risk_level: str = "中等"                      # 低 / 中等 / 高
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    # params schema: {param_name: {type, default, description, choices, min, max}}

    @property
    def is_risky(self) -> bool:
        return self.risk_level == "高"


class StrategyRegistry:
    """Central strategy registry.

    Strategies register themselves with full metadata, enabling:
    - UI discovery (list all strategies with descriptions)
    - Parameter validation and defaults
    - Category-based filtering
    - Compatible strategy combination (multi-strategy portfolios)

    Usage:
        registry = StrategyRegistry()
        registry.register(StrategyMeta(
            name="macd_cross",
            label="MACD 金叉死叉",
            strategy_cls=MACDCrossStrategy,
            config_cls=MACDCrossConfig,
            description="MACD 金叉买入/死叉卖出，支持双重交叉和顶底背离",
            category="趋势跟踪",
            params={"fast": {"type": "int", "default": 12, "description": "快线周期"}},
        ))

        # Later, in UI:
        for meta in registry.list_all():
            print(f"{meta.label}: {meta.description}")
    """

    _instance: StrategyRegistry | None = None

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyMeta] = {}

    # ---- Singleton ----

    @classmethod
    def instance(cls) -> StrategyRegistry:
        """Get (or create) the global singleton registry."""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._auto_register()
        return cls._instance

    # ---- Registration ----

    def register(self, meta: StrategyMeta) -> None:
        """Register a strategy with full metadata."""
        self._strategies[meta.name] = meta

    def unregister(self, name: str) -> bool:
        """Remove a strategy. Returns True if found."""
        if name in self._strategies:
            del self._strategies[name]
            return True
        return False

    # ---- Query ----

    def get(self, name: str) -> StrategyMeta | None:
        """Get metadata for a specific strategy."""
        return self._strategies.get(name)

    def list_all(self) -> list[StrategyMeta]:
        """List all registered strategies."""
        return list(self._strategies.values())

    def list_by_category(self, category: str) -> list[StrategyMeta]:
        """List strategies in a specific category."""
        return [m for m in self._strategies.values() if m.category == category]

    def list_names(self) -> list[str]:
        """List all registered strategy names."""
        return list(self._strategies.keys())

    @property
    def count(self) -> int:
        return len(self._strategies)

    def create_strategy(self, name: str, ctx: Any, **overrides: Any) -> BaseStrategy | None:
        """Create a strategy instance by name.

        Args:
            name: Strategy identifier.
            ctx: StrategyContext instance.
            **overrides: Override default config parameters.

        Returns:
            Strategy instance, or None if name not found.
        """
        meta = self._strategies.get(name)
        if meta is None:
            return None

        # Build config with defaults, overridden by kwargs
        cfg_kwargs: dict[str, Any] = {}
        for param_name, param_info in meta.params.items():
            cfg_kwargs[param_name] = param_info.get("default")
        cfg_kwargs.update(overrides)

        cfg = meta.config_cls(**cfg_kwargs)
        return meta.strategy_cls(ctx, cfg=cfg)

    def build_config_options(self, name: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build a config dict for a strategy with defaults + overrides.

        Returns an empty dict if the strategy doesn't exist.
        """
        meta = self._strategies.get(name)
        if meta is None:
            return {}

        opts: dict[str, Any] = {}
        for pname, pinfo in meta.params.items():
            opts[pname] = pinfo.get("default")
        if overrides:
            opts.update(overrides)
        return opts

    # ---- Auto-registration ----

    def _auto_register(self) -> None:
        """Auto-register all built-in strategies on first access."""
        self._register_builtin_macd()
        self._register_builtin_grid()
        self._register_builtin_stop_loss()
        self._register_builtin_ma()
        self._register_builtin_price()
        self._register_builtin_limit_up()
        self._register_builtin_bounce()
        self._register_builtin_board_break()
        self._register_builtin_intraday()
        self._register_builtin_scheduled()
        self._register_builtin_bull_line()
        self._register_builtin_dragon_tiger()
        self._register_builtin_ma_cross()

    def _register_builtin_macd(self) -> None:
        try:
            from quant_framework.strategy.builtin.macd_cross import MACDCrossConfig, MACDCrossStrategy
            self.register(StrategyMeta(
                name="macd_cross", label="MACD 金叉死叉",
                strategy_cls=MACDCrossStrategy, config_cls=MACDCrossConfig,
                description="MACD 金叉买入、死叉卖出，支持双重交叉确认和顶底背离检测",
                category="趋势跟踪", risk_level="中等",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "period": {"type": "str", "default": "1d", "description": "K线周期", "choices": ["1m", "5m", "15m", "30m", "1h", "1d"]},
                    "fast": {"type": "int", "default": 12, "description": "MACD 快线周期"},
                    "slow": {"type": "int", "default": 26, "description": "MACD 慢线周期"},
                    "signal_period": {"type": "int", "default": 9, "description": "信号线周期"},
                    "cross_type": {"type": "str", "default": "golden", "description": "交叉类型", "choices": ["golden", "death"]},
                    "cross_count": {"type": "int", "default": 1, "description": "交叉次数 (1=单次, 2=双交叉, 3=背离)"},
                    "volume": {"type": "int", "default": 1000, "description": "每笔交易股数"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_grid(self) -> None:
        try:
            from quant_framework.strategy.builtin.grid_trading import GridTradingConfig, GridTradingStrategy
            self.register(StrategyMeta(
                name="grid_trading", label="网格交易",
                strategy_cls=GridTradingStrategy, config_cls=GridTradingConfig,
                description="价格区间内网格自动买卖，适合震荡行情",
                category="网格交易", risk_level="低",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "base_price": {"type": "float", "default": 10.0, "description": "基准价格"},
                    "grid_spacing": {"type": "float", "default": 0.05, "description": "网格间距 (比例)"},
                    "grid_levels": {"type": "int", "default": 5, "description": "网格层数"},
                    "volume_per_grid": {"type": "int", "default": 1000, "description": "每格股数"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_stop_loss(self) -> None:
        try:
            from quant_framework.strategy.builtin.stop_profit_loss import StopProfitLossConfig, StopProfitLossStrategy
            self.register(StrategyMeta(
                name="stop_profit_loss", label="止盈止损",
                strategy_cls=StopProfitLossStrategy, config_cls=StopProfitLossConfig,
                description="价格/百分比/均线止盈止损，保护持仓利润",
                category="风控", risk_level="低",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "stop_loss_pct": {"type": "float", "default": 0.05, "description": "止损比例"},
                    "stop_profit_pct": {"type": "float", "default": 0.10, "description": "止盈比例"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_ma(self) -> None:
        try:
            from quant_framework.strategy.builtin.ma_condition import MAConditionConfig, MAConditionStrategy
            self.register(StrategyMeta(
                name="ma_condition", label="均线条件",
                strategy_cls=MAConditionStrategy, config_cls=MAConditionConfig,
                description="价格上穿/下穿均线时触发交易信号",
                category="趋势跟踪", risk_level="中等",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "ma_period": {"type": "int", "default": 20, "description": "均线周期"},
                    "condition": {"type": "str", "default": "cross_above", "description": "触发条件", "choices": ["cross_above", "cross_below"]},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_price(self) -> None:
        try:
            from quant_framework.strategy.builtin.price_condition import PriceConditionConfig, PriceConditionStrategy
            self.register(StrategyMeta(
                name="price_condition", label="价格条件",
                strategy_cls=PriceConditionStrategy, config_cls=PriceConditionConfig,
                description="价格或涨跌幅达到阈值时触发交易",
                category="事件驱动", risk_level="中等",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "price": {"type": "float", "default": 0.0, "description": "目标价格 (0=不启用)"},
                    "change_pct": {"type": "float", "default": 3.0, "description": "涨跌幅阈值 (%)"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_limit_up(self) -> None:
        try:
            from quant_framework.strategy.builtin.limit_up_chase import LimitUpChaseConfig, LimitUpChaseStrategy
            self.register(StrategyMeta(
                name="limit_up_chase", label="涨停打板",
                strategy_cls=LimitUpChaseStrategy, config_cls=LimitUpChaseConfig,
                description="监控观察列表，首个触及涨停的股票自动买入（打板策略）",
                category="打板", risk_level="高",
                params={
                    "watchlist": {"type": "list", "default": ["600000"], "description": "观察列表"},
                    "volume": {"type": "int", "default": 1000, "description": "每笔股数"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_bounce(self) -> None:
        try:
            from quant_framework.strategy.builtin.bounce_buy import BounceBuyConfig, BounceBuyStrategy
            self.register(StrategyMeta(
                name="bounce_buy", label="反弹买入",
                strategy_cls=BounceBuyStrategy, config_cls=BounceBuyConfig,
                description="股价先跌后反弹时分两阶段触发买入",
                category="反转交易", risk_level="高",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "drop_pct": {"type": "float", "default": 5.0, "description": "下跌幅度 (%)"},
                    "bounce_pct": {"type": "float", "default": 1.0, "description": "反弹幅度 (%)"},
                    "volume": {"type": "int", "default": 1000, "description": "每笔股数"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_board_break(self) -> None:
        try:
            from quant_framework.strategy.builtin.board_break import BoardBreakConfig, BoardBreakStrategy
            self.register(StrategyMeta(
                name="board_break", label="炸板预警",
                strategy_cls=BoardBreakStrategy, config_cls=BoardBreakConfig,
                description="涨停封单不足时自动卖出，防止炸板亏损",
                category="打板", risk_level="中等",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "board_value_threshold": {"type": "float", "default": 5000000.0, "description": "封单金额阈值 (元)"},
                    "sell_rate": {"type": "float", "default": 1.0, "description": "卖出比例"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_intraday(self) -> None:
        try:
            from quant_framework.strategy.builtin.intraday_change import IntradayChangeConfig, IntradayChangeStrategy
            self.register(StrategyMeta(
                name="intraday_change", label="日内异动",
                strategy_cls=IntradayChangeStrategy, config_cls=IntradayChangeConfig,
                description="检测 N 秒内涨跌幅超过阈值的异动，捕捉短线机会",
                category="事件驱动", risk_level="高",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "change_pct": {"type": "float", "default": 1.0, "description": "异动阈值 (%)"},
                    "time_window": {"type": "int", "default": 60, "description": "时间窗口 (秒)"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_scheduled(self) -> None:
        try:
            from quant_framework.strategy.builtin.scheduled_trade import ScheduledTradeConfig, ScheduledTradeStrategy
            self.register(StrategyMeta(
                name="scheduled_trade", label="定时交易",
                strategy_cls=ScheduledTradeStrategy, config_cls=ScheduledTradeConfig,
                description="在指定时间执行买卖操作，适合尾盘/开盘策略",
                category="事件驱动", risk_level="低",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "trade_time": {"type": "str", "default": "14:55", "description": "交易时间 (HH:MM)"},
                    "direction": {"type": "str", "default": "buy", "description": "交易方向", "choices": ["buy", "sell"]},
                    "volume": {"type": "int", "default": 1000, "description": "每笔股数"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_bull_line(self) -> None:
        try:
            from quant_framework.strategy.builtin.bull_line_breakout import BullLineBreakoutConfig, BullLineBreakoutStrategy
            self.register(StrategyMeta(
                name="bull_line_breakout", label="牛线突破",
                strategy_cls=BullLineBreakoutStrategy, config_cls=BullLineBreakoutConfig,
                description="牛线突破+DMA动态均线+55日高点突破+回踩确认，高质量选股信号",
                category="打板", risk_level="高",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "min_change_pct": {"type": "float", "default": 9.0, "description": "最小涨幅%"},
                    "hold_days": {"type": "int", "default": 5, "description": "持有天数"},
                    "stop_loss_pct": {"type": "float", "default": -5.0, "description": "止损%"},
                    "stop_profit_pct": {"type": "float", "default": 15.0, "description": "止盈%"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_dragon_tiger(self) -> None:
        try:
            from quant_framework.strategy.builtin.dragon_tiger import DragonTigerConfig, DragonTigerStrategy
            self.register(StrategyMeta(
                name="dragon_tiger", label="双信号共振",
                strategy_cls=DragonTigerStrategy, config_cls=DragonTigerConfig,
                description="擒龙决（打板信号）+涨停先锋（趋势确认）双信号共振，7日内首次触发",
                category="打板", risk_level="高",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "vol_ratio_threshold": {"type": "float", "default": 1.8, "description": "量比阈值"},
                    "hold_days": {"type": "int", "default": 3, "description": "持有天数"},
                    "stop_loss_pct": {"type": "float", "default": -4.0, "description": "止损%"},
                    "stop_profit_pct": {"type": "float", "default": 10.0, "description": "止盈%"},
                },
            ))
        except ImportError:
            pass

    def _register_builtin_ma_cross(self) -> None:
        """注册双均线金叉死叉策略 (E225)."""
        try:
            from quant_framework.strategy.builtin.ma_cross import MACrossConfig, MACrossStrategy
            self.register(StrategyMeta(
                name="ma_cross", label="双均线金叉死叉",
                strategy_cls=MACrossStrategy, config_cls=MACrossConfig,
                description="快线上穿慢线买入(金叉)，快线下穿慢线卖出(死叉)",
                category="趋势跟踪", risk_level="中等",
                params={
                    "symbol": {"type": "str", "default": "600000", "description": "股票代码"},
                    "fast_period": {"type": "int", "default": 5, "description": "快线周期"},
                    "slow_period": {"type": "int", "default": 20, "description": "慢线周期"},
                    "volume": {"type": "int", "default": 1000, "description": "每笔交易股数"},
                },
            ))
        except ImportError:
            pass
