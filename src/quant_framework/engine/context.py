"""Strategy Context — runtime environment provided to each strategy.

The StrategyContext is the bridge between a strategy and the framework.
It provides access to market data, order execution, portfolio state,
logging, and configuration — all through a single, typed interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from quant_framework.core.events import Event
from quant_framework.core.types import JsonDict, Symbol


@dataclass
class PortfolioSnapshot:
    """Snapshot of current portfolio/account state."""

    total_equity: float = 0.0          # Total equity (cash + market value)
    cash: float = 0.0                  # Available cash
    frozen_cash: float = 0.0           # Frozen cash (pending orders)
    market_value: float = 0.0          # Total market value of holdings
    total_pnl: float = 0.0             # Total P&L (realized + unrealized)
    daily_pnl: float = 0.0             # Today's P&L
    positions: dict[Symbol, "PositionInfo"] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class PositionInfo:
    """Position information for a single symbol."""

    symbol: str
    volume: int = 0                    # Total holding volume
    available: int = 0                 # Available (sellable) volume
    avg_cost: float = 0.0              # Average cost price
    current_price: float = 0.0         # Current market price
    market_value: float = 0.0          # Current market value
    unrealized_pnl: float = 0.0        # Floating P&L
    unrealized_pnl_pct: float = 0.0    # Floating P&L percentage


@dataclass
class StrategyContext:
    """Runtime context for a strategy instance.

    Provides all framework capabilities to the strategy:
    - Access to market data (via data_provider)
    - Order submission (via broker)
    - Portfolio query (via portfolio)
    - Structured logging (via logger)
    - Strategy configuration (via config)
    - Event emission (via put_event)

    Each strategy instance has its own isolated context.
    """

    strategy_id: str
    name: str = ""

    # Framework services — set by engine during strategy registration
    data_provider: Any = None          # DataProvider instance
    broker: Any = None                 # AbstractBroker instance
    logger: Any = None                 # StrategyLogger instance
    config: JsonDict = field(default_factory=dict)

    # Portfolio state — updated by engine
    portfolio: PortfolioSnapshot = field(default_factory=PortfolioSnapshot)

    # Infrastructure
    clock: Callable[[], datetime] = field(default_factory=datetime.now)
    put_event: Callable[[Event], None] = lambda e: None

    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    extra: JsonDict = field(default_factory=dict)

    # ---- Convenience methods ----

    def get_position(self, symbol: str) -> PositionInfo | None:
        """Get position info for a symbol, or None if no position."""
        return self.portfolio.positions.get(symbol)

    @property
    def available_cash(self) -> float:
        """Quick access to available cash."""
        return self.portfolio.cash

    @property
    def total_equity(self) -> float:
        """Quick access to total equity."""
        return self.portfolio.total_equity
