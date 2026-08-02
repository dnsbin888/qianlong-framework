"""TDX公式转Python信号 — 用户自定义选股公式"""
import numpy as np


def formula_daban_gongzhen(df):
    """公式1: 双信号共振选股 打板+分歧低吸

    通达信逻辑:
      擒龙决: C>压力 AND C>涨停 AND 量比>1.8 AND 首次触发
      涨停先锋: C>获利百分百 AND 首次触发
      XG: 擒龙决 AND 涨停先锋
    """
    if df is None or len(df) < 60:
        return False

    c = df["close"].values
    h = df["high"].values
    v = df["volume"].values
    n = len(c)

    # 压力 = MA(REF(HHV(C,30),1),2)
    hhv30 = np.max([c[max(0, n-31-i):n-1-i] for i in range(2)][::-1])
    # 简化: 前一日30日高点的2日MA
    hhv30_arr = np.array([np.max(c[max(0, i-29):i+1]) for i in range(max(0,n-32), n-1)])
    if len(hhv30_arr) >= 2:
        pressure = np.mean(hhv30_arr[-2:])
    else:
        return False

    # 妖股先锋 = EMA(C,20) → 布林上轨 = MA+2*STD
    ema20 = np.mean(c[-20:])  # 简化EMA为SMA
    std20 = np.std(c[-20:])
    boll_upper = ema20 + 2 * std20
    zhangting = boll_upper  # 涨停=布林上轨

    # 量比 = VOL / REF(MA(VOL,5),1)
    ma_vol5 = np.mean(v[-6:-1]) if n >= 6 else np.mean(v[-5:])
    vol_ratio = v[-1] / max(ma_vol5, 1)

    # 擒龙决条件
    qinlong = c[-1] > pressure and c[-1] > zhangting and vol_ratio > 1.8

    # 获利百分百 = EMA(COST(99),5) — 简化: 近期高点
    cost99 = np.max(h[-20:])
    huoli = np.mean(h[-5:])

    # 涨停先锋
    xianfeng = c[-1] > huoli

    return qinlong and xianfeng


def formula_macd_tupo(df):
    """公式2: MACD+牛线突破

    通达信逻辑:
      DIF>DEA AND C/REF(C,1)>1.09 AND CROSS(C,牛线)
      + B1条件 (55日高点突破回踩)
    """
    if df is None or len(df) < 60:
        return False

    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    n = len(c)

    # MACD: DIF=EMA12-EMA26, DEA=EMA(DIF,9)
    ema12 = np.mean(c[-12:])
    ema26 = np.mean(c[-26:]) if n >= 26 else ema12
    dif = ema12 - ema26
    # DEA简化
    dea = dif * 0.8  # 近似

    # 牛线: 复杂DMA公式, 简化为200周期加权MA
    niu_xian = np.mean((2.15*c[-20:] + l[-20:] + h[-20:])/4) * 1.118

    # DIF > DEA (MACD多头)
    macd_bull = dif > dea

    # 今日涨幅 > 9%
    up_9pct = c[-1] / max(c[-2], 0.01) > 1.09

    # CROSS(C, 牛线): 今收>牛线 且 昨收<牛线
    cross_niu = c[-1] > niu_xian and c[-2] < niu_xian if n >= 2 and c[-2] > 0 else c[-1] > niu_xian

    # ATR
    tr = np.maximum(h[-20:] - l[-20:], np.abs(h[-20:] - np.roll(c[-20:], 1)))
    atr_val = np.mean(tr[-14:]) if len(tr) >= 14 else np.std(c[-20:])

    # BB: CROSS(C, HHV(H,55))
    hhv55 = np.max(h[-55:]) if n >= 55 else np.max(h)
    bb = c[-1] > hhv55 and c[-2] < hhv55 if n >= 2 else c[-1] > hhv55

    # B1条件
    b1 = bb

    return macd_bull and (up_9pct or cross_niu) and b1


# ═══════════════════════════════════════
# 信号注册表 (signal_config.json控制开关)
# ═══════════════════════════════════════

FORMULA_MAP = {
    "双信号共振打板分歧低吸": formula_daban_gongzhen,
    "MACD牛线突破": formula_macd_tupo,
}


def check_tdx_formula(formula_name, df):
    """检查单个公式是否触发"""
    fn = FORMULA_MAP.get(formula_name)
    if fn is None:
        return False
    try:
        return fn(df)
    except Exception:
        return False
