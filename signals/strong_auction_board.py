"""强筹打板 v1.0 — 竞价抢筹 + 涨停封板 (日线版)
对标: 游资竞价战法 2026前沿
逻辑: 竞价抢筹(高开+放量)→涨停封板→博次日溢价
日线代理: 高开>2% + 量比>2 + 收盘涨停 = 强筹打板
"""
import sys, os, json, numpy as np


def generate_strong_auction_board(sd, factor_cache=None):
    """日线强筹打板候选 — 竞价抢筹当天封板

    条件:
      1. 高开 > 2% (竞价抢筹)
      2. 量比 > 2 (放量确认)
      3. 收盘涨停 (封板成功)
      4. 非ST, 市值30-500亿
      5. 换手5-40%
    """
    candidates = []
    for sym, df in sd.items():
        try:
            c = df['close'].values
            o = df['open'].values
            v = df['volume'].values
            h = df['high'].values
            if len(c) < 22:
                continue
            close = float(c[-1])
            open_p = float(o[-1])
            prev_close = float(c[-2])
            high = float(h[-1])

            if 'ST' in sym.upper():
                continue

            # 涨停价
            lim_pct = 0.098
            if sym.startswith(('sh68','sz30')): lim_pct = 0.20
            if sym.startswith(('bj8','bj4')): lim_pct = 0.30
            board_price = round(prev_close * (1 + lim_pct), 2)

            # ══ 1. 竞价抢筹: 高开>2% ══
            gap_pct = (open_p / prev_close - 1) * 100
            if gap_pct < 2.0:
                continue

            # ══ 2. 放量确认: 量比>2 ══
            yest_vol = v[-1]
            avg_vol = float(np.mean(v[-6:-1]))
            vol_ratio = yest_vol / max(avg_vol, 1)
            if vol_ratio < 2.0:
                continue

            # ══ 3. 封板成功: 收盘≈涨停价 ══
            if abs(close - board_price) / max(board_price, 0.01) > 0.005:
                continue
            if high < board_price * 0.995:  # 没碰到涨停
                continue

            # 换手率
            if 'outstanding' in df.columns:
                out = float(df['outstanding'].values[-1])
                if out > 0:
                    turnover = yest_vol / out * 100
                    if turnover < 5 or turnover > 40:
                        continue

            # 市值
            if 'outstanding' in df.columns:
                _cap = out * close / 1e8
                if _cap < 30 or _cap > 500:
                    continue

            # 评分
            score = 50
            if 3.0 <= gap_pct <= 6.0:
                score += 20  # 最佳抢筹区间
            elif gap_pct <= 9.0:
                score += 10
            if vol_ratio >= 3.0:
                score += 20  # 强放量
            elif vol_ratio >= 2.0:
                score += 12
            if open_p == board_price:  # 一字板
                score += 25
            elif close == open_p:  # 开盘即封
                score += 15

            candidates.append({
                "symbol": sym,
                "score": round(min(100, score), 1),
                "action": "buy",
                "close": round(close, 2),
                "stop_loss": round(close * (1 - 0.04), 2),
                "take_profit": round(close * 1.10, 2),
                "reason": f"强筹打板 gap={gap_pct:.1f}% vol={vol_ratio:.1f}",
                "hold_days": 1,
                "strategy_id": "strong_auction_board",
                "strategy_type": "pattern",
            })
        except Exception:
            continue

    candidates = [c for c in candidates if c["score"] >= 60]
    candidates.sort(key=lambda x: -x["score"])
    return candidates[:10]
