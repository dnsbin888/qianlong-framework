"""Internal event bus — decouples components via publish/subscribe.

Single-threaded, in-process event bus. Components register handlers
for specific event types, and events are dispatched synchronously
in the order they are received.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from quant_framework.core.constants import EventType
from quant_framework.core.events import Event


Handler = Callable[[Event], None]
"""Event handler signature: receives an Event, returns nothing."""


class EventBus:
    """Simple publish/subscribe event bus.

    Handlers are registered per EventType and called in FIFO order.
    All dispatch is synchronous — no threading inside the bus itself.
    Thread safety of put() is the caller's responsibility (e.g. via queue.Queue).

    Usage:
        bus = EventBus()
        bus.register(EventType.BAR, my_handler)
        bus.register(EventType.ORDER_FILLED, my_handler)
        bus.dispatch(event)
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)

    def register(self, event_type: EventType, handler: Handler) -> None:
        """Register a handler for a specific event type.

        The same handler can be registered multiple times — each call
        adds another entry. Call unregister() to remove all instances.

        Args:
            event_type: The event type to listen for.
            handler: Callable that receives an Event.
        """
        self._handlers[event_type].append(handler)

    def unregister(self, event_type: EventType, handler: Handler) -> None:
        """Remove all registrations of a handler for an event type.

        Args:
            event_type: The event type.
            handler: The handler to remove.
        """
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h is not handler
            ]

    def dispatch(self, event: Event) -> None:
        """Dispatch an event to all registered handlers.

        Handlers are called synchronously in registration order.
        Exceptions in handlers are caught and logged — they do not
        prevent other handlers from running.

        Args:
            event: The event to dispatch.
        """
        handlers = self._handlers.get(event.type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception as e:
                # Log but don't crash the event loop
                import logging
                logging.getLogger("quant_framework.event_bus").error(
                    "Handler %s failed for event %s: %s",
                    handler.__name__,
                    event.type.name,
                    e,
                    exc_info=True,
                )

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()

    def handler_count(self, event_type: EventType | None = None) -> int:
        """Return the number of registered handlers.

        Args:
            event_type: If provided, count only handlers for this type.
                        If None, count all handlers.
        """
        if event_type is not None:
            return len(self._handlers.get(event_type, []))
        return sum(len(hs) for hs in self._handlers.values())
