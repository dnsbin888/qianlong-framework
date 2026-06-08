"""交易规则引擎 — 可组合的自动交易规则。

P0-模拟-01: 从 PaperAccount.auto_trade_check() 抽取的独立规则类。
每个规则实现 check() 方法，返回 RuleAction 或 None。

用法:
  from quant_framework.execution.rules import RuleEngine
  from quant_framework.execution.rules.stop_loss import AutoStopLossRule
  from quant_framework.execution.rules.trailing_stop import AutoTrailingStopRule

  engine = RuleEngine()
  engine.add_rule(AutoStopLossRule(threshold=-0.05))
  engine.add_rule(AutoTrailingStopRule(tier=1, profit_pct=0.05, trail_pct=0.01))
  actions = engine.check_all(positions, market_data, context)
"""

from quant_framework.execution.rules.engine import RuleEngine, RuleAction
from quant_framework.execution.rules.base import BaseRule
from quant_framework.execution.rules.stop_loss import AutoStopLossRule
from quant_framework.execution.rules.trailing_stop import AutoTrailingStopRule
from quant_framework.execution.rules.circuit_breaker import CircuitBreakerRule
from quant_framework.execution.rules.daily_limits import MaxDailyTradesRule, DailyLossLimitRule
from quant_framework.execution.rules.signal_filter import SignalQualityFilter
from quant_framework.execution.rules.position_sizing import PositionSizingRule

__all__ = [
    "RuleEngine", "RuleAction", "BaseRule",
    "AutoStopLossRule", "AutoTrailingStopRule",
    "CircuitBreakerRule", "MaxDailyTradesRule", "DailyLossLimitRule",
    "SignalQualityFilter", "PositionSizingRule",
]
