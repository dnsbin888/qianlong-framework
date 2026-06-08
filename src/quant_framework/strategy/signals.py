"""Signal model — represents a strategy's trading intent.

Signals are the output of strategy analysis. They pass through the
RiskEngine → PositionSizer → Broker pipeline before becoming Orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant_framework.core.constants import SignalDirection
from quant_framework.core.types import JsonDict


@dataclass
class Signal:
    """Trading signal produced by a strategy.

    Signals express INTENT, not execution details.
    The PositionSizer determines the actual order size.
    The RiskEngine can block or modify signals.

    Attributes:
        strategy_id: Which strategy produced this signal.
        symbol: Target security code.
        direction: BUY, SELL, CLOSE, or HOLD.
        price: Suggested limit price (None = market order).
        reason: Human-readable trigger reason for logging.
        confidence: Signal confidence score 0.0-1.0.
        timestamp: When the signal was generated.
        metadata: Strategy-specific extra data.
    """

    strategy_id: str
    symbol: str
    direction: SignalDirection
    price: float | None = None
    reason: str = ""
    confidence: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: JsonDict = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"Signal({self.direction.value} {self.symbol} "
            f"@{self.price or 'MKT'} conf={self.confidence:.0%} "
            f"reason='{self.reason}')"
        )
