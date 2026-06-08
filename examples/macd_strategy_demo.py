"""MACD Crossover Strategy — Live Demo.

This example demonstrates running the MACD strategy in polling mode
with the THS data provider (同花顺 environment).

Usage:
    python examples/macd_strategy_demo.py
"""

from quant_framework.data.providers.ths import THSDataProvider
from quant_framework.engine.context import StrategyContext
from quant_framework.engine.polling_engine import PollingEngine
from quant_framework.monitor.logger import StrategyLogger, setup_framework_logging
from quant_framework.strategy.builtin.macd_cross import MACDCrossConfig, MACDCrossStrategy


def main() -> None:
    # Setup logging
    setup_framework_logging(log_dir="./logs", log_level="INFO")

    # 1. Create data provider (同花顺行情)
    provider = THSDataProvider()
    provider.connect()
    provider.subscribe_quote(["600000"])

    # 2. Create strategy configuration
    config = MACDCrossConfig(
        symbol="600000",
        period="15m",           # 15-minute K-line
        fast=12,
        slow=26,
        signal_period=9,
        cross_type="golden",    # 金叉
        cross_count=1,          # Single cross
        divergence_window=50,
        volume=500,
    )

    # 3. Create strategy context
    ctx = StrategyContext(
        strategy_id="macd_demo",
        name="MACD Golden Cross Demo",
        data_provider=provider,
        logger=StrategyLogger("macd_demo"),
        config=config.__dict__,
    )

    # 4. Create strategy instance
    strategy = MACDCrossStrategy(ctx, config)
    strategy.on_init()

    # 5. Start polling engine
    engine = PollingEngine(provider)
    engine.register_strategy(strategy)

    print("MACD Strategy Demo started. Monitoring 600000 for golden cross...")
    print("Press Ctrl+C to stop.")

    engine.run()


if __name__ == "__main__":
    main()
