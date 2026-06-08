"""Core enumerations and constants for the quant framework."""

from __future__ import annotations

from enum import Enum, auto


class OrderDirection(str, Enum):
    """Buy or sell direction."""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    """Order lifecycle status."""

    CREATED = "created"              # Created, pending risk check
    PENDING_SUBMIT = "pending"       # Risk check passed, queued for broker
    SUBMITTED = "submitted"          # Submitted to broker
    PARTIALLY_FILLED = "partial"     # Partially filled
    FILLED = "filled"                # Fully filled
    CANCELLED = "cancelled"          # Cancelled
    REJECTED = "rejected"            # Rejected (by risk or broker)
    EXPIRED = "expired"              # Expired


class OrderType(str, Enum):
    """Order type."""

    LIMIT = "limit"                   # Limit order
    MARKET = "market"                 # Market order
    LIMIT_UP = "limit_up"            # Buy at limit-up price
    LIMIT_DOWN = "limit_down"        # Sell at limit-down price
    LATEST = "latest"                # Counterparty's latest price


class TimeInForce(str, Enum):
    """Time-in-force for orders."""

    DAY = "day"                       # Valid for the day (A-share default)
    GTC = "gtc"                       # Good till cancelled
    IOC = "ioc"                       # Immediate or cancel
    FOK = "fok"                       # Fill or kill


class SignalDirection(str, Enum):
    """Signal direction for trading intent."""

    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"                   # Close existing position
    HOLD = "hold"                     # No action


class RiskDecision(str, Enum):
    """Risk engine decision for an order request."""

    ALLOW = "allow"
    BLOCK = "block"
    REDUCE = "reduce"                 # Allow with reduced position


class EngineMode(str, Enum):
    """Engine operating mode."""

    LIVE = "live"                     # Real trading
    PAPER = "paper"                   # Paper/simulated trading
    BACKTEST = "backtest"             # Historical backtest


class EventType(Enum):
    """Event types for the internal event bus."""

    # Market data
    BAR = auto()
    TICK = auto()
    QUOTE = auto()

    # Order lifecycle
    ORDER_SUBMITTED = auto()
    ORDER_FILLED = auto()
    ORDER_PARTIALLY_FILLED = auto()
    ORDER_CANCELLED = auto()
    ORDER_REJECTED = auto()

    # Trade
    TRADE = auto()

    # Risk
    RISK_BREACH = auto()

    # Timer
    TIMER = auto()

    # System
    SYSTEM = auto()
    ENGINE_START = auto()
    ENGINE_STOP = auto()

    # Portfolio
    PORTFOLIO_UPDATE = auto()
