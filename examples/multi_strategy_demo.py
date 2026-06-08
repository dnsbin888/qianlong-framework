"""Multi-Strategy Demo — Run multiple strategies in parallel.

Demonstrates:
- Multiple strategies sharing the same data provider
- RiskEngine protecting the portfolio
- PositionSizer integration
- TradeRecorder persistence

Usage:
    python examples/multi_strategy_demo.py
"""

from quant_framework.data.providers.ths import THSDataProvider
from quant_framework.engine.context import PortfolioSnapshot, StrategyContext
from quant_framework.engine.polling_engine import PollingEngine
from quant_framework.execution.brokers.ths import THSBroker
from quant_framework.monitor.logger import StrategyLogger, setup_framework_logging
from quant_framework.monitor.notifier import ConsoleNotifier, NotifierManager
from quant_framework.monitor.recorder import TradeRecorder
from quant_framework.position.sizers import FixedRatioSizer
from quant_framework.risk.engine import RiskEngine
from quant_framework.risk.rules import (
    DailyLossLimitRule,
    MaxDrawdownRule,
    PositionLimitRule,
)
from quant_framework.strategy.builtin.bounce_buy import BounceBuyConfig, BounceBuyStrategy
from quant_framework.strategy.builtin.limit_up_chase import LimitUpChaseConfig, LimitUpChaseStrategy
from quant_framework.strategy.builtin.stop_profit_loss import StopProfitLossConfig, StopProfitLossStrategy


def main() -> None:
    # Infrastructure
    setup_framework_logging(log_dir="./logs")
    recorder = TradeRecorder("./data/trades.db")
    notifier = NotifierManager()
    notifier.add(ConsoleNotifier())

    # Risk engine
    risk = RiskEngine()
    risk.add_global_rule(MaxDrawdownRule(0.20))
    risk.add_global_rule(DailyLossLimitRule(50000.0))
    risk.add_global_rule(PositionLimitRule(0.30))

    # Data & Execution
    provider = THSDataProvider()
    provider.connect()
    broker = THSBroker()
    broker.connect()

    symbols = ["600000", "000001", "300033"]

    provider.subscribe_quote(symbols)

    # Portfolio (shared across strategies)
    portfolio = PortfolioSnapshot()

    # Position sizer
    sizer = FixedRatioSizer(ratio=0.10)

    # Strategy 1: Limit-up chasing
    chase_ctx = StrategyContext(
        strategy_id="chase_1",
        name="Limit-Up Chase",
        data_provider=provider,
        broker=broker,
        logger=StrategyLogger("chase_1"),
        portfolio=portfolio,
    )
    chase_config = LimitUpChaseConfig(
        watchlist=symbols,
        max_positions=3,
        volume=1000,
    )
    chase_strategy = LimitUpChaseStrategy(chase_ctx, chase_config)
    risk.add_strategy_rule("chase_1", PositionLimitRule(0.30))

    # Strategy 2: Stop-profit/stop-loss
    stop_ctx = StrategyContext(
        strategy_id="stop_1",
        name="Stop Profit/Loss",
        data_provider=provider,
        broker=broker,
        logger=StrategyLogger("stop_1"),
        portfolio=portfolio,
    )
    stop_config = StopProfitLossConfig(
        symbol="600000",
        take_profit_pct=0.10,
        stop_loss_pct=0.05,
        close_position=True,
    )
    stop_strategy = StopProfitLossStrategy(stop_ctx, stop_config)

    # Run
    engine = PollingEngine(provider)
    engine.register_strategy(chase_strategy)
    engine.register_strategy(stop_strategy)

    print("=" * 50)
    print("  Multi-Strategy Demo")
    print(f"  Strategies: Limit-Up Chase + Stop Profit/Loss")
    print(f"  Watching: {symbols}")
    print(f"  Risk Rules: {risk.list_rules('chase_1')}")
    print("=" * 50)
    print("Press Ctrl+C to stop.")

    engine.run()


if __name__ == "__main__":
    main()
