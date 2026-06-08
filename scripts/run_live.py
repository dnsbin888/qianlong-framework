#!/usr/bin/env python
"""Run strategies in live/paper trading mode.

Loads configuration from YAML, sets up data providers, broker, risk engine,
and starts the engine with all registered strategies.

Usage:
    python scripts/run_live.py --config config/default.yaml
    python scripts/run_live.py --config config/default.yaml --strategy macd_cross
    python scripts/run_live.py -c config/default.yaml -m paper
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quant_framework.config import FrameworkConfig, load_config
from quant_framework.monitor.logger import setup_framework_logging
from quant_framework.position.sizers import FixedRatioSizer
from quant_framework.risk.engine import RiskEngine
from quant_framework.risk.rules import (
    BlacklistRule,
    ConsecutiveLossRule,
    DailyLossLimitRule,
    DailyTradeCountRule,
    MarketCircuitBreakerRule,
    MaxDrawdownRule,
    OrderFrequencyRule,
    PositionLimitRule,
    SingleOrderAmountRule,
    TotalPositionsRule,
)

logger = logging.getLogger("quant_framework.run_live")

# ---------------------------------------------------------------------------
# Strategy registry — same as in run_backtest.py
# ---------------------------------------------------------------------------

def _build_strategy_registry() -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    registry: dict[str, tuple[Any, Any, dict[str, Any]]] = {}
    try:
        from quant_framework.strategy.builtin.macd_cross import MACDCrossConfig, MACDCrossStrategy
        registry["macd_cross"] = (MACDCrossStrategy, MACDCrossConfig, {
            "symbol": "600000", "period": "1d",
            "fast": 12, "slow": 26, "signal_period": 9,
            "cross_type": "golden", "cross_count": 1, "volume": 1000,
        })
    except ImportError:
        pass
    try:
        from quant_framework.strategy.builtin.grid_trading import GridTradingConfig, GridTradingStrategy
        registry["grid_trading"] = (GridTradingStrategy, GridTradingConfig, {
            "symbol": "600000", "period": "1d",
            "base_price": 10.0, "grid_spacing": 0.05,
            "grid_levels": 5, "volume_per_grid": 1000,
        })
    except ImportError:
        pass
    try:
        from quant_framework.strategy.builtin.stop_profit_loss import StopProfitLossConfig, StopProfitLossStrategy
        registry["stop_profit_loss"] = (StopProfitLossStrategy, StopProfitLossConfig, {
            "symbol": "600000", "period": "1d",
            "stop_loss_pct": 0.05, "stop_profit_pct": 0.10,
        })
    except ImportError:
        pass
    try:
        from quant_framework.strategy.builtin.ma_condition import MAConditionConfig, MAConditionStrategy
        registry["ma_condition"] = (MAConditionStrategy, MAConditionConfig, {
            "symbol": "600000", "period": "1d",
            "ma_period": 20, "condition": "cross_above",
        })
    except ImportError:
        pass
    try:
        from quant_framework.strategy.builtin.price_condition import PriceConditionConfig, PriceConditionStrategy
        registry["price_condition"] = (PriceConditionStrategy, PriceConditionConfig, {
            "symbol": "600000", "period": "1d",
            "price": 0.0, "change_pct": 3.0,
        })
    except ImportError:
        pass
    return registry


STRATEGY_REGISTRY = _build_strategy_registry()

# Global flag for graceful shutdown
_running = True


def _signal_handler(signum: int, frame: Any) -> None:
    global _running
    print("\nShutting down...")
    _running = False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quant Framework — Live Trading Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available strategies: {', '.join(STRATEGY_REGISTRY.keys())}",
    )
    parser.add_argument("--config", "-c", default="config/default.yaml", help="Configuration file path")
    parser.add_argument("--strategy", "-s", default="all", help="Strategy name to run (or 'all')")
    parser.add_argument("--symbol", default=None, help="Stock symbol (overrides strategy default)")
    parser.add_argument("--mode", "-m", default="paper", choices=["live", "paper"], help="Trading mode")
    parser.add_argument("--interval", type=float, default=3.0, help="Polling interval in seconds")
    parser.add_argument("--list-strategies", action="store_true", help="List available strategies and exit")
    args = parser.parse_args()

    if args.list_strategies:
        print("Available strategies:")
        for name, (cls, cfg_cls, defaults) in STRATEGY_REGISTRY.items():
            print(f"  {name:<20s} — {cls.__doc__ or '(no description)'}")
        return

    print("=" * 60)
    print("  Quant Framework — Live Trading Runner")
    print("=" * 60)

    # 1. Load configuration
    config_path = Path(args.config)
    if config_path.exists():
        print(f"  Config:    {config_path}")
        fw_cfg = load_config(config_path)
    else:
        print(f"  Config:    (not found, using defaults)")
        fw_cfg = FrameworkConfig()

    print(f"  Mode:      {args.mode}")
    print(f"  Strategy:  {args.strategy}")
    print(f"  Interval:  {args.interval}s")
    print("=" * 60)

    # 2. Setup logging
    setup_framework_logging(
        log_dir=fw_cfg.framework.log_dir,
        log_level=fw_cfg.framework.log_level,
        console=True,
    )

    # 3. Setup data provider
    provider = _create_data_provider(fw_cfg)
    provider.connect()
    print(f"  Data:      {fw_cfg.data.provider.value} {'(connected)' if provider.is_connected() else '(failed)'}")

    # 4. Setup broker
    broker = _create_broker(fw_cfg, args.mode)
    print(f"  Broker:    {fw_cfg.execution.broker.value if args.mode != 'paper' else 'simulated'}")

    # 5. Setup risk engine
    risk_engine: RiskEngine | None = None
    if fw_cfg.risk.enabled:
        risk_engine = RiskEngine()
        risk_engine.add_global_rule(MaxDrawdownRule(fw_cfg.risk.max_drawdown_pct))
        # 优先使用百分比模式; 如果配置了 max_daily_loss_pct 则用百分比, 否则回退到绝对金额
        risk_engine.add_global_rule(DailyLossLimitRule(
            max_daily_loss_pct=fw_cfg.risk.max_daily_loss_pct,
        ))
        risk_engine.add_global_rule(PositionLimitRule(fw_cfg.risk.max_single_position_pct))
        risk_engine.add_global_rule(TotalPositionsRule(fw_cfg.risk.max_total_positions))
        risk_engine.add_global_rule(OrderFrequencyRule(fw_cfg.risk.order_cooldown_seconds))
        # 新增规则
        risk_engine.add_global_rule(SingleOrderAmountRule(
            max_amount_pct=fw_cfg.risk.single_order_max_pct,
        ))
        risk_engine.add_global_rule(DailyTradeCountRule(
            max_daily_trades=fw_cfg.risk.max_daily_trades,
        ))
        risk_engine.add_global_rule(MarketCircuitBreakerRule(
            reference_symbol="sh000300",
            level1_pct=0.03,
            level2_pct=0.05,
        ))
        risk_engine.add_global_rule(ConsecutiveLossRule(
            max_consecutive_days=3,
            action="reduce",
            reduce_to_pct=0.5,
        ))
        if fw_cfg.risk.blacklist:
            risk_engine.add_global_rule(BlacklistRule(fw_cfg.risk.blacklist))
        print(f"  Risk:      {risk_engine.total_rule_count} rules active")

    # 6. Setup position sizer
    sizer = FixedRatioSizer(ratio=fw_cfg.risk.max_single_position_pct or 0.10)

    # 7. Setup polling engine
    from quant_framework.engine.event_engine import EventEngine
    from quant_framework.engine.polling_engine import PollingEngine

    event_engine = EventEngine()
    polling_engine = PollingEngine(
        provider=provider,
        event_engine=event_engine,
        symbols=_resolve_symbols(args),
        interval=args.interval,
    )

    # 8. Create and register strategies
    from quant_framework.engine.context import StrategyContext

    strategies: list[Any] = []
    strategy_names = _resolve_strategies(args.strategy)

    for name in strategy_names:
        cls, cfg_cls, defaults = STRATEGY_REGISTRY[name]
        if args.symbol:
            defaults = {**defaults, "symbol": args.symbol}
        strategy_cfg = cfg_cls(**defaults)

        ctx = StrategyContext(
            strategy_id=name,
            name=name,
            data_provider=provider,
            broker=broker,
            config={"mode": args.mode, **defaults},
        )
        strategy = cls(ctx, cfg=strategy_cfg)
        strategy.on_init()
        strategy.on_start()
        strategies.append(strategy)

        # Register strategy handlers on event engine
        event_engine.register_strategy_handlers(strategy)
        print(f"  Strategy:  {name} ({cfg_cls.__name__}) on {defaults.get('symbol', 'N/A')}")

    # 9. Setup graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # 10. Main loop
    print(f"\nLive trading started. Press Ctrl+C to stop.")
    print("-" * 60)

    global _running
    try:
        polling_engine.start()

        while _running:
            # Polling engine handles data flow and event dispatch in its thread
            # Main thread monitors risk and displays status
            time.sleep(args.interval)

            # Display status update
            for strategy in strategies:
                ctx = strategy.ctx
                pos_count = len(ctx.portfolio.positions)
                cash = ctx.portfolio.cash if ctx.portfolio else "N/A"
                logger.info(
                    "status_update",
                    strategy=ctx.name,
                    positions=pos_count,
                    equity=ctx.total_equity,
                )

    except KeyboardInterrupt:
        pass
    finally:
        print("\nStopping...")
        polling_engine.stop()
        for strategy in strategies:
            strategy.on_stop()
        provider.disconnect()
        print("All components stopped. Goodbye.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_strategies(strategy_arg: str) -> list[str]:
    if strategy_arg == "all":
        return list(STRATEGY_REGISTRY.keys())
    names = [s.strip() for s in strategy_arg.split(",")]
    return [n for n in names if n in STRATEGY_REGISTRY]


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbol:
        return [s.strip() for s in args.symbol.split(",")]
    symbols = []
    for name in _resolve_strategies(args.strategy):
        sym = STRATEGY_REGISTRY[name][2].get("symbol")
        if sym and sym not in symbols:
            symbols.append(sym)
    return symbols or ["600000"]


def _create_data_provider(fw_cfg: FrameworkConfig) -> Any:
    """Create a data provider based on config."""
    provider_name = fw_cfg.data.provider.value

    if provider_name == "akshare":
        try:
            from quant_framework.data.providers.akshare import AKShareDataProvider
            return AKShareDataProvider()
        except ImportError:
            logger.warning("akshare not installed, falling back to simulated")

    if provider_name == "tushare":
        try:
            from quant_framework.data.providers.tushare import TushareDataProvider
            return TushareDataProvider()
        except ImportError:
            logger.warning("tushare not installed, falling back to simulated")

    if provider_name == "ths":
        try:
            from quant_framework.data.providers.ths import THSDataProvider
            return THSDataProvider()
        except ImportError:
            logger.warning("THS provider not available, falling back to simulated")

    # Fallback: simulated provider
    from quant_framework.data.providers.simulated import SimulatedDataProvider
    from quant_framework.data.store import CSVDataStore

    store = CSVDataStore(fw_cfg.framework.data_dir)
    return SimulatedDataProvider(store=store, symbols=["600000"], period="1d")


def _create_broker(fw_cfg: FrameworkConfig, mode: str) -> Any:
    """Create a broker based on config and mode."""
    if mode == "paper":
        from quant_framework.execution.brokers.simulated import SimulatedBroker
        return SimulatedBroker(initial_cash=fw_cfg.backtest.initial_cash)

    broker_name = fw_cfg.execution.broker.value
    if broker_name == "ths":
        try:
            from quant_framework.execution.brokers.ths import THSBroker
            return THSBroker()
        except ImportError:
            logger.warning("THS broker not available, falling back to simulated")
            from quant_framework.execution.brokers.simulated import SimulatedBroker
            return SimulatedBroker(initial_cash=fw_cfg.backtest.initial_cash)

    from quant_framework.execution.brokers.simulated import SimulatedBroker
    return SimulatedBroker(initial_cash=fw_cfg.backtest.initial_cash)


if __name__ == "__main__":
    main()
