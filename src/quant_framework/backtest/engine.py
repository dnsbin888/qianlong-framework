"""Backtest Engine — historical data replay and performance analysis.

Replays historical bars in chronological order, driving strategies
through their on_bar() callback, simulating fills, and recording
the equity curve.

Output: BacktestReport with full performance metrics and equity curve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Type

import pandas as pd

from quant_framework.backtest.metrics import PerformanceMetrics, compute_metrics
from quant_framework.core.constants import RiskDecision, SignalDirection
from quant_framework.core.types import Symbol
from quant_framework.data.models import Bar
from quant_framework.data.provider import DataProvider
from quant_framework.engine.context import PortfolioSnapshot, PositionInfo, StrategyContext
from quant_framework.execution.fill_simulator import FillSimulator, FillSimulatorConfig, SlippageModel
from quant_framework.execution.order import Order, OrderRequest, Position, Trade
from quant_framework.execution.order_manager import OrderManager
from quant_framework.position.sizers import BasePositionSizer, FixedRatioSizer
from quant_framework.risk.engine import RiskEngine
from quant_framework.risk.rules import RiskResult
from quant_framework.strategy.base import BaseStrategy
from quant_framework.strategy.signals import Signal

logger = logging.getLogger("quant_framework.backtest")


@dataclass
class BacktestConfig:
    """Backtest configuration."""

    initial_cash: float = 1_000_000.0
    commission_rate: float = 0.0003      # 万三
    slippage_model: str = "proportional"  # "fixed" | "proportional"
    slippage_value: float = 0.001
    benchmark: str = "000300"            # Benchmark symbol for relative performance
    risk_free_rate: float = 0.03
    start_date: str = "2020-01-01"
    end_date: str = ""


@dataclass
class BacktestReport:
    """Complete backtest results."""

    config: BacktestConfig
    metrics: PerformanceMetrics
    equity_curve: pd.DataFrame         # Columns: datetime, equity, cash, market_value
    trades: list[Trade] = field(default_factory=list)
    daily_returns: pd.Series | None = None
    monthly_returns: pd.Series | None = None
    drawdown_curve: pd.Series | None = None

    def summary(self) -> str:
        """Human-readable summary string."""
        m = self.metrics
        lines = [
            "=" * 50,
            "  Backtest Results",
            "=" * 50,
            f"  Total Return:      {m.total_return:>10.2%}",
            f"  Annual Return:     {m.annual_return:>10.2%}",
            f"  Sharpe Ratio:      {m.sharpe_ratio:>10.2f}",
            f"  Max Drawdown:      {m.max_drawdown:>10.2%}",
            f"  Win Rate:          {m.win_rate:>10.2%}",
            f"  Profit/Loss Ratio: {m.profit_loss_ratio:>10.2f}",
            f"  Total Trades:      {m.total_trades:>10d}",
            f"  Calmar Ratio:      {m.calmar_ratio:>10.2f}",
            f"  Annual Volatility: {m.annual_volatility:>10.2%}",
            "=" * 50,
        ]
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
#  E227: 结构化日志适配器 (兼容策略的 logger.info(msg, **kw))
# ═══════════════════════════════════════════════════════════════

class _StrategyLogger:
    """适配器：将策略的结构化日志调用 (``logger.info("msg", key=val)``)
    转为标准 ``logging`` 模块输出。
    """

    def __init__(self, name: str) -> None:
        import logging
        self._logger = logging.getLogger(name)

    def info(self, msg: str, **kwargs: object) -> None:
        extra: str = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._logger.info(f"{msg} | {extra}" if extra else msg)

    def warning(self, msg: str, **kwargs: object) -> None:
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._logger.warning(f"{msg} | {extra}" if extra else msg)

    def error(self, msg: str, **kwargs: object) -> None:
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._logger.error(f"{msg} | {extra}" if extra else msg)

    def debug(self, msg: str, **kwargs: object) -> None:
        extra = " ".join(f"{k}={v}" for k, v in kwargs.items())
        self._logger.debug(f"{msg} | {extra}" if extra else msg)


class BacktestEngine:
    """Historical backtesting engine.

    Simulates running strategies against historical data:
    1. Load historical bars via DataProvider
    2. Replay bars chronologically
    3. Call strategy.on_bar() for each bar
    4. Process signals through:
       PositionSizer -> OrderRequest -> RiskEngine -> Order -> FillSimulator -> Trade
    5. Track portfolio, orders, trades, and equity curve
    6. Generate performance metrics and report

    Usage:
        provider = SimulatedDataProvider(store=CSVDataStore("./data"))
        engine = BacktestEngine(
            provider,
            BacktestConfig(initial_cash=1000000),
            risk_engine=RiskEngine(),
            position_sizer=FixedRatioSizer(ratio=0.10),
        )
        engine.add_strategy(MyStrategy, config={"symbol": "600000"})
        report = engine.run()
        print(report.summary())
    """

    def __init__(
        self,
        data_provider: DataProvider,
        config: BacktestConfig | None = None,
        risk_engine: RiskEngine | None = None,
        position_sizer: BasePositionSizer | None = None,
    ) -> None:
        self._provider = data_provider
        self.config = config or BacktestConfig()

        # Portfolio state
        self._cash: float = self.config.initial_cash
        self._positions: dict[Symbol, Position] = {}
        self._equity_curve: list[dict[str, Any]] = []
        self._trades: list[Trade] = []

        # Engine components
        slip_model = SlippageModel(self.config.slippage_model)
        fill_cfg = FillSimulatorConfig(
            slippage_model=slip_model,
            slippage_value=self.config.slippage_value,
            commission_rate=self.config.commission_rate,
        )
        self._fill_sim = FillSimulator(fill_cfg)
        self._order_manager = OrderManager()

        # Risk & position sizing (optional, with sensible defaults)
        self._risk_engine = risk_engine
        self._position_sizer = position_sizer or FixedRatioSizer(ratio=0.10)

        # Strategies (will be instantiated per registered class)
        self._strategy_registry: list[tuple[Type[BaseStrategy], dict[str, Any]]] = []

        # Pending limit orders waiting for price match
        self._pending_orders: list[Order] = []

        # Clock override for deterministic timestamps (set per bar in run loop)
        self._clock: Callable[[], datetime] = datetime.now

    # ---- Strategy Registration ----

    def add_strategy(
        self, strategy_cls: Type[BaseStrategy], config: dict[str, Any] | None = None
    ) -> None:
        """Register a strategy for backtesting.

        Args:
            strategy_cls: A BaseStrategy subclass (class, not instance).
            config: Dict of config kwargs to pass to the strategy constructor.
        """
        self._strategy_registry.append((strategy_cls, config or {}))

    # ---- Run ----

    def run(self) -> BacktestReport:
        """Execute the full backtest.

        Returns:
            BacktestReport with metrics, equity curve, and trades.
        """
        logger.info(
            "Starting backtest: %s -> %s",
            self.config.start_date,
            self.config.end_date or "now",
        )

        # 1. Instantiate strategies with contexts
        strategies: list[BaseStrategy] = []
        for cls, cfg in self._strategy_registry:
            ctx = self._create_context(str(cls.__name__))
            try:
                strategy = cls(ctx, **cfg)
            except TypeError:
                # E228: 某些策略不接受额外参数，尝试无参构造
                try:
                    strategy = cls(ctx)
                except TypeError:
                    raise TypeError(
                        f"策略 {cls.__name__} 实例化失败: "
                        f"cls(ctx, **{cfg}) 和 cls(ctx) 均不匹配，"
                        f"请检查 __init__ 签名"
                    )
            strategy.on_init()
            strategies.append(strategy)

        # 2. Start strategies
        # FIXED: variable shadowing bug — was `for strategies in strategies:`
        for strategy in strategies:
            strategy.on_start()

        # 3. Replay loop
        while True:
            changed = self._provider.wait_update(timeout=0.1)
            if not changed:
                break  # No more data

            for symbol in changed:
                # Get latest quote/bar for this symbol
                quotes = self._provider.get_quote([symbol])
                quote = quotes.get(symbol)
                if quote is None:
                    continue

                # Update timestamp for this bar
                self._clock = lambda q=quote: q.timestamp

                # Update position market values
                self._update_market_values(quotes)

                # Sync portfolio state to strategy contexts
                for strategy in strategies:
                    self._sync_context(strategy.ctx)

                # Check pending limit orders for fills FIRST
                for strategy in strategies:
                    self._check_fills(strategy.ctx.strategy_id, symbol, quote)

                # Drive each strategy
                for strategy in strategies:
                    try:
                        signals = strategy.on_quote(quote)
                        if signals:
                            self._process_signals(strategy, signals, quote)
                    except Exception as e:
                        logger.error(
                            "Strategy %s error: %s", strategy, e, exc_info=True
                        )

            # Record equity snapshot
            self._record_equity(self._clock())

        # 4. Build report
        for strategy in strategies:
            strategy.on_stop()

        return self._build_report()

    # ---- Signal Processing ----

    def _process_signals(
        self, strategy: BaseStrategy, signals: list[Signal], quote: Any
    ) -> None:
        """Process signals through the full pipeline:

        Signal -> PositionSizer -> OrderRequest -> RiskEngine -> Order -> FillSimulator -> Trade

        Args:
            strategy: The strategy that generated the signals.
            signals: List of trading signals.
            quote: Current market quote (with OHLC for fill simulation).
        """
        for signal in signals:
            if signal.direction == SignalDirection.HOLD:
                continue

            # Step 1: Position sizing — convert Signal to OrderRequest
            order_req = self._position_sizer.size(
                signal, strategy.ctx.portfolio, strategy.ctx
            )
            if order_req.volume is None or order_req.volume <= 0:
                continue

            # Step 2: Risk check — run through RiskEngine if available
            if self._risk_engine is not None:
                risk_result = self._risk_engine.check(strategy.ctx, order_req)
                if risk_result.decision == RiskDecision.BLOCK:
                    logger.info(
                        "Order blocked by risk: %s — %s",
                        signal.symbol,
                        risk_result.reason,
                    )
                    continue
                if risk_result.decision == RiskDecision.REDUCE:
                    if risk_result.adjusted_volume is not None:
                        order_req.volume = min(order_req.volume, risk_result.adjusted_volume)
                    if risk_result.adjusted_amount is not None:
                        order_req.amount = min(
                            order_req.amount or float("inf"), risk_result.adjusted_amount
                        )
                    logger.info(
                        "Order reduced by risk: %s — %s",
                        signal.symbol,
                        risk_result.reason,
                    )

            # Step 3: Validate cash / position availability
            price = signal.price or quote.price
            if signal.direction == SignalDirection.BUY:
                if order_req.amount and order_req.amount > 0:
                    volume = int(order_req.amount / price / 100) * 100
                elif order_req.volume:
                    volume = order_req.volume
                else:
                    # Fallback: 10% of cash
                    volume = int(self._cash * 0.10 / price / 100) * 100

                cost = volume * price * 1.001  # ~commission buffer
                if cost > self._cash or volume <= 0:
                    continue

                # Create Order
                order = Order(
                    order_id=f"bt_{len(self._trades)}_{signal.symbol}",
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    direction="buy",
                    order_type=order_req.order_type,
                    price=signal.price,
                    requested_volume=volume,
                )

                # For limit orders, don't fill immediately — add to pending queue
                if order.order_type.value == "limit" and order.price is not None:
                    self._pending_orders.append(order)
                    # E231: 先注册到 _orders 再传 order_id (字符串)
                    self._order_manager._orders[order.order_id] = order
                    self._order_manager.mark_pending(order.order_id)
                else:
                    trade = self._fill_sim.simulate_fill(
                        order,
                        quote.price,
                        high=getattr(quote, "high", quote.price),
                        low=getattr(quote, "low", quote.price),
                        limit_up=getattr(quote, "limit_up", 0),
                        limit_down=getattr(quote, "limit_down", 0),
                    )
                    if trade:
                        self._apply_trade(trade)

            elif signal.direction in (SignalDirection.SELL, SignalDirection.CLOSE):
                pos = self._positions.get(signal.symbol)
                if not pos or pos.available <= 0:
                    continue

                if order_req.volume:
                    sell_vol = min(order_req.volume, pos.available)
                else:
                    sell_vol = pos.available  # Close full position

                sell_vol = int(sell_vol / 100) * 100
                if sell_vol <= 0:
                    continue

                order = Order(
                    order_id=f"bt_{len(self._trades)}_{signal.symbol}",
                    strategy_id=signal.strategy_id,
                    symbol=signal.symbol,
                    direction="sell",
                    order_type=order_req.order_type,
                    price=signal.price,
                    requested_volume=sell_vol,
                )

                if order.order_type.value == "limit" and order.price is not None:
                    self._pending_orders.append(order)
                    # E231: 先注册到 _orders 再传 order_id (字符串)
                    self._order_manager._orders[order.order_id] = order
                    self._order_manager.mark_pending(order.order_id)
                else:
                    trade = self._fill_sim.simulate_fill(
                        order,
                        quote.price,
                        high=getattr(quote, "high", quote.price),
                        low=getattr(quote, "low", quote.price),
                        limit_up=getattr(quote, "limit_up", 0),
                        limit_down=getattr(quote, "limit_down", 0),
                    )
                    if trade:
                        self._apply_trade(trade)

    # ---- Portfolio Management ----

    def _apply_trade(self, trade: Trade) -> None:
        """Apply a trade to the simulated portfolio."""
        if trade.direction.value == "buy":
            cost = trade.price * trade.volume + trade.commission
            self._cash -= cost

            if trade.symbol not in self._positions:
                self._positions[trade.symbol] = Position(symbol=trade.symbol)
            pos = self._positions[trade.symbol]
            total_cost = pos.avg_cost * pos.volume + trade.price * trade.volume
            pos.volume += trade.volume
            pos.available += trade.volume
            pos.avg_cost = total_cost / pos.volume if pos.volume > 0 else 0.0
            pos.commission += trade.commission
        else:
            proceeds = trade.price * trade.volume - trade.commission
            self._cash += proceeds

            pos = self._positions[trade.symbol]
            realized = (trade.price - pos.avg_cost) * trade.volume - trade.commission
            pos.realized_pnl += realized
            pos.volume -= trade.volume
            pos.available -= trade.volume
            pos.commission += trade.commission
            if pos.volume <= 0:
                pos.avg_cost = 0.0

        self._trades.append(trade)

        # Update risk engine daily P&L if using DailyLossLimitRule
        if self._risk_engine is not None:
            for rule in self._risk_engine._global_rules:
                if hasattr(rule, "update_pnl"):
                    realized_pnl = (
                        (trade.price - self._positions.get(trade.symbol, Position(symbol=trade.symbol)).avg_cost)
                        * trade.volume
                        - trade.commission
                    ) if trade.direction.value == "sell" else 0.0
                    rule.update_pnl(trade.strategy_id, realized_pnl)

    def _update_market_values(self, quotes: dict[str, Any]) -> None:
        """Update position market values and unrealized P&L."""
        for symbol, quote in quotes.items():
            pos = self._positions.get(symbol)
            if pos and pos.volume > 0:
                pos.current_price = quote.price
                pos.market_value = pos.volume * quote.price
                pos.unrealized_pnl = pos.volume * (quote.price - pos.avg_cost)

    def _check_fills(self, strategy_id: str, symbol: str, quote: Any) -> None:
        """Check pending limit orders for fills at this bar.

        A limit buy order fills if the bar's low <= limit price.
        A limit sell order fills if the bar's high >= limit price.

        This replaces the previous no-op implementation.
        """
        remaining: list[Order] = []
        for order in self._pending_orders:
            if order.symbol != symbol:
                remaining.append(order)
                continue

            high = getattr(quote, "high", quote.price)
            low = getattr(quote, "low", quote.price)

            trade = self._fill_sim.simulate_fill(
                order,
                quote.price,
                high=high,
                low=low,
                limit_up=getattr(quote, "limit_up", 0),
                limit_down=getattr(quote, "limit_down", 0),
            )
            if trade:
                self._apply_trade(trade)
                self._order_manager.mark_filled(order, trade.price)
            else:
                # Order not yet fillable — keep in queue
                remaining.append(order)

        self._pending_orders = remaining

    def _record_equity(self, dt: datetime) -> None:
        """Record a snapshot of portfolio equity."""
        market_value = sum(
            p.market_value or p.volume * (p.current_price or p.avg_cost)
            for p in self._positions.values()
            if p.volume > 0
        )
        total = self._cash + market_value
        self._equity_curve.append({
            "datetime": dt,
            "equity": total,
            "cash": self._cash,
            "market_value": market_value,
        })

    def _sync_context(self, ctx: StrategyContext) -> None:
        """Sync live portfolio state into a strategy's context.

        This ensures the RiskEngine and PositionSizer see up-to-date
        portfolio state when evaluating signals.
        """
        positions_info: dict[str, PositionInfo] = {}
        for sym, pos in self._positions.items():
            if pos.volume > 0:
                positions_info[sym] = PositionInfo(
                    symbol=sym,
                    volume=pos.volume,
                    available=pos.available,
                    avg_cost=pos.avg_cost,
                    current_price=pos.current_price,
                    market_value=pos.market_value,
                    unrealized_pnl=pos.unrealized_pnl,
                    unrealized_pnl_pct=pos.unrealized_pnl_pct,
                )

        market_value = sum(p.market_value or 0 for p in positions_info.values())
        total_equity = self._cash + market_value

        ctx.portfolio = PortfolioSnapshot(
            total_equity=total_equity,
            cash=self._cash,
            market_value=market_value,
            total_pnl=total_equity - self.config.initial_cash,
            positions=positions_info,
            timestamp=self._clock(),
        )

    # ---- Report Generation ----

    def _build_report(self) -> BacktestReport:
        """Generate the final backtest report."""
        equity_df = pd.DataFrame(self._equity_curve)
        if equity_df.empty:
            return BacktestReport(
                config=self.config,
                metrics=PerformanceMetrics(),
                equity_curve=equity_df,
            )

        equity_df.set_index("datetime", inplace=True)
        equity_series = equity_df["equity"]

        metrics = compute_metrics(
            equity_curve=equity_series,
            trades=self._trades,
            risk_free_rate=self.config.risk_free_rate,
        )

        # Daily returns
        daily = equity_series.resample("D").last().dropna()
        daily_returns = daily.pct_change().dropna()

        # Monthly returns
        monthly = equity_series.resample("ME").last().dropna()
        monthly_returns = monthly.pct_change().dropna()

        # Drawdown curve
        peak = equity_series.expanding().max()
        drawdown = (equity_series - peak) / peak

        return BacktestReport(
            config=self.config,
            metrics=metrics,
            equity_curve=equity_df,
            trades=self._trades,
            daily_returns=daily_returns,
            monthly_returns=monthly_returns,
            drawdown_curve=drawdown,
        )

    def _create_context(self, strategy_id: str) -> StrategyContext:
        """Create a StrategyContext for a strategy instance."""
        ctx = StrategyContext(
            strategy_id=strategy_id,
            name=strategy_id,
            data_provider=self._provider,
            broker=self._fill_sim,
            logger=_StrategyLogger(f"quant_framework.strategy.{strategy_id}"),
            config={"initial_cash": self.config.initial_cash},
        )
        return ctx


# ═══════════════════════════════════════════════════════════════
#  E225: 便捷回测接口
# ═══════════════════════════════════════════════════════════════

def quick_backtest(
    strategy_name: str,
    symbol: str,
    start_date: str = "2024-01-01",
    end_date: str = "",
    initial_cash: float = 1_000_000.0,
    **overrides: Any,
) -> dict[str, Any]:
    """一键回测：按策略名运行回测，返回简化报告。

    Args:
        strategy_name: 策略注册名 (如 "ma_cross", "macd_cross")
        symbol: 股票代码 (如 "600000")
        start_date: 回测开始日期
        end_date: 回测结束日期 (空字符串=至今)
        initial_cash: 初始资金
        **overrides: 策略参数覆盖 (如 fast_period=10, slow_period=30)

    Returns:
        dict 包含: total_return, sharpe_ratio, max_drawdown,
                  win_rate, total_trades, strategy_name, symbol
        如果数据不足或策略不存在，返回空 dict + warning 日志
    """
    import hashlib
    import json as _json
    import logging
    import os as _os
    _logger = logging.getLogger("quant_framework.backtest")

    # ═══ E252: 回测结果缓存层 ═══
    _CACHE_DIR: str = r"D:\quant_framework\data\backtest_cache"
    _os.makedirs(_CACHE_DIR, exist_ok=True)

    # 计算缓存键 (参数哈希 → 唯一 key)
    _cache_args: dict[str, Any] = {
        "strategy": strategy_name, "symbol": symbol,
        "start": start_date, "end": end_date,
    }
    if overrides:
        _cache_args["overrides"] = dict(sorted(overrides.items()))
    _cache_raw: str = _json.dumps(_cache_args, sort_keys=True)
    _cache_key: str = hashlib.md5(_cache_raw.encode()).hexdigest()[:12]
    _cache_path: str = _os.path.join(_CACHE_DIR, f"{_cache_key}.json")

    # 缓存命中 → 直接返回
    if _os.path.exists(_cache_path):
        try:
            with open(_cache_path, "r", encoding="utf-8") as _cf:
                _cached = _json.load(_cf)
            # 哈希校验: 防止代码更新导致旧缓存误用
            if _cached.get("_cache_hash") == hashlib.md5(_cache_raw.encode()).hexdigest():
                _logger.info(f"[CACHE] 命中: {_cache_key} (跳过计算)")
                return {k: v for k, v in _cached.items() if not k.startswith("_")}
        except Exception:
            pass  # 缓存损坏 → 重新计算

    try:
        from datetime import datetime as _datetime
        from quant_framework.strategy.registry import StrategyRegistry
        from quant_framework.data.store import CSVDataStore
        from quant_framework.data.providers.simulated import SimulatedDataProvider
    except ImportError as e:
        _logger.error(f"quick_backtest 依赖导入失败: {e}")
        return {}

    # 1. 获取策略元数据
    registry = StrategyRegistry.instance()
    meta = registry.get(strategy_name)
    if meta is None:
        _logger.warning(f"策略 '{strategy_name}' 未注册")
        return {}

    # 2. 构建数据源
    store = CSVDataStore(data_dir=r"D:\quant_framework\data\market")
    start_dt = _datetime.fromisoformat(start_date) if start_date else _datetime(2024, 1, 1)
    end_dt = _datetime.fromisoformat(end_date) if end_date else _datetime.now()

    provider = SimulatedDataProvider(
        store=store,
        symbols=[symbol],
        period="1d",
        start=start_dt,
        end=end_dt,
    )
    try:
        provider.connect()
    except Exception as e:
        _logger.warning(f"数据源连接失败: {e}")
        return {}

    # 3. 检查数据量
    if not getattr(provider, "_timeline", None):
        _logger.warning(f"回测数据不足: {symbol} 在 {start_date}~{end_date or 'now'} 无数据")
        return {}

    # 4. 创建引擎并运行
    config = BacktestConfig(
        initial_cash=initial_cash,
        start_date=start_date,
        end_date=end_date or _datetime.now().strftime("%Y-%m-%d"),
    )
    engine = BacktestEngine(provider, config=config)

    # 5. 添加策略 (通过上下文创建实例后手动注入)
    ctx = engine._create_context(strategy_name)
    strategy = registry.create_strategy(strategy_name, ctx, symbol=symbol, **overrides)
    if strategy is None:
        _logger.warning(f"策略 '{strategy_name}' 实例化失败")
        return {}
    strategy.on_init()

    # E228: 构建正确的 config 对象 (修复 "missing cfg" TypeError)
    _cfg_kwargs: dict[str, Any] = {}
    for _pname, _pinfo in meta.params.items():
        _cfg_kwargs[_pname] = _pinfo.get("default")
    _cfg_kwargs["symbol"] = symbol
    _cfg_kwargs.update(overrides)  # E230: 合并参数覆盖
    _cfg = meta.config_cls(**_cfg_kwargs)
    engine._strategy_registry.append((meta.strategy_cls, {"cfg": _cfg}))

    # 6. 运行
    try:
        report = engine.run()
    except Exception as e:
        _logger.warning(f"回测运行失败: {e}")
        return {}

    # 7. 返回简化报告
    m = report.metrics
    _result: dict[str, Any] = {
        "strategy_name": strategy_name,
        "symbol": symbol,
        "start_date": start_date,
        "end_date": end_date or _datetime.now().strftime("%Y-%m-%d"),
        "total_return": getattr(m, "total_return", 0.0),
        "annual_return": getattr(m, "annual_return", 0.0),
        "sharpe_ratio": getattr(m, "sharpe_ratio", 0.0),
        "max_drawdown": getattr(m, "max_drawdown", 0.0),
        "win_rate": getattr(m, "win_rate", 0.0),
        "total_trades": getattr(m, "total_trades", 0),
    }

    # E252: 保存缓存
    try:
        _result["_cache_hash"] = hashlib.md5(_cache_raw.encode()).hexdigest()
        with open(_cache_path, "w", encoding="utf-8") as _cf:
            _json.dump(_result, _cf, ensure_ascii=False)
        _logger.info(f"[CACHE] 已保存: {_cache_key}")
    except Exception:
        pass

    return {k: v for k, v in _result.items() if not k.startswith("_")}
