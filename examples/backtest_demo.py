"""Backtest Demo — Run a MACD strategy through the backtest engine.

This example shows how to:
1. Load historical data from CSV
2. Register strategy for backtesting
3. Run backtest and generate performance report

Usage:
    python examples/backtest_demo.py
"""

from datetime import datetime

from quant_framework.backtest.engine import BacktestConfig, BacktestEngine
from quant_framework.data.providers.simulated import SimulatedDataProvider
from quant_framework.data.store import CSVDataStore
from quant_framework.monitor.logger import setup_framework_logging
from quant_framework.strategy.builtin.macd_cross import MACDCrossConfig, MACDCrossStrategy


def main() -> None:
    setup_framework_logging(log_dir="./logs", log_level="INFO", console=True)

    # 1. Prepare data
    print("=" * 50)
    print("  Quant Framework — Backtest Demo")
    print("=" * 50)

    store = CSVDataStore("./data/market")
    print(f"Data store: {store._data_dir}")

    # 2. Setup backtest
    config = BacktestConfig(
        initial_cash=1_000_000.0,
        commission_rate=0.0003,      # 万三
        slippage_model="proportional",
        slippage_value=0.001,        # 0.1%
        benchmark="000300",
        start_date="2024-01-01",
        end_date="2024-12-31",
    )

    provider = SimulatedDataProvider(
        store=store,
        symbols=["600000"],
        period="1d",
    )
    provider.connect()

    engine = BacktestEngine(provider, config)

    # 3. Register strategy
    strategy_config = MACDCrossConfig(
        symbol="600000",
        period="1d",
        fast=12,
        slow=26,
        signal_period=9,
        cross_type="golden",
        cross_count=1,
        volume=1000,
    )

    engine.add_strategy(MACDCrossStrategy, config={"cfg": strategy_config})

    # 4. Run backtest
    print(f"Running backtest on {provider.total_steps} bars...")
    report = engine.run()

    # 5. Print results
    print()
    print(report.summary())

    # 6. Generate HTML report
    from quant_framework.backtest.report import generate_html_report
    path = generate_html_report(report, "backtest_report.html")
    print(f"\nHTML report saved to: {path}")


if __name__ == "__main__":
    main()
