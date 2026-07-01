def factor_volume_breakout(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    if len(c) < 20 or len(v) < 20:
        return None
    high20 = np.max(c[-20:])
    vol_mean = np.mean(v[-20:-1])
    vol_std = np.std(v[-20:-1])
    if vol_mean == 0:
        return None
    vol_ratio = v[-1] / vol_mean
    if c[-1] > high20 * 0.99 and vol_ratio > 1.5:
        return float(vol_ratio)
    return None