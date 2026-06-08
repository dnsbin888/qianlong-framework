"""Risk Engine — orchestrates risk rule chain evaluation.

All order requests must pass through the RiskEngine before reaching
the broker. The engine applies global rules (applied to all strategies)
and strategy-specific rules in a chain-of-responsibility pattern.

Any rule returning BLOCK causes immediate rejection.
"""

from __future__ import annotations

import logging
from typing import Any

from quant_framework.core.constants import RiskDecision
from quant_framework.engine.context import StrategyContext
from quant_framework.risk.rules import RiskResult, RiskRule

logger = logging.getLogger("quant_framework.risk")


class RiskEngine:
    """Central risk management engine.

    Maintains two tiers of risk rules:
    1. Global rules — apply to every order regardless of strategy
    2. Strategy rules — apply only to a specific strategy

    Rules are evaluated in order: global rules first, then strategy rules.
    The first BLOCK result terminates the chain immediately.

    Usage:
        engine = RiskEngine()
        engine.add_global_rule(MaxDrawdownRule(0.20))
        engine.add_global_rule(DailyLossLimitRule(max_daily_loss_pct=0.03))
        engine.add_global_rule(MarketCircuitBreakerRule("sh000300", level1_pct=0.03, level2_pct=0.05))
        engine.add_strategy_rule("grid_1", PositionLimitRule(0.30))

        result = engine.check(ctx, order_request)
        if result.decision == RiskDecision.ALLOW:
            broker.submit(order_request)
    """

    def __init__(self) -> None:
        self._global_rules: list[RiskRule] = []
        self._strategy_rules: dict[str, list[RiskRule]] = {}

    # ---- Rule management ----

    def add_global_rule(self, rule: RiskRule) -> None:
        """Add a rule that applies to all strategies.

        Args:
            rule: RiskRule instance.
        """
        self._global_rules.append(rule)
        logger.info("Global risk rule added: %s", rule.name)

    def remove_global_rule(self, rule_name: str) -> bool:
        """Remove a global rule by name.

        Returns True if found and removed.
        """
        for i, rule in enumerate(self._global_rules):
            if rule.name == rule_name:
                self._global_rules.pop(i)
                return True
        return False

    def add_strategy_rule(self, strategy_id: str, rule: RiskRule) -> None:
        """Add a rule that applies only to a specific strategy.

        Args:
            strategy_id: Strategy identifier.
            rule: RiskRule instance.
        """
        self._strategy_rules.setdefault(strategy_id, []).append(rule)
        logger.info("Strategy risk rule added for '%s': %s", strategy_id, rule.name)

    def remove_strategy_rule(self, strategy_id: str, rule_name: str) -> bool:
        """Remove a strategy-specific rule by name.

        Returns True if found and removed.
        """
        rules = self._strategy_rules.get(strategy_id, [])
        for i, rule in enumerate(rules):
            if rule.name == rule_name:
                rules.pop(i)
                return True
        return False

    # ---- Order checking ----

    def check(self, ctx: StrategyContext, order: Any) -> RiskResult:
        """Run an order request through the risk rule chain.

        Evaluates global rules first, then strategy-specific rules.
        Returns immediately on first BLOCK.

        Args:
            ctx: Strategy context (provides portfolio state).
            order: Order request object.

        Returns:
            RiskResult(ALLOW) if all rules pass,
            RiskResult(BLOCK) with reason on first violation,
            RiskResult(REDUCE) if a rule suggests reduction.
        """
        # Phase 1: Global rules
        for rule in self._global_rules:
            try:
                result = rule.check(ctx, order)
                if result.decision == RiskDecision.BLOCK:
                    logger.warning(
                        "Order blocked by global rule '%s': %s",
                        rule.name,
                        result.reason,
                    )
                    return result
            except Exception as e:
                logger.error("Global rule '%s' threw exception: %s", rule.name, e, exc_info=True)
                # Fail-safe: block on rule error
                return RiskResult.block(f"规则 '{rule.name}' 执行异常: {e}")

        # Phase 2: Strategy-specific rules
        strategy_id = ctx.strategy_id
        for rule in self._strategy_rules.get(strategy_id, []):
            try:
                result = rule.check(ctx, order)
                if result.decision == RiskDecision.BLOCK:
                    logger.warning(
                        "Order blocked by strategy rule '%s': %s",
                        rule.name,
                        result.reason,
                    )
                    return result
            except Exception as e:
                logger.error("Strategy rule '%s' threw exception: %s", rule.name, e, exc_info=True)
                return RiskResult.block(f"规则 '{rule.name}' 执行异常: {e}")

        return RiskResult.allow()

    # ---- Query ----

    def list_rules(self, strategy_id: str | None = None) -> list[str]:
        """List all active rule names.

        Args:
            strategy_id: If provided, include strategy-specific rules too.
        """
        names = [r.name for r in self._global_rules]
        if strategy_id:
            names.extend(r.name for r in self._strategy_rules.get(strategy_id, []))
        return names

    @property
    def global_rule_count(self) -> int:
        return len(self._global_rules)

    @property
    def total_rule_count(self) -> int:
        return self.global_rule_count + sum(len(v) for v in self._strategy_rules.values())
