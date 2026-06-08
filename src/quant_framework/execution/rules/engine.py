"""规则引擎 — 组合多个规则，批量检查持仓/信号。

P0-模拟-01: 统一管理所有自动交易规则。
"""

from __future__ import annotations

from typing import Any, Optional

from quant_framework.execution.rules.base import BaseRule, RuleAction


class RuleEngine:
    """规则引擎 — 将所有规则应用于持仓和信号。

    检查顺序:
      1. 全局规则 (不依赖具体持仓) → 熔断/频率限制
      2. 持仓规则 (逐条检查) → 止损/止盈
      3. 信号规则 → 过滤/调整

    Usage:
        engine = RuleEngine()
        engine.add_rule(AutoStopLossRule(threshold=-0.05))
        engine.add_rule(AutoTrailingStopRule(tier=1, profit_pct=0.05))

        # 检查持仓
        actions = engine.check_positions(positions, market_data, context)

        # 检查买入许可
        can_buy = engine.can_buy(context)
    """

    def __init__(self):
        self._rules: list[BaseRule] = []
        self._position_rules: list[BaseRule] = []
        self._global_rules: list[BaseRule] = []

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

    @property
    def rules(self) -> list[BaseRule]:
        return list(self._rules)

    @property
    def rule_count(self) -> int:
        return len(self._rules)
