def factor_volume_shrink_rebound(df):
    import numpy as np
    if len(df) < 21:
        return None
    vol = df["volume"].values
    close = df["close"].values
    low = df["low"].values
    ma20_vol = np.mean(vol[-20:])
    if ma20_vol == 0:
        return None
    # 判断最近一日成交量是否萎缩到20日均量的30%以下
    if vol[-1] / ma20_vol >= 0.3:
        return None
    # 判断价格企稳：最近5日最低点不低于之前20日最低点
    recent_low = np.min(low[-5:])
    previous_low = np.min(low[-25:-5])
    if recent_low < previous_low:
        return None
    # 判断反弹时成交量放大：最近一日成交量大于前一日
    if vol[-1] <= vol[-2]:
        return None
    # 计算反弹幅度
    rebound = close[-1] / np.min(close[-5:]) - 1
    return float(rebound) if rebound > 0 else None