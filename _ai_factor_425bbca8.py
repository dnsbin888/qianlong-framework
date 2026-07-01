def factor_xxx(df):
    import numpy as np
    if len(df) < 20:
        return None
    volume = df["volume"].values
    close = df["close"].values
    open_ = df["open"].values
    vol_ma20 = np.mean(volume[-20:])
    if vol_ma20 == 0:
        return None
    vol_ratio = volume[-1] / vol_ma20
    if vol_ratio > 1.5 and close[-1] > open_[-1]:
        ret = close[-1] / open_[-1] - 1
        return float(vol_ratio * ret)
    return None