"""Data models for market data — Quote, Bar, Kline.

All models use Pydantic v2 for serialization, validation, and type safety.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Quote(BaseModel):
    """Real-time quote snapshot for a single symbol.

    Maps to the raw dict returned by THS hq_api quote[code].
    Fields use Chinese-exchange conventions: bid=买盘, ask=卖盘.
    """

    symbol: str = Field(..., description="Security code, e.g. '600000'")
    timestamp: datetime = Field(default_factory=datetime.now, description="Quote timestamp")

    # Price fields
    open: float = Field(default=0.0, description="Open price today")
    high: float = Field(default=0.0, description="High price today")
    low: float = Field(default=0.0, description="Low price today")
    price: float = Field(default=0.0, description="Latest transaction price")
    pre_close: float = Field(default=0.0, description="Previous close")

    # Volume fields
    volume: float = Field(default=0.0, description="Cumulative volume (shares)")
    amount: float = Field(default=0.0, description="Cumulative turnover amount")

    # Bid/Ask depth (5 levels)
    bid_prices: list[float] = Field(default_factory=lambda: [0.0] * 5, description="Bid prices b1~b5")
    bid_volumes: list[int] = Field(default_factory=lambda: [0] * 5, description="Bid volumes b1~b5")
    ask_prices: list[float] = Field(default_factory=lambda: [0.0] * 5, description="Ask prices a1~a5")
    ask_volumes: list[int] = Field(default_factory=lambda: [0] * 5, description="Ask volumes a1~a5")

    # Limit prices
    limit_up: float = Field(default=0.0, description="Limit-up price (涨停价)")
    limit_down: float = Field(default=0.0, description="Limit-down price (跌停价)")

    # Derived
    change_pct: float = Field(default=0.0, description="Price change % from pre_close")

    @property
    def bid1_value(self) -> float:
        """Buy-1 order book value = price * volume."""
        return self.bid_prices[0] * self.bid_volumes[0] if self.bid_prices else 0.0

    @property
    def ask1_value(self) -> float:
        """Sell-1 order book value."""
        return self.ask_prices[0] * self.ask_volumes[0] if self.ask_prices else 0.0

    @property
    def is_limit_up(self) -> bool:
        """Whether the stock is at the limit-up price."""
        return abs(self.price - self.limit_up) < 1e-6

    @property
    def is_limit_down(self) -> bool:
        """Whether the stock is at the limit-down price."""
        return abs(self.price - self.limit_down) < 1e-6

    @property
    def spread(self) -> float:
        """Bid-ask spread in absolute price."""
        return self.ask_prices[0] - self.bid_prices[0] if self.ask_prices and self.bid_prices else 0.0

    @property
    def spread_pct(self) -> float:
        """Bid-ask spread as percentage of mid price."""
        mid = (self.ask_prices[0] + self.bid_prices[0]) / 2
        return self.spread / mid if mid > 0 and self.bid_prices[0] > 0 else 0.0


class Bar(BaseModel):
    """A single K-line bar (OHLCV).

    Represents one candlestick for any timeframe (1m, 5m, 15m, 60m, 1d, etc.).
    """

    symbol: str = Field(..., description="Security code")
    dt: datetime = Field(..., description="Bar datetime (open time)")
    open: float = Field(..., description="Open price")
    high: float = Field(..., description="High price")
    low: float = Field(..., description="Low price")
    close: float = Field(..., description="Close price")
    volume: float = Field(default=0.0, description="Volume in shares")
    amount: float = Field(default=0.0, description="Turnover amount")
    period: str = Field(default="1d", description="Bar timeframe: 1m,5m,15m,60m,1d etc.")

    @property
    def is_bullish(self) -> bool:
        """Bull candle: close >= open."""
        return self.close >= self.open

    @property
    def is_bearish(self) -> bool:
        """Bear candle: close < open."""
        return self.close < self.open

    @property
    def body(self) -> float:
        """Absolute body size."""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        """Upper shadow/wick length."""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """Lower shadow/wick length."""
        return min(self.open, self.close) - self.low

    @property
    def typical_price(self) -> float:
        """Typical price (H+L+C)/3."""
        return (self.high + self.low + self.close) / 3


class KlineData(BaseModel):
    """A series of K-line bars for a symbol.

    Wraps a list of Bar objects with metadata for efficient access.
    """

    symbol: str
    period: str
    bars: list[Bar] = Field(default_factory=list)

    def to_dataframe(self):
        """Convert bars to pandas DataFrame (lazy import pandas)."""
        import pandas as pd

        if not self.bars:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "amount"])

        data = [
            {
                "datetime": b.dt,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "amount": b.amount,
            }
            for b in self.bars
        ]
        df = pd.DataFrame(data)
        df.set_index("datetime", inplace=True)
        return df

    @property
    def closes(self) -> list[float]:
        """List of close prices."""
        return [b.close for b in self.bars]

    @property
    def highs(self) -> list[float]:
        """List of high prices."""
        return [b.high for b in self.bars]

    @property
    def lows(self) -> list[float]:
        """List of low prices."""
        return [b.low for b in self.bars]

    @property
    def volumes(self) -> list[float]:
        """List of volumes."""
        return [b.volume for b in self.bars]

    def __len__(self) -> int:
        return len(self.bars)

    def __getitem__(self, index: int) -> Bar:
        return self.bars[index]
