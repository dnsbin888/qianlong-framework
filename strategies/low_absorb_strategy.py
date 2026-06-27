"""低吸策略 v2 — V1-5修正版 (蓝图 v3.0 S1-1)

V1-5发现: 全市场IC=-0.064(20d) — 高因子分→后续继续下跌 (接飞刀效应)。
修正: 新增反弹确认条件 (price > MA5), 过滤未止跌个股。
逻辑: 超跌 → 放量 → 反弹确认 → 买入
"""

import pandas as pd
import numpy as np
from typing import Optional

# ═══ 可配置参数 ═══
CONF = {
    "ma20_discount": 0.90,         # 跌破MA20的折扣率
    "change_5d_threshold": -0.10,  # 5日累计跌幅阈值
    "volume_ratio": 1.3,           # 放量倍数 (从1.5降到1.3, 配合反弹确认)
    "change_pct_min": -9.5,        # 排除跌停
    "market_cap_min": 50e8,        # 最小市值(50亿)
    "rsi_oversold": 35,            # RSI超卖阈值 (从30放宽到35)
    "bounce_ma5": True,            # V1-5修正: 价格必须站上MA5 (反弹确认)
    "lookback": 60,                # 回看天数
    "stop_loss_pct": -0.03,        # 止损
    "tp1": 0.05, "tp2": 0.07, "tp3": 0.12,  # 三级止盈
}


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """计算RSI指标。"""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def generate_low_absorb_signal(
    stock_code: str,
    quote_cache: dict,
    lookback: int = None,
) -> Optional[dict]:
    """生成低吸策略买入信号。

    Args:
        stock_code: 股票代码 (如 '600000')
        quote_cache: 行情缓存 {code: {history: [{date,close,volume,change_pct},...], market_cap}}
        lookback: 回看天数 (默认 CONF['lookback'])

    Returns:
        None 或 {strategy, signal, score, entry_price, stop_loss, take_profit, reason}
    """
    if lookback is None:
        lookback = CONF["lookback"]

    ticker = quote_cache.get(stock_code, {})
    hist = ticker.get("history", [])
    if len(hist) < lookback:
        return None

    df = pd.DataFrame(hist).sort_values("date").tail(lookback)
    if len(df) < 20:
        return None

    # 技术指标
    df["ma5"] = df["close"].rolling(5).mean()
    df["ma20"] = df["close"].rolling(20).mean()
    df["volume_ma20"] = df["volume"].rolling(20).mean()
    df["rsi"] = _compute_rsi(df["close"], 14)

    latest = df.iloc[-1]
    prev_5d_start = df.iloc[-6]["close"] if len(df) >= 6 else df.iloc[0]["close"]

    # ═══ V1-5修正: 6条件 (新增反弹确认) ═══
    cond1 = latest["close"] < latest["ma20"] * CONF["ma20_discount"]
    change_5d = (latest["close"] - prev_5d_start) / max(prev_5d_start, 0.01)
    cond2 = change_5d < CONF["change_5d_threshold"]
    cond3 = latest["volume"] > latest["volume_ma20"] * CONF["volume_ratio"]
    cond4 = latest.get("change_pct", 0) > CONF["change_pct_min"]
    market_cap = ticker.get("market_cap", 0)
    cond5 = market_cap > CONF["market_cap_min"]
    # V1-5: 反弹确认 — 价格站上MA5 (过滤"接飞刀")
    cond6 = not CONF["bounce_ma5"] or float(latest["close"]) > float(latest["ma5"]) if not pd.isna(latest["ma5"]) else True

    if not (cond1 and cond2 and cond3 and cond4 and cond5 and cond6):
        return None

    # V1-5修正: 信号强度评分 (反弹确认加权重)
    rsi_val = float(latest["rsi"]) if not pd.isna(latest["rsi"]) else 50
    vol_ratio = float(latest["volume"]) / max(float(latest["volume_ma20"]), 1)
    bounce_strength = float(latest["close"]) / max(float(latest["ma5"]), 0.01) - 1 if not pd.isna(latest["ma5"]) else 0

    # V1-5修正: 反弹确认加权重(正向IC预期), 超跌分降权重(负向IC)
    score = 0.0
    score += min(30.0, max(0, bounce_strength * 600))     # 反弹越强分越高 (主权重)
    score += min(25.0, abs(change_5d) * 250)               # 跌幅 (降权重, 从35→25)
    score += min(25.0, (vol_ratio - 1) * 50)               # 放量
    score += min(10.0, max(0, (CONF["rsi_oversold"] - rsi_val) * 0.67))  # RSI (降权重 20→10)
    ma20_dist = 1 - latest["close"] / max(latest["ma20"], 0.01)
    score += min(10.0, max(0, ma20_dist * 200))            # MA20距离 (降权重 20→10)

    entry_price = float(latest["close"])
    return {
        "strategy": "low_absorb",
        "signal": "buy",
        "score": round(min(score, 100.0), 2),
        "entry_price": entry_price,
        "stop_loss": round(entry_price * (1 + CONF["stop_loss_pct"]), 2),
        "take_profit": [
            round(entry_price * (1 + CONF["tp1"]), 2),
            round(entry_price * (1 + CONF["tp2"]), 2),
            round(entry_price * (1 + CONF["tp3"]), 2),
        ],
        "reason": (
            f"低吸v2: 5d跌{change_5d*100:.1f}% "
            f"反弹+{bounce_strength*100:.1f}% 量比{vol_ratio:.1f}x"
        ),
    }
