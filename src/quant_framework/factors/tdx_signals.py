r"""通达信选股公式 → Python 因子转换。

将用户提供的通达信主图指标翻译为可批量计算的因子函数，
每个函数输入 kline_df (OHLCV DataFrame)，返回 0/1 信号 Series 或连续值因子。

信号体系:
  1. 擒龙决 — 放量突破压力线+布林上轨 (打板信号)
  2. 涨停先锋 — 突破获利盘99%成本线 (分歧低吸信号)
  3. 起爆点 — 底部放量突破多重条件 (V2_DC14)
  4. 强庄/连板 — 连续涨停+爆量 (V2_BB3/V2_BB4)
  5. 连阳 — 6连阳+低位 (V2_BB1)
  6. 黑马启动 — 短波上穿HZ2均线
  7. DMI趋势 — PDI/MDI/ADX 组合
  8. 趋势线底部 — GSZB V11<=13
  9. 财神金叉 — 财线上穿神线
  10. 加仓信号 — DX底背离
  11. 高度控局 — 获利盘>50% + 成本线在价下 + 控局>0
  12. 资金流 — 主力进 vs 主力撤
"""

from __future__ import annotations

import numpy as np
import pandas as pd


# ======================================================================
# 辅助函数 — 通达信函数的 Python 实现
# ======================================================================

def _ref(series: pd.Series, n: int) -> pd.Series:
    """REF(X, N): 引用N周期前的值"""
    return series.shift(n)


def _hhv(series: pd.Series, n: int) -> pd.Series:
    """HHV(X, N): N周期内最大值"""
    return series.rolling(n, min_periods=1).max()


def _llv(series: pd.Series, n: int) -> pd.Series:
    """LLV(X, N): N周期内最小值"""
    return series.rolling(n, min_periods=1).min()


def _count(condition: pd.Series, n: int) -> pd.Series:
    """COUNT(X, N): N周期内满足条件的次数"""
    return condition.astype(int).rolling(n, min_periods=1).sum()


def _exist(condition: pd.Series, n: int) -> pd.Series:
    """EXIST(X, N): N周期内是否存在满足条件"""
    return condition.rolling(n, min_periods=1).max().fillna(0).astype(bool)


def _every(condition: pd.Series, n: int) -> pd.Series:
    """EVERY(X, N): N周期内是否一直满足条件"""
    s = condition.rolling(n, min_periods=1).min()
    return s.fillna(0).astype(bool)


def _barslast(condition: pd.Series) -> pd.Series:
    """BARSLAST(X): 上一次条件成立到当前的周期数（向量化版）"""
    cond_arr = np.asarray(condition.values, dtype=bool)
    true_positions = np.where(cond_arr)[0]
    if len(true_positions) == 0:
        return pd.Series(np.nan, index=condition.index)
    positions = np.arange(len(condition))
    indices = np.searchsorted(true_positions, positions, side='right') - 1
    result = np.full(len(condition), np.nan)
    valid = indices >= 0
    result[valid] = positions[valid] - true_positions[indices[valid]]
    return pd.Series(result, index=condition.index)


def _filter(condition: pd.Series, n: int) -> pd.Series:
    """FILTER(X, N): X成立后，N周期内再次成立不显示"""
    result = pd.Series(False, index=condition.index)
    last_trigger = -999
    for i, idx in enumerate(condition.index):
        if condition.iloc[i] and (i - last_trigger) > n:
            result.iloc[i] = True
            last_trigger = i
    return result


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _sma(series: pd.Series, n: int, m: int) -> pd.Series:
    """SMA(X, N, M): 加权移动平均, Y = (M*X + (N-M)*Y')/N"""
    result = series.copy()
    alpha = m / n
    for i in range(1, len(result)):
        if pd.notna(result.iloc[i]):
            result.iloc[i] = alpha * series.iloc[i] + (1 - alpha) * result.iloc[i - 1]
    return result


def _dma(series: pd.Series, weight: pd.Series) -> pd.Series:
    """DMA(X, A): 动态移动平均, Y = A*X + (1-A)*Y'"""
    result = series.copy()
    for i in range(1, len(result)):
        if pd.notna(weight.iloc[i]):
            result.iloc[i] = weight.iloc[i] * series.iloc[i] + (1 - weight.iloc[i]) * result.iloc[i - 1]
    return result


def _cost(close: pd.Series, pct: float, period: int = 60) -> pd.Series:
    """COST(N): N%获利盘价格 (简化为N%分位数)"""
    return close.rolling(period, min_periods=1).quantile(pct / 100.0)


def _winner(close: pd.Series, price: pd.Series) -> pd.Series:
    """WINNER(CLOSE): 获利盘比例 (简化: 价格在N日均线下方比例)"""
    ma = close.rolling(20, min_periods=1).mean()
    return (ma < price).astype(float)


def _atan(x: pd.Series) -> pd.Series:
    """ATAN: 反正切值"""
    return np.arctan(x)


# ======================================================================
# 信号因子 — 直接翻译通达信公式
# ======================================================================


def factor_qlj(df: pd.DataFrame) -> pd.Series:
    """擒龙决 — 放量突破压力线+布林上轨。

    条件: C>压力 AND C>涨停 AND 量比>1.8 AND 7日内首次出现

    对应通达信: 擒龙决:=C>压力 AND C>涨停 AND 量比>1.8 AND COUNT(...,7)=1
    """
    c, h, v = df["close"], df["high"], df["volume"]
    # 压力线 = MA(REF(HHV(C,30),1), 2)
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    # 涨停 = 布林上轨: EMA20 + 2*STD(EMA20, 20)
    ema20 = _ema(c, 20)
    dev = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper_band = _ref(ema20 + 2 * dev, 1)
    # 量比 = VOL / REF(MA(VOL,5),1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)

    cond = (c > pressure) & (c > upper_band) & (vol_ratio > 1.8)
    # 7日内首次出现
    first_in_7 = _count(cond, 7) == 1
    return (cond & first_in_7).astype(int)


def factor_ztxf(df: pd.DataFrame) -> pd.Series:
    """涨停先锋 — 突破获利盘99%成本线。

    条件: C>获利百分百 AND 7日内首次出现

    对应通达信: 涨停先锋:=C>获利百分百 AND COUNT(C>获利百分百,7)=1
    """
    c = df["close"]
    # 获利百分百 = EMA(COST(99),5)
    cost99 = _cost(c, 99, 60)
    profit100 = _ema(cost99, 5)

    cond = c > profit100
    first_in_7 = _count(cond, 7) == 1
    return (cond & first_in_7).astype(int)


def factor_qbd(df: pd.DataFrame) -> pd.Series:
    """起爆点 — 底部放量突破+多重技术条件。

    对应通达信: V2_DC14 (涨停板+前期有放量/起爆信号)
    简化为核心条件: 涨停 + 近期出现过放量 + 价格在EMA10上方 + 非连续涨停
    """
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]

    # 涨停判断 (A股10%涨跌停)
    limit_up = c / _ref(c, 1) > 1.097
    # 爆量: 当日成交量 >= 2倍100日均量
    volume_break = v >= 2 * v.rolling(100).mean()
    # 非连续涨停 (90日内无5连板)
    consecutive_limit = _every(limit_up, 5)
    no_5_in_90 = ~_exist(consecutive_limit, 90)
    # 价格在EMA10上方
    above_ema10 = c > _ema(c, 10)
    # 首次涨停 (7日内无2连板)
    consecutive2 = _every(limit_up, 2)
    no_2_in_7 = ~_exist(consecutive2, 7)
    # 前期出现过放量(15日内)
    had_volume = _exist(volume_break, 15)

    return (limit_up & had_volume & above_ema10 & no_5_in_90 & no_2_in_7).astype(int)


def factor_qz(df: pd.DataFrame) -> pd.Series:
    """强庄信号 — 连续涨停+爆量阳线。

    对应通达信: V2_BB3 (两连板+非三连板+非180日四连板+6连阳低位)
    简化: 底部连阳+放量
    """
    c, o, h, v = df["close"], df["open"], df["high"], df["volume"]

    # 连续6阳
    bullish_6 = _every(c > o, 6)
    # 低位: H < 90日最高
    low_position = h < _hhv(h, 90)
    # 有爆量 (5日内)
    has_volume = _exist(v >= v.rolling(100).mean(), 5)
    # 非四连板 (180日内)
    limit_up = c / _ref(c, 1) > 1.097
    four_limit = _every(limit_up, 4)
    no_4_in_180 = ~_exist(four_limit, 180)

    return (bullish_6 & low_position & has_volume & no_4_in_180).astype(int)


def factor_hmqd(df: pd.DataFrame) -> pd.Series:
    """黑马启动 — 短波上穿HZ2均线。

    对应通达信: 拉升一号:=CROSS(短波, HZ2)
    短波=EMA(EMA(C,13),1), HZ2=EMA(EMA(C,13),8)
    即: EMA13上穿EMA(EMA13,8)
    """
    c = df["close"]
    hz = _ema(c, 13)          # HZ
    short_wave = _ema(hz, 1)   # 短波 = HZ (EMA1不改变)
    hz2 = _ema(hz, 8)          # HZ2

    # CROSS: 前一日短波<=HZ2 AND 今日短波>HZ2
    cross_up = (_ref(short_wave, 1) <= _ref(hz2, 1)) & (short_wave > hz2)
    return cross_up.astype(int)


def factor_dmi_trend(df: pd.DataFrame) -> pd.Series:
    """DMI趋势强度 — PDI>MDI且ADX>=30为强趋势。

    返回连续值: 正值=多头(PDI>MDI), 负值=空头
    强度由ADX决定。

    对应通达信: PDI:=DMP*100/TR1, MDI:=DMM*100/TR1
    """
    h, l, c = df["high"], df["low"], df["close"]

    n = 14
    # TR (True Range)
    tr = pd.concat([
        h - l,
        (h - _ref(c, 1)).abs(),
        (l - _ref(c, 1)).abs()
    ], axis=1).max(axis=1)
    tr1 = _sma(tr, n, 1)

    # +DM, -DM
    hd = h - _ref(h, 1)
    ld = _ref(l, 1) - l
    dmp = _sma(pd.Series(np.where((hd > 0) & (hd > ld), hd, 0), index=df.index), n, 1)
    dmm = _sma(pd.Series(np.where((ld > 0) & (ld > hd), ld, 0), index=df.index), n, 1)

    pdi = dmp * 100 / tr1.replace(0, np.nan)
    mdi = dmm * 100 / tr1.replace(0, np.nan)
    adx = _sma((mdi - pdi).abs() / (mdi + pdi).replace(0, np.nan) * 100, n, 1)

    # 趋势因子: 正值=多头趋势, 负值=空头趋势
    trend = pd.Series(0.0, index=df.index)
    trend[pdi > mdi] = adx[pdi > mdi] / 100.0   # 多头: 0~1
    trend[pdi < mdi] = -adx[pdi < mdi] / 100.0   # 空头: -1~0
    return trend


def factor_trend_bottom(df: pd.DataFrame) -> pd.Series:
    """趋势线底部 — V11<=13 底部区域。

    对应通达信: GSZB 当V11<=13时标记底部

    V11 = 3*SMA((C-LLV(L,55))/(HHV(H,55)-LLV(L,55))*100,5,1) - 2*SMA(SMA(...,5,1),3,1)
    即: 3*SMA5(K,5,1) - 2*SMA(SMA5(K,3,1),3,1)  其中K = 价格在55日区间位置*100
    """
    h, l, c = df["high"], df["low"], df["close"]

    k = (c - _llv(l, 55)) / (_hhv(h, 55) - _llv(l, 55) + 1e-9) * 100
    v11 = 3 * _sma(k, 5, 1) - 2 * _sma(_sma(k, 5, 1), 3, 1)

    # 趋势线 = EMA(V11, 3)
    trend_line = _ema(v11, 3)

    # 底部信号: 趋势线<=13, 值越小底部越明确
    bottom = pd.Series(0.0, index=df.index)
    bottom[trend_line <= 13] = 1.0 - trend_line[trend_line <= 13] / 13.0  # 0~1, 越大越底
    return bottom


def factor_money_flow(df: pd.DataFrame) -> pd.Series:
    """主力资金流 — 主力进 vs 主力撤。

    对应通达信: VV1:=(C*2+H+L)/4*10, VV2:=EMA(VV1,13)-EMA(VV1,34)
    主力进:=IF(V4>=0, V4, 0), 主力撤:=IF(V4<=0, V4, 0)

    V4 = 2*(VV2-VV3)*5.5 其中 VV3=EMA(VV2,5)
    即 MACD(VV1, 13, 34, 5) 的变体
    """
    h, l, c = df["high"], df["low"], df["close"]

    vv1 = (c * 2 + h + l) / 4 * 10
    vv2 = _ema(vv1, 13) - _ema(vv1, 34)
    vv3 = _ema(vv2, 5)
    v4 = 2 * (vv2 - vv3) * 5.5

    # 归一化到 -1 ~ 1 范围 (除以近期标准差)
    std = v4.rolling(60).std()
    normalized = v4 / std.replace(0, np.nan)
    return normalized.clip(-3, 3) / 3.0  # clip and normalize to -1~1


def factor_high_control(df: pd.DataFrame) -> pd.Series:
    """高度控局 — 获利盘>50% + 成本线在价下 + 控局>0。

    对应通达信: VAR2:=100*WINNER(C*0.95); 高度控局:=IF(VAR2>50 AND COST(85)<C AND 控局>0, 控局, 0)
    """
    c = df["close"]

    # 控局: VAR1 = EMA(EMA(C,13),13)
    var1 = _ema(_ema(c, 13), 13)
    control = (var1 - _ref(var1, 1)) / _ref(var1, 1).replace(0, np.nan) * 1000

    # 简化 WINNER 和 COST
    cost85 = _cost(c, 85, 60)
    winner_95 = _winner(c, c * 0.95)

    cond = (winner_95 > 0.5) & (cost85 < c) & (control > 0)
    result = pd.Series(0.0, index=df.index)
    result[cond] = control[cond].clip(0, 50) / 50.0  # normalize 0~1
    return result


def factor_add_position(df: pd.DataFrame) -> pd.Series:
    """加仓信号 — DX底背离。

    对应通达信: MTM:=C-REF(C,1); DX:=100*EMA(EMA(MTM,6),6)/EMA(EMA(ABS(MTM),6),6)
    加仓:=FILTER(DX底背离, 5)
    """
    c = df["close"]
    mtm = c - _ref(c, 1)

    dx_num = _ema(_ema(mtm, 6), 6) * 100
    dx_den = _ema(_ema(mtm.abs(), 6), 6)
    dx = dx_num / dx_den.replace(0, np.nan)

    # 底背离: DX<0 且 2日最低=7日最低 且 DX上穿DX均线
    dx_ma2 = dx.rolling(2).mean()
    dx_low2 = _llv(dx, 2)
    dx_low7 = _llv(dx, 7)
    cross_up = (_ref(dx, 1) <= _ref(dx_ma2, 1)) & (dx > dx_ma2)

    condition = (dx_low2 == dx_low7) & (_count(dx < 0, 2) > 0) & cross_up
    return _filter(condition, 5).astype(int)


def factor_caishen_cross(df: pd.DataFrame) -> pd.Series:
    """财神金叉 — 财线(EMA8-EMA55的10倍)上穿神线(EMA(财,5))。

    对应通达信: 财:=(EMA(C,8)-EMA(C,55))*10; 神:=EMA(财,5)
    GSZJ3:=CROSS(财,神) AND 财<-0.3
    返回: 0=无信号, 1=弱金叉, 2=强金叉(财<-0.3深水区)
    """
    c = df["close"]
    cai = (_ema(c, 8) - _ema(c, 55)) * 10
    shen = _ema(cai, 5)

    cross = (_ref(cai, 1) <= _ref(shen, 1)) & (cai > shen)
    result = pd.Series(0, index=df.index)
    result[cross & (cai < -0.1)] = 1  # weak
    result[cross & (cai < -0.3)] = 2  # strong
    return result


def factor_bbiboll_break(df: pd.DataFrame) -> pd.Series:
    """BBI布林突破 — 价格突破BBI布林上轨。

    对应通达信: BBIBOLL:=(MA(C,3)+MA(C,6)+MA(C,12)+MA(C,24))/4
    UPR:=BBIBOLL+6*STD(BBIBOLL,11)
    强势突破上轨且收阳线
    """
    c, o = df["close"], df["open"]
    bbi = (c.rolling(3).mean() + c.rolling(6).mean() + c.rolling(12).mean() + c.rolling(24).mean()) / 4
    std_bbi = bbi.rolling(11).std()
    upr = bbi + 6 * std_bbi

    # Breakout factor: positive = above upper band
    breakout = (c - upr) / std_bbi.replace(0, np.nan)  # Z-score of breakout
    return breakout.clip(-3, 5) / 5.0  # normalized ~[-0.6, 1.0]


def factor_red_line(df: pd.DataFrame) -> pd.Series:
    """红线股起 — 加权移动平均线的斜率。

    对应通达信: 红线股起=WMA(MID, 20)/210 (20日加权移动平均)
    红线上升=趋势向好, 下穿绿线=趋势转弱
    """
    c, o, h, l = df["close"], df["open"], df["high"], df["low"]
    mid = (3 * c + l + o + h) / 6

    # 20日加权移动平均
    weights = np.arange(20, 0, -1)
    red_line = mid.rolling(20).apply(lambda x: np.average(x, weights=weights), raw=True)

    # 绿线 = MA(红线, 6)
    green_line = red_line.rolling(6).mean()

    # 因子: 红线斜率 + 红绿线位置 (正值=红线上方且上升)
    slope = (red_line - _ref(red_line, 5)) / _ref(red_line, 5).replace(0, np.nan) * 100
    position = (red_line - green_line) / green_line.replace(0, np.nan)

    return (slope.clip(-5, 5) / 5.0 + position.clip(-0.05, 0.05) * 20).clip(-2, 2)


def factor_resonance(df: pd.DataFrame) -> pd.Series:
    """双信号共振 — 擒龙决 AND 涨停先锋 同时触发。

    对应通达信:
      擒龙决:=C>压力 AND C>涨停 AND 量比>1.8 AND COUNT(...,7)=1
      涨停先锋:=C>获利百分百 AND COUNT(C>获利百分百,7)=1
      XG: 擒龙决 AND 涨停先锋;

    双信号共振含义:
      - 擒龙决: 放量突破压力线(30日HHV均值) + 布林上轨 = 主力拉升意图明确
      - 涨停先锋: 突破99%获利盘成本线 = 套牢盘全部解套, 上方无阻力
      - 同时满足: 强拉升 + 无套牢盘 = 高确定性打板机会

    返回: 0/1/2 信号 (0=无, 1=单信号, 2=双信号共振)
    """
    qlj = factor_qlj(df)
    ztxf = factor_ztxf(df)
    # 双信号共振
    both = (qlj.astype(bool) & ztxf.astype(bool)).astype(int) * 2
    # 单信号 (但不是双信号)
    single = (qlj.astype(bool) | ztxf.astype(bool)) & ~(qlj.astype(bool) & ztxf.astype(bool))
    return both.where(both > 0, single.astype(int))


def factor_yaogu_bandit(df: pd.DataFrame) -> pd.Series:
    """妖股先锋×波段擒妖 — 强势启动 + 量能确认 + 趋势共振。

    对应通达信: 妖股先锋x_波段擒妖选股.cos (六合一竞价擒龙系列，完全加密公式)

    反推逻辑（基于2026-06-22当日选出的5只样本分析）:
      000519 中兵红箭  +9.98%   CCI=245  量比=2.14  DIF>DEA  站所有均线
      002068 黑猫股份  +10.05%  CCI=193  量比=2.76  DIF>DEA  站所有均线
      300560 中富通    +9.60%   CCI=257  量比=2.06  DIF>DEA  站所有均线
      301356 天振股份  +20.01%  CCI=354  量比=6.92  DIF>DEA  站所有均线
      301548 崇德科技  +20.00%  CCI=323  量比=3.01  DIF>DEA  站所有均线

    触发条件 (5/5 AND):
      1. CCI(14) > 150 — 超强势能
      2. 量比 > 2.0 — 放量确认 (当日量/5日均量)
      3. 收盘价 > MA5 > MA10 > MA20 — 均线多头排列
      4. MACD: DIF > DEA — 多头趋势
      5. 今日涨幅 > 5% — 加速突破

    返回: 0/1 信号 (满足全部5个条件=1)
    """
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]

    # 1. CCI(14)
    tp = (h + l + c) / 3.0
    ma_tp = tp.rolling(14).mean()
    mad = (tp - ma_tp).abs().rolling(14).mean()
    cci = (tp - ma_tp) / (0.015 * mad.replace(0, np.nan))
    cond_cci = cci > 150

    # 2. 量比 (当日量 / 5日均量)
    ma_vol5 = v.rolling(5).mean()
    vol_ratio = v / ma_vol5.replace(0, np.nan)
    cond_vol = vol_ratio > 2.0

    # 3. 均线多头排列: C > MA5 > MA10 > MA20
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    cond_ma = (c > ma5) & (ma5 > ma10) & (ma10 > ma20)

    # 4. MACD 多头: DIF > DEA
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    cond_macd = dif > dea

    # 5. 今日涨幅 > 5%
    ret_today = c / _ref(c, 1) - 1
    cond_ret = ret_today > 0.05

    signal = (cond_cci & cond_vol & cond_ma & cond_macd & cond_ret).astype(int)
    return signal


# ---- 波段擒妖 (源码精确版) ----


def factor_bandit_sniper(df: pd.DataFrame) -> pd.Series:
    """波段擒妖 — 牛线突破 + 55日新高V反 + MACD多头。

    对应通达信: 波段擒妖选股 (完整源码翻译)

    逻辑:
      XG = CROSS(C, 牛线) AND DIF>DEA AND 今日涨幅>9%
        牛线 = EMA(DMA(typ_price, deviation_ratio), 200) * 1.118
      B1 = 今日突破55日新高 AND 上次信号是破位T (V型反转确认)
      FINAL = XG AND B1

    返回: 0/1 信号
    """
    c, h, l = df["close"], df["high"], df["low"]

    # ── 牛线 ──
    # typ = (2.15*C + L + H) / 4
    typ = (2.15 * c + l + h) / 4.0
    # weight = ABS((3.48*C + H + L)/4 - EMA(C,23)) / EMA(C,23)
    ema23 = _ema(c, 23)
    cmp_price = (3.48 * c + h + l) / 4.0
    weight = (cmp_price - ema23).abs() / ema23.replace(0, np.nan)
    # 牛线 = EMA(DMA(typ, weight), 200) * 1.118
    niu = _ema(_dma(typ, weight), 200) * 1.118

    # ── MACD ──
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)

    # ── XG: 突破牛线 + MACD多头 + 涨幅>9% ──
    cross_niu = (_ref(c, 1) <= _ref(niu, 1)) & (c > niu)
    xg = cross_niu & (dif > dea) & (c / _ref(c, 1) > 1.09)

    # ── ATR ──
    tr = pd.concat([h - l, (h - _ref(c, 1)).abs(), (l - _ref(c, 1)).abs()], axis=1).max(axis=1)
    atr = _ema(tr, 14)

    # ── AA = HHV(H,20) - 2*ATR ──
    aa = _hhv(h, 20) - 2.0 * atr

    # ── BB: CROSS(C, REF(HHV(H,55),1)) 突破55日最高价 ──
    ref_hhv55 = _ref(_hhv(h, 55), 1)
    bb = (_ref(c, 1) <= _ref(ref_hhv55, 1)) & (c > ref_hhv55)

    # ── T: CROSS(MIN(MA(C,13), AA), C) 支撑线交叉 ──
    ma13 = c.rolling(13).mean()
    min_ma13_aa = pd.concat([ma13, aa], axis=1).min(axis=1)
    t = (_ref(min_ma13_aa, 1) <= _ref(c, 1)) & (min_ma13_aa > c)

    # ── B1: BB今日发生 AND 前次T比前次BB更近 (V反确认) ──
    bbb = _barslast(bb)
    r = _barslast(t)
    # BBB=0 且 昨日 R < 昨日 BBB
    b1 = bbb.fillna(999).astype(int) == 0
    b1 = b1 & (_ref(r, 1).fillna(999) < _ref(bbb, 1).fillna(999))

    # ── 最终: XG AND B1 ──
    return (xg & b1).astype(int)


def factor_yaogu_resonance_bandit(df: pd.DataFrame) -> pd.Series:
    """妖股先锋×波段擒妖 双重共振 — 擒龙决+涨停先锋 AND 波段擒妖。

    对应通达信: 妖股先锋x_波段擒妖选股.cos (双公式交集)
    即: 双信号共振选股 AND 波段擒妖 同时触发
    """
    resonance = factor_resonance(df)
    bandit = factor_bandit_sniper(df)
    return (resonance.fillna(0).astype(bool) & bandit.fillna(0).astype(bool)).astype(int)


# ---- 妖股先锋×波段擒妖 组合改进变体 (已有,保持不变) ----

def factor_yaogu_bandit_ab(df: pd.DataFrame) -> pd.Series:
    """A+B: 延迟1天确认 + 回调买入 (CCI回落到100以下才入)。

    逻辑链: 昨日触发原始信号 AND 今日CCI<100 (高位回落确认)
    类型: 0/1 信号
    """
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 昨日信号
    signal_yesterday = _ref(factor_yaogu_bandit(df), 1)

    # 今日CCI < 100 (高位回落)
    tp = (h + l + c) / 3.0
    ma_tp = tp.rolling(14).mean()
    mad = (tp - ma_tp).abs().rolling(14).mean()
    cci_today = (tp - ma_tp) / (0.015 * mad.replace(0, np.nan))
    pullback = cci_today < 100

    out = (signal_yesterday.fillna(0).astype(bool) & pullback.fillna(False)).astype(int)
    return out


def factor_yaogu_bandit_ac(df: pd.DataFrame) -> pd.Series:
    """A+C: 延迟1天确认 + 强度分层 (0~1连续分值)。

    逻辑链: 昨日触发原始信号 AND 今日逆势强度评分
    - CCI回落程度: CCI越低越好 (买在回调)
    - 均线结构保持: MA5>MA10>MA20 各 +0.15
    - MACD多头保持: DIF>DEA +0.15
    - 量能收敛: 量比<2 +0.15
    - 跌幅可控: 跌幅<3% +0.15
    类型: continuous (0~1)
    """
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 昨日信号
    signal_yesterday = _ref(factor_yaogu_bandit(df), 1).fillna(0).astype(bool)
    s = signal_yesterday.astype(float)

    # --- 强度评分 (仅昨日有信号的行) ---
    # 1. CCI回落: CCI在0~150之间分数线性给 (越低越好)
    tp = (h + l + c) / 3.0
    ma_tp = tp.rolling(14).mean()
    mad = (tp - ma_tp).abs().rolling(14).mean()
    cci = (tp - ma_tp) / (0.015 * mad.replace(0, np.nan))
    score_cci = np.clip((150 - cci.fillna(200)) / 150, 0, 1) * 0.25

    # 2. 均线结构: 每满足一层 +0.10
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    score_ma = ((c > ma5).astype(int) * 0.10
                + (ma5 > ma10).astype(int) * 0.10
                + (ma10 > ma20).astype(int) * 0.10)

    # 3. MACD 多头保持 +0.15
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    score_macd = (dif > dea).astype(float) * 0.15

    # 4. 量能收敛 (量比<2 加分, 放量减分) +0.15
    vol_ratio = v / v.rolling(5).mean().replace(0, np.nan)
    score_vol = np.clip((2 - vol_ratio.fillna(3)) / 2, 0, 1) * 0.15

    # 5. 跌幅可控 (当日跌幅<3%) +0.10
    ret_today = c / _ref(c, 1) - 1
    score_ret = np.clip((0.03 + ret_today.fillna(0)) / 0.06, 0, 1) * 0.10

    strength = score_cci + score_ma + score_macd + score_vol + score_ret
    strength = strength.fillna(0)

    return s * strength  # 昨日有信号=强度分, 无信号=0


def factor_yaogu_bandit_abc(df: pd.DataFrame) -> pd.Series:
    """A+B+C: 延迟1天 + 回调确认 + 强度分层。

    逻辑链: 昨日触发原始信号 AND 今日CCI<100 AND 逆势强度评分
    类型: continuous (0~1)
    """
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    # 昨日信号
    signal_yesterday = _ref(factor_yaogu_bandit(df), 1).fillna(0).astype(bool)

    # 今日CCI < 100 (回调确认)
    tp = (h + l + c) / 3.0
    ma_tp = tp.rolling(14).mean()
    mad = (tp - ma_tp).abs().rolling(14).mean()
    cci_today = (tp - ma_tp) / (0.015 * mad.replace(0, np.nan))
    pullback = cci_today < 100

    # 前置条件: 昨日有信号 AND 今日回调
    eligible = signal_yesterday & pullback.fillna(False)
    s = eligible.astype(float)

    # --- 强度评分 (与AC相同) ---
    score_cci = np.clip((150 - cci_today.fillna(200)) / 150, 0, 1) * 0.25
    ma5 = c.rolling(5).mean()
    ma10 = c.rolling(10).mean()
    ma20 = c.rolling(20).mean()
    score_ma = ((c > ma5).astype(int) * 0.10
                + (ma5 > ma10).astype(int) * 0.10
                + (ma10 > ma20).astype(int) * 0.10)
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    score_macd = (dif > dea).astype(float) * 0.15
    vol_ratio = v / v.rolling(5).mean().replace(0, np.nan)
    score_vol = np.clip((2 - vol_ratio.fillna(3)) / 2, 0, 1) * 0.15
    ret_today = c / _ref(c, 1) - 1
    score_ret = np.clip((0.03 + ret_today.fillna(0)) / 0.06, 0, 1) * 0.10
    strength = (score_cci + score_ma + score_macd + score_vol + score_ret).fillna(0)

    return s * strength


# ======================================================================
# 因子注册表 — 通达信信号因子
# ======================================================================

TDX_SIGNAL_FACTORS: dict[str, dict] = {
    "tdx_qlj": {
        "name": "tdx_qlj",
        "label": "擒龙决(打板信号)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",  # 0/1 signal
        "compute": factor_qlj,
        "description": "放量突破压力线+布林上轨, 量比>1.8, 7日首次 — 打板追涨信号",
    },
    "tdx_ztxf": {
        "name": "tdx_ztxf",
        "label": "涨停先锋(分歧低吸)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_ztxf,
        "description": "突破获利盘99%成本线, 7日首次 — 分歧转一致低吸信号",
    },
    "tdx_qbd": {
        "name": "tdx_qbd",
        "label": "起爆点",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_qbd,
        "description": "涨停+前期放量+非连板+EMA10上 — 底部起爆点",
    },
    "tdx_qz": {
        "name": "tdx_qz",
        "label": "强庄信号",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_qz,
        "description": "6连阳+低位+放量+非四连板 — 庄家控盘信号",
    },
    "tdx_hmqd": {
        "name": "tdx_hmqd",
        "label": "黑马启动",
        "category": "technical",
        "direction": 1,
        "type": "signal",
        "compute": factor_hmqd,
        "description": "短波(EMA13)上穿HZ2(EMA(EMA13,8)) — 黑马启动信号",
    },
    "tdx_dmi": {
        "name": "tdx_dmi",
        "label": "DMI趋势强度",
        "category": "technical",
        "direction": 1,
        "type": "continuous",  # -1 ~ 1
        "compute": factor_dmi_trend,
        "description": "PDI/MDI+ADX趋势判断, 正值多头/负值空头, 强度由ADX决定",
    },
    "tdx_trend_bottom": {
        "name": "tdx_trend_bottom",
        "label": "趋势线底部",
        "category": "reversal",
        "direction": 1,
        "type": "continuous",  # 0 ~ 1
        "compute": factor_trend_bottom,
        "description": "V11<=13底部区域, 值越大越接近底部 — 抄底因子",
    },
    "tdx_money_flow": {
        "name": "tdx_money_flow",
        "label": "主力资金流",
        "category": "volume_price",
        "direction": 1,
        "type": "continuous",  # -1 ~ 1
        "compute": factor_money_flow,
        "description": "VV1 MACD变体, 正值=主力流入, 负值=主力流出",
    },
    "tdx_high_control": {
        "name": "tdx_high_control",
        "label": "高度控局",
        "category": "technical",
        "direction": 1,
        "type": "continuous",  # 0 ~ 1
        "compute": factor_high_control,
        "description": "获利盘>50%+成本线在价下+控局>0 — 高控盘因子",
    },
    "tdx_add_position": {
        "name": "tdx_add_position",
        "label": "加仓信号",
        "category": "reversal",
        "direction": 1,
        "type": "signal",
        "compute": factor_add_position,
        "description": "DX底背离+上穿DX均线 — 加仓信号",
    },
    "tdx_caishen": {
        "name": "tdx_caishen",
        "label": "财神金叉",
        "category": "technical",
        "direction": 1,
        "type": "signal",
        "compute": factor_caishen_cross,
        "description": "财线(EMA8-EMA55)上穿神线, 深水区金叉更强(2>1)",
    },
    "tdx_bbiboll": {
        "name": "tdx_bbiboll",
        "label": "BBI布林突破",
        "category": "momentum",
        "direction": 1,
        "type": "continuous",
        "compute": factor_bbiboll_break,
        "description": "BBI布林上轨突破Z-score, 正值=突破上轨",
    },
    "tdx_red_line": {
        "name": "tdx_red_line",
        "label": "红线斜率",
        "category": "momentum",
        "direction": 1,
        "type": "continuous",
        "compute": factor_red_line,
        "description": "加权移动平均线斜率+红绿线位置, 正值=趋势向好",
    },
    "tdx_resonance": {
        "name": "tdx_resonance",
        "label": "双信号共振(擒龙决+涨停先锋)",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_resonance,
        "description": "擒龙决 AND 涨停先锋 同时触发 — 打板+分歧低吸双确认, 高胜率选股",
    },
    "tdx_yaogu_bandit": {
        "name": "tdx_yaogu_bandit",
        "label": "妖股先锋×波段擒妖(反推版)",
        "category": "momentum",
        "direction": 1,
        "type": "signal",
        "compute": factor_yaogu_bandit,
        "description": "[反推版] CCI>150+量比>2+均线多头+MACD多头+涨幅>5% — 保留作对比",
    },
    "tdx_bandit_sniper": {
        "name": "tdx_bandit_sniper",
        "label": "波段擒妖(源码版)",
        "category": "momentum",
        "direction": 1,
        "type": "signal",
        "compute": factor_bandit_sniper,
        "description": "牛线突破+55日新高V反+MACD多头+涨幅>9% — 波段擒妖源码精确翻译",
    },
    "tdx_yaogu_resonance_bandit": {
        "name": "tdx_yaogu_resonance_bandit",
        "label": "双信号共振×波段擒妖",
        "category": "pattern",
        "direction": 1,
        "type": "signal",
        "compute": factor_yaogu_resonance_bandit,
        "description": "擒龙决+涨停先锋 AND 波段擒妖 同时触发 — 实际选出5只标的的原始公式组合",
    },
    "tdx_yaogu_bandit_ab": {
        "name": "tdx_yaogu_bandit_ab",
        "label": "妖股A+B(延迟+回调)",
        "category": "momentum",
        "direction": 1,
        "type": "signal",
        "compute": factor_yaogu_bandit_ab,
        "description": "昨日触发原始信号 + 今日CCI<100 — 延迟1天确认+高位回落买入",
    },
    "tdx_yaogu_bandit_ac": {
        "name": "tdx_yaogu_bandit_ac",
        "label": "妖股A+C(延迟+分层)",
        "category": "momentum",
        "direction": 1,
        "type": "continuous",
        "compute": factor_yaogu_bandit_ac,
        "description": "昨日触发原始信号 + 今日逆势强度评分(0~1) — 延迟确认+分仓买入",
    },
    "tdx_yaogu_bandit_abc": {
        "name": "tdx_yaogu_bandit_abc",
        "label": "妖股A+B+C(全组合)",
        "category": "momentum",
        "direction": 1,
        "type": "continuous",
        "compute": factor_yaogu_bandit_abc,
        "description": "昨日触发+CCI<100回调+强度评分 — 延迟确认+回调买入+分仓",
    },
}
