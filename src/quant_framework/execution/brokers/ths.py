"""同花顺 (THS) Broker Adapter.

Wraps the THS xd.cmd() trading interface behind the AbstractBroker interface.
Translates framework Order models to THS command strings.
"""

from __future__ import annotations

import logging
from typing import Any

from quant_framework.core.constants import OrderDirection
from quant_framework.core.types import Symbol
from quant_framework.execution.broker import AbstractBroker
from quant_framework.execution.order import (
    AccountInfo,
    Order,
    OrderRequest,
    Position,
    Trade,
)

logger = logging.getLogger("quant_framework.broker.ths")


class THSBroker(AbstractBroker):
    """同花顺交易接口适配器。

    Maps framework order semantics to THS xd.cmd() string commands:
        "buy 600000 zxjg 100 -notip"
        "sell 000001 15.67 500 -cw 1/2 -notip"
        "cancel 600000"

    价格关键词:
        zxjg (最新价) = market order
        ztjg (涨停价) = limit-up
        dtjg (跌停价) = limit-down
        dsj1~dsj5 (对手价1~5) = hit bid/ask
        具体数字 = limit price
    """

    def __init__(self) -> None:
        self._xd: Any = None  # xd module from ths_api
        self._orders: dict[str, Order] = {}
        self._connected: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        try:
            from ths_api import xd  # type: ignore[import-untyped]
            self._xd = xd
            self._connected = True
            logger.info("THS broker connected")
        except ImportError:
            raise ImportError(
                "ths_api not available. THSBroker requires the 同花顺 Python environment."
            )

    def disconnect(self) -> None:
        self._connected = False
        self._xd = None

    def is_connected(self) -> bool:
        return self._connected and self._xd is not None

    @property
    def name(self) -> str:
        return "ths"

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    def submit_order(self, request: OrderRequest) -> Order:
        """Submit an order via xd.cmd()."""
        if not self._xd:
            raise RuntimeError("THS broker not connected")

        # Build THS command string
        cmd = self._build_cmd(request)
        logger.info("THS cmd: %s", cmd)

        # Send via xd.cmd()
        try:
            self._xd.cmd(cmd)
        except Exception as e:
            raise RuntimeError(f"THS xd.cmd() failed: {e}") from e

        # Create order record
        order = Order(
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            direction=request.direction,
            price=request.price,
            requested_volume=request.volume or 0,
            metadata={"ths_cmd": cmd, **request.metadata},
        )
        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        if not self._xd:
            return False

        order = self._orders.get(order_id)
        if not order:
            return False

        cmd = f"cancel {order.symbol}"
        try:
            self._xd.cmd(cmd)
            logger.info("Cancel order sent: %s", order_id)
            return True
        except Exception as e:
            logger.error("Cancel failed: %s", e)
            return False

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        count = 0
        for order in self.get_open_orders(symbol):
            if self.cancel_order(order.order_id):
                count += 1
        return count

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(self, symbol: str | None = None, status: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        if status:
            orders = [o for o in orders if o.status.value == status]
        return orders

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = [o for o in self._orders.values() if o.is_active]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_positions(self) -> dict[Symbol, Position]:
        """Read positions from THS xd.g_position."""
        if not self._xd:
            return {}

        positions: dict[Symbol, Position] = {}
        try:
            for code, data in self._xd.g_position.items():
                positions[code] = Position(
                    symbol=code,
                    volume=int(data.get("gpye", 0)),
                    available=int(data.get("kyye", 0)),
                    avg_cost=float(data.get("cbj", 0)),
                )
        except Exception as e:
            logger.error("Failed to read positions: %s", e)

        return positions

    def get_position(self, symbol: str) -> Position | None:
        return self.get_positions().get(symbol)

    def get_account(self) -> AccountInfo:
        """Read account info from THS."""
        if not self._xd:
            return AccountInfo()

        try:
            return AccountInfo(
                total_equity=float(getattr(self._xd, "g_money", {}).get("zzc", 0)),
                cash=float(getattr(self._xd, "g_money", {}).get("kyje", 0)),
            )
        except Exception:
            return AccountInfo()

    def get_trades(self, symbol: str | None = None, limit: int = 100) -> list[Trade]:
        """THS does not natively provide trade history via this API."""
        return []  # Use TradeRecorder for persistence instead

    # ------------------------------------------------------------------
    # Command builder
    # ------------------------------------------------------------------

    def _build_cmd(self, req: OrderRequest) -> str:
        """Build a THS xd.cmd() command string from an OrderRequest.

        Format: "<buy|sell> <code> <price> [volume] [options]"

        Options:
            -notip: suppress confirmation dialog
            -cw <ratio>: 仓位比例, e.g. -cw 1/2 (half position)
            -zcw <ratio>: 增仓比例, e.g. -zcw 1/2
        """
        mmlb = "buy" if req.direction == OrderDirection.BUY else "sell"

        # Price
        if req.price is None:
            wtjg = "zxjg"  # market = 最新价
        else:
            wtjg = f"{req.price:.3f}"

        # Volume
        if req.volume:
            wtsl = str(req.volume)
        else:
            wtsl = ""

        parts = [mmlb, req.symbol, wtjg, wtsl, "-notip"]

        # Position percentage handling
        if req.position_pct is not None:
            parts.append(f"-cw {req.position_pct}")

        return " ".join(p for p in parts if p)
