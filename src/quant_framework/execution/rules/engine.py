"""规则引擎 — 组合多个规则，批量检查持仓/信号。

蓝图v3.0 R1-1: 支持 Broker 注入，从纯决策升级为决策+执行一体。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction

logger = logging.getLogger(__name__)


class RuleEngine:
    """规则引擎 — 将所有规则应用于持仓和信号，通过注入的 Broker 执行。

    检查顺序:
      1. 全局规则 (不依赖具体持仓) → 熔断/频率限制
      2. 持仓规则 (逐条检查) → 止损/止盈
      3. 信号规则 → 过滤/调整

    蓝图v3.0 R1-1: 支持 Broker 注入，统一:
      - PaperBroker (模拟盘)
      - QMTBroker (QMT xttrader)
      - THSBroker (同花顺联动精灵)

    Usage:
        engine = RuleEngine(broker=QMTBroker(...))
        engine.add_rule(AutoStopLossRule(threshold=-0.05))

        # 决策
        can_buy, reason = engine.can_buy(context)

        # 执行 (通过注入的Broker)
        result = engine.execute_order(code="600000", direction="buy", volume=100, price=10.50)
    """

    def __init__(self, broker=None):
        self._rules: list[BaseRule] = []
        self._position_rules: list[BaseRule] = []
        self._global_rules: list[BaseRule] = []
        self._broker = broker  # R1-1: Broker 注入 (AbstractBroker 实例)

    def add_rule(self, rule: BaseRule) -> "RuleEngine":
        """添加规则。"""
        self._rules.append(rule)
        # 分类：持仓规则 vs 全局规则
        self._position_rules.append(rule)
        self._global_rules.append(rule)
        return self

    def remove_rule(self, rule: BaseRule) -> "RuleEngine":
        """移除规则。"""
        for lst in [self._rules, self._position_rules, self._global_rules]:
            if rule in lst:
                lst.remove(rule)
        return self

    def clear(self) -> "RuleEngine":
        """清空所有规则。"""
        self._rules.clear()
        self._position_rules.clear()
        self._global_rules.clear()
        return self

    def check_position(
        self,
        position: dict,
        market_data: dict,
        context: dict,
    ) -> list[RuleAction]:
        """检查单条持仓，返回触发的所有操作。"""
        actions = []
        for rule in self._position_rules:
            try:
                result = rule.check(position, market_data, context)
                if result is not None and result.action:
                    actions.append(result)
            except Exception:
                continue
        return actions

    def check_all_positions(
        self,
        positions: list[dict],
        market_data: dict,
        context: dict,
    ) -> list[RuleAction]:
        """检查所有持仓，返回触发的所有操作。"""
        all_actions = []
        for pos in positions:
            actions = self.check_position(pos, market_data, context)
            all_actions.extend(actions)
        return all_actions

    def check_global(self, context: dict) -> list[RuleAction]:
        """检查全局规则（不依赖具体持仓）。"""
        actions = []
        for rule in self._global_rules:
            try:
                result = rule.check(None, {}, context)
                if result is not None and result.action:
                    actions.append(result)
            except Exception:
                continue
        return actions

    def can_buy(self, context: dict) -> tuple[bool, str]:
        """检查是否允许买入。

        Returns:
            (ok, reason) — ok=False 时 reason 说明原因。
        """
        for rule in self._global_rules:
            try:
                result = rule.check(None, {}, context)
                if result and result.action in ("reject", "liquidate_all"):
                    return False, result.reason
            except Exception:
                continue
        return True, ""

    # ═══════════════════════════════════════════════════════
    #  R1-1: Broker 注入 + 执行接口
    # ═══════════════════════════════════════════════════════

    def set_broker(self, broker) -> "RuleEngine":
        """注入交易通道 (QMT/THS/Paper)。"""
        self._broker = broker
        logger.info(f"[RuleEngine] Broker已注入: {getattr(broker, 'name', type(broker).__name__)}")
        return self

    @property
    def broker(self):
        """获取当前注入的 Broker 实例。"""
        return self._broker

    @property
    def has_broker(self) -> bool:
        """是否已注入 Broker。"""
        return self._broker is not None

    def execute_order(
        self,
        code: str,
        direction: str,
        volume: int,
        price: float = 0.0,
        strategy_name: str = "RuleEngine",
    ) -> dict:
        """通过注入的 Broker 执行订单 (R1-1 统一入口)。

        所有交易通道(QMT/THS/Paper)通过此方法统一执行。
        调用前应已通过 can_buy() 和 check_position() 完成风控检查。

        Args:
            code: 股票代码 (如 "600000" 或 "600000.SH")
            direction: "buy" | "sell"
            volume: 数量 (股)
            price: 限价 (0 = 市价)
            strategy_name: 策略标识

        Returns:
            {"success": bool, "order_id": str, "channel": str, "error": str|None}
        """
        if not self._broker:
            return {"success": False, "order_id": "", "channel": "none",
                    "error": "未注入Broker，无法执行订单"}

        if not self._broker.is_connected():
            return {"success": False, "order_id": "", "channel": self.name,
                    "error": "Broker未连接"}

        # 数量校验
        if volume % 100 != 0:
            volume = max(100, (volume // 100) * 100)
        if volume < 100:
            return {"success": False, "order_id": "", "channel": self.name,
                    "error": f"数量{volume}<100"}

        try:
            if direction == "buy":
                order = self._submit_buy(code, volume, price, strategy_name)
            elif direction == "sell":
                order = self._submit_sell(code, volume, price, strategy_name)
            else:
                return {"success": False, "order_id": "", "channel": self.name,
                        "error": f"未知方向: {direction}"}

            if order and order.order_id:
                logger.info(
                    f"[RuleEngine] {direction} {code} x{volume} @{price:.2f} "
                    f"→ order_id={order.order_id} channel={self.name}"
                )
                return {"success": True, "order_id": str(order.order_id),
                        "channel": self.name, "error": None}
            else:
                return {"success": False, "order_id": "", "channel": self.name,
                        "error": "Broker返回空订单"}

        except Exception as e:
            logger.error(f"[RuleEngine] 执行异常: {e}")
            return {"success": False, "order_id": "", "channel": self.name,
                    "error": str(e)}

    def _submit_buy(self, code, volume, price, strategy_name):
        """提交买单 — 子类可覆盖。"""
        from quant_framework.execution.order import OrderRequest
        return self._broker.submit_order(OrderRequest(
            symbol=code, direction="buy", volume=volume,
            price=price, strategy_name=strategy_name,
        ))

    def _submit_sell(self, code, volume, price, strategy_name):
        """提交卖单 — 子类可覆盖。"""
        from quant_framework.execution.order import OrderRequest
        return self._broker.submit_order(OrderRequest(
            symbol=code, direction="sell", volume=volume,
            price=price, strategy_name=strategy_name,
        ))

    # ═══════════════════════════════════════════════════════
    #  R1-3/4/5: 止损/止盈/仓位/熔断 便捷方法
    # ═══════════════════════════════════════════════════════

    def check_stop_loss(self, position: dict) -> dict | None:
        """R1-3: 检查单个持仓是否需要止损。

        Args:
            position: {symbol, avg_cost, last_price, qty, ...}

        Returns:
            None | {"action": "sell", "reason": str, "qty": int}
        """
        if position is None:
            return None
        cost = float(position.get("avg_cost", position.get("cost_price", 0)))
        current = float(position.get("last_price", position.get("current_price", 0)))
        if cost <= 0 or current <= 0:
            return None
        pnl_pct = (current - cost) / cost
        qty = int(position.get("qty", position.get("quantity", 0)))

        if pnl_pct <= -0.05:
            return {"action": "sell", "reason": f"硬止损({pnl_pct*100:.1f}%)", "qty": qty}
        if pnl_pct <= -0.03:
            sell_qty = max(100, (qty // 2) // 100 * 100)
            return {"action": "sell", "reason": f"软止损({pnl_pct*100:.1f}%)", "qty": sell_qty}
        return None

    def check_take_profit(self, position: dict) -> dict | None:
        """R1-4: 检查单个持仓是否需要止盈（三级5%/7%/12%）。

        Args:
            position: {symbol, avg_cost, last_price, qty, ...}

        Returns:
            None | {"action": "sell", "reason": str, "qty": int}
        """
        if position is None:
            return None
        cost = float(position.get("avg_cost", position.get("cost_price", 0)))
        current = float(position.get("last_price", position.get("current_price", 0)))
        if cost <= 0 or current <= 0:
            return None
        pnl_pct = (current - cost) / cost
        qty = int(position.get("qty", position.get("quantity", 0)))
        sell_qty = max(100, (qty // 3) // 100 * 100)
        if sell_qty < 100:
            return None

        if pnl_pct >= 0.12:
            return {"action": "sell", "reason": f"三级止盈({pnl_pct*100:.1f}%)", "qty": sell_qty}
        if pnl_pct >= 0.07:
            return {"action": "sell", "reason": f"二级止盈({pnl_pct*100:.1f}%)", "qty": sell_qty}
        if pnl_pct >= 0.05:
            return {"action": "sell", "reason": f"一级止盈({pnl_pct*100:.1f}%)", "qty": sell_qty}
        return None

    def calc_position_size(self, cash: float, price: float, max_positions: int = 5,
                           strategy: str = "chase", market_state: str = "unknown") -> int:
        """R1-5 + S1-3: 计算应买入股数（含策略权重调整）。

        Args:
            cash: 可用资金
            price: 股票单价
            max_positions: 最大持仓数
            strategy: 策略名 ('chase'|'low_absorb'|'defensive')
            market_state: 市场状态 ('bull'|'bear'|'volatile'|'unknown')

        Returns:
            整百股数
        """
        if cash <= 0 or price <= 0:
            return 0
        base_ratio = min(0.20, 1.0 / max(max_positions, 1))
        # S1-3: 策略权重调整
        try:
            from strategy_weights import adjust_position_size
            target = adjust_position_size(cash * base_ratio, strategy, market_state)
        except ImportError:
            target = cash * base_ratio
        qty = int(target / price / 100) * 100
        return max(100, qty) if qty >= 100 else 0

    def check_circuit_breaker(self, account: dict) -> tuple[bool, str]:
        """R1-5: 检查熔断条件。

        Returns:
            (is_triggered, reason)
        """
        # 连续亏损
        cl = int(account.get("consecutive_losses", 0))
        if cl >= 3:
            return True, f"连续亏损{cl}次≥3次"
        # 日亏超限
        daily = float(account.get("daily_pnl", 0) or account.get("daily_loss", 0) or 0)
        total = float(account.get("total_asset", 1) or 1)
        if total > 0 and daily / total < -0.02:
            return True, f"日亏{daily/total*100:.1f}%>2%"
        return False, ""

    def check_concentration(self, positions: dict, new_symbol: str, new_value: float) -> tuple[bool, str]:
        """R1-5: 检查集中度。

        Returns:
            (pass, reason)
        """
        total_existing = sum(
            float(p.get("market_value", 0) or
                  p.get("last_price", p.get("avg_cost", 0)) * p.get("qty", p.get("quantity", 0)))
            for p in (positions.values() if isinstance(positions, dict) else positions)
        )
        new_pct = new_value / (total_existing + new_value) * 100 if (total_existing + new_value) > 0 else 0
        if new_pct > 50:
            return False, f"集中度{new_pct:.0f}%>硬上限50%"
        if new_pct > 30:
            logger.warning(f"集中度{new_pct:.0f}%超过建议线30%")
        return True, ""

    # ═══════════════════════════════════════════════════════
    #  S1-1/2/3: 多策略信号入口
    # ═══════════════════════════════════════════════════════

    def check_buy_signal(
        self, stock_code: str, quote_cache: dict,
        fundamental_cache: dict = None, market_state: str = "unknown",
    ) -> dict | None:
        """FactorRegistry 驱动 — 自动迭代所有 active 策略。

        加新策略不改此方法。只需在 factor_registry.json 注册 + 写策略模块。
        """
        signals = []

        # 追涨 (原有, 不走Registry — 依赖_FACTOR_CACHE特殊格式)
        chase = self._check_chase_signal(stock_code, quote_cache)
        if chase:
            signals.append(chase)

        # ═══ Registry 驱动: 自动发现所有 active 策略 ═══
        # 策略模块映射: registry name → (module, function)
        STRATEGY_MODULES = {
            "defensive_v2": ("strategies.defensive_strategy", "generate_defensive_signal"),
            "chip_v2": ("strategies.chip_strategy", "generate_chip_signal"),
            # 退役策略不在此列表, 但代码保留
            # "low_absorb_v2": ("strategies.low_absorb_strategy", "generate_low_absorb_signal"),
            # "fund_v2": ("strategies.fund_flow_strategy", "generate_fund_flow_signal"),
        }

        try:
            from factor_registry import get_active_factors
            active = {f["name"] for f in get_active_factors()}
        except ImportError:
            active = set()

        if fundamental_cache is None:
            fundamental_cache = {}

        for factor_name, (mod_path, fn_name) in STRATEGY_MODULES.items():
            if factor_name not in active:
                continue  # 退役或未注册 → 跳过
            try:
                import importlib
                mod = importlib.import_module(mod_path)
                fn = getattr(mod, fn_name)
                sig = fn(stock_code, quote_cache, fundamental_cache) if factor_name == "defensive_v2" else fn(stock_code, quote_cache)
                if sig:
                    signals.append(sig)
            except ImportError:
                pass

        if not signals:
            return None

        # ═══ 用户构建策略 (策略构建器出品, 状态=sim) ═══
        try:
            from strategy_builder import get_active_user_strategies, evaluate_strategy
            from factor_registry import get_all_compute_fns
            user_strategies = get_active_user_strategies()
            if user_strategies:
                all_fns = get_all_compute_fns()
                for us in user_strategies[:5]:  # 最多5个用户策略并行
                    sig = evaluate_strategy(us, stock_code, all_fns)
                    if sig:
                        signals.append(sig)
        except ImportError:
            pass

        # 多策略投票: 按策略权重×信号分排序
        try:
            from strategy_weights import get_strategy_weights
            weights = get_strategy_weights(market_state)
            for s in signals:
                s["_weighted_score"] = s["score"] * weights.get(s["strategy"], 0.33)
            signals.sort(key=lambda x: x["_weighted_score"], reverse=True)
        except ImportError:
            signals.sort(key=lambda x: x["score"], reverse=True)

        best = signals[0]
        best.pop("_weighted_score", None)
        return best

    def _check_chase_signal(self, stock_code: str, quote_cache: dict) -> dict | None:
        """追涨策略信号 (原有因子体系)。兼容现有 _FACTOR_CACHE 格式。"""
        try:
            import app as _app
            cache = getattr(_app, '_FACTOR_CACHE', None)
            if not cache:
                return None
            for s in cache:
                sym = getattr(s, 'symbol', '')
                if sym.replace('sh', '').replace('sz', '') == stock_code.replace('sh', '').replace('sz', ''):
                    bs = getattr(s, 'buy_signal', 0) or 0
                    if bs < 3:
                        return None
                    price = getattr(s, 'close', 0) or 0
                    if price <= 0:
                        return None
                    return {
                        "strategy": "chase",
                        "signal": "buy",
                        "score": min(float(bs) * 20, 100.0),
                        "entry_price": price,
                        "stop_loss": round(price * 0.97, 2),
                        "take_profit": [round(price * 1.05, 2), round(price * 1.07, 2), round(price * 1.12, 2)],
                        "reason": f"追涨: 信号{bs}级",
                    }
        except Exception:
            pass
        return None

    # ── 原有属性 ──

    @property
    def name(self) -> str:
        """当前 Broker 名称。"""
        if self._broker and hasattr(self._broker, 'name'):
            return self._broker.name
        return type(self._broker).__name__ if self._broker else "none"

    # ── 原有属性 ──

    @property
    def rules(self) -> list[BaseRule]:
        return list(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)
