"""打板策略 日线评分 (Flask调用)
数据源: 日线OHLCV → 输出: score 0-100 + candidate pool
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

def score_board_quality(df, code="", sector_bd_count=0):
    """封板质量日线评分"""
    from daban_quality import score_board_quality as _score
    return _score(df, code, sector_bd_count)

def generate_daban_candidates(sd, factor_cache=None):
    """日线扫描涨停候选"""
    from daban_quality import generate_daban_candidates as _gen
    return _gen(sd)
