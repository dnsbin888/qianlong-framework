"""Event-driven engine — single-threaded event loop with timer support.

The EventEngine is the heart of the event-driven runtime. It:
1. Maintains a thread-safe event queue
2. Runs a single-threaded dispatch loop (no concurrency bugs)
3. Supports periodic timers via a min-heap scheduler
4. Bridges data providers, strategies, risk, execution, and monitoring
"""

from __future__ import annotations

import heapq
import logging
import threading
import time as _time
from datetime import datetime
from queue import Empty, Queue
from typing import Callable

from quant_framework.core.constants import EventType, OrderStatus
from quant_framework.core.events import Event, OrderEvent, SystemEvent, TimerEvent
from quant_framework.engine.event_bus import EventBus, Handler

logger = logging.getLogger("quant_framework.event_engine")


class Timer:
    """A scheduled timer entry."""

    __slots__ = ("next_fire", "interval", "callback", "timer_id", "repeat")

    def __init__(
        self,
        timer_id: int,
        interval: float,
        callback: Callable[[], None],
        repeat: bool = True,
    ) -> None:
        self.timer_id = timer_id
        self.interval = interval
        self.callback = callback
        self.repeat = repeat
        self.next_fire = _time.monotonic() + interval

    def __lt__(self, other: "Timer") -> bool:
        return self.next_fire < other.next_fire


class EventEngine:
    """Single-threaded event-driven engine.

    Features:
    - Thread-safe event queue (can put() from any thread)
    - Single-threaded dispatch loop (all handlers run sequentially)
    - Periodic timer scheduler (heap-based, O(log n) per tick)
    - Graceful start/stop

    Usage:
        engine = EventEngine()
        engine.register(EventType.BAR, my_bar_handler)
        engine.register_timer(1.0, my_periodic_task)
        engine.start()

        # From data thread:
        engine.put(bar_event)

        engine.stop()
    """

    def __init__(self, name: str = "EventEngine") -> None:
        self.name = name
        self._queue: Queue[Event] = Queue()
        self._bus = EventBus()
        self._running = False
        self._thread: threading.Thread | None = None
        self._timer_counter = 0
        self._timers: list[Timer] = []  # heap
        self._timer_lock = threading.Lock()

    # ---- Event Bus delegation ----

    def register(self, event_type: EventType, handler: Handler) -> None:
        """Register an event handler.

        Args:
            event_type: Type of event to handle.
            handler: Callable(Event) -> None.
        """
        self._bus.register(event_type, handler)

    def unregister(self, event_type: EventType, handler: Handler) -> None:
        """Remove a previously registered handler."""
        self._bus.unregister(event_type, handler)

    def put(self, event: Event) -> None:
        """Put an event into the queue (thread-safe).

        Can be called from any thread — typically from a data provider
        thread to push market data events into the engine.
        """
        self._queue.put(event)

    # ---- Timer management ----

    def register_timer(
        self,
        interval_seconds: float,
        callback: Callable[[], None],
        repeat: bool = True,
    ) -> int:
        """Register a periodic or one-shot timer.

        Args:
            interval_seconds: Seconds between firings (or until first fire for one-shot).
            callback: Called with no arguments when the timer fires.
            repeat: If True, re-schedule after each fire. If False, fire once.

        Returns:
            timer_id that can be used to cancel the timer.
        """
        with self._timer_lock:
            self._timer_counter += 1
            timer_id = self._timer_counter
            timer = Timer(
                timer_id=timer_id,
                interval=interval_seconds,
                callback=callback,
                repeat=repeat,
            )
            heapq.heappush(self._timers, timer)
            return timer_id

    def cancel_timer(self, timer_id: int) -> bool:
        """Cancel a registered timer by ID.

        Returns True if the timer was found and removed.
        """
        with self._timer_lock:
            for i, timer in enumerate(self._timers):
                if timer.timer_id == timer_id:
                    self._timers.pop(i)
                    heapq.heapify(self._timers)
                    return True
        return False

    # ---- Lifecycle ----

    def start(self) -> None:
        """Start the event engine in a background daemon thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name=self.name, daemon=True)
        self._thread.start()
        logger.info("EventEngine '%s' started", self.name)

        # Emit engine start event
        self.put(SystemEvent(
            type=EventType.ENGINE_START,
            timestamp=datetime.now(),
            message=f"Engine '{self.name}' started",
        ))

    def stop(self) -> None:
        """Gracefully stop the event engine."""
        if not self._running:
            return
        self._running = False

        # Emit engine stop event (synchronous — put directly, thread is stopping)
        self.put(SystemEvent(
            type=EventType.ENGINE_STOP,
            timestamp=datetime.now(),
            message=f"Engine '{self.name}' stopping",
        ))

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("EventEngine '%s' stopped")

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- Internal event loop ----

    def _run(self) -> None:
        """Main event loop. Runs in a dedicated thread."""
        while self._running:
            # Check timers
            self._fire_due_timers()

            # Poll for events with a short timeout (allows checking timers)
            try:
                event = self._queue.get(timeout=0.1)
            except Empty:
                continue

            # Dispatch
            self._bus.dispatch(event)

    def _fire_due_timers(self) -> None:
        """Fire any timers whose time has come."""
        now = _time.monotonic()
        with self._timer_lock:
            while self._timers and self._timers[0].next_fire <= now:
                timer = heapq.heappop(self._timers)

                # Fire the timer
                try:
                    timer.callback()
                except Exception:
                    logger.exception("Timer %d callback failed", timer.timer_id)

                # Re-schedule if repeating
                if timer.repeat:
                    timer.next_fire = now + timer.interval
                    heapq.heappush(self._timers, timer)

    # ---- Convenience: subscribe strategy to relevant events ----

    def register_strategy_handlers(
        self,
        on_bar: Handler | None = None,
        on_quote: Handler | None = None,
        on_order: Handler | None = None,
        on_trade: Handler | None = None,
    ) -> None:
        """Register a set of strategy callbacks at once.

        Args:
            on_bar: Handler for BarEvent.
            on_quote: Handler for QuoteEvent.
            on_order: Handler for OrderEvent (filled, rejected, cancelled).
            on_trade: Handler for TradeEvent.
        """
        if on_bar:
            self.register(EventType.BAR, on_bar)
        if on_quote:
            self.register(EventType.QUOTE, on_quote)
        if on_order:
            self.register(EventType.ORDER_FILLED, on_order)
            self.register(EventType.ORDER_REJECTED, on_order)
            self.register(EventType.ORDER_CANCELLED, on_order)
            self.register(EventType.ORDER_PARTIALLY_FILLED, on_order)
        if on_trade:
            self.register(EventType.TRADE, on_trade)
