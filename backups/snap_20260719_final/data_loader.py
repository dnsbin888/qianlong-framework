"""数据加载 + 多因子计算引擎。

从 TDX vipdoc 日线数据加载 A 股行情，计算所有通达信因子，
为 Flask Web 选股面板提供数据支撑。

因子体系:
  - tdx2_final:  牛线突破 + B1底部反转
  - tdx2_xg:     涨停突破牛线 + MACD多头
  - tdx_qlj:     擒龙决 (放量突破)
  - tdx_ztxf:    涨停先锋 (分歧低吸)
  - tdx_resonance: 双信号共振
  - 趋势质量、量能质量、位置质量、ATR
"""

import sys
sys.path.insert(0, r"d:\quant_framework\src")

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

# 股票名称引擎
try:
    from stock_names import init_names, get_stock_name
except ImportError:
    # 如果独立运行数据加载器
    init_names = lambda: None
    get_stock_name = lambda s: ""


# ======================================================================
# TDX 数据加载 (精简版)
# ======================================================================

def _date_to_datetime(date_int: int) -> datetime | None:
    """将 TDX date int (如 20240520) 转为 datetime。"""
    try:
        s = str(date_int)
        if len(s) != 8:
            return None
        return datetime(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        return None


def _read_day_file(filepath: str) -> dict[int, tuple]:
    """读取单个 .day 文件 (TDX 32B 格式)。

    TDX 格式: <I I I I I f I I (32 bytes)
      date(uint32), open(uint32), high(uint32), low(uint32),
      close(uint32), amount(float), volume(uint32), rsv(uint32)
    价格 = raw_int / divisor (divisor=100 or 1000)
    """
    import struct
    data = {}
    try:
        with open(filepath, "rb") as f:
            content = f.read()
        record_size = 32
        for i in range(0, len(content), record_size):
            if i + record_size > len(content):
                break
            date_int, o_raw, h_raw, l_raw, c_raw, amt, vol, _ = struct.unpack(
                "<I I I I I f I I", content[i:i + record_size]
            )
            divisor = 100.0 if o_raw < 10_000_000 else 1000.0
            o = o_raw / divisor
            h = h_raw / divisor
            l = l_raw / divisor
            c = c_raw / divisor
            if date_int > 0 and c > 0:
                data[date_int] = (o, h, l, c, amt, vol)
    except Exception:
        pass
    return data


def scan_day_files(data_root: str) -> list[str]:
    """扫描 vipdoc 目录下所有 .day 文件。沪深优先，跳过北交所旧数据。"""
    files = []
    # 沪深优先
    priority_dirs = [
        os.path.join(data_root, "sh", "lday"),
        os.path.join(data_root, "sz", "lday"),
    ]
    for pd in priority_dirs:
        if os.path.isdir(pd):
            for f in os.listdir(pd):
                if f.endswith(".day"):
                    files.append(os.path.join(pd, f))
    # 其他目录
    for root, dirs, filenames in os.walk(data_root):
        # 跳过已处理的
        if any(root.startswith(pd) for pd in priority_dirs):
            continue
        for f in filenames:
            if f.endswith(".day"):
                files.append(os.path.join(root, f))
    return files


def load_all_stocks(data_root: str, min_days: int = 200, max_stocks: int = 3000) -> dict[str, pd.DataFrame]:
    """加载所有 A 股日线数据。

    Args:
        data_root: vipdoc 数据目录
        min_days: 最少历史K线要求
        max_stocks: 最大加载股票数

    Returns:
        {symbol: DataFrame(index=date, columns=[open,high,low,close,volume])}
    """
    files = scan_day_files(data_root)
    stock_data: dict[str, pd.DataFrame] = {}
    loaded = 0

    for filepath in files[:max_stocks]:
        symbol = os.path.splitext(os.path.basename(filepath))[0]
        raw_data = _read_day_file(filepath)
        if not raw_data:
            continue

        records = []
        for date_int, (o, h, l, c, amt, vol) in raw_data.items():
            dt = _date_to_datetime(date_int)
            if dt is None or o <= 0 or c <= 0:
                continue
            records.append({
                "date": dt,
                "open": o, "high": h, "low": l,
                "close": c, "volume": vol,
            })

        if len(records) < min_days:
            continue

        # 数据质量: 最近日期必须在2022年之后(排除退市股)
        last_date = records[-1]["date"]
        if last_date < datetime(2022, 1, 1):
            continue

        df = pd.DataFrame(records).sort_values("date").set_index("date")
        stock_data[symbol] = df
        loaded += 1

    return stock_data


# ======================================================================
# 通达信辅助函数 (精简版)
# ======================================================================

def _ref(series: pd.Series, n: int) -> pd.Series:
    return series.shift(n)


def _hhv(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=1).max()


def _llv(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=1).min()


def _ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def _count(condition: pd.Series, n: int) -> pd.Series:
    return condition.astype(int).rolling(n, min_periods=1).sum()


def _exist(condition: pd.Series, n: int) -> pd.Series:
    return condition.rolling(n, min_periods=1).max().fillna(0).astype(bool)


# ======================================================================
# 核心因子计算
# ======================================================================

def compute_factors(df: pd.DataFrame) -> dict:
    """计算所有因子，返回最新一期的因子字典。

    因子列表:
      signal_xg:      涨停突破牛线 (0/1)
      signal_b1:      底部反转结构 (0/1)
      signal_final:   终极选股 XG+B1 (0/1)
      signal_qlj:     擒龙决 (0/1)
      signal_ztxf:    涨停先锋 (0/1)
      signal_resonance: 双共振 (0/1/2)
      trend_score:    趋势质量 (0~1)
      volume_score:   量能质量 (0~1)
      position_score: 位置质量 (0~1)
      quality_score:  综合质量分 (0~1)
      atr_pct:        ATR百分比
      ma_position:    均线位置 (-1~1)
      vol_ratio:      量比
      change_pct:     涨跌幅
      limit_up:       是否涨停
    """
    if len(df) < 60:
        return {}

    def _ref(s, n): return s.shift(n)
    def _hhv(s, n): return s.rolling(n, min_periods=1).max()
    def _llv(s, n): return s.rolling(n, min_periods=1).min()
    def _every(s, n): return s.rolling(n).sum() == n
    def _ma(s, n): return s.rolling(n).mean()

    c, h, l, o, v = df["close"], df["high"], df["low"], df["open"], df["volume"]

    factors = {}

    # ── XG: 涨停突破牛线 ──
    # 牛线 = EMA(DMA((2.15*C+L+H)/4, weight), 200) * 1.118
    typical1 = (2.15 * c + l + h) / 4
    typical2 = (3.48 * c + h + l) / 4
    weight = np.abs(typical2 - _ema(c, 23)) / _ema(c, 23).replace(0, np.nan)
    weight = weight.clip(0, 1)
    dma_val = typical1.copy()
    for i in range(1, len(dma_val)):
        w = weight.iloc[i] if not pd.isna(weight.iloc[i]) else 0
        dma_val.iloc[i] = w * typical1.iloc[i] + (1 - w) * dma_val.iloc[i - 1]
    bull_line = _ema(dma_val, 200) * 1.118
    deviation = (c / bull_line.replace(0, np.nan) - 1) * 100

    cross_bull = (_ref(deviation, 1) <= 0) & (deviation > 0)
    dif = _ema(c, 12) - _ema(c, 26)
    dea = _ema(dif, 9)
    macd_bull = dif > dea
    surge = c / _ref(c, 1) > 1.09
    factors["signal_xg"] = int((cross_bull & macd_bull & surge).iloc[-1] if len(cross_bull) > 0 else 0)

    # ── B1: 底部反转 ──
    # BB: 突破55日最高价
    h_55_ref = _ref(_hhv(h, 55), 1)
    bb = ((_ref(c, 1) <= _ref(_hhv(h, 55), 2)) & (c > h_55_ref)).astype(int)
    # T: 回踩支撑
    tr = pd.concat([h - l, (h - _ref(c, 1)).abs(), (l - _ref(c, 1)).abs()], axis=1).max(axis=1)
    atr = _ema(tr, 14)
    aa = _hhv(h, 20) - 2 * atr
    ma13 = c.rolling(13).mean()
    min_line = pd.concat([ma13, aa], axis=1).min(axis=1)
    t_sig = ((_ref(c, 1) >= _ref(min_line, 1)) & (c < min_line)).astype(int)
    # BARSLAST 简化
    b1_val = 0
    if bb.iloc[-1] == 1:
        # 检查最近T发生在BB之前
        for j in range(len(t_sig) - 2, max(0, len(t_sig) - 30), -1):
            if t_sig.iloc[j] == 1:
                for k in range(j + 1, len(bb)):
                    if bb.iloc[k] == 1:
                        b1_val = 1
                        break
                break
    factors["signal_b1"] = b1_val

    # ── Final = XG AND B1 ──
    factors["signal_final"] = 1 if factors.get("signal_xg") and factors.get("signal_b1") else 0

    # ── 擒龙决 ──
    pressure = _ref(_hhv(c, 30), 1).rolling(2).mean()
    ema20 = _ema(c, 20)
    std20 = (c - ema20).pow(2).rolling(20).mean().pow(0.5)
    upper_band = _ref(ema20 + 2 * std20, 1)
    vol_ratio = v / _ref(v.rolling(5).mean(), 1)
    cond_qlj = (c > pressure) & (c > upper_band) & (vol_ratio > 1.8)
    factors["signal_qlj"] = int((cond_qlj & (_count(cond_qlj, 7) == 1)).iloc[-1]) if len(cond_qlj) > 0 else 0

    # ── 涨停先锋 ──
    cost99 = c.rolling(60, min_periods=1).quantile(0.99)
    profit100 = _ema(cost99, 5)
    cond_ztxf = c > profit100
    factors["signal_ztxf"] = int((cond_ztxf & (_count(cond_ztxf, 7) == 1)).iloc[-1]) if len(cond_ztxf) > 0 else 0

    # ── 双共振 ──
    both = factors.get("signal_qlj", 0) and factors.get("signal_ztxf", 0)
    single = factors.get("signal_qlj", 0) or factors.get("signal_ztxf", 0)
    factors["signal_resonance"] = 2 if both else (1 if single else 0)

    # ── B策略: DC5试涨(起爆点) ──
    g3 = c > o
    g10 = (_ref(c, 1) / _ref(c, 2) < 1.098) & (_ref(h, 1) > _ref(c, 1))
    g4 = (h - np.maximum(c, o)) / _ref(c, 1)
    g6 = (c / _ref(c, 1) > 1.098) & (h == c)
    g8 = _every(pd.Series(g6), 2); g9 = _every(pd.Series(g6), 5)
    g11 = ~_exist(pd.Series(g9), 90); g12 = ~_exist(pd.Series(g8), 7)
    dc = (v / _ref(v, 1) >= 2) & (v / v.rolling(100).mean() >= 2)
    dc1 = (v / _ref(v, 1) >= 3) & (v / v.rolling(100).mean() > 1.1)
    bb_arr = (l == np.round(_ref(c, 1) * 0.90, 2)).values
    dc2 = ((dc1 | dc) & g11 & g12 & g11 & (pd.Series(bb_arr).rolling(10, min_periods=1).sum().astype(float) < 2) & g10 & g3)
    dc3 = dc2.copy(); lt = -999
    for i in range(len(dc3)):
        if dc3.iloc[i] and i - lt > 3: lt = i
        elif i - lt <= 3: dc3.iloc[i] = False
    dc4 = ((h / _ref(c, 1) >= 1.07) & g3 & g10 & (h > c) & _exist(pd.Series(dc3).astype(float), 5) & (g4 > 0.01) & g12 & g11 & (v.astype(float) == v.rolling(5).max().astype(float)))
    dc5_arr = dc4.copy(); lt = -999
    for i in range(len(dc5_arr)):
        if dc5_arr.iloc[i] and i - lt > 15: lt = i
        elif i - lt <= 15: dc5_arr.iloc[i] = False
    factors["signal_dc5"] = int(dc5_arr.iloc[-1]) if len(dc5_arr) > 0 else 0

    # ── 趋势评分 ──
    cur = c.iloc[-1]
    ma5 = c.iloc[-5:].mean()
    ma10 = c.iloc[-10:].mean()
    ma20 = c.iloc[-20:].mean()
    if cur > ma5 > ma10 > ma20:
        trend = 0.9
    elif cur > ma5 and cur > ma10:
        trend = 0.7
    elif cur > ma20:
        trend = 0.5
    elif cur > ma5:
        trend = 0.3
    else:
        trend = 0.1
    if dif.iloc[-1] > dea.iloc[-1] and dif.iloc[-1] > 0:
        trend = min(1.0, trend + 0.1)
    factors["trend_score"] = round(trend, 3)

    # ── 量能评分 ──
    v_cur = v.iloc[-1]
    v_ma5 = v.iloc[-6:-1].mean() if len(v) >= 6 else v.iloc[:-1].mean()
    vr = v_cur / v_ma5 if v_ma5 > 0 else 1.0
    if 1.5 <= vr <= 3.0:
        v_score = 1.0
    elif 1.2 <= vr < 1.5:
        v_score = 0.7
    elif vr > 3.0:
        v_score = 0.6
    elif vr >= 0.8:
        v_score = 0.4
    else:
        v_score = 0.2
    if len(c) >= 2 and c.iloc[-1] > c.iloc[-2] and vr > 1.0:
        v_score = min(1.0, v_score + 0.1)
    factors["volume_score"] = round(v_score, 3)
    factors["vol_ratio"] = round(vr, 2)

    # ── 位置评分 ──
    h20 = h.iloc[-20:].max()
    l20 = l.iloc[-20:].min()
    pos = (cur - l20) / (h20 - l20) if h20 > l20 else 0.5
    if 0.2 <= pos <= 0.5:
        p_score = 1.0
    elif 0.5 < pos <= 0.7:
        p_score = 0.8
    elif 0.7 < pos <= 0.85:
        p_score = 0.5
    elif pos > 0.85:
        p_score = 0.2
    else:
        p_score = 0.6
    factors["position_score"] = round(p_score, 3)

    # ── 综合质量分 ──
    raw = factors.get("signal_resonance", 0)
    intensity = 1.0 if raw >= 2 else (0.6 if raw == 1 else 0.3)
    factors["quality_score"] = round(
        intensity * 0.25 + trend * 0.30 + v_score * 0.25 + p_score * 0.20, 3
    )

    # ── 动量评分 (5/10/20日收益加权) ──
    chg5 = (cur / c.iloc[-6] - 1) if len(c) >= 6 else 0
    chg10 = (cur / c.iloc[-11] - 1) if len(c) >= 11 else 0
    chg20 = (cur / c.iloc[-21] - 1) if len(c) >= 21 else 0
    momentum = (chg5 * 0.5 + chg10 * 0.3 + chg20 * 0.2)  # ~-0.15 to +0.30 typical
    momentum_norm = max(0, min(1, (momentum + 0.10) / 0.25))  # normalize to 0~1
    factors["momentum_score"] = round(momentum_norm, 3)
    factors["chg_5d"] = round(chg5, 4)
    factors["chg_10d"] = round(chg10, 4)
    factors["chg_20d"] = round(chg20, 4)

    # ── 突破强度评分 ──
    # 价格相对关键均线的突破程度
    breakout = 0.0
    if cur > ma5: breakout += 0.15
    if cur > ma10: breakout += 0.15
    if cur > ma20: breakout += 0.20
    if cur > c.iloc[-60:].mean(): breakout += 0.25  # 突破60日均线
    if cur > c.iloc[-120:].mean(): breakout += 0.25  # 突破120日均线
    factors["breakout_score"] = round(min(1.0, breakout), 3)

    # ── 综合强度评分 (power_score: 0-100) ──
    # 加权: 信号+质量+资金+动量+突破+趋势+量能
    power = (
        intensity * 15 +           # 信号强度 0-15
        v_score * 15 +             # 量能 0-15
        trend * 15 +               # 趋势 0-15
        p_score * 10 +             # 位置 0-10
        momentum_norm * 15 +       # 动量 0-15
        factors.get("capital_score", 50) / 100 * 20 +  # 资金 0-20
        breakout * 10              # 突破 0-10
    )
    factors["power_score"] = round(min(100, max(0, power)))

    # ── 买入信号等级 ──
    # 0=无, 1=观察, 2=关注, 3=强买入
    buy_level = 0
    signal_count = sum([
        1 if factors.get("signal_qlj") else 0,
        1 if factors.get("signal_ztxf") else 0,
        1 if factors.get("signal_xg") else 0,
        1 if factors.get("signal_b1") else 0,
        1 if factors.get("signal_final") else 0,
    ])
    if signal_count >= 3 and momentum_norm > 0.5 and v_score > 0.5:
        factors["buy_signal"] = 3  # 强买入: 多信号共振+动量+放量
    elif signal_count >= 2 and v_score > 0.4:
        factors["buy_signal"] = 2  # 关注: 双信号+量能
    elif signal_count >= 1 and v_score > 0.3:
        factors["buy_signal"] = 1  # 观察: 单信号
    else:
        factors["buy_signal"] = 0  # 无信号

    # ── ATR ──
    tr_14 = tr.iloc[-14:].mean()
    factors["atr_pct"] = round(tr_14 / cur, 4) if cur > 0 else 0.02

    # ── 其他 ──
    factors["ma_position"] = round((cur - ma20) / ma20, 3) if ma20 > 0 else 0
    factors["change_pct"] = round((c.iloc[-1] / c.iloc[-2] - 1), 4) if len(c) >= 2 else 0

    # ── 涨停判断 ──
    if len(c) >= 2:
        limit_up_price = c.iloc[-2] * 1.10
        factors["limit_up"] = 1 if abs(c.iloc[-1] - limit_up_price) < 0.01 else 0
    else:
        factors["limit_up"] = 0

    # ── 昨收 ──
    factors["pre_close"] = round(c.iloc[-2], 2) if len(c) >= 2 else 0
    factors["open"] = round(df["open"].iloc[-1], 2)
    factors["high"] = round(h.iloc[-1], 2)
    factors["low"] = round(l.iloc[-1], 2)
    factors["close"] = round(cur, 2)
    factors["volume"] = int(v.iloc[-1])

    # ══════════════════════════════════════════════════════════
    # 原站匹配因子 — 升级版算法
    # ══════════════════════════════════════════════════════════

    # ── 低开评分 V2 (greenPlateLowSuctionScore) ──
    # 核心逻辑: 低开≠弱，低开+有承接资金=主力洗盘吸筹
    # 评分维度:
    #   1. 低开幅度 (gap severity) — 低开越大，潜在机会越大
    #   2. 盘中承接 (intraday recovery) — 从低开到收盘的回升力度
    #   3. 量能确认 (volume confirmation) — 放量回升=主力介入
    #   4. 位置安全 (position safety) — 低位低开>高位低开
    #   5. 历史胜率 (historical pattern) — 近期低开后表现
    open_pct = (df["open"].iloc[-1] - factors["pre_close"]) / factors["pre_close"] if factors["pre_close"] > 0 else 0
    recovery = (cur - df["open"].iloc[-1]) / df["open"].iloc[-1] if df["open"].iloc[-1] > 0 else 0

    if open_pct < -0.005:  # 低开超过-0.5%
        # 1. 低开幅度分 (0-25): -0.5%~-3%最佳, >-5%恐慌过度
        gap_severity = abs(open_pct * 100)
        if gap_severity <= 3:
            gap_score = gap_severity / 3 * 25  # 低开越大分越高
        elif gap_severity <= 5:
            gap_score = 25 - (gap_severity - 3) / 2 * 10  # 递减
        else:
            gap_score = max(0, 25 - (gap_severity - 5) * 5)  # 恐慌低开降分

        # 2. 盘中承接分 (0-30): 从开盘到收盘的回升幅度
        if recovery > 0.03:
            recovery_score = 30  # 强力回升
        elif recovery > 0.01:
            recovery_score = 20 + recovery * 300  # 中等回升
        elif recovery > -0.01:
            recovery_score = 15  # 企稳
        elif recovery > -0.03:
            recovery_score = 8   # 弱承接
        else:
            recovery_score = 0   # 无承接，低开低走

        # 3. 量能确认分 (0-20): 回升放量=主力介入
        if recovery > 0 and vr > 1.2:
            vol_confirm = min(20, vr * 10)  # 放量回升加分
        elif recovery > 0 and vr > 0.8:
            vol_confirm = 10  # 量能正常
        elif recovery < 0 and vr < 1.0:
            vol_confirm = 5   # 缩量下跌不算太差
        else:
            vol_confirm = 0

        # 4. 位置安全分 (0-15): 中低位>高位
        if p_score > 0.7:
            pos_safety = 15
        elif p_score > 0.5:
            pos_safety = 10
        elif p_score > 0.3:
            pos_safety = 5
        else:
            pos_safety = 0

        # 5. 趋势配合分 (0-10): 上升趋势中低开=洗盘
        if trend > 0.6:
            trend_bonus = 10
        elif trend > 0.4:
            trend_bonus = 6
        elif trend > 0.2:
            trend_bonus = 3
        else:
            trend_bonus = 0

        low_suction = gap_score + recovery_score + vol_confirm + pos_safety + trend_bonus
    elif open_pct < 0:
        # 微低开: 基础30-50分
        low_suction = 30 + abs(open_pct * 500)
        if recovery > 0.01: low_suction += 10
    else:
        # 高开: 低分(0-30)
        low_suction = max(0, 30 - open_pct * 200)

    factors["low_suction_score"] = round(min(100, max(0, low_suction)))

    # ── 资金评分 V2 (capitalScore) ──
    # 核心逻辑: 多维度资金流向综合评分
    # 评分维度:
    #   1. MFI (资金流量指数) — 经典量价指标
    #   2. OBV趋势 — 能量潮方向
    #   3. 连续资金流 — 近期主力持续买/卖
    #   4. 大单判断 — 放量阳线=主力买
    #   5. 价量背离 — 价涨量缩=转弱信号
    #
    # MFI = 100 - 100/(1 + PMF/NMF)
    tp = (h + l + c) / 3
    tp_change = tp.diff().fillna(0)
    mf = tp * v
    pmf = mf.where(tp_change > 0, 0.0)
    nmf = mf.where(tp_change < 0, 0.0)
    pmf_14 = pmf.rolling(14, min_periods=1).sum()
    nmf_14 = nmf.rolling(14, min_periods=1).sum()
    mfi_val = 50.0
    try:
        ratio = pmf_14.iloc[-1] / max(1.0, nmf_14.iloc[-1])
        mfi_val = 100.0 - 100.0 / (1.0 + ratio)
    except:
        pass
    mfi_score = min(35, max(0, mfi_val * 0.35)) if not (np.isnan(mfi_val) or np.isinf(mfi_val)) else 15

    # OBV 趋势 (0-20)
    c_diff = c.diff().fillna(0)
    obv = (v * np.sign(c_diff)).cumsum()
    obv_score = 10
    try:
        if len(obv) >= 20:
            obv_ma20 = obv.rolling(20, min_periods=1).mean()
            if obv_ma20.iloc[-1] > 0:
                obv_trend = (obv.iloc[-1] - obv_ma20.iloc[-1]) / obv_ma20.iloc[-1]
                obv_score = max(0, min(20, 12 + obv_trend * 200))
    except:
        pass

    # 连续资金流 (0-20): 近10日阳线成交量占比
    bull_vol_sum = 0.0
    total_vol_sum = 0.0
    for i in range(-1, -11, -1):
        if len(c) >= abs(i) and len(v) >= abs(i):
            vi = float(v.iloc[i])
            total_vol_sum += vi
            if i > -len(c) and c.iloc[i] > c.iloc[i-1]:
                bull_vol_sum += vi
    bull_ratio = bull_vol_sum / total_vol_sum if total_vol_sum > 0 else 0.5
    flow_score = min(20, max(0, bull_ratio * 20))

    # 大单强度 (0-15): 结合量比和涨幅
    if vr > 1.5 and factors["change_pct"] > 0.02:
        big_order = 15  # 放量大涨=主力大量买入
    elif vr > 1.2 and factors["change_pct"] > 0:
        big_order = 12
    elif vr > 1.0 and factors["change_pct"] > -0.01:
        big_order = 8
    elif vr < 0.6 and factors["change_pct"] < 0:
        big_order = 5  # 缩量下跌，主力未出
    else:
        big_order = 3

    # 价量背离检测 (0-10): 分数越低背离越严重
    if factors["change_pct"] > 0.02 and vr < 0.8:
        diverge_score = 2  # 涨但缩量=背离风险
    elif factors["change_pct"] < -0.02 and vr > 1.5:
        diverge_score = 2  # 跌但放量=出货
    elif factors["change_pct"] > 0 and vr > 1.0:
        diverge_score = 10  # 价量配合良好
    elif factors["change_pct"] < 0 and vr < 0.8:
        diverge_score = 8   # 缩量下跌尚可
    else:
        diverge_score = 5

    capital = mfi_score + obv_score + flow_score + big_order + diverge_score
    factors["capital_score"] = round(min(100, max(0, capital)))

    # ── 机构强度 (institutionStrength) ──
    # 判断: 连续放量阳线天数 + 量比强度
    consec_bull_vol = 0
    for i in range(-1, -10, -1):
        if abs(i) < len(c) and c.iloc[i] > c.iloc[i-1] and v.iloc[i] > v.iloc[i-5:i].mean():
            consec_bull_vol += 1
        else:
            break
    vol_strength = vr if vr > 1 else 0.5
    inst_raw = consec_bull_vol * 0.15 + vol_strength * 0.3 + (1 if factors.get("signal_qlj") else 0) * 0.3
    if inst_raw > 0.8:
        factors["institution_strength"] = "super_high"
    elif inst_raw > 0.55:
        factors["institution_strength"] = "high"
    elif inst_raw > 0.3:
        factors["institution_strength"] = "middle"
    else:
        factors["institution_strength"] = "low"

    # ── 进出天数 (inOutDays) ──
    # 资金连续流入天数 vs 流出天数
    inflow_days = 0
    for i in range(-1, -20, -1):
        if abs(i) < len(c) and c.iloc[i] > c.iloc[i-1]:
            inflow_days += 1
        else:
            break
    factors["in_out_days"] = inflow_days if inflow_days > 0 else -consec_bull_vol

    # ── 需求区 (inDemandArea) ──
    # 价格在支撑位附近+缩量=需求区
    near_support = pos < 0.35 and factors["position_score"] > 0.5
    vol_contracting = vr < 0.8 and v_score > 0.3
    factors["in_demand_area"] = 1 if (near_support and vol_contracting) else 0

    # ── 主力 (mainUp) ──
    # 综合擒龙决信号+量能+趋势
    main_force = (1 if factors.get("signal_qlj") else 0) * 0.4 + (1 if factors.get("signal_xg") else 0) * 0.3 + v_score * 0.3
    factors["main_up"] = 1 if main_force > 0.45 else 0

    # ── 高控 (highControlUp) ──
    # 高控盘: 趋势好+位置中高+缩量上涨
    high_control = trend * 0.4 + p_score * 0.3 + (1 if vr < 1.5 and cur > df["open"].iloc[-1] else 0) * 0.3
    factors["high_control_up"] = 1 if high_control > 0.55 else 0

    # ── 擒龙信号 (catchBullSignalTime) ──
    # 信号共振等级
    if factors.get("signal_resonance", 0) >= 2:
        factors["three_axes_signal"] = 1  # 三轴共振: 擒龙决+涨停先锋+牛线突破
        factors["double_axes_signal"] = 0
    elif factors.get("signal_resonance", 0) == 1 or factors.get("signal_final", 0) == 1:
        factors["three_axes_signal"] = 0
        factors["double_axes_signal"] = 1  # 双轴信号
    else:
        factors["three_axes_signal"] = 0
        factors["double_axes_signal"] = 0
    # ── 信号入池时间戳 ──
    entry_time = ""
    buy_level = factors.get("buy_signal", 0)
    if buy_level >= 3:
        entry_time = "收盘确认 15:00"
    elif buy_level >= 2:
        if factors.get("signal_qlj") and factors.get("signal_ztxf"):
            entry_time = "尾盘双确认 14:55"
        elif factors.get("signal_xg"):
            entry_time = "牛线突破 14:30"
        else:
            entry_time = "尾盘选股 14:55"
    elif buy_level >= 1:
        if factors.get("signal_qlj") or factors.get("signal_ztxf"):
            entry_time = "盘中触发 10:30"
        elif factors.get("signal_xg"):
            entry_time = "盘中突破 14:00"
        else:
            entry_time = "盘中关注 14:30"

    signal_date = ""
    if buy_level >= 1:
        signal_date = datetime.now().strftime("%m-%d")
    factors["entry_time"] = entry_time
    factors["signal_date"] = signal_date
    factors["catch_bull_signal_time"] = (signal_date + " " + entry_time).strip() if signal_date else ""

    # ── 开幅 (openPctChangeRate) ──
    factors["open_pct"] = round(open_pct * 100, 2)

    # ── 当日盈亏 (dailyProfitAndLossRate) ──
    factors["daily_pl"] = round(factors["change_pct"] * 100, 2)

    return factors


@dataclass
class StockInfo:
    """股票信息 + 因子 — 匹配原站全部列。"""
    symbol: str
    name: str = ""
    close: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    pre_close: float = 0.0
    volume: int = 0
    change_pct: float = 0.0
    open_pct: float = 0.0           # 开幅
    daily_pl: float = 0.0           # 当日盈亏
    vol_ratio: float = 0.0
    quality_score: float = 0.0
    trend_score: float = 0.0
    volume_score: float = 0.0
    position_score: float = 0.0
    atr_pct: float = 0.0
    signal_xg: int = 0
    signal_b1: int = 0
    signal_final: int = 0
    signal_qlj: int = 0
    signal_ztxf: int = 0
    signal_resonance: int = 0
    signal_bandit: int = 0          # 波段擒妖(源码版)
    limit_up: int = 0
    ma_position: float = 0.0
    # 原站新增字段
    low_suction_score: int = 0
    capital_score: int = 0
    institution_strength: str = "low"
    in_out_days: int = 0
    in_demand_area: int = 0
    main_up: int = 0
    high_control_up: int = 0
    three_axes_signal: int = 0
    double_axes_signal: int = 0
    catch_bull_signal_time: str = ""
    entry_time: str = ""
    signal_date: str = ""
    # 新增精度字段
    power_score: float = 0.0
    momentum_score: float = 0.0
    breakout_score: float = 0.0
    buy_signal: int = 0
    ma_bull_score: float = 0.0    # 均线多头评分
    chg_5d: float = 0.0
    chg_10d: float = 0.0
    industry: str = ""
    # 12因子扩展
    chg_score: float = 0.0
    rsi_score: float = 0.0
    macd_score: float = 0.0
    boll_score: float = 0.0
    atr_score: float = 0.0
    vol_score: float = 0.0
    bias_score: float = 0.0
    money_score: float = 0.0
    turnover_score: float = 0.0


def compute_all_stocks(
    stock_data: dict[str, pd.DataFrame],
    signal_filter: str = "signal_final",
    min_quality: float = 0.0,
    sort_by: str = "quality_score",
    limit: int = 100,
) -> list[StockInfo]:
    """批量计算所有股票因子, 筛选并排序。

    Args:
        stock_data: {symbol: DataFrame}
        signal_filter: 筛选信号类型 (signal_final, signal_xg, signal_qlj, signal_ztxf, signal_resonance, all)
        min_quality: 最低质量分
        sort_by: 排序字段
        limit: 返回数量上限

    Returns:
        排序后的股票列表
    """
    results: list[StockInfo] = []

    for symbol, df in stock_data.items():
        try:
            factors = compute_factors(df)
            if not factors:
                continue

            # 信号筛选
            if signal_filter != "all":
                sig_val = factors.get(signal_filter, 0)
                if sig_val <= 0:
                    continue

            # 质量筛选
            quality = factors.get("quality_score", 0)
            if quality < min_quality:
                continue

            info = StockInfo(
                symbol=symbol,
                name=get_stock_name(symbol),
                close=factors.get("close", 0),
                open=factors.get("open", 0),
                high=factors.get("high", 0),
                low=factors.get("low", 0),
                pre_close=factors.get("pre_close", 0),
                volume=factors.get("volume", 0),
                change_pct=factors.get("change_pct", 0),
                vol_ratio=factors.get("vol_ratio", 0),
                quality_score=quality,
                trend_score=factors.get("trend_score", 0),
                volume_score=factors.get("volume_score", 0),
                position_score=factors.get("position_score", 0),
                atr_pct=factors.get("atr_pct", 0),
                signal_xg=factors.get("signal_xg", 0),
                signal_b1=factors.get("signal_b1", 0),
                signal_final=factors.get("signal_final", 0),
                signal_qlj=factors.get("signal_qlj", 0),
                signal_ztxf=factors.get("signal_ztxf", 0),
                signal_resonance=factors.get("signal_resonance", 0),
                limit_up=factors.get("limit_up", 0),
                ma_position=factors.get("ma_position", 0),
            )
            results.append(info)
        except Exception:
            continue

    # 排序
    sort_keys = {
        "quality_score": lambda x: x.quality_score,
        "change_pct": lambda x: x.change_pct,
        "vol_ratio": lambda x: x.vol_ratio,
        "trend_score": lambda x: x.trend_score,
        "volume_score": lambda x: x.volume_score,
        "signal_resonance": lambda x: x.signal_resonance,
        "close": lambda x: x.close,
        "atr_pct": lambda x: x.atr_pct,
    }

    key_fn = sort_keys.get(sort_by, sort_keys["quality_score"])
    results.sort(key=key_fn, reverse=True)

    return results[:limit]


def compute_factors_for_date(df: pd.DataFrame, target_date: str) -> dict | None:
    """计算指定日期的因子（截断数据到目标日期）。

    Args:
        df: 完整的历史DataFrame
        target_date: 目标日期 (YYYY-MM-DD)

    Returns:
        因子字典，数据不足返回None
    """
    try:
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
    except ValueError:
        return None

    # 截断到目标日期
    df_cut = df[df.index <= target_dt]
    if len(df_cut) < 60:
        return None

    # 传递目标日期，让 signal_date/entry_time 使用正确日期
    factors = compute_factors(df_cut)
    if factors:
        # 覆盖日期相关字段
        date_str = target_dt.strftime("%m-%d")
        buy_level = factors.get("buy_signal", 0)
        if buy_level >= 1:
            factors["signal_date"] = date_str
            et = factors.get("entry_time", "")
            factors["catch_bull_signal_time"] = (date_str + " " + et).strip() if et else date_str
        else:
            factors["signal_date"] = ""
            factors["catch_bull_signal_time"] = ""
    return factors


def get_stock_kline(stock_data: dict[str, pd.DataFrame], symbol: str, days: int = 120) -> list[dict]:
    """获取某只股票最近 N 天 K线数据。"""
    df = stock_data.get(symbol)
    if df is None or df.empty:
        return []

    df_tail = df.iloc[-days:]
    klines = []
    for idx, row in df_tail.iterrows():
        klines.append({
            "date": idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx),
            "open": round(row["open"], 2),
            "high": round(row["high"], 2),
            "low": round(row["low"], 2),
            "close": round(row["close"], 2),
            "volume": int(row["volume"]),
        })
    return klines


# ======================================================================
# P0-2: Parquet 缓存 — 替代 284MB pickle.gz
# ======================================================================

_HAS_PARQUET = None


def _check_parquet() -> bool:
    """检测 pyarrow/fastparquet 是否可用。"""
    global _HAS_PARQUET
    if _HAS_PARQUET is None:
        try:
            import pyarrow
            _HAS_PARQUET = True
        except ImportError:
            try:
                import fastparquet
                _HAS_PARQUET = True
            except ImportError:
                _HAS_PARQUET = False
                print("[DataLoader] pyarrow/fastparquet 不可用，回退到 pickle.gz 格式")
    return _HAS_PARQUET


def save_stock_data_cache(stock_data: dict, path: str) -> bool:
    """将 STOCK_DATA 保存为 parquet 格式 (zstd压缩, 约80MB)。

    Args:
        stock_data: {symbol: DataFrame(columns=[open,high,low,close,volume])}
        path: 输出路径 (建议 .parquet)

    Returns:
        True 成功, False 回退
    """
    if not _check_parquet():
        return False
    try:
        import time as _t
        _t0 = _t.time()
        frames = []
        for symbol, df in stock_data.items():
            if df is None or df.empty:
                continue
            df_copy = df.copy()
            df_copy.reset_index(inplace=True)
            df_copy['date'] = df_copy['date'].astype(str)
            df_copy['symbol'] = symbol
            frames.append(df_copy)
        if not frames:
            print("[DataLoader] 无数据可保存")
            return False
        full_df = pd.concat(frames, ignore_index=True)
        full_df.to_parquet(path, compression='zstd', index=False)
        print(f"[DataLoader] Parquet 缓存已保存: {path} ({os.path.getsize(path)/1024/1024:.1f}MB, {_t.time()-_t0:.1f}s)")
        return True
    except Exception as e:
        print(f"[DataLoader] Parquet 保存失败({e})，回退到 pickle.gz")
        return False


# 全局: 最后一次加载的数据范围 (根治日期硬编码问题)
_LAST_DATA_RANGE = (None, None)


def get_data_range():
    """返回最后一次 load_stock_data_cache() 的数据日期范围 (min, max) 或 (None, None)"""
    return _LAST_DATA_RANGE


def load_stock_data_cache(path: str, keep_days: int = 60) -> dict | None:
    """从 parquet 格式加载 STOCK_DATA。

    Args:
        path: .parquet 文件路径
        keep_days: 每只股票保留最近N天（默认120天），节省内存

    Returns:
        {symbol: DataFrame} 或 None (失败时)
    """
    if not _check_parquet():
        return None
    try:
        import time as _t
        _t0 = _t.time()
        full_df = pd.read_parquet(path)
        full_df['date'] = pd.to_datetime(full_df['date'])
        # 分区优化: 只保留最近N天 (keep_days=0=全量,用于回测)
        if keep_days > 0:
            _cutoff = full_df['date'].max() - pd.Timedelta(days=keep_days)
            full_df = full_df[full_df['date'] >= _cutoff]
            print(f"[DataLoader] 截断至最近{keep_days}天 ({_cutoff.date()} ~ {full_df['date'].max().date()})")
        stock_data: dict[str, pd.DataFrame] = {}
        # P0修复: 分块加载，每500只让出GIL — 避免启动时阻塞Flask
        for _i, (symbol, group_df) in enumerate(full_df.groupby('symbol', sort=False)):
            df = group_df.drop(columns=['symbol']).set_index('date').sort_index()
            stock_data[symbol] = df
            if _i > 0 and _i % 500 == 0:
                _t.sleep(0.1)  # 让出GIL给Flask
        # 计算实际数据范围 (所有股票并集)
        _all_dates = set()
        for _df in list(stock_data.values())[:100]:
            _all_dates.update(_df.index)
        _d_min = min(_all_dates) if _all_dates else _cutoff
        _d_max = max(_all_dates) if _all_dates else full_df['date'].max()
        _data_range = (str(_d_min)[:10], str(_d_max)[:10])
        print(f"[DataLoader] Parquet 缓存已加载: {len(stock_data)} stocks × {keep_days}d, {os.path.getsize(path)/1024/1024:.1f}MB, {_t.time()-_t0:.1f}s")
        # 存为模块全局, 供 get_data_range() 查询
        global _LAST_DATA_RANGE
        _LAST_DATA_RANGE = _data_range

        # B1: 合并退市股 (消除幸存者偏差)
        stock_data = merge_delisted_to_universe(stock_data, keep_days=keep_days)

        return stock_data
    except Exception as e:
        print(f"[DataLoader] Parquet 加载失败({e})")
        return None


def convert_legacy_cache_to_parquet(legacy_path: str, parquet_path: str) -> bool:
    """将旧 pickle.gz 缓存转换为 parquet 格式 (一次性迁移)。

    Args:
        legacy_path: 旧的 stock_data.pkl.gz 路径
        parquet_path: 目标 .parquet 路径

    Returns:
        True 成功, False 失败
    """
    import pickle as _pk
    import gzip as _gz
    try:
        if not os.path.exists(legacy_path):
            print(f"[DataLoader] 旧缓存不存在: {legacy_path}")
            return False
        print(f"[DataLoader] 迁移旧缓存 → parquet: {os.path.getsize(legacy_path)/1024/1024:.0f}MB...")
        stock_data = _pk.load(_gz.open(legacy_path, 'rb'))
        ok = save_stock_data_cache(stock_data, parquet_path)
        if ok:
            # 迁移成功后备份旧文件
            backup = legacy_path + ".legacy_backup"
            try:
                os.replace(legacy_path, backup)
                print(f"[DataLoader] 旧缓存已备份: {backup}")
            except Exception:
                pass
        return ok
    except Exception as e:
        print(f"[DataLoader] 迁移失败: {e}")
        return False


def get_stock_data_cache_path() -> str | None:
    """返回当前最佳 stock_data 缓存路径 (parquet > gzip > pickle)。"""
    base = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base, "stock_data.parquet"),
        os.path.join(base, "stock_data.pkl.gz"),
        os.path.join(base, "stock_data.pkl"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def load_stock_data_from_cache(cache_path: str = None) -> dict | None:
    """从任意格式缓存加载 STOCK_DATA (parquet/gzip/pickle 自动检测)。

    外部模块统一入口 — 不再直接依赖 pickle.load(gzip.open(...))
    """
    import pickle as _pk
    import gzip as _gz
    if cache_path is None:
        cache_path = get_stock_data_cache_path()
    if cache_path is None:
        return None
    try:
        if cache_path.endswith('.parquet'):
            return load_stock_data_cache(cache_path)
        elif cache_path.endswith('.gz'):
            return _pk.load(_gz.open(cache_path, 'rb'))
        else:
            return _pk.load(open(cache_path, 'rb'))
    except Exception as e:
        # 损坏时返回None，调用方自行处理
        return None


# ══════════════════════════════════════════════════════════════════════
# B1: 幸存者偏差修正 — Point-in-Time 过滤器 (2026-07-18)
# ══════════════════════════════════════════════════════════════════════

_STATUS_CACHE = None  # {symbol: {listed, delisted, name, active}}


def _load_stock_status() -> dict:
    """加载 stock_status.json (懒加载+缓存)"""
    global _STATUS_CACHE
    if _STATUS_CACHE is not None:
        return _STATUS_CACHE

    status_path = os.path.join(os.path.dirname(__file__), "stock_status.json")
    if not os.path.exists(status_path):
        print("[B1] stock_status.json 不存在, 先运行 baostock_sync.py")
        _STATUS_CACHE = {}
        return _STATUS_CACHE

    try:
        import json
        with open(status_path, 'r', encoding='utf-8') as f:
            _STATUS_CACHE = json.load(f)
        n_delisted = sum(1 for v in _STATUS_CACHE.values() if not v.get('active', True))
        print(f"[B1] 股票状态已加载: {len(_STATUS_CACHE)}只 (退市{n_delisted}只)")
    except Exception as e:
        print(f"[B1] stock_status.json 加载失败: {e}")
        _STATUS_CACHE = {}
    return _STATUS_CACHE


def load_delisted_stocks() -> dict[str, pd.DataFrame]:
    """加载退市股K线数据 (从 delisted_stocks.parquet)

    Returns:
        {symbol: DataFrame(index=date, columns=[open,high,low,close,volume,amount])}
        空dict如果没有退市数据
    """
    delisted_path = r"D:\quant_framework\delisted_stocks.parquet"
    if not os.path.exists(delisted_path):
        return {}

    try:
        full_df = pd.read_parquet(delisted_path)
        full_df['date'] = pd.to_datetime(full_df['date'])
        result = {}
        for symbol, group_df in full_df.groupby('symbol', sort=False):
            df = group_df.drop(columns=['symbol']).set_index('date').sort_index()
            result[symbol] = df
        print(f"[B1] 退市股数据已加载: {len(result)}只")
        return result
    except Exception as e:
        print(f"[B1] 退市股加载失败: {e}")
        return {}


def get_tradable_stocks(date_str: str, stock_data: dict = None,
                        include_delisted: bool = True) -> list[str]:
    """Point-in-Time 过滤: 返回某日期可交易的股票列表

    Args:
        date_str: '2020-06-15' 或 '20200615'
        stock_data: 主数据 {symbol: DataFrame} (可选, 用于补全无状态的股票)
        include_delisted: True=包含退市股, False=仅现存股(旧行为)

    Returns:
        可交易股票symbol列表
    """
    # 标准化日期
    date_str = date_str.replace('-', '')[:8]

    status = _load_stock_status()

    if not status and stock_data:
        # 无状态文件 → 退回旧行为 (所有股都视为一直存活)
        return list(stock_data.keys())

    tradable = []
    for sym, info in status.items():
        listed = str(info.get('listed', '')).replace('-', '')[:8]
        delisted = str(info.get('delisted', '')).replace('-', '')[:8] if info.get('delisted') else None

        # 还没上市 → 不可交易
        if listed and date_str < listed:
            continue
        # 已退市 → 不可交易
        if delisted and date_str >= delisted:
            continue
        # 停牌/不活跃 → 跳过 (但active=True的保留)
        if not info.get('active', True):
            continue

        tradable.append(sym)

    # 对于stock_data里有但status里没有的股 (新上市, baostock未收录), 视为可交易
    if stock_data:
        status_set = set(status.keys())
        for sym in stock_data:
            if sym not in status_set:
                tradable.append(sym)

    return tradable


def merge_delisted_to_universe(stock_data: dict[str, pd.DataFrame],
                               keep_days: int = 0) -> dict[str, pd.DataFrame]:
    """将退市股合并到主股票池, 返回全量 {symbol: DataFrame}

    Args:
        stock_data: 现有存活股数据
        keep_days: >0时只保留每只股票最近N天 (节省内存)

    Returns:
        合并后的全量数据 (含退市股)
    """
    delisted = load_delisted_stocks()
    if not delisted:
        return stock_data

    merged = dict(stock_data)
    for sym, df in delisted.items():
        if sym not in merged:
            # 按keep_days截断
            if keep_days > 0 and len(df) > keep_days:
                df = df.iloc[-keep_days:]
            merged[sym] = df

    n_added = len(delisted)
    print(f"[B1] 全量股票池: {len(merged)}只 (原有{len(stock_data)} + 退市{n_added})")
    return merged


def get_survivorship_stats() -> dict:
    """返回幸存者偏差统计 (供诊断用)"""
    status = _load_stock_status()
    if not status:
        return {'error': 'stock_status.json 不存在'}

    total = len(status)
    active = sum(1 for v in status.values() if v.get('active', True))
    delisted = total - active

    # 按年份统计退市数
    by_year = {}
    for sym, info in status.items():
        d = info.get('delisted')
        if d and len(d) >= 4:
            year = d[:4]
            by_year[year] = by_year.get(year, 0) + 1

    return {
        'total_stocks_ever': total,
        'active_today': active,
        'delisted': delisted,
        'delisted_pct': round(delisted / max(total, 1) * 100, 1),
        'delisted_by_year': dict(sorted(by_year.items())),
        'estimated_bias': f'{delisted / max(active, 1) * 100:.0f}%',  # 退市股占比≈偏差量级
    }
