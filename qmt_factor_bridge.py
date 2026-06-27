"""QMT因子桥接 (蓝图 v3.0 Phase 2)

将 QMT xtdata 因子库 (161个) 转换为 V1-5 全市场IC计算的统一接口。
数据源优先级: QMT xtdata → stock_data.pkl.gz 降级
"""

import numpy as np, pandas as pd
from typing import Optional

# ═══ QMT因子 → V1-5统一接口 ═══
# 161个QMT因子分6大类, 映射到我们的因子评分函数

QMT_FACTOR_MAP = {
    # 技术指标类 (可直接用OHLCV计算, 无需QMT在线)
    "ma_bull": "牛线多头排列",       # MA5>MA20>MA60
    "ma_bear": "熊线空头排列",
    "vol_ratio": "量比",
    "turnover_rate": "换手率",
    "amplitude": "振幅",
    "rsi_14": "RSI(14)",
    "macd_dif": "MACD-DIF",
    "boll_width": "布林带宽度",
    "atr_14": "ATR(14)",

    # 资金面类 (Westock/QMT)
    "main_flow_5d": "主力资金流向(5日)",
    "big_order_ratio": "大单比例",
    "institution_holding": "机构持仓比例",

    # 筹码面类
    "profit_ratio": "获利盘比例",
    "concentration": "筹码集中度",
    "avg_cost_position": "平均成本位置",

    # 基本面类 (需QMT在线或Westock)
    "pe_ttm": "PE(TTM)",
    "pb": "PB",
    "roe": "ROE",
    "dividend_yield": "股息率",
    "revenue_growth": "营收增长率",

    # 情绪面类
    "limit_up_days": "涨停天数",
    "breakout_count": "突破次数",
    "new_high_20d": "20日新高",
}


def get_qmt_factor_names() -> list[str]:
    """获取QMT可用因子列表 (需mini QMT在线)。"""
    try:
        from xtquant import xtdata
        meta = xtdata.get_metatable_list()
        if meta:
            return list(meta.keys())
    except Exception:
        pass
    return list(QMT_FACTOR_MAP.keys())


def compute_qmt_factors(df: pd.DataFrame) -> dict[str, float]:
    """基于OHLCV DataFrame近似计算QMT因子 (离线模式)。

    在线模式下可改用 xtdata.get_market_data_ex() 获取精确因子值。
    """
    if len(df) < 20:
        return {}
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    v = df["volume"].values
    if c[-1] <= 0:
        return {}

    ma5 = np.mean(c[-5:]) if len(c) >= 5 else c[-1]
    ma20 = np.mean(c[-20:]) if len(c) >= 20 else c[-1]
    ma60 = np.mean(c[-60:]) if len(c) >= 60 else c[-1]
    vol_ma20 = np.mean(v[-20:]) if len(v) >= 20 else v[-1]

    factors = {}

    # 技术指标
    factors["ma_bull"] = 100.0 if c[-1] > ma5 > ma20 else (50.0 if c[-1] > ma20 else 0.0)
    factors["vol_ratio"] = float(v[-1] / max(vol_ma20, 1))
    factors["amplitude"] = float((h[-1] - l[-1]) / max(c[-1], 0.01))

    # RSI
    rets = np.diff(c[-15:]) if len(c) >= 15 else np.zeros(14)
    gains = np.mean(np.maximum(rets, 0)) if len(rets) > 0 else 0
    losses = np.mean(np.abs(np.minimum(rets, 0))) + 1e-9 if len(rets) > 0 else 1e-9
    factors["rsi_14"] = float(100 - 100 / (1 + gains / losses))

    # ATR
    tr = np.maximum(h[-14:] - l[-14:], np.abs(c[-14:] - np.roll(c[-14:], 1))) if len(c) >= 15 else np.ones(14)
    factors["atr_14"] = float(np.mean(tr) / max(c[-1], 0.01))

    # 资金面 (近似)
    rets_5 = np.diff(c[-6:]) / np.maximum(c[-6:-1], 0.01) if len(c) >= 6 else np.zeros(5)
    vol_chg_5 = np.diff(v[-6:]) / np.maximum(v[-6:-1], 1) if len(v) >= 6 else np.zeros(5)
    factors["main_flow_5d"] = float(np.sum(np.sign(rets_5) * np.sign(vol_chg_5) * np.abs(rets_5)))

    # 筹码面
    c60 = c[-60:] if len(c) >= 60 else c
    h60, l60 = float(np.max(c60)), float(np.min(c60))
    factors["profit_ratio"] = float((c[-1] - l60) / max(h60 - l60, 0.01) * 100)
    factors["concentration"] = float(1 - np.std(v[-60:]) / max(np.mean(v[-60:]), 1)) if len(v) >= 60 else 0.5

    # 情绪面
    factors["new_high_20d"] = 100.0 if c[-1] >= np.max(h[-20:]) else 0.0

    return factors


def compute_qmt_composite(factors: dict[str, float]) -> float:
    """QMT多因子加权综合评分 (0-100)。

    权重基于V1-5 IC分析: 筹码+资金 > 技术 > 情绪
    """
    weights = {
        "profit_ratio": 0.20, "concentration": 0.15,   # 筹码 (IC=+0.037)
        "main_flow_5d": 0.20,                            # 资金 (IC=+0.060)
        "vol_ratio": 0.10, "rsi_14": 0.05,              # 技术
        "ma_bull": 0.10, "atr_14": 0.05,                # 趋势
        "new_high_20d": 0.10, "amplitude": 0.05,        # 情绪
    }
    score = 50.0
    for k, w in weights.items():
        val = factors.get(k, 50)
        if k in ("profit_ratio", "ma_bull", "new_high_20d"):
            score += val * w / 100  # 已是0-100
        elif k == "main_flow_5d":
            score += min(50, max(-50, val * 200)) * w / 100
        elif k == "vol_ratio":
            score += min(50, max(-50, (val - 1) * 100)) * w / 100
        elif k == "concentration":
            score += min(50, max(-50, (val - 0.5) * 200)) * w / 100
    return max(0, min(100, score))
