"""Abstract Broker interface for order execution.

All trading gateways must implement this interface.
Submits orders, tracks positions, and reports account state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from quant_framework.core.types import Symbol
from quant_framework.execution.order import AccountInfo, Order, OrderRequest, Position, Trade


class AbstractBroker(ABC):
    """Abstract base class for brokerage/trading gateways.

    Concrete implementations:
    - THSBroker: 同花顺 xd.cmd() 交易接口
    - XTQuantBroker: QMT/MiniQMT 交易接口
    - SimulatedBroker: Paper trading with virtual fills
    """

    # ---- Order management ----

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> Order:
        """Submit an order to the broker.

        Args:
            request: Populated OrderRequest with concrete volume/price.

        Returns:
            An Order object with the assigned order_id and initial status.
        """

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order.

        Args:
            order_id: The order identifier to cancel.

        Returns:
            True if the cancellation request was accepted.
        """

    @abstractmethod
    def cancel_all_orders(self, symbol: str | None = None) -> int:
        """Cancel all open orders, optionally filtered by symbol.

        Args:
            symbol: If provided, cancel only orders for this symbol.

        Returns:
            Number of orders cancelled.
        """

    # ---- Order query ----

    @abstractmethod
    def get_order(self, order_id: str) -> Order | None:
        """Get an order by ID."""

    @abstractmethod
    def get_orders(
        self,
        symbol: str | None = None,
        status: str | None = None,
    ) -> list[Order]:
        """Query orders with optional filters."""

    @abstractmethod
    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        """Get all open (active) orders."""

    # ---- Position & Account ----

    @abstractmethod
    def get_positions(self) -> dict[Symbol, Position]:
        """Get all current positions."""

    @abstractmethod
    def get_position(self, symbol: str) -> Position | None:
        """Get position for a specific symbol, or None."""

    @abstractmethod
    def get_account(self) -> AccountInfo:
        """Get current account information."""

    # ---- Trade history ----

    @abstractmethod
    def get_trades(self, symbol: str | None = None, limit: int = 100) -> list[Trade]:
        """Get recent trade history."""

    # ---- Lifecycle ----

    @abstractmethod
    def connect(self) -> None:
        """Establish connection to the broker."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect and clean up."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if the broker connection is alive."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable broker name."""
