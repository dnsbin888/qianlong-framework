"""因子评分策略 — 已有策略，按power_score排序"""
from .base import BaseStrategy


class FactorScoreStrategy(BaseStrategy):
    name = "因子评分"
    description = "按power_score综合评分排序，多因子共振"

    def generate(self, factor_cache, stock_data) -> list:
        candidates = []
        for s in (factor_cache or [])[:300]:
            sym = getattr(s, "symbol", "")
            ps = getattr(s, "power_score", 0) or 0
            bs = getattr(s, "buy_signal", 0) or 0
            close = getattr(s, "close", 0) or 0

            if ps < 48 or bs < 3 or close <= 0:
                continue
            if sym not in stock_data:
                continue

            candidates.append({
                "symbol": sym,
                "price": close,
                "power_score": ps,
                "buy_signal": bs,
                "change_pct": getattr(s, "change_pct", 0) or 0,
                "vol_ratio": getattr(s, "vol_ratio", 1) or 1,
            })

        candidates.sort(key=lambda x: -x["power_score"])
        return candidates
