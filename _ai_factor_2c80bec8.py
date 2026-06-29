def factor_volume_breakout(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    if len(c) < 21 or len(v) < 21:
        return None
    # 计算20日最高价
    high20 = np.max(c[-21:-1])  # 前20个收盘价最高
    # 判断价格创20日新高
    price_new_high = c[-1] > high20
    # 计算成交量放大：当日成交量 > 过去20日均量的1.5倍
    avg_vol20 = np.mean(v[-21:-1])
    vol_surge = v[-1] > avg_vol20 * 1.5
    # 盘整：过去20日价格波动幅度小于15%
    low20 = np.min(c[-21:-1])
    range_ratio = (high20 - low20) / low20
    consolidation = range_ratio < 0.15
    if price_new_high and vol_surge and consolidation:
        return 1.0
    else:
        return 0.0