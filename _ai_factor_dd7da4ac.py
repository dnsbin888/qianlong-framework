def factor_vol_price_trend(df):
    import numpy as np
    c = df["close"].values
    v = df["volume"].values
    if len(c) < 20:
        return None
    close_ma = np.mean(c[-20:])
    vol_ma = np.mean(v[-20:])
    ret = (c[-1] - close_ma) / max(close_ma, 0.01)
    vol_ratio = v[-1] / max(vol_ma, 0.01)
    base = 50 + ret * 300 + (vol_ratio - 1) * 50
    clip = np.clip(base, 0, 100)
    return float(clip)