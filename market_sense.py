"""市场环境感知 — 每日识别牛/熊/震荡/极端行情"""
import os, json, pickle
import numpy as np
from datetime import datetime

STATE_FILE = r"D:\quant_web\data\market_state.json"


def detect():
    """检测当前市场状态

    Returns:
        {"state": "bull|bear|oscillate|extreme", "confidence": 0-1, "indicators": {...}}
    """
    # 加载指数数据 (P2: DataManager统一入口)
    inds = {"sh000300": "沪深300", "sz399006": "创业板指"}
    try:
        import sys as _ms_sys
        _ms_sys.path.insert(0, r"D:\quant_web")
        from data_loader import load_stock_data_from_cache
        sd = load_stock_data_from_cache() or {}
    except Exception:
        sd = {}

    scores = {"bull": 0, "bear": 0, "oscillate": 0, "extreme": 0}
    indicators = {}

    for sym, name in inds.items():
        df = sd.get(sym)
        if df is None or len(df) < 60:
            continue
        close = df["close"].values[-60:]
        if "volume" in df.columns:
            vol = df["volume"].values[-60:]
        else:
            vol = np.ones(60)

        ma20 = np.mean(close[-20:])
        ma60 = np.mean(close[-60:])
        vol_ma20 = np.mean(vol[-20:])
        vol_ratio = np.mean(vol[-5:]) / max(vol_ma20, 1)

        # 涨跌家数 (简化: 用最后一天的涨跌)
        chg = (close[-1] / close[-2] - 1) * 100 if len(close) > 1 else 0
        # 波动率
        daily_rets = np.diff(close) / close[:-1]
        volatility = float(np.std(daily_rets[-20:]) * np.sqrt(252) * 100)

        indicators[name] = {
            "ma20": round(float(ma20), 0),
            "ma60": round(float(ma60), 0),
            "ma_trend": "up" if ma20 > ma60 else "down",
            "vol_ratio": round(float(vol_ratio), 2),
            "change_1d": round(float(chg), 2),
            "volatility_annual": round(volatility, 1),
        }

        # 牛市特征
        if ma20 > ma60 and vol_ratio > 1.2 and chg > 0:
            scores["bull"] += 2
        # 熊市特征
        if ma20 < ma60:
            scores["bear"] += 1
        if chg < -1:
            scores["bear"] += 1
        # 极端行情
        if volatility > 40:
            scores["extreme"] += 3
        elif volatility > 30:
            scores["extreme"] += 1

    # 确定状态
    max_score = max(scores.values())
    if max_score == 0:
        state, conf = "oscillate", 0.5
    else:
        state = max(scores, key=scores.get)
        conf = min(1.0, max_score / sum(scores.values())) if sum(scores.values()) > 0 else 0.5

    result = {
        "state": state,
        "confidence": round(conf, 2),
        "indicators": indicators,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    # 持久化
    try:
        json.dump(result, open(STATE_FILE, "w"), ensure_ascii=False)
    except: pass

    return result


def get_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, "r"))
        except: pass
    return detect()


def state_label(s):
    return {"bull": "🐂 牛市", "bear": "🐻 熊市", "oscillate": "📊 震荡", "extreme": "🌋 极端"}.get(s, "未知")
