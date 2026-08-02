"""ML训练工具 v1.0 — 时间衰减加权 (前沿: 近1年×1.0, 2年前×0.5, 3年前×0.2)"""
import numpy as np
from datetime import datetime

def time_decay_weights(dates, ref_date=None):
    """样本时间衰减权重: 近1年=1.0, 1-2年=0.5, 2-3年=0.2, >3年=0.1

    Args:
        dates: array of datetime or date strings
        ref_date: 参考日期(默认今天)
    Returns:
        weights: array of same length, [0.1, 1.0]
    """
    if ref_date is None:
        ref_date = datetime.now()
    if isinstance(ref_date, str):
        ref_date = datetime.fromisoformat(ref_date)

    days_ago = np.array([
        (ref_date - (d if hasattr(d, 'date') else datetime.fromisoformat(str(d)[:10]))).days
        for d in dates
    ])

    weights = np.ones(len(days_ago))
    weights[days_ago > 365] = 0.5    # 1-2年
    weights[days_ago > 730] = 0.2    # 2-3年
    weights[days_ago > 1095] = 0.1   # >3年
    return weights
