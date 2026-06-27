"""反转策略 — 超跌反弹检测"""
import numpy as np
from .base import BaseStrategy


class ReversalStrategy(BaseStrategy):
    name = "反转策略"
    description = "5日跌幅<-5% + RSI<30 + 今日收阳 + power_score≥35"

    def generate(self, factor_cache, stock_data) -> list:
        candidates = []
        for s in (factor_cache or [])[:500]:
            sym = getattr(s, "symbol", "")
            bs = getattr(s, "buy_signal", 0) or 0
            ps = getattr(s, "power_score", 0) or 0
            close = getattr(s, "close", 0) or 0
            chg = getattr(s, "change_pct", 0) or 0

            if bs < 2 or ps < 35 or close <= 0:
                continue

            df = stock_data.get(sym)
            if df is None or len(df) < 15:
                continue
            try:
                close_arr = df["close"].values
                # 5日跌幅 < -5%
                mom_5d = (close_arr[-1] / close_arr[-6] - 1) * 100 if len(close_arr) >= 6 else 0
                if mom_5d > -5:
                    continue
                # RSI < 30 (超跌)
                diffs = np.diff(close_arr[-15:])
                gains = np.sum(diffs[diffs > 0]) if len(diffs[diffs > 0]) > 0 else 0
                losses = abs(np.sum(diffs[diffs < 0])) if len(diffs[diffs < 0]) > 0 else 1
                rs = gains / losses if losses > 0 else 1
                rsi = 100 - 100 / (1 + rs)
                if rsi > 30:
                    continue
                # 今日收阳
                if chg <= 0:
                    continue
            except Exception:
                continue

            candidates.append({
                "symbol": sym, "price": close, "power_score": ps,
                "buy_signal": bs, "change_pct": round(mom_5d, 2),
                "rsi": round(rsi, 1),
            })

        candidates.sort(key=lambda x: abs(x.get("change_pct", 0)))
        return candidates
