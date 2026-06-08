"""Grid Trading Strategy — Demo.

Demonstrates the grid trading strategy in polling mode.

Usage:
    python examples/grid_trading_demo.py
"""

from quant_framework.data.providers.ths import THSDataProvider
from quant_framework.engine.context import StrategyContext
from quant_framework.engine.polling_engine import PollingEngine
from quant_framework.monitor.logger import StrategyLogger, setup_framework_logging
from quant_framework.strategy.builtin.grid_trading import GridTradingConfig, GridTradingStrategy


def main() -> None:
    setup_framework_logging(log_dir="./logs")

    provider = THSDataProvider()
    provider.connect()
    provider.subscribe_quote(["300033"])

    config = GridTradingConfig(
        symbol="300033",
        price_init=89.80,
        up_spread=0.10,         # Sell when price rises 0.10 above base
        down_spread=0.10,       # Buy when price drops 0.10 below base
        max_price=120.0,
        min_price=70.0,
        buy_volume=200,
        sell_volume=200,
        max_position=35000,
        min_position=400,       # Keep 400 shares as base
    )

    ctx = StrategyContext(
        strategy_id="grid_demo",
        name="Grid Trading Demo",
        data_provider=provider,
        logger=StrategyLogger("grid_demo"),
        config=config.__dict__,
    )

    strategy = GridTradingStrategy(ctx, config)
    strategy.on_init()

    engine = PollingEngine(provider)
    engine.register_strategy(strategy)

    print(f"Grid Trading Demo started. Base price: {config.price_init}")
    print("Grid range: {:.2f} - {:.2f}".format(config.min_price, config.max_price))
    print("Press Ctrl+C to stop.")

    engine.run()


if __name__ == "__main__":
    main()
