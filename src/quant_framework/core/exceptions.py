"""Custom exception hierarchy for the quant framework.

All framework-specific exceptions inherit from QuantFrameworkError,
making it easy to catch all framework errors with a single except clause.
"""

from __future__ import annotations


class QuantFrameworkError(Exception):
    """Base exception for all framework errors."""


# --- Data Errors ---


class DataError(QuantFrameworkError):
    """Base for data-related errors."""


class DataProviderError(DataError):
    """Raised when a data provider encounters an error."""


class DataSubscriptionError(DataError):
    """Raised when subscribing to market data fails."""


class DataNotAvailableError(DataError):
    """Raised when requested data is not available."""


# --- Strategy Errors ---


class StrategyError(QuantFrameworkError):
    """Base for strategy-related errors."""


class StrategyInitError(StrategyError):
    """Raised when strategy initialization fails."""


class StrategyConfigError(StrategyError):
    """Raised when strategy configuration is invalid."""


# --- Execution Errors ---


class ExecutionError(QuantFrameworkError):
    """Base for order execution errors."""


class OrderRejectedError(ExecutionError):
    """Raised when an order is rejected by broker."""

    def __init__(self, order_id: str, reason: str) -> None:
        self.order_id = order_id
        self.reason = reason
        super().__init__(f"Order {order_id} rejected: {reason}")


class OrderCancelledError(ExecutionError):
    """Raised when order cancellation fails."""


class BrokerError(ExecutionError):
    """Raised when broker encounters an error."""


class BrokerConnectionError(BrokerError):
    """Raised when broker connection fails."""


# --- Risk Errors ---


class RiskError(QuantFrameworkError):
    """Base for risk management errors."""


class RiskBlockedError(RiskError):
    """Raised when an order is blocked by risk engine."""

    def __init__(self, order_id: str, rule_name: str, reason: str) -> None:
        self.order_id = order_id
        self.rule_name = rule_name
        self.reason = reason
        super().__init__(f"Order {order_id} blocked by {rule_name}: {reason}")


# --- Position Errors ---


class PositionError(QuantFrameworkError):
    """Base for position-related errors."""


class InsufficientPositionError(PositionError):
    """Raised when trying to sell more than available."""


class InsufficientFundsError(PositionError):
    """Raised when trying to buy with insufficient funds."""


# --- Engine Errors ---


class EngineError(QuantFrameworkError):
    """Base for engine errors."""


class EngineAlreadyRunningError(EngineError):
    """Raised when trying to start an already-running engine."""


class EngineNotRunningError(EngineError):
    """Raised when trying to use a stopped engine."""


# --- Configuration Errors ---


class ConfigError(QuantFrameworkError):
    """Raised when configuration is invalid."""


class ConfigFileNotFoundError(ConfigError):
    """Raised when a configuration file is missing."""
