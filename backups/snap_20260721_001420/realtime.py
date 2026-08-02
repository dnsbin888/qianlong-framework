"""超跌反弹 + 弱转强 实时确认 (QMT handle_bar调用)
数据源: QMT tick → 输出: True/False
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from signals.reversal.weights import (
    OVERSOLD_Z_MIN, OVERSOLD_CONNORS_MAX, OVERSOLD_STABILIZE_MAX, OVERSOLD_VOL_MIN,
    WTS_VOL_MIN, WTS_TURNOVER_MIN, WTS_TURNOVER_MAX, WTS_PRICE_MIN
)

def confirm_oversold(sym, tick, prev_close):
    """QMT实时: 超跌反弹确认 — 已有日线候选, tick数据验证
    tick: QMT get_full_tick 返回的dict
    返回: True=触发买入, False=不触发
    """
    try:
        price = tick.get('lastPrice', 0)
        vol = tick.get('volume', 0)
        if price <= 0: return False
        # 今日企稳: 不继续暴跌
        chg = (price / prev_close - 1) if prev_close > 0 else 0
        if chg < -0.05: return False  # 还在暴跌, 不接飞刀
        # 量比确认 (需要QMT提供历史量, 简化版用vol>0判断)
        if vol <= 0: return False
        return True
    except Exception:
        return False

def confirm_weak_to_strong(sym, tick, prev_close, open_price):
    """QMT实时: 弱转强竞价质量打分 v3.0 — AND逻辑+加权求和

    三维全过才计分 (陈小群铁律: 一项不过=不买)
    返回: 0-100 分数, 0=不通过
    """
    try:
        try:
            from sentiment_cycle import get_stage
            if get_stage() == "retreat":
                return 0
        except Exception: pass
        price = tick.get('lastPrice', 0)
        if price <= 0 or prev_close <= 0 or open_price <= 0:
            return 0
        gap_pct = (open_price / prev_close - 1) * 100
        bid_v = float(tick.get('bidVol', 0) or 0)
        ask_v = float(tick.get('askVol', 0) or 0)
        l2_ratio = bid_v / ask_v if ask_v > 0 else 0

        # ══ AND门槛: 一项不过=0 ══
        if gap_pct < 0:         return 0  # 低开
        if l2_ratio < 1.0:      return 0  # 卖方主导
        if price < open_price * 0.99: return 0  # 破开盘
        # 五帧撤单: 直接否决
        if tick.get('cancel_risk'): return 0

        # ══ 打分: 三项求和 (50起) ══
        sc = 50
        if 1.0 <= gap_pct <= 4.0:   sc += 25  # 最佳攻击区间
        elif gap_pct <= 6.0:         sc += 12  # 追高
        else:                        sc += 18  # 温和
        if l2_ratio >= 1.5:          sc += 20  # 主力抢筹
        elif l2_ratio >= 1.2:        sc += 12  # 买方
        else:                        sc += 6   # 平衡
        # 竞价量 (陈小群: 竞价量≥前日总量5%)
        _auction_vol = float(tick.get('volume', 0) or 0)
        if _auction_vol > 5000000:       sc += 15  # 竞价额>500万
        elif _auction_vol > 2000000:     sc += 8
        elif _auction_vol > 0:           sc += 3
        # 盘中强度
        if price >= open_price * 1.005: sc += 10  # 即冲

        # 缺口质量 (最后一秒价量变化) — 压测修正: 假诱多/真抛筹硬拦截
        _gap_d = float(tick.get('gap_delta', 0) or 0)
        _gap_v = float(tick.get('gap_vol_delta', 0) or 0)
        if _gap_d > 0 and _gap_v > 0:      sc += 10  # 真抢筹
        elif _gap_d > 0 and _gap_v < 0:    return 0  # 假诱多(硬拒: 价涨量缩=出货)
        elif _gap_d < 0 and _gap_v > 0:    return 0  # 真抛筹(硬拒: 价跌量增=出逃)
        elif _gap_d < 0 and _gap_v < 0:    sc += 5   # 假洗盘(机会)

        return max(0, min(sc, 100))
    except Exception:
        return 0
