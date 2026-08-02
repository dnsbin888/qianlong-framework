"""超跌反弹 + 弱转强 日线评分 (Flask调用)
数据源: 日线OHLCV → 输出: score 0-100 + action buy/sell
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

def score_oversold(sd, factor_cache=None):
    """超跌反弹日线评分 (Connors RSI + Z-score)"""
    from reversal_strategy import generate_oversold_bounce
    return generate_oversold_bounce(sd, factor_cache)

def score_weak_to_strong(sd, factor_cache=None):
    """弱转强日线评分"""
    from reversal_strategy import generate_weak_to_strong
    return generate_weak_to_strong(sd, factor_cache)
