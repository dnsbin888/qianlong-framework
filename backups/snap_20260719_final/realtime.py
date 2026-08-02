"""打板策略 实时确认 (QMT handle_bar调用)
数据源: QMT tick (lastPrice, bidVol, time) → 输出: True/False
"""
import sys, os, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from signals.daban.weights import (
    TIME_EARLY, TIME_MID, TIME_LATE, TIME_AFTERNOON,
    SEAL_STRONG, SEAL_MEDIUM, SEAL_WEAK,
    BREAK_NONE, BREAK_ONCE, BREAK_TWICE,
    RESEAL_BONUS, SECTOR_BONUS,
    REALTIME_PASS_SCORE,
)

def confirm_board(sym, tick, prev_close, limit_up_price, sector_bd_count=0, threshold=0.70):
    """QMT实时: 封板确认
    tick: QMT get_full_tick dict (含 lastPrice, bidVol, open, time)
    prev_close: 昨日收盘价
    limit_up_price: 涨停价
    sector_bd_count: 同板块涨停数
    threshold: 动态确认阈值 (市场自适应, 默认0.70)
    返回: True=触发买入, False=不触发
    """
    try:
        price = tick.get('lastPrice', 0)
        if price <= 0: return False

        # ① 必须涨停
        if abs(price - limit_up_price) / max(limit_up_price, 0.01) > 0.005:
            return False

        # ② 时间分
        now = datetime.datetime.now()
        if now.hour < 10 or (now.hour == 10 and now.minute == 0):
            time_score = TIME_EARLY
        elif now.hour == 10 and now.minute <= 30:
            time_score = TIME_MID
        elif now.hour == 10 or (now.hour == 11 and now.minute == 0):
            time_score = TIME_LATE
        else:
            time_score = TIME_AFTERNOON

        # ③ 封单分 (QMT盘口数据)
        bid_vol = tick.get('bidVol', 0) or tick.get('bid1_vol', 0)
        outstanding = tick.get('outstanding', 1e9)
        seal_ratio = bid_vol / max(outstanding, 1)
        if seal_ratio >= 0.03: seal_score = SEAL_STRONG
        elif seal_ratio >= 0.02: seal_score = SEAL_MEDIUM
        elif seal_ratio >= 0.01: seal_score = SEAL_WEAK
        else: return False  # 封单太弱, 不买

        # ④ 炸板检测 (简化: 用最低价)
        low = tick.get('low', price)
        was_open = low < limit_up_price * 0.99
        break_score = BREAK_ONCE if was_open else BREAK_NONE

        # ⑤ 回封加分
        reseal_bonus = RESEAL_BONUS if was_open else 1.0

        # ⑥ 板块联动
        sector_bonus = SECTOR_BONUS if sector_bd_count >= 3 else (1.15 if sector_bd_count>=1 else 1.0)

        # 综合评分
        quality = time_score * seal_score * break_score * reseal_bonus * sector_bonus
        return quality >= threshold

    except Exception:
        return False
