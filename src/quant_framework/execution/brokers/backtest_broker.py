"""BacktestBroker — 历史数据回测 (蓝图 v5.0 Phase 4.2)

对标: LEAN BacktestingBrokerageModel / vnpy BacktestingEngine
实现 AbstractBroker，让同一策略代码在回测/模拟/实盘三个环境通用。

关键:
  - 使用历史K线数据模拟成交
  - 支持 T+1 约束 (当日买入次日才能卖)
  - 支持涨跌停约束 (P3-04)
  - 支持滑点模拟
  - 佣金/印花税

用法:
    broker = BacktestBroker(initial_cash=1_000_000, data=ohlcv_dict)
    strategy.set_broker(broker)  # 同一策略，切换Broker即可
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from quant_framework.execution.broker import AbstractBroker
from quant_framework.execution.order import (
    AccountInfo, Order, OrderRequest, Position, Trade,
)
from quant_framework.core.types import Symbol
from quant_framework.core.constants import OrderDirection

logger = logging.getLogger("quant_framework.broker.backtest")


class BacktestBroker(AbstractBroker):
    """历史数据回测 Broker。

    从历史OHLCV数据中模拟订单成交。
    与 SimulatedBroker 接口完全一致，策略代码无需修改即可切换。

    Args:
        initial_cash: 初始资金
        commission_rate: 佣金率 (默认万三)
        stamp_tax_rate: 印花税率 (卖出0.1%)
        slippage_pct: 滑点 (默认0.1%)
        min_commission: 最低佣金 (默认5元)
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000.0,
        commission_rate: float = 0.0003,
        stamp_tax_rate: float = 0.001,
        slippage_pct: float = 0.001,
        min_commission: float = 5.0,
    ):
        self._initial_cash = initial_cash
        self._cash = initial_cash
        self._frozen_cash = 0.0
        self._positions: dict[Symbol, Position] = {}
        self._orders: dict[str, Order] = {}
        self._trades: list[Trade] = []
        self._connected = False

        self._commission_rate = commission_rate
        self._stamp_tax_rate = stamp_tax_rate
        self._slippage_pct = slippage_pct
        self._min_commission = min_commission

        self._commission_total = 0.0
        self._realized_pnl = 0.0
        self._current_date: str = ""  # 当前回测日期 (YYYY-MM-DD)
        self._current_prices: dict[str, float] = {}  # 当日收盘价缓存
        self._buy_dates: dict[str, str] = {}  # 买入日期追踪 (T+1)

    # ── 生命周期 ──

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "backtest"

    # ── 日期推进 ──

    def set_date(self, date_str: str):
        """推进回测日期 (YYYY-MM-DD)。每个交易日调用一次。"""
        self._current_date = date_str

    def set_prices(self, prices: dict[str, float], prev_closes: dict[str, float] | None = None):
        """设置当日收盘价缓存 + 前收盘价。{symbol: close_price}, {symbol: prev_close}"""
        self._current_prices = prices
        if prev_closes:
            self._prev_closes = prev_closes

    # ── 订单 ──

    def submit_order(self, request: OrderRequest) -> Order:
        from uuid import uuid4

        symbol = request.symbol
        direction = request.direction
        volume = request.volume or 0

        # 获取成交价
        fill_price = self._get_fill_price(symbol, direction)

        order = Order(
            order_id=f"bt_{uuid4().hex[:12]}",
            strategy_id=request.strategy_id,
            symbol=symbol,
            direction=direction,
            price=fill_price or request.price or 0,
            requested_volume=volume,
        )

        # 资金/持仓检查
        if direction == OrderDirection.BUY:
            estimated_cost = (fill_price or 1.0) * volume * 1.001
            if estimated_cost > self._cash:
                raise ValueError(f"资金不足: 需{estimated_cost:.0f} 有{self._cash:.0f}")
            # 涨停不买 (回测中涨停无法成交, 实盘应挂单排队 → QMTBroker处理)
            if self._is_limit_up(symbol):
                raise ValueError(f"涨停板不可买入(回测): {symbol}")

        if direction == OrderDirection.SELL:
            pos = self._positions.get(symbol)
            available = pos.available if pos else 0
            # T+1 检查
            buy_date = self._buy_dates.get(symbol, "")
            if buy_date == self._current_date:
                raise ValueError(f"T+1锁定: {symbol} 今日买入不可卖出")
            if not pos or available < volume:
                raise ValueError(f"持仓不足: 需{volume} 有{available}")

            # 跌停检查
            if self._is_limit_down(symbol):
                raise ValueError(f"跌停板不可卖出: {symbol}")

        # 模拟成交
        if fill_price and fill_price > 0:
            trade = self._create_trade(order, fill_price, volume)
            self._apply_fill(order, trade, symbol, direction)
            self._trades.append(trade)

        self._orders[order.order_id] = order
        return order

    def cancel_order(self, order_id: str) -> bool:
        order = self._orders.get(order_id)
        if not order or not order.is_active:
            return False
        order.status = "cancelled"
        return True

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        count = 0
        for o in self.get_open_orders(symbol):
            if self.cancel_order(o.order_id):
                count += 1
        return count

    # ── 查询 ──

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(self, symbol: str | None = None, status: str | None = None) -> list[Order]:
        orders = list(self._orders.values())
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        if status:
            orders = [o for o in orders if o.status == status]
        return orders

    def get_open_orders(self, symbol: str | None = None) -> list[Order]:
        orders = [o for o in self._orders.values() if o.is_active]
        if symbol:
            orders = [o for o in orders if o.symbol == symbol]
        return orders

    def get_positions(self) -> dict[Symbol, Position]:
        return self._positions

    def get_position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol)

    def get_account(self) -> AccountInfo:
        market_value = sum(
            p.volume * (p.current_price or p.avg_cost)
            for p in self._positions.values() if p.volume > 0
        )
        return AccountInfo(
            account_id="backtest",
            total_equity=self._cash + market_value,
            cash=self._cash,
            frozen_cash=self._frozen_cash,
            market_value=market_value,
            total_pnl=self._realized_pnl,
            commission=self._commission_total,
            updated_time=datetime.now(),
        )

    def get_trades(self, symbol: str | None = None, limit: int = 100) -> list[Trade]:
        trades = self._trades
        if symbol:
            trades = [t for t in trades if t.symbol == symbol]
        return trades[-limit:]

    # ── 内部 ──

    def _get_fill_price(self, symbol: str, direction) -> float:
        """从当前价格缓存获取成交价 (含滑点)。"""
        price = self._current_prices.get(symbol, 0)
        if not price:
            # fallback: 从 position 取
            pos = self._positions.get(symbol)
            price = pos.avg_cost if pos else 0
        if price <= 0:
            return 0

        # 滑点: 买入加, 卖出减
        if direction == OrderDirection.BUY:
            price *= (1 + self._slippage_pct)
        else:
            price *= (1 - self._slippage_pct)
        return round(price, 2)

    def _create_trade(self, order: Order, price: float, volume: int) -> Trade:
        from uuid import uuid4
        return Trade(
            trade_id=f"trd_{uuid4().hex[:8]}",
            order_id=order.order_id,
            symbol=order.symbol,
            direction=order.direction,
            price=price,
            volume=volume,
            commission=0,
            time=datetime.now(),
        )

    def _apply_fill(self, order: Order, trade: Trade, symbol: str, direction):
        """更新账户/持仓。"""
        volume = trade.volume
        price = trade.price
        commission = 0.0
        stamp_tax = 0.0

        if direction == OrderDirection.BUY:
            cost = price * volume
            commission = max(self._min_commission, cost * self._commission_rate)
            total_cost = cost + commission
            self._cash -= total_cost

            if symbol not in self._positions:
                self._positions[symbol] = Position(symbol=symbol)
            pos = self._positions[symbol]
            total_basis = pos.avg_cost * pos.volume + price * volume
            pos.volume += volume
            pos.available += volume
            pos.avg_cost = total_basis / pos.volume if pos.volume > 0 else 0
            pos.current_price = price

            self._buy_dates[symbol] = self._current_date

        else:  # SELL
            proceeds = price * volume
            commission = max(self._min_commission, proceeds * self._commission_rate)
            stamp_tax = proceeds * self._stamp_tax_rate
            self._cash += proceeds - commission - stamp_tax

            pos = self._positions[symbol]
            realized = (price - pos.avg_cost) * volume
            self._realized_pnl += realized
            pos.volume -= volume
            pos.available -= volume
            if pos.volume <= 0:
                pos.avg_cost = 0

        trade.commission = commission
        self._commission_total += commission

    def _is_limit_up(self, symbol: str) -> bool:
        """检查涨停 (回测中涨停无法成交, 跳过买入)。

        实盘应挂涨停价排队打板 (由 QMTBroker 处理)。
        回测无真实订单簿, 涨停视为不可买入。
        """
        price = self._current_prices.get(symbol, 0)
        prev = getattr(self, '_prev_closes', {}).get(symbol, 0)
        if price <= 0 or prev <= 0:
            return False
        chg = (price - prev) / prev
        return chg >= 0.095

    def _is_limit_down(self, symbol: str) -> bool:
        """检查跌停 (回测中跌停无法成交, 跳过卖出)。

        实盘应挂跌停价排队 (由 QMTBroker 处理)。
        回测无真实订单簿, 跌停视为不可卖出。
        """
        price = self._current_prices.get(symbol, 0)
        prev = getattr(self, '_prev_closes', {}).get(symbol, 0)
        if price <= 0 or prev <= 0:
            return False
        chg = (price - prev) / prev
        return chg <= -0.095

    @property
    def cash(self) -> float:
        return self._cash
