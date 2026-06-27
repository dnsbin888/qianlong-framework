"""P2-2: 市场状态分类器 — 四状态（牛/熊/震荡/反弹），仅依赖已有日线数据。"""
from __future__ import annotations

import logging
import os
import pickle
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# 四状态定义
STATE_BULL = "bull"          # 🐂 牛市上涨
STATE_BEAR = "bear"          # 🐻 熊市下跌
STATE_OSCILLATE = "oscillate"  # 〰️ 震荡
STATE_REBOUND = "rebound"    # ↕️ 反弹/调整

STATE_EMOJI = {
    STATE_BULL: "🐂",
    STATE_BEAR: "🐻",
    STATE_OSCILLATE: "〰️",
    STATE_REBOUND: "↕️",
}

# 缓存: 5分钟有效
_cache: dict[str, Any] = {}
_CACHE_TTL = 300  # 秒


def _load_index_data() -> np.ndarray | None:
    """从 stock_data 缓存加载上证指数数据 (P0-2: 统一入口)。"""
    import sys
    sys.path.insert(0, r"D:\quant_web")
    from data_loader import load_stock_data_from_cache
    data = load_stock_data_from_cache()
    if data:
        df = data.get("sh000001") or data.get("000001")
        if df is not None and len(df) >= 20:
            return df["close"].values[-120:]
        return None
    # 兜底: 旧路径
    paths = [
        r"D:\quant_web\stock_data.parquet",
        r"D:\quant_web\stock_data.pkl.gz",
        r"D:\quant_web\stock_data.pkl",
        r"D:\quant_framework\stock_data.pkl.gz",
        r"D:\quant_framework\stock_data.pkl",
    ]
    for pp in paths:
        if not os.path.exists(pp):
            continue
        try:
            if pp.endswith(".parquet"):
                from data_loader import load_stock_data_cache
                d = load_stock_data_cache(pp)
                if d:
                    df = d.get("sh000001") or d.get("000001")
                    if df is not None and len(df) >= 20:
                        return df["close"].values[-120:]
                    return None
                continue
            if pp.endswith(".gz"):
                import gzip
                with gzip.open(pp, "rb") as f:
                    data = pickle.load(f)
            else:
                with open(pp, "rb") as f:
                    data = pickle.load(f)
            # 上证指数代码
            for sym in ("sh000001", "SH000001", "000001"):
                if sym in data:
                    df = data[sym]
                    if hasattr(df, "iloc") and len(df) >= 60:
                        return df["close"].values.astype(float)
                    elif isinstance(df, np.ndarray):
                        return df.astype(float)
            logger.warning("[MarketState] 上证指数不在数据中, 尝试沪深300")
            # 回退: 沪深300
            for sym in ("sh000300", "SH000300", "sh000300"):
                if sym in data:
                    df = data[sym]
                    if hasattr(df, "iloc") and len(df) >= 60:
                        return df["close"].values.astype(float)
            return None
        except Exception as _e:
            logger.warning(f"[MarketState] 数据加载失败({pp}): {_e}")
            continue
    return None


class MarketStateClassifier:
    """市场状态分类器（四状态）— 仅依赖日K线数据，不引入外部API。"""

    def __init__(self) -> None:
        self._close: np.ndarray | None = None

    def _ensure_data(self) -> None:
        """加载数据（如果尚未加载或缓存过期）。"""
        import time as _time
        now = _time.time()
        if _cache.get("ts") and (now - _cache["ts"]) < _CACHE_TTL and self._close is not None:
            return
        self._close = _load_index_data()
        _cache["ts"] = now

    def _check_ma_alignment(self) -> tuple[str, float]:
        """均线排列分析。

        Returns:
            (direction, gap_pct): 'up'多头/'down'空头/'flat'粘合, 均线间距百分比
        """
        c = self._close
        if c is None or len(c) < 60:
            return "flat", 0.0

        ma5 = float(np.mean(c[-5:]))
        ma20 = float(np.mean(c[-20:]))
        ma60 = float(np.mean(c[-60:]))
        avg = (ma5 + ma20 + ma60) / 3.0
        if avg == 0:
            return "flat", 0.0

        gap = max(ma5, ma20, ma60) / min(ma5, ma20, ma60) - 1.0  # 最大间距比

        if ma5 > ma20 > ma60:
            return "up", round(gap * 100, 2)  # 多头排列
        elif ma5 < ma20 < ma60:
            return "down", round(gap * 100, 2)  # 空头排列
        else:
            return "flat", round(gap * 100, 2)

    def _check_volatility(self) -> tuple[float, str]:
        """计算近期波动率水平。

        Returns:
            (atr_pct, level): ATR占价格百分比, 'high'/'normal'/'low'
        """
        c = self._close
        if c is None or len(c) < 20:
            return 0.0, "normal"

        # ATR(14) 简化: 用高低差代替（无high/low数据时用相邻收盘差）
        diffs = np.abs(np.diff(c[-20:]))
        atr = float(np.mean(diffs[-14:])) if len(diffs) >= 14 else float(np.mean(diffs))
        price = float(c[-1])
        atr_pct = round(atr / (price + 0.01) * 100, 2)

        # 相对自身历史波动率
        hist_diffs = np.abs(np.diff(c[-120:])) if len(c) >= 120 else diffs
        hist_atr = float(np.mean(hist_diffs))
        hist_pct = hist_atr / (price + 0.01) * 100 if hist_atr > 0 else 1.0
        if hist_pct == 0:
            hist_pct = 1.0

        ratio = atr_pct / (hist_pct + 0.01)
        if ratio > 1.5:
            level = "high"
        elif ratio < 0.6:
            level = "low"
        else:
            level = "normal"
        return atr_pct, level

    def _check_volume_trend(self) -> str:
        """成交量趋势判断（仅基于价格变化推断，无真实成交量数据时回退）。

        Returns:
            'expanding'放量 / 'contracting'缩量 / 'flat'持平
        """
        c = self._close
        if c is None or len(c) < 20:
            return "flat"

        # 无成交量数据时用价格波动幅度代理
        recent_range = float(np.std(c[-5:]) / (np.mean(c[-5:]) + 0.01))
        hist_range = float(np.std(c[-20:]) / (np.mean(c[-20:]) + 0.01))
        ratio = recent_range / (hist_range + 0.01)
        if ratio > 1.3:
            return "expanding"
        elif ratio < 0.7:
            return "contracting"
        return "flat"

    def classify(self) -> dict[str, Any]:
        """分类当前市场状态。

        Returns:
            {
                "state": str,        # bull/bear/oscillate/rebound
                "emoji": str,        # 🐂/🐻/〰️/↕️
                "confidence": float, # 0~1
                "signals": {
                    "ma_trend": str,     # up/down/flat
                    "ma_gap_pct": float, # 均线间距%
                    "volatility": str,   # high/normal/low
                    "vol_pct": float,    # ATR%
                    "volume": str,       # expanding/contracting/flat
                },
                "recommendation": str, # 策略建议简述
            }
        """
        self._ensure_data()
        if self._close is None:
            return {
                "state": STATE_OSCILLATE,
                "emoji": STATE_EMOJI[STATE_OSCILLATE],
                "confidence": 0.1,
                "signals": {"ma_trend": "flat", "ma_gap_pct": 0, "volatility": "normal", "vol_pct": 0, "volume": "flat"},
                "recommendation": "无数据,默认震荡",
            }

        ma_dir, ma_gap = self._check_ma_alignment()
        vol_pct, vol_level = self._check_volatility()
        vol_trend = self._check_volume_trend()

        # ── 状态判定 ──
        state = STATE_OSCILLATE
        confidence = 0.5
        recommendation = "观望为主"

        if ma_dir == "up" and vol_level != "high" and vol_trend != "contracting":
            state = STATE_BULL
            confidence = 0.7 + (0.1 if ma_gap > 3 else 0) + (0.1 if vol_trend == "expanding" else 0)
            confidence = min(confidence, 0.95)
            recommendation = "趋势跟踪（动量+突破+通道）"
        elif ma_dir == "down" and (vol_level == "high" or vol_trend == "contracting"):
            state = STATE_BEAR
            confidence = 0.7 + (0.1 if ma_gap > 3 else 0)
            confidence = min(confidence, 0.95)
            recommendation = "防御为主（反转+均值回归+轻仓）"
        elif ma_dir == "flat" and ma_gap < 2.0:
            state = STATE_OSCILLATE
            confidence = 0.6 + (0.2 if vol_level == "low" else 0)
            confidence = min(confidence, 0.9)
            recommendation = "网格震荡（均值回归+条件单）"
        else:
            # 均线混乱 → 反弹/调整
            state = STATE_REBOUND
            confidence = 0.5
            recommendation = "短线反弹（涨停跟随+机构跟踪）"

        return {
            "state": state,
            "emoji": STATE_EMOJI[state],
            "confidence": round(confidence, 2),
            "signals": {
                "ma_trend": ma_dir,
                "ma_gap_pct": ma_gap,
                "volatility": vol_level,
                "vol_pct": vol_pct,
                "volume": vol_trend,
            },
            "recommendation": recommendation,
        }


# 全局单例
_classifier: MarketStateClassifier | None = None


def get_market_state() -> dict[str, Any]:
    """获取当前市场状态（带缓存）。"""
    global _classifier
    if _classifier is None:
        _classifier = MarketStateClassifier()
    return _classifier.classify()
