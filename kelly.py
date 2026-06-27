"""Kelly仓位管理 — 根据胜率和盈亏比动态计算最优仓位

Kelly公式: f* = (p * b - q) / b  where p=胜率, b=盈亏比(avg_win/avg_loss), q=1-p
简化形式: f* = win_rate - (1 - win_rate) / profit_factor

保守做法: 使用 Half-Kelly (f*/2) 控制波动

用法:
    k = KellyCriterion.from_paper_account()
    pct = k.get_position_pct(signal_strength=3)  # -> 0.25 (25%)
"""

import json, os


class KellyCriterion:
    """Kelly仓位计算器"""

    def __init__(self, win_rate: float, profit_factor: float, n_trades: int = 0):
        """
        Args:
            win_rate: 胜率 (0-1)
            profit_factor: 盈亏比 (avg_win / avg_loss)
            n_trades: 交易次数（样本不足时降低激进程度）
        """
        self.win_rate = max(0.1, min(0.9, win_rate))  # 钳制
        self.profit_factor = max(0.5, min(10, profit_factor))
        self.n_trades = n_trades

    @classmethod
    def from_paper_account(cls):
        """从模拟盘回测指标构建"""
        try:
            pa = r"D:\quant_framework\paper_account.json"
            if not os.path.exists(pa):
                return cls(0.45, 1.5, 0)  # 默认保守值

            with open(pa) as f:
                paper = json.load(f)

            # 从交易记录计算
            trades = paper.get("trade_log", [])
            sells = [t for t in trades if t.get("side") == "sell"]

            if len(sells) < 10:
                # 样本不足，从backtest缓存取
                return cls._from_backtest()

            # 计算胜率和盈亏比
            wins = []
            losses = []
            buy_queue = {}
            for t in trades:
                if t.get("side") == "buy":
                    sym = t.get("symbol", "")
                    buy_queue[sym] = t
                elif t.get("side") == "sell":
                    sym = t.get("symbol", "")
                    rev = t.get("revenue", 0)
                    buy = buy_queue.pop(sym, {})
                    cost = buy.get("cost", rev * 0.9) if buy else rev * 0.9
                    pnl = rev - cost
                    if pnl > 0:
                        wins.append(pnl / max(cost, 1))
                    else:
                        losses.append(abs(pnl) / max(cost, 1))

            n = len(wins) + len(losses)
            if n < 5:
                return cls._from_backtest()

            wr = len(wins) / n
            avg_win = sum(wins) / len(wins) if wins else 0.01
            avg_loss = sum(losses) / len(losses) if losses else 0.01
            pf = avg_win / avg_loss if avg_loss > 0 else 1.0

            return cls(wr, pf, n)

        except Exception:
            return cls(0.45, 1.5, 0)

    @classmethod
    def _from_backtest(cls):
        """从回测缓存获取指标（仅用于胜率/盈亏比, n_trades=0 强制学习期）"""
        try:
            bt = r"D:\quant_web\backtest_cache.json"
            if os.path.exists(bt):
                with open(bt) as f:
                    cache = json.load(f)
                if cache and len(cache) > 0:
                    latest = cache[0]
                    metrics = latest.get("metrics", {})
                    wr = metrics.get("win_rate", 0.45)
                    pf = metrics.get("profit_factor", 1.5)
                    return cls(wr, pf, n_trades=0)  # n=0 → 强制学习期用固定比例
        except Exception:
            pass
        return cls(0.45, 1.5, 0)

    @property
    def full_kelly(self) -> float:
        """全Kelly比例"""
        if self.profit_factor <= 0:
            return 0.05
        k = self.win_rate - (1 - self.win_rate) / self.profit_factor
        return max(0.01, min(0.50, k))

    @property
    def half_kelly(self) -> float:
        """半Kelly比例（推荐用于实盘）"""
        return self.full_kelly / 2

    def get_position_pct(self, signal_strength: int = 3, kelly_type: str = "half") -> float:
        """根据信号强度计算仓位比例

        Args:
            signal_strength: buy_signal 0-5级
            kelly_type: "full" | "half" | "quarter"

        Returns:
            float: 仓位比例 (如0.25=25%)
        """
        # FIX: 样本<5笔时用固定比例，避免Kelly无数据时仓位过小
        if self.n_trades < 5:
            fixed = {2: 0.20, 3: 0.25, 4: 0.33, 5: 0.50}
            return fixed.get(signal_strength, 0.25)

        if kelly_type == "full":
            base = self.full_kelly
        elif kelly_type == "quarter":
            base = self.full_kelly / 4
        else:
            base = self.half_kelly  # 默认半Kelly

        # 样本不足时额外打折（5-20笔时渐进过渡）
        if self.n_trades < 20:
            base *= (0.5 + 0.5 * self.n_trades / 20)

        # 按信号强度调整
        # 信号3级=基准, 4级=1.3倍, 5级=1.6倍, 2级=0.7倍, 1级=0.4倍
        multipliers = {1: 0.4, 2: 0.7, 3: 1.0, 4: 1.3, 5: 1.6}
        mult = multipliers.get(signal_strength, 1.0)

        # 上限保护
        pct = base * mult
        max_by_signal = {2: 0.20, 3: 0.25, 4: 0.33, 5: 0.50}  # 硬上限
        cap = max_by_signal.get(signal_strength, 0.20)

        return round(min(pct, cap), 4)

    def summary(self) -> dict:
        return {
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 2),
            "n_trades": self.n_trades,
            "full_kelly": round(self.full_kelly, 4),
            "half_kelly": round(self.half_kelly, 4),
            "position_lv3": self.get_position_pct(3),
            "position_lv4": self.get_position_pct(4),
            "position_lv5": self.get_position_pct(5),
        }
