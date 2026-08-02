"""市场状态分类器 — 牛/熊/震荡四分类 (蓝图 v3.0 S1-4)

基于沪深300指数数据，规则决策树分类。
输出: 'bull' | 'bear' | 'volatile' | 'unknown'
"""

import numpy as np
from typing import Optional

# ═══ 沪深300代码 (通达信格式) ═══
INDEX_CODE = "sh000300"

# ═══ 分类阈值 ═══
CONF = {
    "lookback": 60,            # 回看天数
    "volatility_low": 0.15,    # 低波动阈值 (年化)
    "volatility_high": 0.30,   # 高波动阈值 (年化)
    "volume_ratio_bull": 1.1,  # 放量阈值
    "confirm_days": 3,         # 状态切换确认天数
}

# 状态缓存 (避免频繁切换) — E349: 持久化,重启不丢
import os as _os_ms, json as _json_ms
_STATE_HISTORY_FILE = r"D:\quant_web\data\market_state.json"
_state_history: list[str] = []
try:
    if _os_ms.path.exists(_STATE_HISTORY_FILE):
        _d = _json_ms.load(open(_STATE_HISTORY_FILE, "r"))
        _h = _d.get("_state_history", [])
        if isinstance(_h, list) and _h:
            _state_history = _h[-10:]  # 最多保留10条
except Exception:
    pass


def classify_market_state(quote_cache: dict = None, index_history: list = None) -> str:
    """分类当前市场状态。

    数据源: stock_data.pkl.gz (sh000300 沪深300)
    """
    global _state_history

    hist = index_history
    if hist is None or len(hist) < 20:
        # 1号源: QMT xtdata (主)
        try:
            from xtquant import xtdata
            data = xtdata.get_market_data_ex(field_list=["close","volume"], stock_list=["000300.SH"], period="1d", count=CONF["lookback"])
            if data and "close" in data and "000300.SH" in data["close"].columns:
                closes = data["close"]["000300.SH"].values
                volumes = data["volume"]["000300.SH"].values if "volume" in data else [0]*len(closes)
                dates = [str(d)[:10] for d in data["close"].index]
                hist = [{"date": dates[i], "close": float(closes[i]), "volume": float(volumes[i])} for i in range(len(closes))]
        except: pass

    if not hist or len(hist) < 20:
        # 2号源: stock_data 缓存 (P0-2: parquet/gzip/pickle 统一入口)
        try:
            import sys, os
            sys.path.insert(0, r"D:\quant_web")
            from data_loader import load_stock_data_from_cache
            sd = load_stock_data_from_cache()
            if sd:
                for key in ["sh000300", "000300"]:
                    if key in sd:
                        df = sd[key]
                        tail = df.tail(CONF["lookback"])
                        hist = [{"date": str(i)[:10], "close": float(row["close"]), "volume": float(row["volume"])} for i, row in tail.iterrows()]
                        break
        except: pass
    if not hist or len(hist) < 20:
        return "unknown"

    # 2. 计算特征
    closes = np.array([float(h.get("close", 0)) for h in hist[-CONF["lookback"]:]])
    volumes = np.array([float(h.get("volume", 0)) for h in hist[-CONF["lookback"]:]])
    if len(closes) < 20:
        return "unknown"

    ma20 = np.mean(closes[-20:])
    ma60 = np.mean(closes[-min(60, len(closes)):])
    ma20_vs_ma60 = (ma20 - ma60) / max(ma60, 0.01)

    returns = np.diff(closes) / np.maximum(closes[:-1], 0.01)
    volatility = float(np.std(returns[-20:]) * np.sqrt(252)) if len(returns) >= 20 else 0.25

    vol_ma5 = np.mean(volumes[-5:]) if len(volumes) >= 5 else 1
    vol_ma20 = np.mean(volumes[-20:]) if len(volumes) >= 20 else 1
    volume_trend = vol_ma5 / max(vol_ma20, 1)

    # 3. 决策树
    state = _classify(ma20_vs_ma60, volatility, volume_trend)

    # 4. 状态切换确认 (连续N日才切换)
    _state_history.append(state)
    if len(_state_history) > CONF["confirm_days"]:
        _state_history.pop(0)
    # E349: 持久化历史 (重启不丢确认缓冲)
    try:
        _existing = {}
        if _os_ms.path.exists(_STATE_HISTORY_FILE):
            try:
                with open(_STATE_HISTORY_FILE, "r") as _f:
                    _existing = _json_ms.load(_f)
            except Exception: pass
        _existing["_state_history"] = _state_history
        _tmp = _STATE_HISTORY_FILE + ".tmp"
        with open(_tmp, "w") as _f:
            _json_ms.dump(_existing, _f)
        _os_ms.replace(_tmp, _STATE_HISTORY_FILE)
    except Exception: pass

    if len(set(_state_history[-CONF["confirm_days"]:])) == 1:
        return state
    # 未确认 → 返回最近多数状态
    if _state_history:
        return max(set(_state_history), key=_state_history.count)
    return state


def _classify(ma20_vs_ma60: float, volatility: float, volume_trend: float) -> str:
    """规则决策树。"""
    if ma20_vs_ma60 > 0.005:  # MA20 > MA60 (上升趋势)
        if volatility < CONF["volatility_low"]:
            return "volatile"   # 温和上涨 = 震荡
        return "bull"           # 放量上涨 = 牛市
    elif ma20_vs_ma60 < -0.005:  # MA20 < MA60 (下降趋势)
        if volatility > CONF["volatility_high"]:
            return "bear"        # 高波动下跌 = 熊市
        return "volatile"        # 温和下跌 = 震荡
    return "volatile"  # 平盘 = 震荡


def _load_index_from_tdx() -> Optional[list]:
    """从通达信 .day 文件读取沪深300日线。"""
    try:
        import struct, os
        path = r"D:\new_tdx\vipdoc\sh\lday\sh000300.day"
        if not os.path.exists(path):
            return None
        result = []
        with open(path, "rb") as f:
            while True:
                chunk = f.read(32)
                if len(chunk) < 32:
                    break
                date_raw, open_p, high, low, close, amount, vol, _ = struct.unpack("=IfffffI4s", chunk)
                year = date_raw // 10000
                month = (date_raw % 10000) // 100
                day = date_raw % 100
                result.append({
                    "date": f"{year:04d}-{month:02d}-{day:02d}",
                    "open": open_p / 100.0, "high": high / 100.0,
                    "low": low / 100.0, "close": close / 100.0,
                    "volume": vol,
                })
        return result
    except Exception:
        return None


def get_market_state_features(quote_cache: dict = None) -> dict:
    """获取市场状态特征（用于前端显示）。"""
    state = classify_market_state(quote_cache)
    return {
        "state": state,
        "label": {"bull": "🐂 牛市", "bear": "🐻 熊市",
                  "volatile": "📊 震荡", "unknown": "❓ 未知"}.get(state, state),
    }


def reset_state_history():
    """重置状态历史 (测试用)。"""
    global _state_history
    _state_history.clear()
