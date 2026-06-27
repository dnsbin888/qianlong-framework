"""筹码策略 (V1-5验证: IC(5d)=+0.034, IC(20d)=+0.069)

核心因子: 获利盘比例 + 筹码集中度 + 换手率稳定性
适用: 全市场, 中长期 (5-20天)
"""

import pandas as pd, numpy as np
from typing import Optional

CONF = {
    "lookback": 60,
    "profit_min": 0.20,       # 获利盘最低比例
    "conc_max_cv": 1.0,       # 成交量CV上限(越低越集中)
    "market_cap_min": 50e8,   # 最低市值
    "stop_loss_pct": -0.03,
    "tp1": 0.05, "tp2": 0.07, "tp3": 0.12,
}


# 模块级缓存
_stock_data = None
def _get_stock_data():
    global _stock_data
    if _stock_data is None:
        from data_cache import get_stock_data
        _stock_data = get_stock_data()
    return _stock_data


def generate_chip_signal(stock_code: str, quote_cache: dict) -> Optional[dict]:
    """筹码策略: 高获利盘 + 高集中度 → 买入。

    V1-5: 全市场IC=+0.034(5d), 递增至+0.069(20d).
    """
    # 优先用 quote_cache，退到 data_cache
    ticker = quote_cache.get(stock_code, {})
    hist = ticker.get("history", [])
    if isinstance(hist, list) and len(hist) >= CONF["lookback"]:
        df = pd.DataFrame(hist).sort_values("date").tail(CONF["lookback"])
    else:
        # 从全市场缓存读取 (根治法: 不依赖 quote_cache)
        sd = _get_stock_data()
        if stock_code not in sd and "sh"+stock_code in sd: stock_code = "sh"+stock_code
        if stock_code not in sd and "sz"+stock_code in sd: stock_code = "sz"+stock_code
        df = sd.get(stock_code)
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 20:
            return None
        df = df.tail(CONF["lookback"])

    if len(df) < 20:
        return None

    c = df["close"].values
    v = df["volume"].values
    if c[-1] <= 0:
        return None

    # 1. 获利盘比例 (60日区间位置)
    h60, l60 = np.max(c), np.min(c)
    profit_ratio = (c[-1] - l60) / max(h60 - l60, 0.01)
    if profit_ratio < CONF["profit_min"]:
        return None

    # 2. 筹码集中度 (低CV = 高集中)
    vol_cv = np.std(v) / max(np.mean(v), 1)
    if vol_cv > CONF["conc_max_cv"]:
        return None

    # 3. 市值
    mkt_cap = ticker.get("market_cap", 0)
    if mkt_cap < CONF["market_cap_min"]:
        return None

    # 评分 (0-100)
    profit_score = min(40, profit_ratio * 40)
    conc_score = min(30, max(0, (1 - vol_cv) * 30))
    # 换手率稳定性
    turnover = v[-20:] / max(np.mean(v[-20:]), 1)
    stab_score = min(30, max(0, (1 - np.std(turnover)) * 30))

    score = profit_score + conc_score + stab_score
    entry_price = float(c[-1])

    return {
        "strategy": "chip",
        "signal": "buy",
        "score": round(min(score, 100.0), 2),
        "entry_price": entry_price,
        "stop_loss": round(entry_price * (1 + CONF["stop_loss_pct"]), 2),
        "take_profit": [
            round(entry_price * (1 + CONF["tp1"]), 2),
            round(entry_price * (1 + CONF["tp2"]), 2),
            round(entry_price * (1 + CONF["tp3"]), 2),
        ],
        "reason": f"筹码: 获利{profit_ratio*100:.0f}% 集中度{1-vol_cv:.2f}",
    }
