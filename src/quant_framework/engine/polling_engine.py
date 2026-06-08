"""Polling engine — compatibility mode for blocking data providers.

Wraps the while-wait_update pattern used by THS and similar providers
that don't natively support event-driven push. Internally converts
wait_update results into Event objects and dispatches them through
the EventEngine.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from quant_framework.core.constants import EventType
from quant_framework.core.events import Event, QuoteEvent, SystemEvent
from quant_framework.data.provider import DataProvider
from quant_framework.engine.event_engine import EventEngine

logger = logging.getLogger("quant_framework.polling_engine")


class PollingEngine:
    """Polling-mode engine.

    Designed for data providers that use a blocking wait_for_update()
    pattern (e.g., 同花顺 THS API). Each data update triggers conversion
    to framework Event objects, which are dispatched through the internal
    EventEngine.

    Supports two modes:
    1. Pure polling: while True -> provider.wait_update() -> dispatch events
    2. Hybrid: polling for data + EventEngine timers for periodic tasks

    Usage:
        provider = THSDataProvider()
        provider.connect()
        provider.subscribe_quote(["600000"])

        engine = PollingEngine(provider)
        engine.register_strategy(my_strategy)
        engine.run()  # blocks until stop() is called
    """

    def __init__(
        self,
        data_provider: DataProvider,
        event_engine: EventEngine | None = None,
    ) -> None:
        self._provider = data_provider
        self._event_engine = event_engine or EventEngine(name="PollingEngine")
        self._running = False
        self._strategies: list[Any] = []  # BaseStrategy instances

    def register_strategy(self, strategy: Any) -> None:
        """Register a strategy instance to receive events.

        The strategy must have on_quote(quote) and/or on_bar(bar) methods.

        Args:
            strategy: BaseStrategy instance.
        """
        self._strategies.append(strategy)
        logger.info("Strategy '%s' registered with PollingEngine", getattr(strategy, 'ctx', strategy))

    def run(self) -> None:
        """Start the main polling loop. Blocks until stop() is called.

        The loop:
        1. Calls provider.wait_update() to get changed symbols
        2. For each symbol, pulls the latest quote
        3. Dispatches QuoteEvent through EventEngine
        4. Strategies receive events via their registered handlers
        """
        if self._running:
            return

        self._running = True
        logger.info("PollingEngine started with provider '%s'", self._provider.name)

        # Start the internal event engine
        self._event_engine.start()

        # Register strategy handlers with the event engine
        for strategy in self._strategies:
            self._event_engine.register_strategy_handlers(
                on_quote=self._make_quote_handler(strategy),
            )

        try:
            while self._running:
                # Block until data update
                try:
                    changed = self._provider.wait_update(timeout=1.0)
                except Exception as e:
                    logger.error("Provider wait_update failed: %s", e)
                    continue

                if not changed:
                    continue

                # Pull latest quotes for changed symbols
                try:
                    quotes = self._provider.get_quote(changed)
                except Exception as e:
                    logger.error("Failed to get quotes: %s", e)
                    continue

                now = datetime.now()
                for symbol in changed:
                    quote = quotes.get(symbol)
                    if quote is None:
                        continue

                    event = QuoteEvent(
                        type=EventType.QUOTE,
                        timestamp=now,
                        symbol=symbol,
                        price=quote.price,
                        change_pct=quote.change_pct,
                        bid1_price=quote.bid_prices[0] if quote.bid_prices else 0.0,
                        bid1_volume=quote.bid_volumes[0] if quote.bid_volumes else 0,
                        ask1_price=quote.ask_prices[0] if quote.ask_prices else 0.0,
                        ask1_volume=quote.ask_volumes[0] if quote.ask_volumes else 0,
                        limit_up=quote.limit_up,
                        limit_down=quote.limit_down,
                        volume=quote.volume,
                        amount=quote.amount,
                    )
                    self._event_engine.put(event)

        except KeyboardInterrupt:
            logger.info("PollingEngine interrupted by user")
        finally:
            self.stop()

    def stop(self) -> None:
        """Stop the polling loop and event engine."""
        self._running = False
        self._event_engine.stop()
        logger.info("PollingEngine stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- Internal ----

    def _make_quote_handler(self, strategy: Any):
        """Create a handler that converts QuoteEvent -> strategy.on_quote()."""
        def handler(event: Event) -> None:
            if not isinstance(event, QuoteEvent):
                return
            quote = self._provider.get_quote([event.symbol]).get(event.symbol)
            if quote and hasattr(strategy, "on_quote"):
                signals = strategy.on_quote(quote)
                if signals:
                    # Process signals through the full pipeline
                    # (risk -> position -> execution — handled by the strategy context)
                    pass
        return handler
