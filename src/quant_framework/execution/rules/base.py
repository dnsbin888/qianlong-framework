"""规则引擎基类 + 动作定义。

P0-模拟-01: 所有交易规则的公共接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class RuleAction:
    """规则检查结果 — 表示需要执行的操作。

    Attributes:
        action: 操作类型 ("sell", "buy", "reduce", "reject", "adjust")
        symbol: 股票代码
        qty: 操作数量 (0=全部)
        price: 执行价格
        reason: 触发原因
        meta: 额外信息
    """
    action: str
    symbol: str = ""
    qty: int = 0
    price: float = 0.0
    reason: str = ""
    meta: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.action)


class BaseRule:
    """交易规则基类。

    子类实现 check() 方法:
      - 返回 RuleAction 表示触发规则需要执行操作
      - 返回 None 表示未触发

    每个规则的上下文 (context dict) 可包含:
      - positions: 当前持仓列表
      - signals: 买入信号列表
      - daily_trade_count: 当日已成交数
      - daily_loss_total: 当日累计亏损
      - cash: 可用资金
      - config: 规则配置
    """

    def check(self, position: dict | None, market_data: dict, context: dict) -> Optional[RuleAction]:
        """检查规则是否触发。

        Args:
            position: 单条持仓记录 (持仓卖出规则时传入) 或 None (全局规则)
            market_data: 市场数据 {price, high, low, volume, ...}
            context: 上下文 {positions, cash, daily_trade_count, daily_loss_total, config, ...}

        Returns:
            RuleAction 或 None
        """
        raise NotImplementedError
