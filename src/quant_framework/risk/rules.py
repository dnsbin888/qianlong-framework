"""Risk rule abstractions and built-in risk rules.

Each RiskRule independently evaluates an order request and returns
ALLOW, BLOCK, or REDUCE. The RiskEngine chains rules together —
any single BLOCK rejects the order.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from quant_framework.core.constants import OrderDirection, RiskDecision
from quant_framework.engine.context import StrategyContext


@dataclass
class RiskResult:
    """Result of a risk rule check.

    Attributes:
        decision: ALLOW (pass), BLOCK (reject), or REDUCE (allow with smaller size).
        reason: Human-readable explanation.
        adjusted_volume: If REDUCE, the maximum allowed volume.
        adjusted_amount: If REDUCE, the maximum allowed amount.
    """

    decision: RiskDecision
    reason: str = ""
    adjusted_volume: int | None = None
    adjusted_amount: float | None = None

    @classmethod
    def allow(cls) -> "RiskResult":
        """Convenience: create an ALLOW result."""
        return cls(RiskDecision.ALLOW)

    @classmethod
    def block(cls, reason: str) -> "RiskResult":
        """Convenience: create a BLOCK result with a reason."""
        return cls(RiskDecision.BLOCK, reason=reason)

    @classmethod
    def reduce(cls, reason: str, volume: int | None = None, amount: float | None = None) -> "RiskResult":
        """Convenience: create a REDUCE result."""
        return cls(RiskDecision.REDUCE, reason=reason, adjusted_volume=volume, adjusted_amount=amount)


class RiskRule(ABC):
    """Abstract base class for risk management rules.

    Each rule is independent and stateless (state is maintained by
    the framework context, not by rules themselves).

    Subclasses must implement:
    - name: property returning the rule name
    - check(ctx, order): return RiskResult
    """

    @abstractmethod
    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Evaluate an order request against this risk rule.

        Args:
            ctx: Strategy context (provides portfolio, history, etc.).
            order: The order request to check.

        Returns:
            RiskResult with decision and optional reason/adjustment.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable rule name for logging."""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.name})"


# ======================================================================
# Built-in Risk Rules
# ======================================================================


class MaxDrawdownRule(RiskRule):
    """Blocks new positions when portfolio drawdown exceeds a threshold.

    Drawdown is measured from the historical peak equity.
    Only blocks BUY/CLOSE orders — allows SELL (risk-reducing) orders.
    """

    def __init__(self, max_drawdown_pct: float = 0.20) -> None:
        """
        Args:
            max_drawdown_pct: Maximum allowed drawdown as fraction (0.20 = 20%).
        """
        self.max_drawdown_pct = max_drawdown_pct
        self._peak_equity: dict[str, float] = {}  # strategy_id -> peak

    @property
    def name(self) -> str:
        return "最大回撤限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if current drawdown exceeds limit."""
        # SELL orders are always allowed (they reduce risk)
        order_dir = getattr(order, "direction", None)
        if order_dir in (OrderDirection.SELL, "sell"):
            return RiskResult.allow()

        peak = self._peak_equity.get(ctx.strategy_id)
        if peak is None or ctx.total_equity >= peak:
            self._peak_equity[ctx.strategy_id] = ctx.total_equity
            return RiskResult.allow()

        drawdown = (peak - ctx.total_equity) / peak if peak > 0 else 0.0
        if drawdown > self.max_drawdown_pct:
            return RiskResult.block(
                f"回撤 {drawdown:.2%} 超过上限 {self.max_drawdown_pct:.2%}，"
                f"峰值净值={peak:.2f}，当前净值={ctx.total_equity:.2f}"
            )
        return RiskResult.allow()


class DailyLossLimitRule(RiskRule):
    """Stops trading when daily loss exceeds a threshold.

    Tracks cumulative P&L per strategy per day.

    Supports two modes (mutually exclusive):
    - **Percentage mode** (default): ``max_daily_loss_pct`` defines the loss as a
      fraction of ``total_equity``.  E.g. ``0.03`` means "stop if today's loss
      exceeds 3% of account equity".  This adapts automatically to any account
      size.
    - **Absolute mode**: pass ``max_daily_loss`` (CNY) to keep the old behaviour.
      Useful when you want a fixed-amount hard stop regardless of equity.

    .. deprecated:: 0.3
       ``max_daily_loss`` (absolute CNY) is kept for backward compatibility but
       percentage-based limiting is strongly recommended.
    """

    def __init__(
        self,
        *,
        max_daily_loss_pct: float | None = None,
        max_daily_loss: float | None = None,
    ) -> None:
        """
        Args:
            max_daily_loss_pct: Maximum daily loss as fraction of total_equity
                (e.g. 0.03 = 3%).  Default is 3%.  Ignored when *max_daily_loss*
                is also provided.
            max_daily_loss: DEPRECATED — absolute daily loss limit in CNY.
                When set, overrides the percentage mode.
        """
        # Backward compat: old code passing positional arg 50000
        if max_daily_loss is not None:
            self._use_pct = False
            self.max_daily_loss = float(max_daily_loss)
            self.max_daily_loss_pct: float | None = None
        else:
            self._use_pct = True
            self.max_daily_loss_pct = float(max_daily_loss_pct) if max_daily_loss_pct is not None else 0.03
            self.max_daily_loss: float | None = None
        self._daily_pnl: dict[str, float] = {}  # strategy_id -> today's pnl
        self._last_date: dict[str, str] = {}     # strategy_id -> last date string

    @property
    def name(self) -> str:
        return "日内亏损限制"

    def _get_limit(self, equity: float) -> float:
        """Return the effective absolute loss limit for the given equity."""
        if self._use_pct:
            return equity * self.max_daily_loss_pct  # type: ignore[operator]
        return self.max_daily_loss  # type: ignore[return-value]

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if daily loss limit is breached."""
        today = datetime.now().strftime("%Y-%m-%d")
        last = self._last_date.get(ctx.strategy_id)

        # Reset daily P&L on new day
        if last != today:
            self._daily_pnl[ctx.strategy_id] = 0.0
            self._last_date[ctx.strategy_id] = today

        current_loss = self._daily_pnl.get(ctx.strategy_id, 0.0)
        # Negative P&L = loss
        if current_loss < 0:
            limit = self._get_limit(ctx.total_equity)
            if abs(current_loss) > limit:
                if self._use_pct:
                    actual_pct = abs(current_loss) / ctx.total_equity if ctx.total_equity > 0 else 0
                    return RiskResult.block(
                        f"日内亏损 {abs(current_loss):.2f} ({actual_pct:.2%}) "
                        f"超过上限 {self.max_daily_loss_pct:.2%} "
                        f"(={limit:.2f}元)"
                    )
                else:
                    return RiskResult.block(
                        f"日内亏损 {abs(current_loss):.2f} 超过上限 {self.max_daily_loss:.2f}",
                    )
        return RiskResult.allow()

    def update_pnl(self, strategy_id: str, realized_pnl: float) -> None:
        """Update the daily P&L for a strategy (called on trade fills)."""
        today = datetime.now().strftime("%Y-%m-%d")
        last = self._last_date.get(strategy_id)
        if last != today:
            self._daily_pnl[strategy_id] = 0.0
            self._last_date[strategy_id] = today
        self._daily_pnl[strategy_id] = self._daily_pnl.get(strategy_id, 0.0) + realized_pnl


class PositionLimitRule(RiskRule):
    """Limits maximum position size for a single stock.

    Blocks buys that would cause the position to exceed max_pct of total equity.
    """

    def __init__(self, max_position_pct: float = 0.30) -> None:
        """
        Args:
            max_position_pct: Maximum single-stock position as fraction of equity.
        """
        self.max_position_pct = max_position_pct

    @property
    def name(self) -> str:
        return "单票仓位限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if the order would exceed the single-stock position limit."""
        order_dir = getattr(order, "direction", None)
        if order_dir not in (OrderDirection.BUY, "buy"):
            return RiskResult.allow()

        symbol = getattr(order, "symbol", "")
        pos = ctx.get_position(symbol)
        current_value = pos.market_value if pos else 0.0

        # Estimate new position value
        order_amount = getattr(order, "amount", 0) or 0
        order_volume = getattr(order, "volume", 0) or 0
        order_price = getattr(order, "price", 0) or 0

        if order_amount > 0:
            new_value = current_value + order_amount
        elif order_volume > 0 and order_price > 0:
            new_value = current_value + order_volume * order_price
        else:
            return RiskResult.allow()  # Can't determine size

        max_allowed = ctx.total_equity * self.max_position_pct
        if new_value > max_allowed:
            return RiskResult.block(
                f"{symbol} 仓位将达 {new_value:.0f}/{ctx.total_equity:.0f} "
                f"({new_value/ctx.total_equity*100:.1f}%)，超出单票上限 {self.max_position_pct*100:.0f}%"
            )
        return RiskResult.allow()


class TotalPositionsRule(RiskRule):
    """Limits the total number of concurrent positions."""

    def __init__(self, max_positions: int = 10) -> None:
        self.max_positions = max_positions

    @property
    def name(self) -> str:
        return "总持仓数量限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Block buys if already at maximum position count."""
        order_dir = getattr(order, "direction", None)
        if order_dir not in (OrderDirection.BUY, "buy"):
            return RiskResult.allow()

        current_count = len(ctx.portfolio.positions)
        if current_count >= self.max_positions:
            # But allow if we already hold the target stock
            symbol = getattr(order, "symbol", "")
            if symbol not in ctx.portfolio.positions:
                return RiskResult.block(
                    f"持仓数量 {current_count} 已达上限 {self.max_positions}"
                )
        return RiskResult.allow()


class OrderFrequencyRule(RiskRule):
    """Prevents orders from being submitted too frequently."""

    def __init__(self, min_interval_seconds: float = 5.0) -> None:
        """
        Args:
            min_interval_seconds: Minimum seconds between orders from the same strategy.
        """
        self.min_interval = min_interval_seconds
        self._last_order_time: dict[str, datetime] = {}  # strategy_id -> last order time

    @property
    def name(self) -> str:
        return "下单频率限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if enough time has passed since the last order."""
        last_time = self._last_order_time.get(ctx.strategy_id)
        now = datetime.now()

        if last_time is not None:
            elapsed = (now - last_time).total_seconds()
            if elapsed < self.min_interval:
                return RiskResult.block(
                    f"下单间隔 {elapsed:.1f}s 小于最小间隔 {self.min_interval}s"
                )

        self._last_order_time[ctx.strategy_id] = now
        return RiskResult.allow()


class BlacklistRule(RiskRule):
    """Blocks trading of specified symbols."""

    def __init__(self, blacklist: list[str] | None = None) -> None:
        """
        Args:
            blacklist: List of security codes that are forbidden.
        """
        self._blacklist: set[str] = set(blacklist or [])

    @property
    def name(self) -> str:
        return "黑名单限制"

    def add(self, symbol: str) -> None:
        """Add a symbol to the blacklist."""
        self._blacklist.add(symbol)

    def remove(self, symbol: str) -> None:
        """Remove a symbol from the blacklist."""
        self._blacklist.discard(symbol)

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Block if the symbol is blacklisted."""
        symbol = getattr(order, "symbol", "")
        if symbol in self._blacklist:
            return RiskResult.block(f"{symbol} 在黑名单中")
        return RiskResult.allow()


# ======================================================================
# Extended Risk Rules (P0 熔断防护)
# ======================================================================


class MarketCircuitBreakerRule(RiskRule):
    """大盘极端行情熔断 — 当参考指数日内跌幅超过阈值时暂停所有买入.

    类似 A 股 2016 年熔断机制的简化版:
    - 一级: 指数跌 ≥ ``level1_pct`` → 只允许卖出（减仓），阻止新开仓
    - 二级: 指数跌 ≥ ``level2_pct`` → 完全停止交易（含卖出也无法保证成交）

    参考标准:
    - 美国市场: S&P 500 跌 7%/13%/20% 三级熔断
    - A 股已废止: 沪深300 跌 5%/7% 两级熔断
    - 本规则默认: level1=3% (暂停开仓), level2=5% (全面停止)

    使用方式::

        breaker = MarketCircuitBreakerRule(
            reference_symbol="sh000300",  # 沪深300
            level1_pct=0.03,               # 3% 暂停开仓
            level2_pct=0.05,               # 5% 全面停止
        )
        engine.add_global_rule(breaker)

    需要 ``ctx`` 提供:
    - ``get_reference_price(symbol)`` → 返回参考指数的实时价格
    - ``get_reference_prev_close(symbol)`` → 返回参考指数的前收盘价
      如果这些方法不存在, 该规则自动降级为仅打印警告但不拦截.
    """

    def __init__(
        self,
        reference_symbol: str = "sh000300",
        level1_pct: float = 0.03,
        level2_pct: float = 0.05,
        cooldown_minutes: int = 30,
    ) -> None:
        """
        Args:
            reference_symbol: 参考指数代码, 默认沪深300 (sh000300).
            level1_pct: 一级熔断阈值 (暂停买入), 如 0.03 = -3%.
            level2_pct: 二级熔断阈值 (全面停止), 如 0.05 = -5%.
            cooldown_minutes: 熔断冷却时间(分钟), 熔断触发后多久自动解除.
                默认 30 分钟.  设为 0 表示不自动解除 (需手动重置).
        """
        self.reference_symbol = reference_symbol
        self.level1_pct = level1_pct
        self.level2_pct = level2_pct
        self.cooldown_minutes = cooldown_minutes
        self._circuit_break_until: datetime | None = None
        self._breach_level: int = 0  # 0=正常, 1=暂停开仓, 2=全面停止

    @property
    def name(self) -> str:
        return "大盘熔断保护"

    def reset(self) -> None:
        """手动重置熔断状态."""
        self._circuit_break_until = None
        self._breach_level = 0

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if market circuit breaker is active."""
        # 检查冷却期
        if (
            self._circuit_break_until is not None
            and datetime.now() > self._circuit_break_until
        ):
            self._circuit_break_until = None
            self._breach_level = 0

        # 如果已在二级熔断中, 阻止一切
        if self._breach_level >= 2:
            return RiskResult.block(
                f"二级熔断生效中 — 参考指数 {self.reference_symbol} 跌幅 ≥ "
                f"{self.level2_pct:.0%}, 所有交易暂停"
            )

        # 尝试获取参考指数实时数据
        # 优先从 ctx 获取, 如果 ctx 不支持则跳过
        ref_price = self._get_ref_price(ctx)
        ref_prev_close = self._get_ref_prev_close(ctx)

        if ref_price is None or ref_prev_close is None or ref_prev_close <= 0:
            # 降级: 无数据时不拦截, 但保留已触发的熔断状态
            return RiskResult.allow()

        ref_change = (ref_price - ref_prev_close) / ref_prev_close

        # 二级熔断
        if ref_change <= -self.level2_pct:
            self._breach_level = 2
            if self.cooldown_minutes > 0:
                self._circuit_break_until = datetime.now() + timedelta(
                    minutes=self.cooldown_minutes
                )
            return RiskResult.block(
                f"二级熔断触发! {self.reference_symbol} 跌幅 {ref_change:.2%} "
                f"≥ {self.level2_pct:.0%}, 所有交易暂停"
            )

        # 一级熔断
        if ref_change <= -self.level1_pct:
            self._breach_level = 1
            if self.cooldown_minutes > 0:
                self._circuit_break_until = datetime.now() + timedelta(
                    minutes=self.cooldown_minutes
                )
            # 一级: 允许卖出, 阻止买入
            order_dir = getattr(order, "direction", None)
            if order_dir in (OrderDirection.SELL, "sell"):
                return RiskResult.allow()
            return RiskResult.block(
                f"一级熔断触发! {self.reference_symbol} 跌幅 {ref_change:.2%} "
                f"≥ {self.level1_pct:.0%}, 仅允许卖出减仓"
            )

        # 正常
        self._breach_level = 0
        return RiskResult.allow()

    def _get_ref_price(self, ctx: StrategyContext) -> float | None:
        """获取参考指数实时价格."""
        for attr in ("get_reference_price", "get_market_price"):
            fn = getattr(ctx, attr, None)
            if callable(fn):
                try:
                    return fn(self.reference_symbol)
                except Exception:
                    pass
        # 从行情快照中获取
        snapshot = getattr(ctx, "market_snapshot", None)
        if snapshot and isinstance(snapshot, dict):
            data = snapshot.get(self.reference_symbol)
            if data:
                return data.get("price") if isinstance(data, dict) else getattr(data, "price", None)
        return None

    def _get_ref_prev_close(self, ctx: StrategyContext) -> float | None:
        """获取参考指数前收盘价."""
        for attr in ("get_reference_prev_close", "get_prev_close"):
            fn = getattr(ctx, attr, None)
            if callable(fn):
                try:
                    return fn(self.reference_symbol)
                except Exception:
                    pass
        snapshot = getattr(ctx, "market_snapshot", None)
        if snapshot and isinstance(snapshot, dict):
            data = snapshot.get(self.reference_symbol)
            if data:
                return data.get("prev_close") if isinstance(data, dict) else getattr(data, "prev_close", None)
        return None


class ConsecutiveLossRule(RiskRule):
    """连续亏损熔断 — 连续 N 天亏损后自动降低仓位或暂停交易.

    机构常见规则:
    - 连续 3 天亏损 → 降仓 50%
    - 连续 5 天亏损 → 完全暂停, 人工干预

    实现方式:
    - 追踪每个策略的连续亏损天数
    - 超过阈值后通过 REDUCE 建议降低仓位或 BLOCK 暂停交易
    """

    def __init__(
        self,
        max_consecutive_days: int = 3,
        action: str = "reduce",
        reduce_to_pct: float = 0.5,
    ) -> None:
        """
        Args:
            max_consecutive_days: 允许的最大连续亏损天数.
            action: 触发后的动作, ``"reduce"`` (降仓) 或 ``"block"`` (暂停).
            reduce_to_pct: action 为 ``"reduce"`` 时, 仓位降至多少 (0.5 = 50%).
        """
        if action not in ("reduce", "block"):
            raise ValueError(f"action must be 'reduce' or 'block', got '{action}'")
        self.max_consecutive_days = max_consecutive_days
        self.action = action
        self.reduce_to_pct = reduce_to_pct
        self._consecutive_loss_days: dict[str, int] = {}
        self._last_pnl_date: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "连续亏损熔断"

    def reset(self, strategy_id: str | None = None) -> None:
        """重置连续亏损计数.

        Args:
            strategy_id: 指定策略, None 则重置全部.
        """
        if strategy_id is None:
            self._consecutive_loss_days.clear()
            self._last_pnl_date.clear()
        else:
            self._consecutive_loss_days.pop(strategy_id, None)
            self._last_pnl_date.pop(strategy_id, None)

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if consecutive loss limit is breached."""
        # SELL orders always allowed
        order_dir = getattr(order, "direction", None)
        if order_dir in (OrderDirection.SELL, "sell"):
            return RiskResult.allow()

        consecutive = self._consecutive_loss_days.get(ctx.strategy_id, 0)
        if consecutive >= self.max_consecutive_days:
            if self.action == "block":
                return RiskResult.block(
                    f"连续亏损 {consecutive} 天 (≥ {self.max_consecutive_days} 天), "
                    f"交易暂停, 请人工检查策略"
                )
            else:  # reduce
                return RiskResult.reduce(
                    f"连续亏损 {consecutive} 天, 建议降至 {self.reduce_to_pct:.0%} 仓位",
                    amount=ctx.total_equity * self.reduce_to_pct,
                )
        return RiskResult.allow()

    def update_daily_pnl(self, strategy_id: str, daily_pnl: float) -> None:
        """每日收盘后调用, 更新连续亏损计数.

        Args:
            strategy_id: 策略标识.
            daily_pnl: 当日已实现盈亏 (正数=盈利, 负数=亏损).
        """
        today = datetime.now().strftime("%Y-%m-%d")
        last = self._last_pnl_date.get(strategy_id)
        if last == today:
            return  # 同一天不重复更新

        self._last_pnl_date[strategy_id] = today
        if daily_pnl < 0:
            self._consecutive_loss_days[strategy_id] = (
                self._consecutive_loss_days.get(strategy_id, 0) + 1
            )
        else:
            self._consecutive_loss_days[strategy_id] = 0


class SingleOrderAmountRule(RiskRule):
    """单笔下单金额限制 — 防止 bug 导致的巨额单笔订单.

    机构标准:
    - 单笔买入不超过总权益的 N% (通常 10%~30%)
    - 或设定绝对金额上限

    这是一道纯防护性规则, 正常策略不应该触发.
    """

    def __init__(
        self,
        max_amount_pct: float | None = None,
        max_amount_abs: float | None = None,
    ) -> None:
        """
        Args:
            max_amount_pct: 单笔最大金额占总权益比例 (如 0.10 = 10%).
            max_amount_abs: 单笔最大绝对金额 (CNY).
            至少提供一个.
        """
        if max_amount_pct is None and max_amount_abs is None:
            raise ValueError("至少提供 max_amount_pct 或 max_amount_abs")
        self.max_amount_pct = max_amount_pct
        self.max_amount_abs = max_amount_abs

    @property
    def name(self) -> str:
        return "单笔金额限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if single order amount exceeds limit."""
        order_amount = getattr(order, "amount", 0) or 0
        order_volume = getattr(order, "volume", 0) or 0
        order_price = getattr(order, "price", 0) or 0

        if order_amount <= 0:
            if order_volume > 0 and order_price > 0:
                order_amount = order_volume * order_price
            else:
                return RiskResult.allow()  # 无法判断金额

        # 比例限制
        if self.max_amount_pct is not None and ctx.total_equity > 0:
            max_by_pct = ctx.total_equity * self.max_amount_pct
            if order_amount > max_by_pct:
                return RiskResult.block(
                    f"单笔金额 {order_amount:.0f} 超过权益比例上限 "
                    f"{self.max_amount_pct:.0%} ({max_by_pct:.0f}元)"
                )

        # 绝对限制
        if self.max_amount_abs is not None:
            if order_amount > self.max_amount_abs:
                return RiskResult.block(
                    f"单笔金额 {order_amount:.0f} 超过绝对上限 {self.max_amount_abs:.0f}元"
                )

        return RiskResult.allow()


class DailyTradeCountRule(RiskRule):
    """每日交易次数限制 — 防止过度交易和被交易所处罚.

    中小投资者账户日内撤单超过一定比例会被交易所关注.
    本规则同时统计已成交和已提交订单数.
    """

    def __init__(self, max_daily_trades: int = 100) -> None:
        """
        Args:
            max_daily_trades: 每日最大交易次数 (含买卖).
        """
        self.max_daily_trades = max_daily_trades
        self._daily_count: dict[str, int] = {}
        self._last_date: dict[str, str] = {}

    @property
    def name(self) -> str:
        return "日交易次数限制"

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Check if daily trade count limit is breached."""
        today = datetime.now().strftime("%Y-%m-%d")
        last = self._last_date.get(ctx.strategy_id)

        if last != today:
            self._daily_count[ctx.strategy_id] = 0
            self._last_date[ctx.strategy_id] = today

        count = self._daily_count.get(ctx.strategy_id, 0)
        if count >= self.max_daily_trades:
            return RiskResult.block(
                f"今日已交易 {count} 次, 达到上限 {self.max_daily_trades} 次"
            )

        self._daily_count[ctx.strategy_id] = count + 1
        return RiskResult.allow()

    def reset(self, strategy_id: str | None = None) -> None:
        """手动重置计数."""
        if strategy_id is None:
            self._daily_count.clear()
            self._last_date.clear()
        else:
            self._daily_count.pop(strategy_id, None)
            self._last_date.pop(strategy_id, None)
