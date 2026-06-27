"""THSBroker — 同花顺联动精灵下单适配器 (蓝图v3.0 R1-2)

将 link_trader.dll 封装为 AbstractBroker 接口，使 RuleEngine
可统一驱动 QMT / THS / Paper 三通道。
"""

from __future__ import annotations

import logging
import sys, os
from typing import Any

sys.path.insert(0, r"D:\quant_framework")

from quant_framework.execution.broker import AbstractBroker
from quant_framework.execution.order import (
    AccountInfo,
    Order,
    OrderRequest,
    Position,
    Trade,
)
from quant_framework.core.types import Symbol

logger = logging.getLogger("quant_framework.execution.brokers.ths")


def _to_clean_code(symbol: str) -> str:
    """QMT代码→THS代码: 600000.SH→600000"""
    return str(symbol).replace(".SH", "").replace(".SZ", "").replace(".BJ", "").lower()


class THSBroker(AbstractBroker):
    """同花顺联动精灵 Broker — 通过 Win32 DLL 驱动 THS 委托端下单。

    安全约束:
        - 仅在下单时短暂持有互斥锁，查询持仓时不持锁
        - 连接检查 = link_trader.is_available()
        - 不自动启动同花顺（遵循E30原则）
    """

    def __init__(self):
        self._connected = False
        self._check_availability()

    def _check_availability(self) -> bool:
        try:
            from link_trader import is_available as _ths_ok
            self._connected = _ths_ok()
        except ImportError:
            self._connected = False
        return self._connected

    # ── 生命周期 ──

    def connect(self) -> None:
        self._check_availability()
        if self._connected:
            logger.info("[THSBroker] 已连接 同花顺联动精灵")
        else:
            logger.warning("[THSBroker] 联动精灵不可用，请确认已手动启动")

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "THSBroker"

    # ── 下单 ──

    def submit_order(self, request: OrderRequest) -> Order:
        """通过联动精灵 DLL 下单。"""
        if not self._connected:
            return Order(order_id="", symbol=request.symbol, status="rejected",
                         filled_volume=0, avg_fill_price=0.0,
                         message="THS未连接")

        clean = _to_clean_code(request.symbol)
        try:
            from link_trader import buy as _ths_buy, sell as _ths_sell

            if request.direction == "buy":
                amount = max(10000, int(request.volume * request.price)) if request.price > 0 else 10000
                result = _ths_buy(clean, request.price, amount)
            else:
                qty = max(1, request.volume // 100) if request.volume > 0 else 1
                result = _ths_sell(clean, request.price, qty)

            if result.get("success"):
                return Order(
                    order_id=str(result.get("seq", "")),
                    symbol=request.symbol,
                    status="submitted",
                    filled_volume=0,
                    avg_fill_price=request.price,
                )
            else:
                return Order(
                    order_id="", symbol=request.symbol, status="rejected",
                    filled_volume=0, avg_fill_price=0.0,
                    message=result.get("error", "下单失败"),
                )
        except Exception as e:
            logger.error(f"[THSBroker] 下单异常: {e}")
            return Order(order_id="", symbol=request.symbol, status="rejected",
                         filled_volume=0, avg_fill_price=0.0, message=str(e))

    def cancel_order(self, order_id: str) -> bool:
        if not self._connected:
            return False
        try:
            from link_trader import cancel as _ths_cancel
            result = _ths_cancel(order_id)
            return result.get("success", False)
        except Exception:
            return False

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        # 联动精灵不支持批量撤单，暂返回0
        return 0

    # ── 订单查询 ──

    def get_order(self, order_id: str) -> Order | None:
        return None  # 联动精灵无单笔查询

    def get_orders(self, symbol: str | None = None, status: str | None = None) -> list[Order]:
        return []  # 联动精灵无委托查询API

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        return []

    # ── 持仓 + 资产 ──

    def get_positions(self) -> dict[Symbol, Position]:
        """从 THS 读取持仓（兜底: 跟踪文件）。"""
        result: dict[Symbol, Position] = {}
        try:
            from live_trader import read_ths_positions, state
            read_ths_positions()  # 刷新
            for p in state.positions:
                sym = str(p.get("symbol", ""))
                if not sym:
                    continue
                result[Symbol(sym)] = Position(
                    symbol=Symbol(sym),
                    volume=int(p.get("qty", p.get("quantity", 0))),
                    avg_price=float(p.get("cost_price", p.get("avg_cost", 0))),
                    market_value=float(p.get("market_value", 0)),
                )
        except Exception as e:
            logger.warning(f"[THSBroker] 读取持仓失败: {e}")
        return result

    def get_position(self, symbol: str) -> Position | None:
        positions = self.get_positions()
        return positions.get(Symbol(symbol))

    def get_account(self) -> AccountInfo:
        """从 CONFIG 读取总资产/现金。"""
        try:
            from live_trader import CONFIG
            total = float(CONFIG.get("live_total_asset", 0) or 0)
            cash = float(CONFIG.get("live_cash", 0) or 0)
            return AccountInfo(
                total_assets=total,
                available_cash=cash,
                market_value=total - cash,
                currency="CNY",
            )
        except Exception:
            return AccountInfo(total_assets=0, available_cash=0, market_value=0, currency="CNY")

    # ── 成交 ──

    def get_trades(self, symbol: str | None = None, limit: int = 100) -> list[Trade]:
        return []  # 联动精灵无成交查询API
