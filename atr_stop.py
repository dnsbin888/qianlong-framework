"""ATR动态止损 — 根据波动率自动调整止损线

ATR高(波动大): 止损放宽，避免被噪声震出
ATR低(波动小): 止损收紧，保护利润

默认: 2倍ATR止损
"""

import numpy as np


def calc_atr(high, low, close, period=14):
    """计算ATR"""
    if len(close) < period + 1:
        return None
    tr = []
    for i in range(1, len(close)):
        h, l, pc = high[i], low[i], close[i - 1]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    return float(np.mean(tr[-period:]))


def get_stop_price(symbol, stock_data, atr_multiplier=2.0, fallback_pct=-0.05):
    """计算ATR动态止损价位

    Args:
        symbol: 股票代码
        stock_data: {symbol: DataFrame}
        atr_multiplier: ATR倍数 (2=正常, 3=宽松, 1.5=严格)
        fallback_pct: ATR计算失败时的回退百分比止损

    Returns:
        dict: {"stop_price": 止损价, "stop_pct": 止损百分比, "atr": ATR值, "method": "atr"|"fixed"}
    """
    df = stock_data.get(symbol)
    if df is None or len(df) < 20:
        return {"stop_price": 0, "stop_pct": fallback_pct, "atr": 0, "method": "fixed"}

    try:
        high = df["high"].values[-20:]
        low = df["low"].values[-20:]
        close = df["close"].values[-20:]
        atr = calc_atr(high, low, close)
        if atr is None or atr <= 0:
            return {"stop_price": 0, "stop_pct": fallback_pct, "atr": 0, "method": "fixed"}

        last_close = float(close[-1])
        atr_pct = (atr * atr_multiplier) / last_close
        # 钳制: ATR止损在2%-8%之间
        atr_pct = max(0.02, min(0.08, atr_pct))
        stop_price = last_close * (1 - atr_pct)

        return {
            "stop_price": round(stop_price, 2),
            "stop_pct": round(-atr_pct, 4),
            "atr": round(atr, 2),
            "atr_pct": round(atr_pct * 100, 1),
            "method": "atr",
        }
    except Exception:
        return {"stop_price": 0, "stop_pct": fallback_pct, "atr": 0, "method": "fixed"}


def get_stop_config(symbol, stock_data, base_pct=-0.03, atr_multiplier=2.0):
    """获取推荐的止损配置（优先ATR，回退固定百分比）

    Returns:
        float: 止损百分比 (如 -0.035 = -3.5%)
    """
    result = get_stop_price(symbol, stock_data, atr_multiplier, base_pct)
    return result["stop_pct"]
