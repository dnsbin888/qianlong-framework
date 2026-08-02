"""竞价抢筹信号策略 v1.0 — 集合竞价异动检测
对标: 游资竞价战法 + 私募多因子竞价模型

核心逻辑:
  集合竞价(9:15-9:25)出现异常放量+高开 → 主力抢筹
  → 9:30开盘后顺势买入 → 当天冲高获利

三层过滤:
  1. 竞价量比 > 3 (集合竞价成交量远超近期均值)
  2. 竞价涨幅 2%-8% (太高=透支, 太低=无力度)
  3. 开盘方向确认 (前5分钟不跌回)
"""
import numpy as np


def signal_jingjia_surge(bar_history, prev_close, code=""):
    """竞价抢筹主信号 — 量价齐升

    条件:
      1. 竞价量 > 5日均量 × 3
      2. 竞价涨幅 2%-8%
      3. 开盘后5分钟未跌破竞价价
    """
    if bar_history is None:
        return False
    c = bar_history.get("c", [])
    v = bar_history.get("v", [])
    o = bar_history.get("o", [])
    if len(c) < 5 or len(v) < 5:
        return False

    # 9:30第一根K线 = 开盘价
    open_price = o[0] if o else c[0]
    if open_price <= 0 or prev_close <= 0:
        return False

    # 竞价涨幅 2%-8%
    jj_chg = (open_price / prev_close - 1) * 100
    if jj_chg < 2 or jj_chg > 8:
        return False

    # 竞价量 (第一根K线量) > 5日均量 × 3
    if len(v) < 6:
        return False
    avg_vol_5 = np.mean(v[1:6]) if len(v) >= 6 else np.mean(v[1:])
    jj_vol_ratio = v[0] / max(avg_vol_5, 1)
    if jj_vol_ratio < 3:
        return False

    # 开盘后5分钟确认: 价格未跌回2%以内 (防止假突破)
    if len(c) >= 5:
        low_5min = min(c[:5])
        if (low_5min / prev_close - 1) * 100 < 1.5:
            return False  # 开盘即回落, 假信号

    return True


def signal_jingjia_continuation(bar_history, prev_close, code=""):
    """竞价接力 — 昨日涨停+今日竞价继续放量高开

    适用: 连板股接力 (游资最爱)
    """
    if bar_history is None or prev_close <= 0:
        return False
    c = bar_history.get("c", [])
    v = bar_history.get("v", [])
    o = bar_history.get("o", [])
    if len(c) < 5 or len(v) < 5:
        return False

    open_price = o[0] if o else c[0]
    jj_chg = (open_price / prev_close - 1) * 100

    # 竞价高开 3%-9%
    if jj_chg < 3 or jj_chg > 9:
        return False

    # 竞价量 > 5日均量 × 5 (更高门槛)
    if len(v) < 6: return False
    avg_vol_5 = np.mean(v[1:6]) if len(v) >= 6 else np.mean(v[1:])
    if v[0] < avg_vol_5 * 5:
        return False

    return True


def signal_jingjia_divergence(bar_history, prev_close, code=""):
    """竞价弱转强 — 竞价低开但放量, 开盘后迅速翻红

    适用: 昨日冲高回落的票, 次日竞价分歧后转一致
    """
    if bar_history is None or prev_close <= 0:
        return False
    c = bar_history.get("c", [])
    v = bar_history.get("v", [])
    o = bar_history.get("o", [])
    if len(c) < 5 or len(v) < 5:
        return False

    open_price = o[0] if o else c[0]
    jj_chg = (open_price / prev_close - 1) * 100

    # 竞价低开 -2% ~ +1% (分歧)
    if jj_chg < -2 or jj_chg > 1:
        return False

    # 竞价放量 > 2倍
    if len(v) < 6: return False
    avg_vol_5 = np.mean(v[1:6]) if len(v) >= 6 else np.mean(v[1:])
    if v[0] < avg_vol_5 * 2:
        return False

    # 开盘后5分钟价格回升 > 0.5%
    if len(c) >= 5:
        current = c[4]
        if (current / prev_close - 1) * 100 < 0.5:
            return False

    return True


# ═══════════════════════════════════
# 注册
# ═══════════════════════════════════

JINGJIA_SIGNALS = {
    "竞价-放量抢筹": signal_jingjia_surge,
    "竞价-接力高开": signal_jingjia_continuation,
    "竞价-弱转强": signal_jingjia_divergence,
}
