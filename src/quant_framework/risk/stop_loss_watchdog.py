"""StopLossWatchdog — 硬止损看门狗 (E245)
==========================================

独立于策略信号的风控模块，在 on_bar 中优先执行。

功能:
    1. 单笔止损: 持仓亏损 >= stop_loss_pct → 强制平仓
    2. 总回撤熔断: 当日回撤 >= max_drawdown_pct → 触发 CircuitBreaker

数据来源:
    - 持仓: trade_orders 表 FIFO 配对推算 (不依赖 QMTBroker.get_positions)
    - 当前价格: on_bar 的 bar_data["close"]

红线:
    - 只读 trade_orders，不写入
    - 平仓使用 broker.place_order(symbol, "sell", ...)
    - 不产生策略信号
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("quant_framework.risk.stop_loss")

class StopLossWatchdog:
    """硬止损看门狗。

    Args:
        db_service: DBService 实例 (get_db_service())
        stop_loss_pct: 单笔止损阈值 (负数，如 -0.08 = -8%)
        max_drawdown_pct: 总回撤熔断阈值 (负数，如 -0.05 = -5%)
    """

    def __init__(
        self,
        db_service: Any,
        stop_loss_pct: float = -0.08,
        max_drawdown_pct: float = -0.05,
    ) -> None:
        self._db = db_service
        self._stop_loss_pct: float = stop_loss_pct
        self._max_drawdown_pct: float = max_drawdown_pct

    # ═══════════════════════════════════════════════════════
    #  单笔止损
    # ═══════════════════════════════════════════════════════

    def check_position(
        self, symbol: str, current_price: float
    ) -> dict[str, Any] | None:
        """检查单个 symbol 是否需要止损。

        Args:
            symbol: 股票代码
            current_price: 当前价格

        Returns:
            None = 不需要止损
            dict = 止损指令 {"symbol", "action", "price", "volume", "reason"}
        """
        pos = self._calc_position(symbol)
        if pos is None:
            return None

        volume, avg_cost = pos
        if volume <= 0 or avg_cost <= 0:
            return None

        loss_ratio: float = (current_price - avg_cost) / avg_cost

        if loss_ratio <= self._stop_loss_pct:
            logger.warning(
                f"[STOP] {symbol} 触发单笔止损: "
                f"亏损 {loss_ratio:.2%} <= {self._stop_loss_pct:.2%}, "
                f"持仓 {volume}股, 成本 {avg_cost:.2f}, 现价 {current_price:.2f}"
            )
            return {
                "symbol": symbol,
                "action": "sell",
                "price": current_price,
                "volume": volume,
                "reason": f"单笔亏损 {loss_ratio:.2%} <= {self._stop_loss_pct:.2%}",
            }

        return None

    # ═══════════════════════════════════════════════════════
    #  持仓推算 (FIFO 配对, 参考 E243)
    # ═══════════════════════════════════════════════════════

    def _calc_position(self, symbol: str) -> tuple[int, float] | None:
        """从 trade_orders 推算持仓 (FIFO 配对, E250 P0-1).

        Returns:
            None = 无持仓
            (volume, avg_cost) = (持仓数量, 平均成本价)
        """
        # E250 P0-1: limit 提升到 100000, 防止截断
        try:
            orders = self._db.get_trades(symbol=symbol, limit=100000)
        except Exception as e:
            logger.error(f"[STOP] 查询 trade_orders 失败 ({symbol}): {e}")
            return None

        if not orders:
            return None

        # 按 created_at 升序排列 (买入在前, FIFO 正确)
        try:
            orders.sort(key=lambda o: o.get("created_at", ""))
        except Exception:
            pass

        # FIFO 配对: 只统计 filled/partial 或旧数据无 fill_status 字段的订单
        holding_qty: int = 0
        cost_sum: float = 0.0
        buy_queue: list[tuple[int, float]] = []  # [(qty, price), ...]

        for o in orders:
            side = o.get("direction", "").lower()
            qty = int(float(o.get("volume", 0)))
            price = float(o.get("price", 0))

            if qty <= 0 or price <= 0:
                continue

            # E250 P0-1: 只统计 filled/partial 的订单, 跳过 submitted/rejected
            fill_status = o.get("fill_status", "filled")
            if fill_status not in ("filled", "partial"):
                continue

            if side == "buy":
                buy_queue.append((qty, price))
                holding_qty += qty
                cost_sum += qty * price
            elif side == "sell":
                remaining = qty
                while remaining > 0 and buy_queue:
                    buy_qty, buy_price = buy_queue[0]
                    matched = min(remaining, buy_qty)
                    buy_queue[0] = (buy_qty - matched, buy_price)
                    if buy_queue[0][0] == 0:
                        buy_queue.pop(0)
                    holding_qty -= matched
                    cost_sum -= matched * buy_price
                    remaining -= matched

        if holding_qty <= 0:
            return None

        avg_cost: float = cost_sum / holding_qty
        return (holding_qty, avg_cost)

    # ═══════════════════════════════════════════════════════
    #  总回撤熔断
    # ═══════════════════════════════════════════════════════

    def check_drawdown(self, start_asset: float, current_asset: float) -> bool:
        """检查总回撤是否触发熔断。

        Args:
            start_asset: 当日开盘总资产
            current_asset: 当前总资产

        Returns:
            True = 回撤超限，应触发熔断, False = 正常
        """
        if start_asset <= 0:
            return False

        drawdown: float = (current_asset - start_asset) / start_asset

        if drawdown <= self._max_drawdown_pct:
            logger.warning(
                f"[STOP] 总回撤 {drawdown:.2%} <= {self._max_drawdown_pct:.2%}，触发熔断"
            )
            return True

        return False
