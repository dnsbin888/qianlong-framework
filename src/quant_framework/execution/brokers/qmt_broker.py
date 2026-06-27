"""QMTBroker — QMT 下单适配器 (E241/E244)
===========================================

将 LiveStrategyRunner 的买卖信号转化为 QMT xttrader 的下单请求。
支持 SIM（仿真）和 REAL（实盘）两种环境。REAL 模式需通过 real_confirmed=True 确认。

安全约束:
    - 环境检查: __init__ 中 env 必须为 "SIM" 或 "REAL"，非法值 raise RuntimeError
    - REAL 确认: REAL 模式需 real_confirmed=True，否则 raise RuntimeError
    - 降级安全: connect() 失败时 is_connected()=False, place_order 返回 None
"""

from __future__ import annotations

import logging
from typing import Any

from quant_framework.execution.broker import AbstractBroker
from quant_framework.execution.order import (
    AccountInfo,
    Order,
    OrderRequest,
    Position,
    Trade,
)

logger = logging.getLogger("quant_framework.execution.brokers.qmt")


class QMTBroker(AbstractBroker):
    """QMT 下单适配器。

    Args:
        account_id: QMT 账户 ID
        env: 环境标识，"SIM" 或 "REAL"
        real_confirmed: REAL 模式确认标志，仅 env="REAL" 时需要传 True
        path: QMT 客户端 userdata 路径
        session_id: QMT 会话 ID
    """

    def __init__(
        self,
        account_id: str,
        env: str = "SIM",
        path: str = r"D:\国金QMT交易端模拟\userdata_mini",
        session_id: int = 9999,
        real_confirmed: bool = False,  # E244: REAL 模式确认标志
    ) -> None:
        # 2026-06-23: 默认路径切换至模拟版QMT (国金QMT交易端模拟)
        # 正式版路径: D:\QMT交易端\userdata_mini (需联系95310开通白名单)
        # 🛡️ P0 红线：环境安全阀 (E244 升级: SIM/REAL/非法值 三路分支)
        env_upper: str = env.upper()
        if env_upper == "SIM":
            pass  # 仿真模式，直接通过
        elif env_upper == "REAL":
            if not real_confirmed:
                raise RuntimeError(
                    "[E244 安全阀] REAL 模式未经交互确认 (real_confirmed=False)。"
                    "禁止初始化实盘下单通道。"
                )
            logger.warning("[QMTBroker] 实盘模式已激活，将使用真实资金下单")
        else:
            raise RuntimeError(
                f"[E244 安全阀] qmt_env='{env}' 非法。"
                f"合法值: 'SIM' 或 'REAL'。"
            )

        self._account_id: str = account_id
        self._env: str = env_upper
        self._path: str = path
        self._session_id: int = session_id
        self._trader: Any = None
        self._account: Any = None
        self._connected: bool = False

        # xtquant 延迟导入
        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant.xttype import StockAccount
            from xtquant import xtconstant

            self._XtQuantTrader = XtQuantTrader
            self._StockAccount = StockAccount
            self._xtconstant = xtconstant
            self._xt_available: bool = True
        except ImportError:
            logger.warning("[QMTBroker] xtquant 未安装，下单通道不可用")
            self._xt_available: bool = False

        logger.info(
            f"[QMTBroker] 初始化完成 | account={account_id} env={env} "
            f"xtquant={'已安装' if self._xt_available else '未安装'}"
        )

    # ── 生命周期 ────────────────────────────────────────

    def connect(self) -> None:
        """连接 QMT 客户端。失败时设 _connected=False，不抛异常。"""
        if not self._xt_available:
            logger.warning("[QMTBroker] xtquant 不可用，跳过连接")
            return

        try:
            self._trader = self._XtQuantTrader(self._path, self._session_id)
            self._trader.start()
            self._account = self._StockAccount(self._account_id)
            self._connected = True
            logger.info(f"[QMTBroker] 已连接 QMT | account={self._account_id}")
        except Exception as e:
            logger.warning(f"[QMTBroker] 连接 QMT 失败，降级为空转: {e}")
            self._connected = False

    def disconnect(self) -> None:
        """断开 QMT 连接。"""
        if self._trader:
            try:
                self._trader.stop()
            except Exception:
                pass
        self._connected = False
        logger.info("[QMTBroker] 已断开")

    def is_connected(self) -> bool:
        return self._connected

    # ── 账户查询 ────────────────────────────────────────

    def get_account_value(self) -> float | None:
        """查询账户总资产（只读，不触碰下单通道）。

        Returns:
            账户总市值 (float)，或 None（未连接/查询失败）
        """
        if not self._connected or not self._xt_available:
            return None
        try:
            asset = self._trader.query_stock_asset(self._account)
            if asset:
                return float(asset.total_asset)
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询账户资产失败: {e}")
        return None

    def get_available_cash(self) -> float | None:
        """查询可用资金 (E250 P2-4: 下单前资金检查).

        Returns:
            可用资金 (float)，或 None（未连接/查询失败）
        """
        if not self._connected or not self._xt_available:
            return None
        try:
            asset = self._trader.query_stock_asset(self._account)
            if asset:
                return float(getattr(asset, "cash", 0) or getattr(asset, "available", 0) or 0)
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询可用资金失败: {e}")
        return None

    def query_order_status(self, order_id: str) -> str:
        """查询订单成交状态 (E250 P0-3).

        Args:
            order_id: QMT 委托序号 (seq)

        Returns:
            "filled" / "pending" / "rejected" / "unknown"
        """
        if not self._connected or not self._xt_available:
            return "unknown"
        try:
            result = self._trader.query_stock_orders(self._account)
            if result:
                for order in result:
                    if str(order.get("seq", "")) == str(order_id):
                        status = order.get("order_status", 0)
                        # QMT 状态码: 56/57/60 = 已成交
                        if status in (56, 57, 60):
                            return "filled"
                        elif status in (50, 52, 54):
                            return "rejected"
                        else:
                            return "pending"
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询订单状态失败: {e}")
        return "unknown"

    def cancel_order(self, order_id: str) -> bool:
        """撤单 (E250 P0-3 完善: 止损单未成交时撤单重挂).

        Args:
            order_id: QMT 委托序号 (seq)

        Returns:
            True = 撤单成功, False = 失败
        """
        if not self._connected or not self._xt_available:
            logger.warning("[QMTBroker] 未连接，无法撤单")
            return False
        try:
            # QMT 撤单 API: cancel_order(account, seq)
            result = self._trader.cancel_order(self._account, int(order_id))
            if result:
                logger.info(f"[QMT] 撤单成功: order_id={order_id}")
                return True
            else:
                logger.warning(f"[QMT] 撤单失败: order_id={order_id} result={result}")
                return False
        except Exception as e:
            logger.error(f"[QMTBroker] 撤单异常: order_id={order_id} error={e}")
            return False

    # ── 便捷下单 ────────────────────────────────────────

    def place_order(
        self, symbol: str, direction: str, price: float, volume: int
    ) -> str | None:
        """便捷下单方法 — 供 LiveStrategyRunner.on_bar 调用。

        Args:
            symbol: 股票代码 (如 "600000")
            direction: "buy" 或 "sell"
            price: 下单价格
            volume: 股数（必须 100 的倍数）

        Returns:
            QMT 委托序号 (str) 或 None（失败/未连接）
        """
        if not self._connected or not self._xt_available:
            logger.warning("[QMTBroker] 未连接，跳过下单")
            return None

        qmt_symbol: str = self._format_symbol(symbol)

        if direction == "buy":
            order_type = self._xtconstant.STOCK_BUY
        elif direction == "sell":
            order_type = self._xtconstant.STOCK_SELL
        else:
            logger.error(f"[QMTBroker] 未知方向: {direction}")
            return None

        try:
            seq: int = self._trader.order_stock(
                self._account,
                qmt_symbol,
                order_type,
                volume,
                self._xtconstant.FIX_PRICE,
                price,
                "ma_cross",
                f"E241_{direction}_{symbol}",
            )
            logger.info(
                f"[QMT-SIM] 委托已提交: {qmt_symbol} {direction} "
                f"{volume}股 @ {price} | seq={seq}"
            )
            return str(seq)
        except Exception as e:
            logger.error(f"[QMTBroker] 下单失败: {e}")
            return None

    @staticmethod
    def _format_symbol(symbol: str) -> str:
        """将股票代码转为 QMT 格式 (E250 P1-6: 去重后缀 + 未知前缀抛异常)."""
        s: str = symbol.strip().upper()

        # 1. 如果已有后缀，先去除
        for suffix in (".SH", ".SZ", ".BJ", ".SS"):
            if s.endswith(suffix):
                s = s[:-len(suffix)]
                break

        # 2. 非纯数字，原样返回
        if not s.isdigit():
            return s

        # 3. 沪市
        if s.startswith(("6", "5", "9", "7", "11", "13")):
            return f"{s}.SH"
        # 4. 深市
        elif s.startswith(("0", "3", "2", "15", "16", "18")):
            return f"{s}.SZ"
        # 5. 北交所
        elif s.startswith(("8", "4")):
            return f"{s}.BJ"
        # 6. 未知前缀 → 抛异常，不默认 .SH
        else:
            raise ValueError(f"未知股票代码前缀: {symbol} (处理后: {s})")

    # ── AbstractBroker 接口 ──

    def submit_order(self, request: OrderRequest) -> Order:
        from quant_framework.core.constants import OrderStatus

        direction_str: str = "buy" if request.direction.value == "BUY" else "sell"
        seq: str | None = self.place_order(
            request.symbol, direction_str, request.price or 0.0, request.volume or 0
        )

        return Order(
            order_id=seq or "qmt_failed",
            strategy_id=request.strategy_id,
            symbol=request.symbol,
            direction=request.direction,
            price=request.price or 0.0,
            requested_volume=request.volume or 0,
            status=OrderStatus.SUBMITTED if seq else OrderStatus.REJECTED,
        )

    def cancel_all_orders(self, symbol: str | None = None) -> int:
        """F1-修复: 批量撤单 — 查询所有委托后逐个撤销。"""
        if not self._connected or not self._xt_available:
            return 0
        cancelled = 0
        try:
            orders = self._trader.query_stock_orders(self._account)
            if orders:
                for o in orders:
                    sym = str(o.get("stock_code", ""))
                    if symbol and sym != symbol:
                        continue
                    seq = int(o.get("seq", 0))
                    if seq and self._trader.cancel_order(self._account, seq):
                        cancelled += 1
                        logger.info(f"[QMT] 撤单: {sym} seq={seq}")
            logger.info(f"[QMT] 批量撤单完成: {cancelled}笔")
        except Exception as e:
            logger.error(f"[QMTBroker] 批量撤单失败: {e}")
        return cancelled

    def get_order(self, order_id: str) -> Order | None:
        """F1-修复: 查询单笔订单 — 从委托列表中匹配。"""
        if not self._connected or not self._xt_available:
            return None
        try:
            orders = self._trader.query_stock_orders(self._account)
            if orders:
                for o in orders:
                    if str(o.get("seq", "")) == str(order_id):
                        return Order(
                            order_id=str(order_id),
                            symbol=str(o.get("stock_code", "")),
                            direction=OrderDirection.BUY if o.get("order_type", 0) in (
                                self._xtconstant.STOCK_BUY,
                            ) else OrderDirection.SELL,
                            price=float(o.get("price", 0)),
                            requested_volume=int(o.get("volume", 0)),
                            filled_volume=int(o.get("traded_volume", 0)),
                            status=self._map_order_status(int(o.get("order_status", 0))),
                        )
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询订单失败: order_id={order_id}, {e}")
        return None

    def get_orders(self, symbol=None, status=None) -> list[Order]:
        """F1-修复: 查询所有委托 — 从xttrader获取并转换。"""
        if not self._connected or not self._xt_available:
            return []
        results = []
        try:
            orders = self._trader.query_stock_orders(self._account)
            if orders:
                for o in orders:
                    sym = str(o.get("stock_code", ""))
                    if symbol and sym != symbol:
                        continue
                    odr = Order(
                        order_id=str(o.get("seq", "")),
                        symbol=sym,
                        direction=OrderDirection.BUY if o.get("order_type", 0) in (
                            self._xtconstant.STOCK_BUY,
                        ) else OrderDirection.SELL,
                        price=float(o.get("price", 0)),
                        requested_volume=int(o.get("volume", 0)),
                        filled_volume=int(o.get("traded_volume", 0)),
                        status=self._map_order_status(int(o.get("order_status", 0))),
                        avg_fill_price=float(o.get("traded_price", 0) or o.get("price", 0)),
                    )
                    results.append(odr)
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询委托列表失败: {e}")
        return results

    def get_open_orders(self, symbol=None) -> list[Order]:
        """F1-修复: 查询未完成委托。"""
        all_orders = self.get_orders(symbol=symbol)
        return [o for o in all_orders if o.is_active]

    def get_positions(self) -> dict:
        """F1-修复: 查询真实持仓 — 调用xttrader.query_stock_position。"""
        if not self._connected or not self._xt_available:
            return {}
        positions = {}
        try:
            result = self._trader.query_stock_position(self._account)
            if result:
                for p in result:
                    sym = str(p.get("stock_code", "")).split(".")[0]  # 去后缀
                    positions[sym] = Position(
                        symbol=sym,
                        volume=int(p.get("volume", 0)),
                        available=int(p.get("can_use_volume", p.get("available", 0))),
                        frozen=int(p.get("frozen_volume", 0)),
                        avg_cost=float(p.get("avg_cost", p.get("open_price", 0))),
                        current_price=float(p.get("current_price", p.get("last_price", 0))),
                        market_value=float(p.get("market_value", 0)),
                        unrealized_pnl=float(p.get("profit", p.get("income", 0))),
                    )
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询持仓失败: {e}")
        return positions

    def get_position(self, symbol: str) -> Position | None:
        """F1-修复: 查询单只持仓。"""
        positions = self.get_positions()
        # 尝试多种代码格式匹配
        clean = symbol.strip().upper().replace(".SH","").replace(".SZ","").replace(".BJ","")
        for sym, pos in positions.items():
            if sym == symbol or sym == clean or sym.startswith(clean) or clean.startswith(sym):
                return pos
        return None

    def get_account(self) -> AccountInfo:
        """F1-修复: 查询完整账户信息 — 资产+持仓汇总。"""
        if not self._connected or not self._xt_available:
            return AccountInfo(account_id=self._account_id)
        try:
            asset = self._trader.query_stock_asset(self._account)
            if asset:
                total_equity = float(asset.total_asset)
                cash = float(getattr(asset, "cash", 0) or getattr(asset, "available", 0) or 0)
            else:
                total_equity, cash = 0.0, 0.0
        except Exception:
            total_equity, cash = 0.0, 0.0

        # 汇总持仓市值
        positions = self.get_positions()
        market_value = sum(p.market_value for p in positions.values())
        daily_pnl = sum(p.unrealized_pnl for p in positions.values())

        return AccountInfo(
            account_id=self._account_id,
            total_equity=total_equity or (cash + market_value),
            cash=cash,
            market_value=market_value,
            daily_pnl=daily_pnl,
        )

    def get_trades(self, symbol=None, limit=100) -> list[Trade]:
        """F1-修复: 查询成交记录 — 调用xttrader.query_stock_trades。"""
        if not self._connected or not self._xt_available:
            return []
        results = []
        try:
            trades = self._trader.query_stock_trades(self._account)
            if trades:
                for t in trades[:limit]:
                    sym = str(t.get("stock_code", ""))
                    if symbol and sym != symbol:
                        continue
                    results.append(Trade(
                        trade_id=str(t.get("seq", "")),
                        order_id=str(t.get("order_seq", t.get("seq", ""))),
                        symbol=sym,
                        direction=OrderDirection.BUY if t.get("order_type", 0) in (
                            self._xtconstant.STOCK_BUY,
                        ) else OrderDirection.SELL,
                        price=float(t.get("price", 0)),
                        volume=int(t.get("volume", t.get("traded_volume", 0))),
                        commission=float(t.get("commission", 0)),
                    ))
        except Exception as e:
            logger.warning(f"[QMTBroker] 查询成交记录失败: {e}")
        return results

    @staticmethod
    def _map_order_status(qmt_status: int) -> OrderStatus:
        """F1-修复: 将QMT状态码映射为OrderStatus。"""
        from quant_framework.core.constants import OrderStatus
        # QMT状态码参考: 48=未报, 50=已报, 52=部成, 54=全成, 56=部撤, 57=全撤, 60=废单
        if qmt_status in (48, 50):
            return OrderStatus.SUBMITTED
        elif qmt_status in (52,):
            return OrderStatus.PARTIALLY_FILLED
        elif qmt_status in (54, 56, 57, 60):
            return OrderStatus.FILLED if qmt_status == 54 else OrderStatus.CANCELLED
        return OrderStatus.SUBMITTED

    @property
    def name(self) -> str:
        return f"QMTBroker({self._account_id}, {self._env})"
