"""资金流向策略 (V1-5验证: IC(5d)=-0.063 → 反转后 +0.063)

核心因子: 量价背离度 (反转: 买低背离 = 机构吸筹信号)
适用: 全市场, 短中期 (3-10天)
"""

import pandas as pd, numpy as np
from typing import Optional

CONF = {
    "lookback": 60,
    "flow_threshold": 0,       # 反转: 买负流向 (机构吸筹)
    "big_order_threshold": 0,  # 大单阈值
    "market_cap_min": 50e8,
    "stop_loss_pct": -0.03,
    "tp1": 0.05, "tp2": 0.08, "tp3": 0.12,
}


def generate_fund_flow_signal(stock_code: str, quote_cache: dict) -> Optional[dict]:
    """资金流向策略 (反转版): 低fund_score → 机构吸筹中 → 买入。

    V1-5发现: fund_score IC=-0.063 → 高流向分=机构出货, 低流向分=机构吸筹.
    反转后买"机构在买但市场未察觉"的股票.
    """
    ticker = quote_cache.get(stock_code, {})
    hist = ticker.get("history", [])
    if len(hist) < CONF["lookback"]:
        return None

    df = pd.DataFrame(hist).sort_values("date").tail(CONF["lookback"])
    if len(df) < 20:
        return None

    c = df["close"].values
    v = df["volume"].values
    if c[-1] <= 0:
        return None

    # 1. 量价背离: 价缩量增 = 机构低位吸筹
    rets = np.diff(c[-10:]) / np.maximum(c[-11:-1], 0.01)
    vol_chg = np.diff(v[-10:]) / np.maximum(v[-11:-1], 1)
    # 买入信号: 价跌量增 (吸筹) 或 价稳量缩 (锁仓)
    flow_raw = np.mean(np.sign(rets) * np.sign(vol_chg) * np.abs(rets))
    if flow_raw > CONF["flow_threshold"]:  # 反转: 排除高流向(出货)
        return None

    # 2. 大单方向: 跌日放量(吸筹) > 涨日放量(出货)
    dn_days = rets < 0
    up_days = rets > 0
    dn_vol = np.mean(vol_chg[dn_days]) if dn_days.any() else 0
    up_vol = np.mean(vol_chg[up_days]) if up_days.any() else 0
    if dn_vol - up_vol < CONF["big_order_threshold"]:
        return None  # 反转: 跌日放量不够说明吸筹不积极

    # 3. 市值
    if ticker.get("market_cap", 0) < CONF["market_cap_min"]:
        return None

    # 评分 (反转: flow越低越好, big_order越高越好)
    flow_score = min(40, max(0, -flow_raw * 200 + 20))
    big_score = min(30, max(0, (dn_vol - up_vol) * 150))
    # 近期微跌最好(吸筹期间不应大涨)
    ret_5d = (c[-1] - c[-6]) / max(c[-6], 0.01) if len(c) >= 6 else 0
    trend_score = min(30, max(0, (-ret_5d + 0.05) * 300))

    score = flow_score + big_score + trend_score
    entry_price = float(c[-1])

    return {
        "strategy": "fund_flow",
        "signal": "buy",
        "score": round(min(score, 100.0), 2),
        "entry_price": entry_price,
        "stop_loss": round(entry_price * (1 + CONF["stop_loss_pct"]), 2),
        "take_profit": [
            round(entry_price * (1 + CONF["tp1"]), 2),
            round(entry_price * (1 + CONF["tp2"]), 2),
            round(entry_price * (1 + CONF["tp3"]), 2),
        ],
        "reason": f"资金反转: 背离{flow_raw:.3f} 跌放{abs(dn_vol):.2f}",
    }
