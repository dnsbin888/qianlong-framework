"""动量突破策略 — 近期涨幅+成交量放大"""
import numpy as np
from .base import BaseStrategy


class MomentumBreakStrategy(BaseStrategy):
    name = "动量突破"
    description = "5日涨幅>3% + 成交量>5日均量1.5倍 + power_score≥35"

    def generate(self, factor_cache, stock_data) -> list:
        candidates = []
        for s in (factor_cache or [])[:500]:
            sym = getattr(s, "symbol", "")
            bs = getattr(s, "buy_signal", 0) or 0
            ps = getattr(s, "power_score", 0) or 0
            close = getattr(s, "close", 0) or 0
            vol_ratio = getattr(s, "vol_ratio", 0) or 0

            if bs < 2 or ps < 35 or close <= 0 or vol_ratio < 1.2:
                continue

            df = stock_data.get(sym)
            if df is None or len(df) < 20:
                continue
            try:
                close_arr = df["close"].values
                mom_5d = (close_arr[-1] / close_arr[-6] - 1) * 100 if len(close_arr) >= 6 else 0
                if mom_5d < 3:
                    continue
            except Exception:
                continue

            candidates.append({
                "symbol": sym, "price": close, "power_score": ps,
                "buy_signal": bs, "change_pct": round(mom_5d, 2),
                "vol_ratio": vol_ratio,
            })

        candidates.sort(key=lambda x: -x.get("change_pct", 0))
        return candidates
