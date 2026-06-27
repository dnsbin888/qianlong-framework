"""防守策略 — 低波动+高股息 (蓝图 v3.0 S1-2)

适用: 熊市 / 震荡市
逻辑: 低波动 + 高股息 → 防御性持仓
持仓: 7-15天 | 止损: -3% | 止盈: 3%/5%/8% (保守)
"""

import pandas as pd
import numpy as np
from typing import Optional

# ═══ 可配置参数 ═══
CONF = {
    "volatility_max": 0.25,        # 年化波动率上限
    "dividend_yield_min": 0.03,    # 最低股息率
    "trailing_7d_min": 0.0,        # 近7日最低收益
    "change_pct_max": 9.5,         # 排除涨停
    "market_cap_min": 100e8,       # 最小市值(100亿)
    "high_vol_industries": ["光伏", "半导体", "新能源汽车", "AI", "军工"],
    "lookback": 60,                # 回看天数
    "stop_loss_pct": -0.03,
    "tp1": 0.03, "tp2": 0.05, "tp3": 0.08,  # 保守止盈
}


def generate_defensive_signal(
    stock_code: str,
    quote_cache: dict,
    fundamental_cache: dict = None,
) -> Optional[dict]:
    """生成防守策略买入信号。

    Args:
        stock_code: 股票代码
        quote_cache: 行情缓存 {code: {history, market_cap}}
        fundamental_cache: 基本面缓存 {code: {dividend_yield, pe_ratio, industry}}

    Returns:
        None 或 {strategy, signal, score, entry_price, stop_loss, take_profit, reason}
    """
    if fundamental_cache is None:
        fundamental_cache = {}

    ticker = quote_cache.get(stock_code, {})
    hist = ticker.get("history", [])
    if len(hist) < CONF["lookback"]:
        return None

    df = pd.DataFrame(hist).sort_values("date").tail(CONF["lookback"])
    if len(df) < 20:
        return None

    # 技术指标
    df["returns"] = df["close"].pct_change()
    df["volatility_20d"] = df["returns"].rolling(20).std() * np.sqrt(252)

    latest = df.iloc[-1]

    # 基本面
    fund = fundamental_cache.get(stock_code, {})
    dividend_yield = float(fund.get("dividend_yield", 0) or 0)
    pe_ratio = float(fund.get("pe_ratio", 0) or 0)
    industry = str(fund.get("industry", "") or "")

    # 条件判断
    cond1 = float(latest["volatility_20d"]) < CONF["volatility_max"] if not pd.isna(latest["volatility_20d"]) else False
    cond2 = dividend_yield > CONF["dividend_yield_min"]
    trailing_7d = (float(latest["close"]) - float(df.iloc[-7]["close"])) / max(float(df.iloc[-7]["close"]), 0.01) if len(df) >= 7 else 0
    cond3 = trailing_7d >= CONF["trailing_7d_min"]
    cond4 = float(latest.get("change_pct", 0) or 0) < CONF["change_pct_max"]
    market_cap = ticker.get("market_cap", 0)
    cond5 = market_cap > CONF["market_cap_min"]
    cond6 = industry not in CONF["high_vol_industries"]

    if not (cond1 and cond2 and cond3 and cond4 and cond5 and cond6):
        return None

    # 信号强度 (0-100)
    vol_val = float(latest["volatility_20d"]) if not pd.isna(latest["volatility_20d"]) else 0.25
    score = 0.0
    score += min(40.0, dividend_yield * 1000)             # 股息率
    score += min(30.0, (CONF["volatility_max"] - vol_val) * 120)  # 低波动
    score += min(20.0, max(0, trailing_7d * 200))         # 近期收益
    if pe_ratio > 0:
        score += min(10.0, max(0, 10 - pe_ratio * 0.1))   # PE低位

    entry_price = float(latest["close"])
    return {
        "strategy": "defensive",
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
            f"防守: 股息{dividend_yield*100:.1f}% "
            f"波动{vol_val*100:.1f}% 7d收益{trailing_7d*100:.1f}%"
        ),
    }
