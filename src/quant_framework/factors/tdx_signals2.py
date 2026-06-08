r"""通达信选股公式 #2 → Python 因子。

公式核心逻辑:
  - 牛线: DMA加权 + 200日EMA 的长周期牛熊分界线
  - XG: 涨停突破牛线 + MACD多头 + 非ST
  - B1: 底部反转结构 (BB=突破55日高点, T=回踩MA13/ATR支撑)
  - 最终选股: XG AND B1 → 涨停突破牛线且处于底部反转结构中

策略含义: "在底部结构形成后，放量涨停突破牛熊分界线"
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 复用 tdx_signals 的辅助函数
from quant_framework.factors.tdx_signals import (
    _ref, _hhv, _llv, _ema, _sma, _dma, _barslast, _count, _exist,
)


def factor_bull_line(df: pd.DataFrame) -> pd.Series:
    """牛线 — 长周期牛熊分界线。

    对应通达信:
      牛线:=EMA(DMA((2.15*C+L+H)/4, ABS((3.48*C+H+L)/4-EMA(C,23))/EMA(C,23)), 200)*1.118

    逻辑: DMA用偏离度作为权重 + 200EMA平滑 + 1.118放大系数
    牛线是"市场平均持仓成本"的长周期估计，价格在牛线上方=牛市

    返回连续值: (C/牛线 - 1) * 100, 即价格相对牛线的偏离百分比
    """
    c, h, l = df["close"], df["high"], df["low"]

    # 典型价格1: (2.15*C+L+H)/4
    typical1 = (2.15 * c + l + h) / 4
    # 典型价格2: (3.48*C+H+L)/4
    typical2 = (3.48 * c + h + l) / 4
    # 权重: ABS(典型价格2 - EMA(C,23)) / EMA(C,23)
    weight = np.abs(typical2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan)
    weight = weight.clip(0, 1)  # 限制权重在0~1

    # DMA 动态移动平均
    dma_val = _dma(typical1, weight)
    # 200EMA + 1.118 放大
    bull_line = _ema(dma_val, 200) * 1.118

    # 返回偏离度: 正值=牛线上方
    deviation = (c / bull_line.replace(0, np.nan) - 1) * 100
    return deviation.clip(-50, 100)


def factor_xg_signal(df: pd.DataFrame) -> pd.Series:
    """XG 选股条件 — 涨停突破牛线 + MACD多头。

    条件:
      1. CROSS(C, 牛线) — 价格上穿牛线
      2. DIF > DEA — MACD多头排列
      3. C/REF(C,1) > 1.09 — 涨幅>9% (涨停)

    返回: 0/1 信号
    """
    c = df["close"]

    # 牛线
    deviation = factor_bull_line(df)
    # 上穿: 前一日偏离<=0, 今日偏离>0
    prev_dev = _ref(deviation, 1)
    cross_bull = (prev_dev <= 0) & (deviation > 0)

    # MACD
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    macd_bullish = dif > dea

    # 涨幅 > 9%
    surge = c / _ref(c, 1) > 1.09

    return (cross_bull & macd_bullish & surge).astype(int)


def factor_bb_signal(df: pd.DataFrame) -> pd.Series:
    """BB 突破信号 — 收盘价突破55日最高价。

    对应通达信: BB:=CROSS(CLOSE, REF(HHV(HIGH,55),1))

    返回: 0/1 信号
    """
    c = df["close"]
    h_55 = _ref(_hhv(df["high"], 55), 1)
    # CROSS: 前一日C<=前一日HHV55, 今日C>今日HHV55(的ref)
    prev_c = _ref(c, 1)
    prev_hhv = _ref(_hhv(df["high"], 55), 2)
    curr_hhv = _ref(_hhv(df["high"], 55), 1)
    return ((prev_c <= prev_hhv) & (c > curr_hhv)).astype(int)


def factor_t_signal(df: pd.DataFrame) -> pd.Series:
    """T 回踩信号 — 收盘价下穿MA13和ATR支撑线的最小值。

    对应通达信:
      AA:=(HHV(HIGH,20) - (2 * ATR))
      T:=CROSS(MIN(MA(CLOSE,13),AA), CLOSE)

    即: 价格从上方跌破 MA13 和 (HHV20-2*ATR) 中较小的一条

    返回: 0/1 信号
    """
    c, h, l = df["close"], df["high"], df["low"]

    # ATR (14 period)
    tr = pd.concat([
        h - l,
        (h - _ref(c, 1)).abs(),
        (l - _ref(c, 1)).abs()
    ], axis=1).max(axis=1)
    atr = _ema(tr, 14)

    # AA = HHV(H,20) - 2*ATR
    aa = _hhv(h, 20) - 2 * atr

    # MIN(MA13, AA)
    ma13 = c.rolling(13).mean()
    min_line = pd.concat([ma13, aa], axis=1).min(axis=1)

    # CROSS(min_line, close): 前一日C>=min_line，今日C<min_line
    prev_c = _ref(c, 1)
    prev_min = _ref(min_line, 1)
    return ((prev_c >= prev_min) & (c < min_line)).astype(int)


def factor_b1_structure(df: pd.DataFrame) -> pd.Series:
    """B1 底部反转结构。

    对应通达信:
      BBB:=BARSLAST(BB)
      R:=BARSLAST(T)
      B1:=((BBB = 0) AND (REF(R,1) < REF(BBB,1)))

    含义: 今日发生BB突破信号，且前一次T回踩发生在前一次BB突破之前
    即: 先回踩(T)，后突破(BB) — 完整的"回调-突破"底部结构

    返回: 0/1 信号
    """
    bb = factor_bb_signal(df)
    t = factor_t_signal(df)

    # BBB = 自上次BB以来的天数
    bbb = _barslast(bb)
    # R = 自上次T以来的天数
    r = _barslast(t)

    # 今日发生BB (BBB==0) 且 上次T发生在之前 (REF(R,1) < REF(BBB,1))
    cond = (bbb == 0) & (_ref(r, 1) < _ref(bbb, 1))
    return cond.astype(int)


def factor_final_pick(df: pd.DataFrame) -> pd.Series:
    """最终选股条件 — XG AND B1。

    对应通达信: 选股条件: XG AND B1;

    涨停突破牛线(MACD多头) + 底部反转结构(T回踩→BB突破)
    这是"涨停突破牛熊分界线且确认底部反转"的高质量信号。

    返回: 0/1 信号
    """
    xg = factor_xg_signal(df)
    b1 = factor_b1_structure(df)
    return (xg.astype(bool) & b1.astype(bool)).astype(int)


def factor_bull_position(df: pd.DataFrame) -> pd.Series:
    """牛线位置因子 — 价格相对于牛线的位置和牛线斜率。

    综合连续值因子:
    - 价格在牛线上方=正值
    - 牛线上升=加分
    - 用途: 判断个股的牛熊状态
    """
    c = df["close"]
    deviation = factor_bull_line(df)

    # 牛线斜率 (20日)
    # 需要先计算牛线原始值
    h, l = df["high"], df["low"]
    typical1 = (2.15 * c + l + h) / 4
    typical2 = (3.48 * c + h + l) / 4
    weight = np.abs(typical2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan)
    weight = weight.clip(0, 1)
    dma_val = _dma(typical1, weight)
    bull_line = _ema(dma_val, 200) * 1.118
    bull_slope = (bull_line - _ref(bull_line, 20)) / _ref(bull_line, 20).replace(0, np.nan) * 100

    # 综合: 偏离度(0~50分) + 斜率(-10~10分)
    score = deviation.clip(-20, 50) / 50.0 * 0.7 + bull_slope.clip(-10, 10) / 10.0 * 0.3
    return score.clip(-1, 1)


# ======================================================================
# 因子注册
# ======================================================================

TDX2_SIGNAL_FACTORS: dict[str, dict] = {
    "tdx2_xg": {
        "name": "tdx2_xg",
        "label": "涨停突破牛线(XG)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_xg_signal,
        "description": "涨停(>9%)突破牛线 + MACD多头(DIF>DEA) — 强势突破信号",
    },
    "tdx2_bb": {
        "name": "tdx2_bb",
        "label": "突破55日高点(BB)",
        "category": "momentum",
        "direction": 1,
        "type": "signal",
        "compute": factor_bb_signal,
        "description": "收盘价突破55日最高价 — 中期趋势突破",
    },
    "tdx2_t": {
        "name": "tdx2_t",
        "label": "回踩支撑(T)",
        "category": "reversal",
        "direction": -1,
        "type": "signal",
        "compute": factor_t_signal,
        "description": "价格跌破MA13和ATR支撑线 — 短期回调信号(做多需等止跌)",
    },
    "tdx2_b1": {
        "name": "tdx2_b1",
        "label": "底部反转结构(B1)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_b1_structure,
        "description": "先回踩(T)→后突破(BB)的完整底部反转结构",
    },
    "tdx2_final": {
        "name": "tdx2_final",
        "label": "终极选股(XG+B1)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_final_pick,
        "description": "涨停突破牛线 + 底部反转结构 — 高胜率选股信号",
    },
    "tdx2_bull_line": {
        "name": "tdx2_bull_line",
        "label": "牛线位置",
        "category": "momentum",
        "direction": 1,
        "type": "continuous",
        "compute": factor_bull_position,
        "description": "价格相对牛线的位置+牛线斜率, 正值=牛市, 用于判断个股牛熊状态",
    },
}
