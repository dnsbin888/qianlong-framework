"""多策略组合架构 — 策略注册/调度/资金分配

支持多策略并行，按权重分配仓位，统一风控。

策略注册:
    mgr = StrategyManager()
    mgr.register(FactorScoreStrategy(), weight=0.4)
    mgr.register(MomentumBreakStrategy(), weight=0.3)
    mgr.register(ReversalStrategy(), weight=0.3)

使用:
    candidates = mgr.generate_signals(factor_cache, stock_data)
    # -> [{"symbol": ..., "price": ..., "signal": ..., "strategy": ..., "weight": ...}]
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from strategies.base import BaseStrategy
from strategies.factor_score import FactorScoreStrategy
from strategies.momentum import MomentumBreakStrategy
from strategies.reversal import ReversalStrategy


class StrategyManager:
    """策略管理器 — 统一注册、调度、资金分配"""

    def __init__(self):
        self._strategies = []  # [(strategy_instance, weight)]

    def register(self, strategy: BaseStrategy, weight: float = 1.0):
        """注册策略及权重"""
        self._strategies.append((strategy, weight))
        print(f"[Strategy] 已注册: {strategy.name} (权重={weight:.0%})")
        return self

    def get_default(self):
        """加载策略组合，从配置读取权重"""
        if not self._strategies:
            weights = {"因子评分": 0.4, "动量突破": 0.3, "反转策略": 0.3}  # 默认
            try:
                cfg = r"D:\quant_framework\live_trader_config.json"
                if os.path.exists(cfg):
                    w = json.load(open(cfg, "r")).get("strategy_weights", {})
                    if w: weights = w
            except: pass
            self.register(FactorScoreStrategy(), weight=weights.get("因子评分", 0.4))
            self.register(MomentumBreakStrategy(), weight=weights.get("动量突破", 0.3))
            self.register(ReversalStrategy(), weight=weights.get("反转策略", 0.3))
        return self

    def generate_signals(self, factor_cache, stock_data, max_total=10):
        """生成多策略聚合信号

        Returns:
            list[dict]: 去重后的买入候选，含 strategy 标识和 allocated_weight
        """
        all_signals = []
        for strategy, weight in self._strategies:
            try:
                signals = strategy.generate(factor_cache, stock_data)
                for sig in signals:
                    sig["strategy"] = strategy.name
                    sig["strategy_weight"] = weight
                    sig["allocated_weight"] = weight / len(self._strategies)
                all_signals.append((signals, weight))
                if signals:
                    print(f"[Strategy] {strategy.name}: {len(signals)}只候选")
            except Exception as e:
                print(f"[Strategy] {strategy.name} 异常: {e}")

        # 合并去重（同一股票取最高评分策略）
        merged = {}
        total_weight = sum(w for _, w in self._strategies)
        for signals, weight in all_signals:
            n = max(1, len(signals))
            take = max(1, int(max_total * weight / total_weight))
            for sig in signals[:take]:
                sym = sig.get("symbol", "")
                score = sig.get("power_score", 0)
                if sym not in merged or score > merged[sym].get("power_score", 0):
                    merged[sym] = sig

        result = sorted(merged.values(), key=lambda x: -x.get("power_score", 0))
        return result[:max_total]

    def get_status(self):
        return [
            {"name": s.name, "weight": w, "enabled": True}
            for s, w in self._strategies
        ]

    def get_strategies(self):
        """别名，兼容验收脚本"""
        return self.get_status()

    def adapt_to_market(self):
        """P3-2: 根据市场环境自适应调整策略权重"""
        try:
            from market_sense import get_state
            ms = get_state()
            state = ms.get("state", "oscillate")
            weights = {
                "bull": {"因子评分": 0.3, "动量突破": 0.6, "反转策略": 0.1},
                "bear": {"因子评分": 0.3, "动量突破": 0.2, "反转策略": 0.5},
                "oscillate": {"因子评分": 0.5, "动量突破": 0.25, "反转策略": 0.25},
                "extreme": {"因子评分": 0.15, "动量突破": 0.15, "反转策略": 0.15},
            }.get(state, {"因子评分": 0.4, "动量突破": 0.3, "反转策略": 0.3})

            # 更新策略权重
            for s, w in self._strategies:
                if s.name in weights:
                    old_w = w
                    w = weights[s.name]
                    if old_w != w:
                        print(f"[Strategy] 市场{state}: {s.name} {old_w:.0%}→{w:.0%}")

            # 保存到配置
            cfg_file = r"D:\quant_framework\live_trader_config.json"
            if os.path.exists(cfg_file):
                cfg = json.load(open(cfg_file, "r"))
                cfg["strategy_weights"] = weights
                json.dump(cfg, open(cfg_file, "w"), ensure_ascii=False, indent=2)
                from market_sense import state_label
                from dingtalk_alerts import send_alert
                send_alert(f"🔄 策略自适应: {state_label(state)}",
                           f"因子{weights['因子评分']:.0%} 动量{weights['动量突破']:.0%} 反转{weights['反转策略']:.0%}", "info")
            return weights
        except Exception as e:
            print(f"[Strategy] 自适应失败: {e}")
            return {}


# 全局单例
mgr = StrategyManager()
