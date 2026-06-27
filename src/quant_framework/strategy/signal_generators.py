"""P2-1: 15个内置策略信号生成器 — 每个返回 +1(买入)/0(观望)/-1(卖出)。

所有函数接口统一: generate_xxx(df: pd.DataFrame) -> int
df 必须是包含 columns=['open','high','low','close','volume'] 的日线数据，按时间升序排列。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# ═══════════════════════════════════════════
# 趋势类 (5个)
# ═══════════════════════════════════════════

def generate_ma_cross(df: pd.DataFrame) -> int:
    """快慢双均线金叉死叉。"""
    if len(df) < 21:
        return 0
    close = df["close"].values
    ma5 = np.mean(close[-5:])
    ma20 = np.mean(close[-20:])
    ma5_prev = np.mean(close[-6:-1])
    ma20_prev = np.mean(close[-21:-1])
    if ma5 > ma20 and ma5_prev <= ma20_prev:
        return 1   # 金叉
    if ma5 < ma20 and ma5_prev >= ma20_prev:
        return -1  # 死叉
    return 0


def generate_macd_cross(df: pd.DataFrame) -> int:
    """MACD DIF/DEA 金叉死叉。"""
    if len(df) < 35:
        return 0
    close = df["close"].values
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    dif = ema12 - ema26
    dea = _ema(np.array([dif[-1]]), 9) if len(dif) > 0 else 0
    # 简化: 用最近两日DIF方向
    if len(dif) < 3:
        return 0
    if dif[-1] > dif[-2] and dif[-2] <= dif[-3]:
        return 1
    if dif[-1] < dif[-2] and dif[-2] >= dif[-3]:
        return -1
    return 0


def generate_ma_condition(df: pd.DataFrame) -> int:
    """价格在MA20之上 + 站上MA60 → 买入。"""
    if len(df) < 61:
        return 0
    close = df["close"].values
    last = close[-1]
    ma20 = np.mean(close[-20:])
    ma60 = np.mean(close[-60:])
    if last > ma20 and last > ma60 and ma20 > ma60:
        return 1
    if last < ma20 and last < ma60:
        return -1
    return 0


def generate_breakout(df: pd.DataFrame) -> int:
    """突破前N日最高价买入（N=20）。"""
    if len(df) < 21:
        return 0
    close = df["close"].values
    high = df["high"].values
    last = close[-1]
    prev_high = np.max(high[-21:-1])
    if last > prev_high:
        return 1
    prev_low = np.min(df["low"].values[-21:-1])
    if last < prev_low:
        return -1
    return 0


def generate_channel(df: pd.DataFrame) -> int:
    """唐奇安通道突破（20日高低轨）。"""
    if len(df) < 21:
        return 0
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    upper = np.max(high[-21:-1])
    lower = np.min(low[-21:-1])
    if close[-1] > upper:
        return 1
    if close[-1] < lower:
        return -1
    return 0


# ═══════════════════════════════════════════
# 动量类 (4个)
# ═══════════════════════════════════════════

def generate_momentum(df: pd.DataFrame) -> int:
    """N日收益率动量（N=10）。"""
    if len(df) < 11:
        return 0
    close = df["close"].values
    ret = (close[-1] / close[-10] - 1) * 100
    if ret > 5:
        return 1
    if ret < -5:
        return -1
    return 0


def generate_rsi_reversal(df: pd.DataFrame) -> int:
    """RSI < 30 超卖反弹 / RSI > 70 超买回调。"""
    if len(df) < 15:
        return 0
    close = df["close"].values
    rsi = _rsi(close, 14)
    if rsi < 30:
        return 1
    if rsi > 70:
        return -1
    return 0


def generate_volatility_expansion(df: pd.DataFrame) -> int:
    """波动率扩张突破（ATR放大>1.5倍均值 + 价格上涨）。"""
    if len(df) < 21:
        return 0
    high = df["high"].values
    low = df["low"].values
    close = df["close"].values
    tr = np.maximum(high[-20:] - low[-20:],
                    np.abs(high[-20:] - np.roll(close[-20:], 1)))
    tr[0] = high[-20] - low[-20]
    atr_now = np.mean(tr[-5:])
    atr_hist = np.mean(tr)
    if atr_hist == 0:
        return 0
    if atr_now / atr_hist > 1.5 and close[-1] > close[-2]:
        return 1
    return 0


def generate_gap_trading(df: pd.DataFrame) -> int:
    """跳空回补策略: 低开超2% → 买入博回补；高开超3% → 卖出。"""
    if len(df) < 2:
        return 0
    open_today = df["open"].values[-1]
    close_yesterday = df["close"].values[-2]
    if close_yesterday == 0:
        return 0
    gap = (open_today / close_yesterday - 1) * 100
    if gap < -2:
        return 1
    if gap > 3:
        return -1
    return 0


# ═══════════════════════════════════════════
# 均值回归类 (3个)
# ═══════════════════════════════════════════

def generate_mean_reversion(df: pd.DataFrame) -> int:
    """布林带触及下轨买入/上轨卖出。"""
    if len(df) < 21:
        return 0
    close = df["close"].values
    ma20 = np.mean(close[-20:])
    std20 = np.std(close[-20:])
    if std20 == 0:
        return 0
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    if close[-1] <= lower:
        return 1
    if close[-1] >= upper:
        return -1
    return 0


def generate_pairs_mean_reversion(df: pd.DataFrame) -> int:
    """配对均值回归简化版: 距MA20偏离>2倍标准差 → 回归。"""
    if len(df) < 21:
        return 0
    close = df["close"].values
    ma20 = np.mean(close[-20:])
    std20 = np.std(close[-20:])
    if std20 == 0 or ma20 == 0:
        return 0
    z = (close[-1] - ma20) / std20
    if z < -2:
        return 1
    if z > 2:
        return -1
    return 0


def generate_volume_mean_reversion(df: pd.DataFrame) -> int:
    """缩量至5日均量50%以下 → 反弹买入。"""
    if len(df) < 11:
        return 0
    volume = df["volume"].values
    # 无成交量数据时回退
    if volume[-1] == 0:
        return 0
    avg_vol5 = np.mean(volume[-6:-1]) if len(volume) >= 6 else np.mean(volume[-1:])
    if avg_vol5 == 0:
        return 0
    ratio = volume[-1] / avg_vol5
    if ratio < 0.5:
        return 1
    return 0


# ═══════════════════════════════════════════
# 事件驱动类 (3个)
# ═══════════════════════════════════════════

def generate_limit_up_follow(df: pd.DataFrame) -> int:
    """涨停后次日策略: 昨日涨停 + 今日高开<3% → 买入。"""
    if len(df) < 2:
        return 0
    close = df["close"].values
    open_today = df["open"].values[-1]
    if len(close) < 2:
        return 0
    chg_yesterday = (close[-2] / close[-3] - 1) * 100 if len(close) >= 3 else 0
    if chg_yesterday >= 9.8 and close[-2] > 0:
        open_chg = (open_today / close[-2] - 1) * 100
        if 0 < open_chg < 3:
            return 1
    return 0


def generate_high_turnover(df: pd.DataFrame) -> int:
    """高换手率强势: 近5日成交额>5日均额1.5倍 + 价格上涨 → 买入。"""
    if len(df) < 6:
        return 0
    volume = df["volume"].values
    close = df["close"].values
    if len(volume) < 6:
        return 0
    avg_vol5 = np.mean(volume[-6:-1]) if len(volume) >= 6 else volume[-1]
    if avg_vol5 == 0:
        return 0
    if volume[-1] / avg_vol5 > 1.5 and close[-1] > close[-2]:
        return 1
    return 0


def generate_institutional_tracking(df: pd.DataFrame) -> int:
    """机构跟踪简化版: 连续3日量价齐升 → 资金流入信号。"""
    if len(df) < 4:
        return 0
    close = df["close"].values
    volume = df["volume"].values
    up_days = 0
    for i in range(1, 4):
        if len(close) > i and len(volume) > i:
            if close[-i] > close[-i-1] and volume[-i] > volume[-i-1]:
                up_days += 1
    if up_days >= 3:
        return 1
    return 0


# ═══════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════

def _ema(data: np.ndarray, period: int) -> float:
    """指数移动平均。"""
    if len(data) < period:
        return np.mean(data) if len(data) > 0 else 0.0
    alpha = 2.0 / (period + 1)
    result = np.mean(data[:period])
    for x in data[period:]:
        result = alpha * x + (1 - alpha) * result
    return result


def _rsi(close: np.ndarray, period: int = 14) -> float:
    """RSI计算。"""
    if len(close) < period + 1:
        return 50.0
    diffs = np.diff(close[-period-1:])
    gains = diffs[diffs > 0].sum() if len(diffs[diffs > 0]) > 0 else 0
    losses = abs(diffs[diffs < 0]).sum() if len(diffs[diffs < 0]) > 0 else 0
    if losses == 0:
        return 100.0
    rs = gains / losses
    return float(100.0 - 100.0 / (1.0 + rs))


# ═══════════════════════════════════════════
# 策略注册表 — 名称 → 函数映射
# ═══════════════════════════════════════════

ALL_SIGNAL_GENERATORS: dict[str, callable] = {
    # 趋势类
    "ma_cross": generate_ma_cross,
    "macd_cross": generate_macd_cross,
    "ma_condition": generate_ma_condition,
    "breakout": generate_breakout,
    "channel": generate_channel,
    # 动量类
    "momentum": generate_momentum,
    "rsi_reversal": generate_rsi_reversal,
    "volatility_expansion": generate_volatility_expansion,
    "gap_trading": generate_gap_trading,
    # 均值回归类
    "mean_reversion": generate_mean_reversion,
    "pairs_mean_reversion": generate_pairs_mean_reversion,
    "volume_mean_reversion": generate_volume_mean_reversion,
    # 事件驱动类
    "limit_up_follow": generate_limit_up_follow,
    "high_turnover": generate_high_turnover,
    "institutional_tracking": generate_institutional_tracking,
}


def get_signal(name: str, df: pd.DataFrame) -> int:
    """获取单个策略的信号。"""
    gen = ALL_SIGNAL_GENERATORS.get(name)
    if gen is None:
        return 0
    try:
        return gen(df)
    except Exception:
        return 0


def get_all_signals(df: pd.DataFrame) -> dict[str, int]:
    """获取全部15个策略的信号。"""
    return {name: get_signal(name, df) for name in ALL_SIGNAL_GENERATORS}
