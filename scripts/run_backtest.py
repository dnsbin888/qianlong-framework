#!/usr/bin/env python
"""Run strategies in backtest mode with full configuration support.

Loads YAML config, sets up data providers, risk engine, position sizing,
and runs registered strategies against historical data.

Usage:
    python scripts/run_backtest.py -c config/default.yaml -s macd_cross
    python scripts/run_backtest.py --start 2024-01-01 --end 2024-12-31 --capital 1000000
    python scripts/run_backtest.py -c config/default.yaml --all
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure the framework package is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from quant_framework.config import FrameworkConfig, load_config
from quant_framework.backtest.engine import BacktestConfig, BacktestEngine
from quant_framework.monitor.logger import setup_framework_logging
from quant_framework.position.sizers import (
    ATRDynamicSizer,
    EqualWeightSizer,
    FixedRatioSizer,
    KellySizer,
    RiskParitySizer,
    TargetVolSizer,
)
from quant_framework.risk.engine import RiskEngine
from quant_framework.risk.rules import (
    BlacklistRule,
    DailyLossLimitRule,
    MaxDrawdownRule,
    OrderFrequencyRule,
    PositionLimitRule,
    TotalPositionsRule,
)

logger = logging.getLogger("quant_framework.run_backtest")

# ---------------------------------------------------------------------------
# Strategy registry — maps CLI names to (StrategyClass, ConfigClass, default_kwargs)
# ---------------------------------------------------------------------------

def _build_strategy_registry() -> dict[str, tuple[Any, Any, dict[str, Any]]]:
    """Build a lookup table of available strategies."""
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

    try:
        from quant_framework.strategy.builtin.limit_up_chase import LimitUpChaseConfig, LimitUpChaseStrategy
        registry["limit_up_chase"] = (LimitUpChaseStrategy, LimitUpChaseConfig, {
            "watchlist": ["600000"], "period": "1d", "volume": 1000,
        })
    except ImportError:
        pass

    try:
        from quant_framework.strategy.builtin.bounce_buy import BounceBuyConfig, BounceBuyStrategy
        registry["bounce_buy"] = (BounceBuyStrategy, BounceBuyConfig, {
            "symbol": "600000", "period": "1d",
            "drop_pct": 5.0, "bounce_pct": 1.0, "volume": 1000,
        })
    except ImportError:
        pass

    try:
        from quant_framework.strategy.builtin.intraday_change import IntradayChangeConfig, IntradayChangeStrategy
        registry["intraday_change"] = (IntradayChangeStrategy, IntradayChangeConfig, {
            "symbol": "600000", "period": "1m",
            "change_pct": 1.0, "time_window": 60,
        })
    except ImportError:
        pass

    return registry


STRATEGY_REGISTRY = _build_strategy_registry()

# ---------------------------------------------------------------------------
# Position sizer registry
# ---------------------------------------------------------------------------

POSITION_SIZERS = {
    "fixed_ratio": lambda cfg: FixedRatioSizer(ratio=cfg.get("ratio", 0.10)),
    "kelly": lambda cfg: KellySizer(
        win_rate=cfg.get("win_rate", 0.50),
        profit_loss_ratio=cfg.get("profit_loss_ratio", 2.0),
        fraction=cfg.get("fraction", 0.5),
    ),
    "atr": lambda cfg: ATRDynamicSizer(
        risk_per_trade=cfg.get("risk_per_trade", 0.02),
        atr_multiplier=cfg.get("atr_multiplier", 2.0),
    ),
    "equal_weight": lambda cfg: EqualWeightSizer(max_positions=cfg.get("max_positions", 10)),
    "risk_parity": lambda cfg: RiskParitySizer(target_risk_pct=cfg.get("target_risk_pct", 0.05)),
    "target_vol": lambda cfg: TargetVolSizer(target_annual_vol=cfg.get("target_annual_vol", 0.15)),
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quant Framework — Backtest Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available strategies: {', '.join(STRATEGY_REGISTRY.keys())}",
    )
    parser.add_argument(
        "--config", "-c", default="config/default.yaml",
        help="Configuration file path (YAML)",
    )
    parser.add_argument(
        "--strategy", "-s", default="all",
        help="Strategy names (comma-separated, or 'all')",
    )
    parser.add_argument(
        "--symbol", default=None,
        help="Stock symbol to trade (e.g. 600000). Overrides strategy defaults.",
    )
    parser.add_argument(
        "--start", default=None,
        help="Start date (YYYY-MM-DD). Overrides config.",
    )
    parser.add_argument(
        "--end", default=None,
        help="End date (YYYY-MM-DD). Overrides config.",
    )
    parser.add_argument(
        "--capital", type=float, default=None,
        help="Initial capital in CNY. Overrides config.",
    )
    parser.add_argument(
        "--sizer", default="fixed_ratio",
        choices=list(POSITION_SIZERS.keys()),
        help="Position sizing algorithm (default: fixed_ratio)",
    )
    parser.add_argument(
        "--output", "-o", default="backtest_report.html",
        help="Output HTML report path",
    )
    parser.add_argument(
        "--csv-output", default=None,
        help="Also write trades to CSV file",
    )
    parser.add_argument(
        "--list-strategies", action="store_true",
        help="List available strategies and exit",
    )
    parser.add_argument(
        "--risk", action="store_true", default=True,
        help="Enable risk engine (default: True)",
    )
    parser.add_argument(
        "--no-risk", action="store_false", dest="risk",
        help="Disable risk engine",
    )
    args = parser.parse_args()

    # List strategies mode
    if args.list_strategies:
        print("Available strategies:")
        for name, (cls, cfg_cls, defaults) in STRATEGY_REGISTRY.items():
            print(f"  {name:<20s} — {cls.__doc__ or '(no description)'}")
        return

    print("=" * 60)
    print("  Quant Framework — Backtest Runner")
    print("=" * 60)

    # 1. Load configuration
    config_path = Path(args.config)
    if config_path.exists():
        print(f"  Config:    {config_path}")
        fw_cfg = load_config(config_path)
    else:
        print(f"  Config:    (not found, using defaults)")
        fw_cfg = FrameworkConfig()

    # 2. Setup logging
    setup_framework_logging(
        log_dir=fw_cfg.framework.log_dir,
        log_level=fw_cfg.framework.log_level,
        console=True,
    )

    # 3. Build backtest config (CLI args override YAML)
    bt_cfg = BacktestConfig(
        initial_cash=args.capital or fw_cfg.backtest.initial_cash,
        commission_rate=fw_cfg.execution.commission_rate,
        slippage_model=args.start or fw_cfg.backtest.slippage_model.value,
        slippage_value=fw_cfg.backtest.slippage_value,
        benchmark=fw_cfg.backtest.benchmark,
        risk_free_rate=fw_cfg.backtest.risk_free_rate,
        start_date=args.start or "2024-01-01",
        end_date=args.end or "",
    )
    # Fix: if CLI args gave slippage model from config, use it properly
    bt_cfg.slippage_model = fw_cfg.backtest.slippage_model.value

    print(f"  Period:    {bt_cfg.start_date} -> {bt_cfg.end_date or 'today'}")
    print(f"  Capital:   {bt_cfg.initial_cash:,.0f} CNY")

    # 4. Setup data provider
    store = _create_data_store(fw_cfg)
    from quant_framework.data.providers.simulated import SimulatedDataProvider

    # Determine symbols from strategy selection
    strategy_names = _resolve_strategies(args.strategy)
    symbols: list[str] = []
    if args.symbol:
        symbols = [s.strip() for s in args.symbol.split(",")]
    else:
        for name in strategy_names:
            defaults = STRATEGY_REGISTRY[name][2]
            sym = defaults.get("symbol")
            if sym and sym not in symbols:
                symbols.append(sym)
            watchlist = defaults.get("watchlist")
            if watchlist:
                for s in watchlist:
                    if s not in symbols:
                        symbols.append(s)

    if not symbols:
        symbols = ["600000"]  # Default fallback

    print(f"  Symbols:   {', '.join(symbols)}")

    provider = SimulatedDataProvider(store=store, symbols=symbols, period="1d")
    provider.connect()
    print(f"  Data bars: {provider.total_steps}")

    # 5. Setup risk engine
    risk_engine: RiskEngine | None = None
    if args.risk and fw_cfg.risk.enabled:
        risk_engine = RiskEngine()
        risk_engine.add_global_rule(MaxDrawdownRule(fw_cfg.risk.max_drawdown_pct))
        risk_engine.add_global_rule(DailyLossLimitRule(fw_cfg.risk.max_daily_loss))
        risk_engine.add_global_rule(PositionLimitRule(fw_cfg.risk.max_single_position_pct))
        risk_engine.add_global_rule(TotalPositionsRule(fw_cfg.risk.max_total_positions))
        risk_engine.add_global_rule(OrderFrequencyRule(fw_cfg.risk.order_cooldown_seconds))
        if fw_cfg.risk.blacklist:
            risk_engine.add_global_rule(BlacklistRule(fw_cfg.risk.blacklist))
        print(f"  Risk:      enabled ({risk_engine.total_rule_count} rules)")

    # 6. Setup position sizer
    sizer = POSITION_SIZERS[args.sizer]({})
    print(f"  Sizer:     {args.sizer}")

    # 7. Create engine
    engine = BacktestEngine(
        provider,
        bt_cfg,
        risk_engine=risk_engine,
        position_sizer=sizer,
    )

    # 8. Register strategies
    print(f"  Strategies:")
    for name in strategy_names:
        cls, cfg_cls, defaults = STRATEGY_REGISTRY[name]
        # Apply symbol override if given
        if args.symbol:
            defaults = {**defaults}
            defaults["symbol"] = args.symbol
        strategy_cfg = cfg_cls(**defaults)
        engine.add_strategy(cls, config={"cfg": strategy_cfg})
        print(f"    - {name} ({cfg_cls.__name__})")

    # 9. Run backtest
    print()
    print("Running backtest...")
    report = engine.run()

    # 10. Print results
    print()
    print(report.summary())

    # 11. Generate reports
    from quant_framework.backtest.report import generate_html_report
    html_path = generate_html_report(report, args.output)
    print(f"\nHTML report: {html_path}")

    if args.csv_output:
        import pandas as pd
        trades_data = [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "direction": t.direction.value,
                "price": t.price,
                "volume": t.volume,
                "commission": t.commission,
                "timestamp": t.timestamp,
            }
            for t in report.trades
        ]
        if trades_data:
            pd.DataFrame(trades_data).to_csv(args.csv_output, index=False)
            print(f"CSV trades:  {args.csv_output}")

    # 12. Print trade summary
    if report.trades:
        buy_trades = [t for t in report.trades if t.direction.value == "buy"]
        sell_trades = [t for t in report.trades if t.direction.value == "sell"]
        print(f"\nTrade summary: {len(buy_trades)} buys, {len(sell_trades)} sells, {len(report.trades)} total")

    print("\nDone.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_strategies(strategy_arg: str) -> list[str]:
    """Resolve strategy names from CLI arg."""
    if strategy_arg == "all":
        return list(STRATEGY_REGISTRY.keys())
    names = [s.strip() for s in strategy_arg.split(",")]
    for name in names:
        if name not in STRATEGY_REGISTRY:
            print(f"Warning: unknown strategy '{name}' — skipping")
    return [n for n in names if n in STRATEGY_REGISTRY]


def _create_data_store(fw_cfg: FrameworkConfig) -> Any:
    """Create a data store based on config."""
    from quant_framework.data.store import CSVDataStore

    data_dir = fw_cfg.framework.data_dir
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    return CSVDataStore(data_dir)


if __name__ == "__main__":
    main()
